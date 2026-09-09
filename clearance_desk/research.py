"""Chain-of-title research via Parallel's Task API.

Why this layer exists at all
----------------------------
Asking a language model "who owns the master recording of this song" produces a
fluent, confident, frequently wrong answer. In clearance work a wrong answer is
worse than no answer: it produces a signed warranty that the production has
cleared something it has not, which is precisely the exposure an E&O policy is
supposed to close.

Parallel's Task API is used here because it returns *evidence*, not prose —
every field comes back with citations, excerpts and a confidence grade, and the
schema forces the research into the shape clearance actually needs (named
holders, named roles, named gaps). The rules engine downstream refuses to clear
anything whose evidence is thin, so honest uncertainty propagates instead of
being smoothed over.

Cost discipline
---------------
Deep research is billed per request and the hard tiers are slow, so items are
triaged before any call is made: categories where the web cannot answer the
question (an unnamed extra in a crowd) are skipped outright, and only genuine
chain-of-title problems get the expensive processor.
"""

from __future__ import annotations

import concurrent.futures
import logging
import re
from typing import Any, Callable, Protocol

from .config import Settings, get_settings
from .models import (
    Category,
    Citation,
    Confidence,
    FieldBasis,
    ProjectMeta,
    RightsFinding,
    RightsHolder,
    RiskItem,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Triage
# ---------------------------------------------------------------------------

#: Chain of title is genuinely hard for these — multiple owners, historical
#: transfers, estates, territory splits. Worth the deep processor.
_DEEP_CATEGORIES: frozenset[Category] = frozenset(
    {
        Category.MUSIC_SYNC,
        Category.ARCHIVAL_FOOTAGE,
        Category.FILM_TV_CLIP,
        Category.ARTWORK,
    }
)

#: The web cannot tell us who the extra in the background of a diner scene is,
#: and it cannot tell us whether the production already holds their release.
#: Researching these would burn budget to learn nothing.
_NO_RESEARCH_CATEGORIES: frozenset[Category] = frozenset(
    {
        Category.PERSON_LIKENESS,
        Category.MUSIC_LIBRARY,
    }
)


def needs_research(item: RiskItem) -> bool:
    """Whether a web research pass can actually move this item forward."""
    if item.release_on_file:
        return False
    if item.category is Category.MUSIC_SYNC and not item.known_work:
        # An unnamed cue cannot be researched: there is no work to look up.
        # Asked anyway, research returns a lecture on copyright law — Copyright
        # Office circulars, an ASCAP FAQ, a generic sync-fee article — at deep
        # processor rates, and those generic citations then pad the report's
        # source count while proving nothing about this cue.
        #
        # By this point the cue sheet has been applied and acoustic
        # identification has had its chance; if the work still has no name,
        # ID-001 already blocks it and asks the production for the cue sheet.
        # Nothing research can return would change that.
        return False
    if item.no_published_identity:
        # There is no registry of murals painted on particular walls, or of
        # neon signs made for particular bars. Research reliably comes back
        # "cannot identify this from a description" — which then reads as a
        # break in the chain of title and blocks the report, having cost real
        # money to learn nothing. The answer lives in the production's own
        # location file or art department records, not on the web.
        return False
    if item.category in _NO_RESEARCH_CATEGORIES:
        # A named public figure is the exception: their publicity rights and
        # any estate are a matter of public record.
        return (
            item.category is Category.PERSON_LIKENESS
            and item.depicted_negatively
            and bool(item.known_work or item.title)
        )
    return True


def processor_for(item: RiskItem, settings: Settings) -> str:
    return (
        settings.parallel_processor_deep
        if item.category in _DEEP_CATEGORIES
        else settings.parallel_processor_standard
    )


# ---------------------------------------------------------------------------
# Output schema
# ---------------------------------------------------------------------------

_HOLDER_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "name": {"type": "string", "description": "Legal name of the rights holder"},
        "role": {
            "type": "string",
            "description": (
                "One of: publisher, sub-publisher, master owner, label, estate, "
                "archive, photographer, artist, trademark proprietor, "
                "distributor, administrator, unknown"
            ),
        },
        "territory": {
            "type": "string",
            "description": "Territory this holder controls, or 'worldwide'",
        },
        "share_percent": {
            "type": "string",
            "description": "Ownership share if split, e.g. '50'. Empty if unknown.",
        },
        "contact": {
            "type": "string",
            "description": "Licensing contact name, department or email if published",
        },
        "contact_url": {
            "type": "string",
            "description": "URL of the licensing or rights enquiry page",
        },
        "notes": {"type": "string", "description": "Anything a clearance desk needs"},
    },
    "required": ["name", "role"],
    "additionalProperties": False,
}


