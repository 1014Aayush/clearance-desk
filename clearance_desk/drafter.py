"""Outreach drafting and the report's executive summary.

Finding out that a 1968 master is controlled by a catalogue in Hamburg is only
half the job — somebody still has to write to them, and on a feature that is
forty near-identical letters that nobody has time to personalise. This module
writes the first draft.

Every generated draft has a deterministic template behind it. If Vertex AI is
unreachable, misconfigured or slow, the report still ships with a usable letter
rather than an empty field, because a clearance report that degrades into
blanks under load is not a deliverable.
"""

from __future__ import annotations

import logging

from .config import Settings, get_settings
from .models import (
    Action,
    Category,
    ClearanceEntry,
    ClearanceReport,
    ClearanceVerdict,
    Document,
    ProjectMeta,
    RightsFinding,
    RightsHolder,
    RiskItem,
    RiskTier,
)

logger = logging.getLogger(__name__)


_DRAFT_SYSTEM_INSTRUCTION = """\
You draft rights clearance correspondence for film productions.

Every letter MUST contain, in this order:

1. A salutation. "Dear <named recipient>," when one is given, otherwise
   "To whom it may concern,". Never omit it.
2. Who is writing, for which production, and what is being requested.
3. Where the material appears — exact timecode, approximate duration, and what
   is happening in the scene.
4. The rights sought, stating both term and territory.
5. For a musical work: a sentence noting that the composition and the sound
   recording may be controlled separately, and asking the recipient to say so
   if they control only one side. This is the most consequential line in a
   music request and must never be dropped.
6. Where the chain of title is unresolved: a request for guidance on current
   ownership.
7. A closing ask for confirmation, fee and any restrictions.

House style:

- Plain and professional. No marketing language, no flattery, no urgency
  theatre, no exclamation marks.
- Concrete over general. Every claim about the use should be checkable against
  the picture.
- Never assert that rights have been granted, cleared or agreed.
- Never invent a fee, a deadline, a contact name, or a prior conversation.

Return only the body of the email. No subject line, no signature block.
"""

_DOCUMENT_PHRASING: dict[Document, str] = {
    Document.SYNC_LICENSE: "a synchronisation licence",
    Document.MASTER_USE_LICENSE: "a master use licence",
    Document.ARCHIVAL_FOOTAGE_LICENSE: "an archival footage licence",
    Document.CLIP_LICENSE: "a clip licence",
    Document.TRADEMARK_RELEASE: "a trademark usage release",
    Document.ARTWORK_RELEASE: "an artwork reproduction release",
    Document.APPEARANCE_RELEASE: "a personal appearance release",
    Document.LOCATION_AGREEMENT: "a location filming agreement",
    Document.FONT_LICENSE: "a broadcast-scope typeface licence",
    Document.PUBLIC_DOMAIN_MEMO: "confirmation of public domain status",
    Document.FAIR_USE_OPINION: "a written permissions position",
}


def contactable_holders(finding: RightsFinding | None) -> list[RightsHolder]:
    """Holders you could actually post a letter to.

    Identical to the model's notion of an identified holder — you cannot write
    to "public domain" any more than you can license from it.
    """
    return finding.identified_holders if finding is not None else []


def _primary_holder(finding: RightsFinding | None) -> RightsHolder | None:
    candidates = contactable_holders(finding)
    if not candidates:
        return None
    # Prefer whoever actually issues the paper.
    by_role = {h.role.lower(): h for h in reversed(candidates)}
    for role in ("publisher", "master owner", "label", "archive", "artist", "estate"):
        if role in by_role:
            return by_role[role]
    return candidates[0]


def _usage_sentence(item: RiskItem, project: ProjectMeta) -> str:
    where = item.timecode or item.scene or "in the picture"
    duration = item.duration_seconds
    length = f" for approximately {duration} seconds" if duration else ""
    return (
        f"The material appears at {where}{length} in '{project.title}' "
        f"({project.cut_label}), where it is {item.prominence.value.replace('_', ' ')} "
        f"in the scene: {item.description.rstrip('.')}."
    )


