"""Domain model for a clearance pass.

The taxonomy here mirrors how clearance is actually practised, not a generic
"risk item" shape. Three things drive every downstream decision:

    category          what kind of right is implicated
    prominence        how much of the frame/story the material occupies
    identifiability   whether the thing is recognisable enough to be claimed

Those three, plus what research turns up about ownership, are the only inputs
the rules engine is allowed to consider. Keeping them explicit is what makes
the adjudication reproducible.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Literal

from pydantic import computed_field, BaseModel, Field, field_validator


# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------


class Category(str, Enum):
    """Kind of right implicated by a piece of material."""

    MUSIC_SYNC = "music_sync"  # a song: needs master + publishing, separately
    MUSIC_LIBRARY = "music_library"  # production library, usually pre-cleared
    ARCHIVAL_FOOTAGE = "archival_footage"  # stock, newsreel, documentary source
    FILM_TV_CLIP = "film_tv_clip"  # another production playing on-screen
    TRADEMARK = "trademark"  # visible brand, logo, livery, packaging
    ARTWORK = "artwork"  # painting, poster, mural, sculpture, photograph
    PERSON_LIKENESS = "person_likeness"  # recognisable person without a release
    LOCATION = "location"  # private property, distinctive architecture
    LITERARY_QUOTE = "literary_quote"  # quoted text, poem, spoken lyric
    SCRIPT_REFERENCE = "script_reference"  # real entity named in dialogue
    FONT_TYPEFACE = "font_typeface"  # licensed typeface in titles or graphics
    SIGNAGE_PRINT = "signage_print"  # newspapers, magazines, background signage


class Prominence(str, Enum):
    """How much weight the material carries on screen or in the story.

    Ordered least to most exposed. De minimis arguments live at the top of
    this scale and evaporate at the bottom.
    """

    INCIDENTAL = "incidental"  # fleeting, unfocused, edge of frame
    BACKGROUND = "background"  # visible but not the subject of the shot
    FEATURED = "featured"  # deliberately framed or audible in the clear
    HERO = "hero"  # the subject of the shot; camera rests on it
    PLOT_CRITICAL = "plot_critical"  # the story does not work without it


class Identifiability(str, Enum):
    """Whether a rights holder could recognise their property in the frame."""

    NOT_IDENTIFIABLE = "not_identifiable"
    PARTIAL = "partial"  # obscured, out of focus, partially cropped
    CLEARLY_IDENTIFIABLE = "clearly_identifiable"


class RiskTier(str, Enum):
    CLEARED = "cleared"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    BLOCKING = "blocking"  # E&O carrier will not bind with this outstanding

    @property
    def rank(self) -> int:
        return _TIER_RANK[self]


_TIER_RANK: dict[RiskTier, int] = {
    RiskTier.CLEARED: 0,
    RiskTier.LOW: 1,
    RiskTier.MEDIUM: 2,
    RiskTier.HIGH: 3,
    RiskTier.BLOCKING: 4,
}


class Action(str, Enum):
    NO_ACTION = "no_action"
    MONITOR = "monitor"
    OBTAIN_RELEASE = "obtain_release"
    OBTAIN_LICENSE = "obtain_license"
    OBSCURE_OR_BLUR = "obscure_or_blur"
    #: The production must supply the identification from its own records —
    #: a cue sheet, a location file, an art department purchase order. No
    #: amount of web research substitutes for it.
    IDENTIFY_SOURCE = "identify_source"
    LEGAL_REVIEW = "legal_review"
    REPLACE_ASSET = "replace_asset"
    REMOVE = "remove"


class Document(str, Enum):
    """The paper that actually has to end up in the delivery binder."""

    NONE = "none"
    SYNC_LICENSE = "sync_license"
    MASTER_USE_LICENSE = "master_use_license"
    ARCHIVAL_FOOTAGE_LICENSE = "archival_footage_license"
    CLIP_LICENSE = "clip_license"
    TRADEMARK_RELEASE = "trademark_release"
    ARTWORK_RELEASE = "artwork_release"
    APPEARANCE_RELEASE = "appearance_release"
    LOCATION_AGREEMENT = "location_agreement"
    FONT_LICENSE = "font_license"
    PUBLIC_DOMAIN_MEMO = "public_domain_memo"
    FAIR_USE_OPINION = "fair_use_opinion"


class Confidence(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class Source(str, Enum):
    VIDEO = "video"
    SCRIPT = "script"
    CUE_SHEET = "cue_sheet"
    MANUAL = "manual"


# ---------------------------------------------------------------------------
# Timecode
# ---------------------------------------------------------------------------

_TIMECODE_RE = re.compile(r"^(?:(\d{1,2}):)?([0-5]?\d):([0-5]?\d)$")


def parse_timecode(value: str) -> int:
    """Convert ``H:MM:SS`` or ``MM:SS`` to whole seconds.

    Gemini emits ``MM:SS`` for short material and ``H:MM:SS`` past the hour, so
    both have to round-trip.
    """
    match = _TIMECODE_RE.match(value.strip())
    if not match:
        raise ValueError(f"unparseable timecode: {value!r}")
    hours, minutes, seconds = match.groups()
    return int(hours or 0) * 3600 + int(minutes) * 60 + int(seconds)


def format_timecode(total_seconds: int) -> str:
    hours, remainder = divmod(max(0, int(total_seconds)), 3600)
    minutes, seconds = divmod(remainder, 60)
    return f"{hours}:{minutes:02d}:{seconds:02d}"


# ---------------------------------------------------------------------------
# Evidence
# ---------------------------------------------------------------------------


class Citation(BaseModel):
    """A web source backing a research finding.

    Mirrors Parallel's citation shape so basis data survives end to end. The
    excerpts matter as much as the URL: a clearance report that cannot quote
    its source is not reviewable by counsel.
    """

    url: str
    title: str | None = None
    excerpts: list[str] = Field(default_factory=list)


class FieldBasis(BaseModel):
    """Per-field evidence for one researched value."""

    field: str
    reasoning: str | None = None
    confidence: Confidence = Confidence.LOW
    citations: list[Citation] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Stage 1 — what Gemini spotted
# ---------------------------------------------------------------------------


class RiskItem(BaseModel):
    """One piece of potentially clearable material found in the cut or script.

    This is the output of the spotting pass and the input to research. It
    deliberately carries no opinion about what should be done — that is the
    rules engine's job.
    """

    id: str
    category: Category
    title: str = Field(description="Short human label, e.g. the song or brand name")
    description: str = Field(description="What is actually on screen or on the page")

    source: Source = Source.VIDEO
    start_seconds: int | None = None
    end_seconds: int | None = None
    scene: str | None = Field(default=None, description="Scene heading or page ref")

    prominence: Prominence = Prominence.BACKGROUND
    identifiability: Identifiability = Identifiability.PARTIAL
    audible_in_clear: bool = Field(
        default=False,
        description="Music only: audible without dialogue over it.",
    )

    depicted_negatively: bool = Field(
        default=False,
        description=(
            "Material shown in an unflattering, disparaging or endorsement-"
            "implying context. Flips several categories from expressive-use "
            "safe harbour into genuine exposure."
        ),
    )
    release_on_file: bool = Field(
        default=False,
        description="Production already holds signed paperwork for this item.",
    )
    no_published_identity: bool = Field(
        default=False,
        description=(
            "Material with no public identity: a one-off physical object (a "
            "hand-painted mural, a custom neon sign, a built prop) or footage "
            "made for this production. Distinct from mass-produced goods, "
            "recognised brands and published works, which the web can identify. "
            "Nothing on the open web can name the owner of a particular sign in "
            "a particular alley — so these are never sent to research, and the "
            "answer has to come from the production's own records."
        ),
    )

    detection_confidence: Confidence = Confidence.MEDIUM
    evidence: str | None = Field(
        default=None, description="What in the frame or text triggered the flag"
    )

    # Populated when the spotter can already name the work (e.g. from a cue
    # sheet) — saves a research hop.
    known_work: str | None = None
    known_year: int | None = None

    @field_validator("start_seconds", "end_seconds")
    @classmethod
    def _non_negative(cls, v: int | None) -> int | None:
        if v is not None and v < 0:
            raise ValueError("timecode seconds cannot be negative")
        return v

    @property
    def duration_seconds(self) -> int | None:
        if self.start_seconds is None or self.end_seconds is None:
            return None
        return max(0, self.end_seconds - self.start_seconds)

    @property
    def timecode(self) -> str | None:
        if self.start_seconds is None:
            return None
        start = format_timecode(self.start_seconds)
        if self.end_seconds is None:
            return start
        return f"{start}–{format_timecode(self.end_seconds)}"

    def research_subject(self) -> str:
        """The phrase handed to the research layer to identify this work."""
        parts = [self.known_work or self.title]
        if self.known_year:
            parts.append(f"({self.known_year})")
        return " ".join(parts)


# ---------------------------------------------------------------------------
# Stage 2 — what research turned up
# ---------------------------------------------------------------------------


#: Research legitimately returns placeholders in the holder slot — "public
#: domain", "unidentified mural artist(s)", "unknown". They are honest and
#: useful findings, but they name no party: you cannot license from them, write
#: to them, or treat them as having closed the chain of title. Everything that
#: asks "did we find an owner?" has to exclude them, or an unattributable work
#: silently reads as attributed.
_PLACEHOLDER_HOLDER_TOKENS = (
    "public domain",
    "unidentified",
    "not identified",
    "unattributed",
    "unknown",
    "unnamed",
    "no owner",
    "orphan",
    "n/a",
)


class RightsHolder(BaseModel):
    """A party with a claim on the material."""

    name: str
    role: str = Field(
        description=(
            "e.g. master owner, publisher, sub-publisher, estate, "
            "trademark proprietor, photographer, archive"
        )
    )
    territory: str | None = None
    share_percent: float | None = None
    contact: str | None = None
    contact_url: str | None = None
    notes: str | None = None

    @computed_field  # type: ignore[prop-decorator]
    @property
    def identified(self) -> bool:
        """Serialised so the browser does not have to re-implement the test.

        The UI needs to know whether an owner was actually found — that is the
        difference between "we traced it to Leon Carr" and "nobody knows" — and
        duplicating the placeholder heuristic in JavaScript would let the two
        drift apart.
        """
        return self.is_identified

    @property
    def is_identified(self) -> bool:
        """Whether this names an actual party rather than a placeholder."""
        name = self.name.strip().lower()
        if not name:
            return False
        return not any(token in name for token in _PLACEHOLDER_HOLDER_TOKENS)


class RightsFinding(BaseModel):
    """Research output for a single risk item.

    ``unresolved`` is the field that matters most. A finding that honestly
    reports "the 1971 master chain is broken at the label's 1975 sale" is far
    more useful than a confident guess, because it tells the producer to start
    the conversation now rather than three weeks before delivery.
    """

    item_id: str
    researched: bool = False
    applicable: bool = Field(
        default=True,
        description=(
            "Whether web research could answer this question at all. False for "
            "items triaged out deliberately — an unnamed extra in a crowd, a "
            "cue already covered by a blanket licence. Distinguishes 'we did "
            "not ask because asking is pointless' from 'we asked and failed', "
            "which carry very different weight."
        ),
    )

    holders: list[RightsHolder] = Field(default_factory=list)
    public_domain: bool | None = None
    public_domain_rationale: str | None = None

    first_publication_year: int | None = None
    territory_notes: str | None = None
    licensing_precedent: str | None = None
    typical_fee_low_usd: float | None = None
    typical_fee_high_usd: float | None = None

    unresolved: list[str] = Field(
        default_factory=list,
        description="Named gaps in the chain of title that a human must close",
    )

    confidence: Confidence = Confidence.LOW
    basis: list[FieldBasis] = Field(default_factory=list)

    provider: str = Field(default="parallel", description="Research backend used")
    processor: str | None = Field(default=None, description="Parallel processor tier")
    run_id: str | None = None
    error: str | None = None

    @property
    def citation_count(self) -> int:
        return sum(len(b.citations) for b in self.basis)

    @property
    def all_citations(self) -> list[Citation]:
        seen: dict[str, Citation] = {}
        for basis in self.basis:
            for citation in basis.citations:
                seen.setdefault(citation.url, citation)
        return list(seen.values())

    @property
    def identified_holders(self) -> list[RightsHolder]:
        """Holders that name a real party. See ``_PLACEHOLDER_HOLDER_TOKENS``."""
        return [h for h in self.holders if h.is_identified]

    def holders_by_role(self, *roles: str) -> list[RightsHolder]:
        wanted = {r.lower() for r in roles}
        return [h for h in self.identified_holders if h.role.lower() in wanted]


# ---------------------------------------------------------------------------
# Stage 3 — deterministic adjudication
# ---------------------------------------------------------------------------


class FiredRule(BaseModel):
    """Audit trail entry: which rule fired and why."""

    rule_id: str
    description: str


class ClearanceVerdict(BaseModel):
    """The adjudicated position on one item.

    Produced by pure Python. No model call sits between the evidence and this
    object, which is what lets the report say the same thing twice.
    """

    item_id: str
    tier: RiskTier
    action: Action
    documents: list[Document] = Field(default_factory=list)

    eo_blocking: bool = False
    rationale: str
    fired_rules: list[FiredRule] = Field(default_factory=list)

    fee_low_usd: float | None = None
    fee_high_usd: float | None = None
    lead_time_days: int | None = None

    @property
    def fee_band(self) -> str | None:
        if self.fee_low_usd is None and self.fee_high_usd is None:
            return None
        if self.fee_low_usd is not None and self.fee_high_usd is not None:
            return f"${self.fee_low_usd:,.0f}–${self.fee_high_usd:,.0f}"
        amount = self.fee_low_usd if self.fee_low_usd is not None else self.fee_high_usd
        return f"~${amount:,.0f}"


# ---------------------------------------------------------------------------
# Composite
# ---------------------------------------------------------------------------


class ClearanceEntry(BaseModel):
    """An item joined to its research and its verdict."""

    item: RiskItem
    finding: RightsFinding | None = None
    verdict: ClearanceVerdict | None = None
    outreach_draft: str | None = None


class ProjectMeta(BaseModel):
    title: str
    cut_label: str = Field(default="rough cut", description="e.g. 'rough cut v4'")
    runtime_seconds: int | None = None
    distribution_intent: str = Field(
        default="worldwide, all media, in perpetuity",
        description="Drives fee bands — festival-only is far cheaper than AVOD",
    )
    territories: list[str] = Field(default_factory=lambda: ["worldwide"])
    delivery_date: str | None = None


class RunStatus(str, Enum):
    PENDING = "pending"
    SPOTTING = "spotting"
    RESEARCHING = "researching"
    ADJUDICATING = "adjudicating"
    DRAFTING = "drafting"
    COMPLETE = "complete"
    FAILED = "failed"


class ClearanceReport(BaseModel):
    """The deliverable.

    This is the artefact a producer hands to an E&O carrier and a distributor's
    legal department.
    """

    run_id: str
    project: ProjectMeta
    status: RunStatus = RunStatus.PENDING
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    completed_at: datetime | None = None

    entries: list[ClearanceEntry] = Field(default_factory=list)
    summary: str | None = None
    error: str | None = None

    # Provenance, so the report can prove how it was produced.
    spotter_model: str | None = None
    research_provider: str | None = None
    stage_timings_ms: dict[str, int] = Field(default_factory=dict)

    # Set when this report is a replay of an earlier live run rather than a
    # fresh one. Surfacing it is not optional: a recording presented as a live
    # call would misrepresent what the system just did.
    replay_of: str | None = None
    replay_recorded_at: datetime | None = None

    @property
    def is_replay(self) -> bool:
        return self.replay_of is not None

    # -- rollups ------------------------------------------------------------

    @property
    def blocking_entries(self) -> list[ClearanceEntry]:
        return [
            e for e in self.entries if e.verdict is not None and e.verdict.eo_blocking
        ]

    @property
    def tier_counts(self) -> dict[str, int]:
        counts = {tier.value: 0 for tier in RiskTier}
        for entry in self.entries:
            if entry.verdict is not None:
                counts[entry.verdict.tier.value] += 1
        return counts

    @property
    def total_citations(self) -> int:
        return sum(e.finding.citation_count for e in self.entries if e.finding)

    @property
    def ownership_found(self) -> tuple[int, int]:
        """(items with a named owner, items actually researched).

        Every other figure on the report counts a problem. Without this one a
        pass that traced an owner for everything it looked at still reads as a
        list of failures — and the reader cannot tell "we could not find out"
        apart from "we found the owner and a gap remains".
        """
        researched = [e for e in self.entries if e.finding and e.finding.researched]
        named = [e for e in researched if e.finding.identified_holders]
        return len(named), len(researched)

    @property
    def estimated_cost_range(self) -> tuple[float, float]:
        low = sum(
            e.verdict.fee_low_usd
            for e in self.entries
            if e.verdict and e.verdict.fee_low_usd
        )
        high = sum(
            e.verdict.fee_high_usd
            for e in self.entries
            if e.verdict and e.verdict.fee_high_usd
        )
        return low, high

    @property
    def longest_lead_time_days(self) -> int:
        leads = [
            e.verdict.lead_time_days
            for e in self.entries
            if e.verdict and e.verdict.lead_time_days
        ]
        return max(leads) if leads else 0

    @property
    def eo_ready(self) -> bool:
        """Whether an E&O carrier could bind on this report as it stands."""
        return not self.blocking_entries

    def sorted_entries(self) -> list[ClearanceEntry]:
        """Most dangerous first, then earliest in the cut."""

        def key(entry: ClearanceEntry) -> tuple[int, int]:
            tier_rank = -entry.verdict.tier.rank if entry.verdict else 0
            start = entry.item.start_seconds if entry.item.start_seconds is not None else 10**9
            return (tier_rank, start)

        return sorted(self.entries, key=key)


# ---------------------------------------------------------------------------
# Progress events (streamed to the UI)
# ---------------------------------------------------------------------------


class ProgressEvent(BaseModel):
    run_id: str
    stage: RunStatus
    message: str
    at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    detail: dict[str, Any] = Field(default_factory=dict)
    kind: Literal["stage", "item", "error", "done"] = "stage"