def _output_schema(category: Category) -> dict[str, Any]:
    """Schema for the research task.

    Kept close to identical across categories so ``basis`` field paths map
    cleanly onto :class:`RightsFinding` regardless of what was researched.
    """
    return {
        "type": "object",
        "properties": {
            "rights_holders": {
                "type": "array",
                "items": _HOLDER_SCHEMA,
                "description": (
                    "Every party with a claim. For a song this MUST separate "
                    "the publisher(s) controlling the composition from the "
                    "owner of the specific sound recording — they are "
                    "different rights held by different companies."
                ),
            },
            "public_domain": {
                "type": "string",
                "description": (
                    "'yes', 'no' or 'unclear' — whether the underlying work is "
                    "out of copyright in the United States"
                ),
            },
            "public_domain_rationale": {
                "type": "string",
                "description": "Why, citing publication date and term",
            },
            "first_publication_year": {
                "type": "string",
                "description": "Year of first publication or release, if known",
            },
            "territory_notes": {
                "type": "string",
                "description": "Territory splits or restrictions affecting licensing",
            },
            "licensing_precedent": {
                "type": "string",
                "description": (
                    "Documented prior licensing of this work in film or "
                    "television, including any reported fee"
                ),
            },
            "typical_fee_low_usd": {
                "type": "string",
                "description": "Low end of reported/typical sync or licence fee in USD",
            },
            "typical_fee_high_usd": {
                "type": "string",
                "description": "High end of reported/typical licence fee in USD",
            },
            "unresolved_issues": {
                "type": "array",
                "items": {"type": "string"},
                "description": (
                    "Specific, named gaps in the chain of title that a human "
                    "must close — e.g. 'catalogue sold in 1975, acquirer not "
                    "identified in public records'. Be concrete. If the chain "
                    "is complete and documented, return an empty array."
                ),
            },
        },
        "required": ["rights_holders", "public_domain", "unresolved_issues"],
        "additionalProperties": False,
    }


_CATEGORY_BRIEF: dict[Category, str] = {
    Category.MUSIC_SYNC: (
        "Identify the chain of title for this musical work as it would need to "
        "be cleared for synchronisation in a film. Separate the composition "
        "(publishing) side from the specific sound recording (master) side. "
        "Name current controlling parties, not historical ones, and say "
        "explicitly where a transfer of ownership cannot be traced."
    ),
    Category.ARCHIVAL_FOOTAGE: (
        "Identify which archive or rights holder currently licenses this "
        "footage, and whether their licence covers the people, music and "
        "underlying works appearing inside the clip or excludes them."
    ),
    Category.FILM_TV_CLIP: (
        "Identify the current rights holder for this film or television "
        "production and the department that handles clip licensing, plus any "
        "known talent or guild residual obligations attaching to clip reuse."
    ),
    Category.ARTWORK: (
        "Identify the artist and the current copyright holder of this artwork "
        "(which is usually not the owner of the physical object), any estate "
        "or artists' rights society administering it, and its copyright status."
    ),
    Category.TRADEMARK: (
        "Identify the current proprietor of this trademark, the entity that "
        "handles brand-use approvals for film and television, and any publicly "
        "documented policy on depiction in entertainment."
    ),
    Category.LITERARY_QUOTE: (
        "Identify the publisher and rights administrator controlling permission "
        "to quote this text, and whether the work is in the public domain."
    ),
    Category.LOCATION: (
        "Identify the owner of this property or building and whether it has a "
        "published filming permissions policy or a protected architectural "
        "or trademark status."
    ),
    Category.SIGNAGE_PRINT: (
        "Identify the publisher or copyright holder of this printed material "
        "and their permissions contact."
    ),
    Category.FONT_TYPEFACE: (
        "Identify the type foundry that owns this typeface and whether their "
        "standard licence extends to film titles, broadcast and motion "
        "graphics."
    ),
    Category.PERSON_LIKENESS: (
        "Identify this individual's representation, and whether their right of "
        "publicity is administered by an estate or agency."
    ),
    Category.SCRIPT_REFERENCE: (
        "Identify the real entity referenced and any publicly documented "
        "litigation history over its depiction in entertainment."
    ),
    Category.MUSIC_LIBRARY: (
        "Identify the production music library that controls this cue and the "
        "scope of its blanket licence."
    ),
}