def template_draft(
    item: RiskItem,
    finding: RightsFinding | None,
    verdict: ClearanceVerdict,
    project: ProjectMeta,
) -> str:
    """The deterministic fallback letter."""
    holder = _primary_holder(finding)
    salutation = f"Dear {holder.name}," if holder else "To whom it may concern,"

    asks = [
        _DOCUMENT_PHRASING[d] for d in verdict.documents if d in _DOCUMENT_PHRASING
    ] or ["permission for this use"]
    ask = asks[0] if len(asks) == 1 else ", ".join(asks[:-1]) + f" and {asks[-1]}"

    lines = [
        salutation,
        "",
        f"I am writing on behalf of the production '{project.title}' to request "
        f"{ask} for {item.research_subject()}.",
        "",
        _usage_sentence(item, project),
        "",
        f"We are seeking rights for {project.distribution_intent}, in the "
        f"following territories: {', '.join(project.territories)}.",
    ]

    if item.category is Category.MUSIC_SYNC:
        lines += [
            "",
            "We understand the composition and the sound recording may be "
            "controlled separately. If your organisation controls only one "
            "side, please let us know so we can approach the other directly.",
        ]

    if finding and finding.unresolved:
        lines += [
            "",
            "We would also be grateful for any guidance on the current chain of "
            "title, which we have not been able to establish from public "
            "sources.",
        ]

    lines += [
        "",
        "Could you confirm whether this use is possible, and let us know your "
        "fee and any restrictions? I am happy to provide a screener of the "
        "relevant scene.",
        "",
        "Thank you for your time.",
    ]
    return "\n".join(lines)


class Drafter:
    """Generates outreach copy and the report summary."""

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        self._client = None

    def _get_client(self):  # noqa: ANN202
        if self._client is not None:
            return self._client
        try:
            from google import genai
        except ImportError:
            logger.warning("google-genai unavailable; using template drafts only")
            return None
        try:
            if self.settings.google_genai_use_vertexai:
                if not self.settings.google_cloud_project:
                    return None
                self._client = genai.Client(
                    vertexai=True,
                    project=self.settings.google_cloud_project,
                    location=self.settings.google_cloud_location,
                )
            else:
                self._client = genai.Client()
        except Exception:  # noqa: BLE001
            logger.exception("Could not initialise Vertex AI client for drafting")
            return None
        return self._client

    # -- outreach -----------------------------------------------------------

    def draft_outreach(
        self,
        item: RiskItem,
        finding: RightsFinding | None,
        verdict: ClearanceVerdict,
        project: ProjectMeta,
    ) -> str:
        fallback = template_draft(item, finding, verdict, project)
        client = self._get_client()
        if client is None:
            return fallback

        holder = _primary_holder(finding)
        context = [
            f"Production: {project.title} ({project.cut_label})",
            f"Rights sought: {project.distribution_intent}",
            f"Territories: {', '.join(project.territories)}",
            f"Material: {item.research_subject()} ({item.category.value})",
            f"Appears at: {item.timecode or item.scene or 'unspecified'}",
            f"On screen: {item.description}",
            f"Prominence: {item.prominence.value}",
            f"Recipient: {holder.name if holder else 'unknown rights holder'}"
            + (f" ({holder.role})" if holder else ""),
            "Documents requested: "
            + ", ".join(
                _DOCUMENT_PHRASING.get(d, d.value) for d in verdict.documents
            ),
        ]
        if finding and finding.unresolved:
            context.append("Open chain-of-title questions: " + "; ".join(finding.unresolved[:2]))

        try:
            from google.genai import types

            response = client.models.generate_content(
                model=self.settings.drafter_model,
                contents=(
                    "Draft the body of a rights clearance request email.\n\n"
                    + "\n".join(context)
                ),
                config=types.GenerateContentConfig(
                    system_instruction=_DRAFT_SYSTEM_INSTRUCTION,
                    temperature=0.3,
                ),
            )
            text = (getattr(response, "text", "") or "").strip()
            return text or fallback
        except Exception:  # noqa: BLE001
            logger.exception("Draft generation failed for %s; using template", item.id)
            return fallback

    # -- summary ------------------------------------------------------------

    def summarise(self, report: ClearanceReport) -> str:
        deterministic = deterministic_summary(report)
        client = self._get_client()
        if client is None:
            return deterministic

        blockers = [
            f"- {e.item.research_subject()} ({e.item.category.value}) at "
            f"{e.item.timecode or e.item.scene or 'n/a'}: {e.verdict.rationale}"
            for e in report.blocking_entries
            if e.verdict
        ]
        try:
            from google.genai import types

            response = client.models.generate_content(
                model=self.settings.drafter_model,
                contents=(
                    "Write a three-sentence status note for a producer, based "
                    "strictly on the facts below. State the position plainly. "
                    "Do not add reassurance, recommendations beyond what is "
                    "listed, or any fact not given here.\n\n"
                    f"{deterministic}\n\n"
                    + ("Blocking items:\n" + "\n".join(blockers) if blockers else "")
                ),
                config=types.GenerateContentConfig(temperature=0.2),
            )
            text = (getattr(response, "text", "") or "").strip()
            return text or deterministic
        except Exception:  # noqa: BLE001
            logger.exception("Summary generation failed; using deterministic summary")
            return deterministic