def build_task_input(item: RiskItem, project: ProjectMeta) -> dict[str, Any]:
    """The research brief handed to Parallel.

    Context matters: the same song cleared for a festival short and for a
    worldwide streaming release are different negotiations, and saying so up
    front produces more useful precedent.
    """
    brief = _CATEGORY_BRIEF.get(
        item.category, "Identify the current rights holders for this material."
    )
    return {
        "objective": brief,
        "material": item.research_subject(),
        "material_type": item.category.value,
        "as_seen_in_production": item.description,
        "on_screen_context": item.evidence or "",
        "timecode": item.timecode or "n/a",
        "prominence_in_cut": item.prominence.value,
        "intended_use": (
            f"Use in the film '{project.title}' for "
            f"{project.distribution_intent}, territories: "
            f"{', '.join(project.territories)}."
        ),
    }


# ---------------------------------------------------------------------------
# Provider interface
# ---------------------------------------------------------------------------


class ResearchProvider(Protocol):
    name: str

    def research(self, item: RiskItem, project: ProjectMeta) -> RightsFinding: ...


# ---------------------------------------------------------------------------
# Parsing helpers
# ---------------------------------------------------------------------------


def _as_bool(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if not isinstance(value, str):
        return None
    lowered = value.strip().lower()
    if lowered in {"yes", "true", "public domain", "pd"}:
        return True
    if lowered in {"no", "false", "in copyright", "copyrighted"}:
        return False
    return None


def _as_float(value: Any) -> float | None:
    if isinstance(value, (int, float)):
        return float(value)
    if not isinstance(value, str):
        return None
    cleaned = value.replace("$", "").replace(",", "").replace("USD", "").strip()
    if not cleaned:
        return None
    # Handle "10k"/"1.5m" shorthand that turns up in reported fees.
    multiplier = 1.0
    if cleaned[-1:].lower() == "k":
        multiplier, cleaned = 1_000.0, cleaned[:-1]
    elif cleaned[-1:].lower() == "m":
        multiplier, cleaned = 1_000_000.0, cleaned[:-1]
    try:
        return float(cleaned) * multiplier
    except ValueError:
        return None


def _as_int(value: Any) -> int | None:
    parsed = _as_float(value)
    return int(parsed) if parsed is not None else None


def _as_confidence(value: Any) -> Confidence:
    if isinstance(value, str):
        try:
            return Confidence(value.strip().lower())
        except ValueError:
            pass
    return Confidence.LOW


def _clean(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    stripped = value.strip()
    if not stripped or stripped.lower() in {"unknown", "n/a", "none", "unclear"}:
        return None
    return stripped


def _to_dict(obj: Any) -> Any:
    """Normalise SDK response objects into plain dicts/lists."""
    if obj is None or isinstance(obj, (str, int, float, bool)):
        return obj
    if isinstance(obj, dict):
        return {k: _to_dict(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_to_dict(v) for v in obj]
    for attr in ("model_dump", "dict", "to_dict"):
        method = getattr(obj, attr, None)
        if callable(method):
            try:
                return _to_dict(method())
            except TypeError:
                continue
    if hasattr(obj, "__dict__"):
        return {k: _to_dict(v) for k, v in vars(obj).items() if not k.startswith("_")}
    return obj


#: Placeholder titles the research backend uses when a page has no usable
#: <title>. Shown verbatim they make a citation list look broken, so they are
#: replaced with the source domain, which is what a reviewer actually scans for.
_GENERIC_TITLES = {"fetched web page", "web page", "untitled", "document", "pdf"}


def _citation_title(raw_title: Any, url: str) -> str | None:
    title = raw_title.strip() if isinstance(raw_title, str) else ""
    if title and title.lower() not in _GENERIC_TITLES:
        return title
    host = re.sub(r"^https?://(www\.)?", "", url).split("/")[0]
    return host or None


def parse_basis(raw_basis: Any) -> list[FieldBasis]:
    """Preserve Parallel's evidence trail verbatim.

    This is the part that must not be lossy — the citations are the reason the
    report is reviewable.
    """
    entries: list[FieldBasis] = []
    for raw in _to_dict(raw_basis) or []:
        if not isinstance(raw, dict):
            continue
        citations = []
        for raw_citation in raw.get("citations") or []:
            if not isinstance(raw_citation, dict):
                continue
            url = raw_citation.get("url")
            if not url:
                continue
            excerpts = raw_citation.get("excerpts") or []
            citations.append(
                Citation(
                    url=url,
                    title=_citation_title(raw_citation.get("title"), url),
                    excerpts=[e for e in excerpts if isinstance(e, str)],
                )
            )
        entries.append(
            FieldBasis(
                field=raw.get("field") or "unknown",
                reasoning=raw.get("reasoning"),
                confidence=_as_confidence(raw.get("confidence")),
                citations=citations,
            )
        )
    return entries


def _aggregate_confidence(basis: list[FieldBasis]) -> Confidence:
    """Overall confidence is governed by the holder fields, not the trivia.

    A run that is highly confident about a publication year but shaky about who
    owns the master is a low-confidence run for our purposes.
    """
    relevant = [b for b in basis if b.field.startswith("rights_holders")]
    pool = relevant or basis
    if not pool:
        return Confidence.LOW
    ranks = {Confidence.LOW: 0, Confidence.MEDIUM: 1, Confidence.HIGH: 2}
    worst = min(pool, key=lambda b: ranks[b.confidence])
    return worst.confidence


def finding_from_output(
    item: RiskItem,
    content: dict[str, Any],
    basis: list[FieldBasis],
    *,
    processor: str | None,
    run_id: str | None,
    provider: str,
) -> RightsFinding:
    holders: list[RightsHolder] = []
    for raw in content.get("rights_holders") or []:
        if not isinstance(raw, dict):
            continue
        name = _clean(raw.get("name"))
        if not name:
            continue
        holders.append(
            RightsHolder(
                name=name,
                role=_clean(raw.get("role")) or "unknown",
                territory=_clean(raw.get("territory")),
                share_percent=_as_float(raw.get("share_percent")),
                contact=_clean(raw.get("contact")),
                contact_url=_clean(raw.get("contact_url")),
                notes=_clean(raw.get("notes")),
            )
        )

    unresolved = [
        u.strip()
        for u in (content.get("unresolved_issues") or [])
        if isinstance(u, str) and u.strip()
    ]

    public_domain = _as_bool(content.get("public_domain"))

    # An empty holder list on a category that must have an owner is itself an
    # unresolved issue — say so rather than letting it read as "nothing found,
    # therefore nothing to clear". A work established to be in the public
    # domain is the exception: having no owner is the answer, not a gap.
    if (
        not holders
        and public_domain is not True
        and item.category not in _NO_RESEARCH_CATEGORIES
    ):
        unresolved.append(
            "No controlling rights holder could be identified from public sources."
        )

    return RightsFinding(
        item_id=item.id,
        researched=True,
        holders=holders,
        public_domain=public_domain,
        public_domain_rationale=_clean(content.get("public_domain_rationale")),
        first_publication_year=_as_int(content.get("first_publication_year")),
        territory_notes=_clean(content.get("territory_notes")),
        licensing_precedent=_clean(content.get("licensing_precedent")),
        typical_fee_low_usd=_as_float(content.get("typical_fee_low_usd")),
        typical_fee_high_usd=_as_float(content.get("typical_fee_high_usd")),
        unresolved=unresolved,
        confidence=_aggregate_confidence(basis),
        basis=basis,
        provider=provider,
        processor=processor,
        run_id=run_id,
    )


# ---------------------------------------------------------------------------
# Parallel provider
# ---------------------------------------------------------------------------


class ParallelResearcher:
    """Chain-of-title research backed by Parallel's Task API."""

    name = "parallel"

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        if not self.settings.parallel_api_key:
            raise RuntimeError(
                "PARALLEL_API_KEY is not set. Set it in .env, or set "
                "USE_FIXTURES=true to run against recorded research."
            )
        try:
            from parallel import Parallel
        except ImportError as exc:  # pragma: no cover - import guard
            raise RuntimeError(
                "The 'parallel-web' package is required. pip install parallel-web"
            ) from exc
        self._client = Parallel(api_key=self.settings.parallel_api_key)

    def research(self, item: RiskItem, project: ProjectMeta) -> RightsFinding:
        processor = processor_for(item, self.settings)
        try:
            run = self._client.task_run.create(
                input=build_task_input(item, project),
                task_spec={
                    "output_schema": {
                        "type": "json",
                        "json_schema": _output_schema(item.category),
                    }
                },
                processor=processor,
            )
            result = self._client.task_run.result(
                run.run_id, api_timeout=self.settings.parallel_timeout_seconds
            )
        except Exception as exc:  # noqa: BLE001 - surfaced into the report
            logger.exception("Parallel research failed for %s", item.id)
            return RightsFinding(
                item_id=item.id,
                researched=False,
                provider=self.name,
                processor=processor,
                error=f"{type(exc).__name__}: {exc}",
            )

        output = _to_dict(getattr(result, "output", None)) or {}
        content = output.get("content")
        if isinstance(content, str):
            # Text-schema fallback: keep the prose as an unresolved note rather
            # than pretending we parsed structure out of it.
            return RightsFinding(
                item_id=item.id,
                researched=True,
                unresolved=[content[:500]],
                basis=parse_basis(output.get("basis")),
                provider=self.name,
                processor=processor,
                run_id=getattr(run, "run_id", None),
            )

        return finding_from_output(
            item,
            content if isinstance(content, dict) else {},
            parse_basis(output.get("basis")),
            processor=processor,
            run_id=getattr(run, "run_id", None),
            provider=self.name,
        )


# ---------------------------------------------------------------------------
# Fixture provider (offline / test)
# ---------------------------------------------------------------------------


class FixtureResearcher:
    """Replays recorded research so the pipeline runs without network or spend."""

    name = "fixtures"

    def __init__(self, fixtures: dict[str, dict[str, Any]] | None = None) -> None:
        from .fixtures import RESEARCH_FIXTURES

        self._fixtures = fixtures if fixtures is not None else RESEARCH_FIXTURES

    def research(self, item: RiskItem, project: ProjectMeta) -> RightsFinding:
        key = (item.known_work or item.title).strip().lower()
        raw = self._fixtures.get(key)
        if raw is None:
            return RightsFinding(
                item_id=item.id,
                researched=True,
                unresolved=[
                    "Not researched — offline fixture mode. Configure "
                    "PARALLEL_API_KEY for live chain-of-title research."
                ],
                confidence=Confidence.LOW,
                provider=self.name,
            )
        return finding_from_output(
            item,
            raw.get("content", {}),
            parse_basis(raw.get("basis")),
            processor="fixture",
            run_id=f"fixture_{key.replace(' ', '_')[:24]}",
            provider=self.name,
        )


def get_research_provider(settings: Settings | None = None) -> ResearchProvider:
    settings = settings or get_settings()
    if settings.parallel_enabled:
        return ParallelResearcher(settings)
    logger.warning("Running in fixture mode — no live Parallel research.")
    return FixtureResearcher()


# ---------------------------------------------------------------------------
# Fan-out
# ---------------------------------------------------------------------------


def research_items(
    items: list[RiskItem],
    project: ProjectMeta,
    provider: ResearchProvider,
    *,
    max_workers: int = 6,
    on_result: Callable[[RiskItem, RightsFinding], None] | None = None,
) -> dict[str, RightsFinding]:
    """Research every item that warrants it, concurrently.

    Deep-tier calls can take minutes each, so a serial pass over a feature's
    worth of items would take longer than the meeting it is meant to inform.
    Items that triage says cannot benefit are short-circuited without a call.
    """
    findings: dict[str, RightsFinding] = {}

    # A song that plays three times in a cut is three items to clear but one
    # question to research. Group by the work itself so the expensive call is
    # made once and shared.
    representatives: dict[tuple[str, str], RiskItem] = {}
    cohort: dict[tuple[str, str], list[RiskItem]] = {}

    for item in items:
        if not needs_research(item):
            finding = RightsFinding(
                item_id=item.id,
                researched=False,
                applicable=False,
                provider="skipped",
            )
            findings[item.id] = finding
            if on_result:
                on_result(item, finding)
            continue

        key = (item.category.value, item.research_subject().strip().lower())
        cohort.setdefault(key, []).append(item)
        representatives.setdefault(key, item)

    if not representatives:
        return findings

    def _record(key: tuple[str, str], finding: RightsFinding) -> None:
        for member in cohort[key]:
            shared = finding.model_copy(update={"item_id": member.id})
            findings[member.id] = shared
            if on_result:
                on_result(member, shared)

    with concurrent.futures.ThreadPoolExecutor(
        max_workers=min(max_workers, len(representatives))
    ) as pool:
        futures = {
            pool.submit(provider.research, item, project): key
            for key, item in representatives.items()
        }
        for future in concurrent.futures.as_completed(futures):
            key = futures[future]
            try:
                finding = future.result()
            except Exception as exc:  # noqa: BLE001
                logger.exception("Research task crashed for %s", key)
                finding = RightsFinding(
                    item_id=representatives[key].id,
                    researched=False,
                    provider=provider.name,
                    error=f"{type(exc).__name__}: {exc}",
                )
            _record(key, finding)

    return findings