def deterministic_summary(report: ClearanceReport) -> str:
    """A summary that is true by construction."""
    counts = report.tier_counts
    total = len(report.entries)
    low, high = report.estimated_cost_range
    blocking = len(report.blocking_entries)

    parts = [
        f"{total} clearable item{'s' if total != 1 else ''} identified in "
        f"'{report.project.title}' ({report.project.cut_label}), supported by "
        f"{report.total_citations} cited source"
        f"{'s' if report.total_citations != 1 else ''}."
    ]

    if blocking:
        parts.append(
            f"{blocking} item{'s' if blocking != 1 else ''} would prevent an "
            "errors-and-omissions policy from binding as the cut stands."
        )
    else:
        parts.append(
            "No item currently blocks errors-and-omissions cover on this cut."
        )

    parts.append(
        f"Risk profile: {counts[RiskTier.BLOCKING.value]} blocking, "
        f"{counts[RiskTier.HIGH.value]} high, {counts[RiskTier.MEDIUM.value]} medium, "
        f"{counts[RiskTier.LOW.value]} low, {counts[RiskTier.CLEARED.value]} cleared."
    )

    if high:
        parts.append(
            f"Estimated licensing exposure ${low:,.0f}–${high:,.0f} for the "
            f"stated distribution intent ({report.project.distribution_intent})."
        )

    lead = report.longest_lead_time_days
    if lead:
        parts.append(
            f"Longest expected lead time is {lead} days, which sets the "
            "earliest realistic delivery date."
        )

    return " ".join(parts)


def next_actions(report: ClearanceReport) -> list[str]:
    """The short list a producer should act on this week."""
    actions: list[str] = []
    for entry in report.sorted_entries():
        verdict = entry.verdict
        if verdict is None or verdict.tier.rank < RiskTier.HIGH.rank:
            continue
        where = entry.item.timecode or entry.item.scene or "unlocated"
        actions.append(
            f"[{verdict.tier.value.upper()}] {entry.item.research_subject()} "
            f"({where}) — {verdict.action.value.replace('_', ' ')}"
            + (f", allow {verdict.lead_time_days} days" if verdict.lead_time_days else "")
        )
    return actions[:10]


#: Paper that has to come from somebody else. A public domain memo and a fair
#: use opinion are written in-house, so neither implies an outgoing letter.
_THIRD_PARTY_DOCUMENTS: frozenset[Document] = frozenset(
    {
        Document.SYNC_LICENSE,
        Document.MASTER_USE_LICENSE,
        Document.ARCHIVAL_FOOTAGE_LICENSE,
        Document.CLIP_LICENSE,
        Document.TRADEMARK_RELEASE,
        Document.ARTWORK_RELEASE,
        Document.APPEARANCE_RELEASE,
        Document.LOCATION_AGREEMENT,
        Document.FONT_LICENSE,
    }
)


def should_draft(
    verdict: ClearanceVerdict | None, finding: RightsFinding | None
) -> bool:
    """Whether writing to somebody is a sensible next step for this item."""
    if verdict is None or verdict.action in (
        Action.NO_ACTION,
        Action.MONITOR,
        Action.REMOVE,
    ):
        return False

    if verdict.action in (Action.OBTAIN_LICENSE, Action.OBTAIN_RELEASE):
        return True

    # Otherwise the test is whether the verdict calls for paper from a named
    # third party we could actually reach. A blocked chain of title qualifies —
    # the last known holder is usually the only route to the current one — but
    # there is no letter to send about an unattributable mural, and none to
    # send to "public domain".
    if not contactable_holders(finding):
        return False
    if verdict.eo_blocking:
        return True
    return any(d in _THIRD_PARTY_DOCUMENTS for d in verdict.documents)


def entry_is_actionable(entry: ClearanceEntry) -> bool:
    return should_draft(entry.verdict, entry.finding)
