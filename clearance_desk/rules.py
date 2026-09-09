"""Deterministic clearance adjudication.

No language model runs in this module. Given the same risk item and the same
research finding, it returns the same verdict every time, and it names the
rules that produced it. That property is the whole point: a clearance report
that changes its mind between runs is not something counsel can rely on, and
"the model said so" is not a defensible basis for a distribution warranty.

The engine works in three passes:

    1. Every rule inspects the (item, finding) pair and may emit a Proposal.
    2. Escalating proposals are merged — highest tier wins, documents union,
       any blocking flag sticks.
    3. A terminal proposal (public domain, de minimis, paperwork already on
       file) can collapse the result, but never silently: the rules that were
       overridden stay in the audit trail.

Rule IDs are stable and are printed in the report so a reviewer can argue with
a specific rule rather than with the system as a whole.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Iterable

from .models import (
    Action,
    Category,
    ClearanceVerdict,
    Confidence,
    Document,
    FiredRule,
    Identifiability,
    Prominence,
    ProjectMeta,
    RightsFinding,
    RiskItem,
    RiskTier,
)

# ---------------------------------------------------------------------------
# Proposal plumbing
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Proposal:
    """One rule's opinion about an item."""

    rule_id: str
    description: str
    tier: RiskTier | None = None
    action: Action | None = None
    documents: tuple[Document, ...] = ()
    eo_blocking: bool = False
    fee_low: float | None = None
    fee_high: float | None = None
    lead_time_days: int | None = None
    #: Terminal proposals collapse the verdict instead of merging into it.
    #: Used for judgement calls — de minimis, expressive use — which should
    #: yield to any serious escalation.
    terminal: bool = False
    #: Dispositive proposals rest on a fact rather than a judgement: no
    #: copyright subsists, or the paperwork is already signed. These survive
    #: escalation, because no amount of prominence creates a right that does
    #: not exist. Only a blocked chain of title overrides them — if we cannot
    #: establish what the work *is*, we cannot assert it is free of claims.
    dispositive: bool = False
    note: str | None = None


@dataclass(frozen=True)
class Rule:
    rule_id: str
    description: str
    applies_to: tuple[Category, ...] | None
    evaluate: Callable[["RuleContext"], Proposal | None]


@dataclass
class RuleContext:
    item: RiskItem
    finding: RightsFinding
    project: ProjectMeta

    @property
    def is_visual(self) -> bool:
        return self.item.category in VISUAL_CATEGORIES

    @property
    def is_exposed(self) -> bool:
        """Framed deliberately, or the story depends on it."""
        return self.item.prominence in (Prominence.HERO, Prominence.PLOT_CRITICAL)

    @property
    def is_recognisable(self) -> bool:
        return self.item.identifiability is Identifiability.CLEARLY_IDENTIFIABLE

    @property
    def has_holders(self) -> bool:
        """Whether research actually named an owner.

        Placeholders such as "Unidentified mural artist(s)" are excluded — they
        are findings, not owners, and treating them as owners would suppress
        the rules that exist to catch unattributable material.
        """
        return bool(self.finding.identified_holders)

    @property
    def unresolved(self) -> bool:
        return bool(self.finding.unresolved)

    @property
    def is_unattributable(self) -> bool:
        """Nobody can be identified as the owner, and nobody will be.

        Either the material has no public identity at all, or research ran and
        came back empty. Public domain is excluded — having no owner because
        copyright expired is an answer, not a dead end.

        This distinction decides the *remedy*. Telling a producer to obtain a
        release from a person who cannot be found is not advice; the real
        options are to replace the material, obscure it, or accept the risk.
        """
        if self.finding.public_domain:
            return False
        if self.has_holders:
            return False
        return self.item.no_published_identity or self.finding.researched


VISUAL_CATEGORIES: frozenset[Category] = frozenset(
    {
        Category.TRADEMARK,
        Category.ARTWORK,
        Category.PERSON_LIKENESS,
        Category.LOCATION,
        Category.SIGNAGE_PRINT,
        Category.FONT_TYPEFACE,
    }
)

#: Categories where a licence is the expected outcome, so a broken chain of
#: title is a delivery blocker rather than a note.
LICENCE_CATEGORIES: frozenset[Category] = frozenset(
    {
        Category.MUSIC_SYNC,
        Category.ARCHIVAL_FOOTAGE,
        Category.FILM_TV_CLIP,
        Category.ARTWORK,
        Category.LITERARY_QUOTE,
    }
)

#: Action precedence when several rules propose different remedies.
_ACTION_PRIORITY: dict[Action, int] = {
    Action.NO_ACTION: 0,
    Action.MONITOR: 1,
    Action.OBSCURE_OR_BLUR: 2,
    Action.OBTAIN_RELEASE: 3,
    Action.OBTAIN_LICENSE: 4,
    Action.LEGAL_REVIEW: 5,
    # Identifying the material comes before deciding what to do about it: you
    # cannot licence, replace or sign off on something nobody can name.
    Action.IDENTIFY_SOURCE: 6,
    Action.REPLACE_ASSET: 7,
    Action.REMOVE: 8,
}

#: Baseline fee bands in USD for worldwide/all-media/perpetuity, before the
#: prominence and distribution multipliers below. Indie-feature scale.
_BASE_FEES: dict[Category, tuple[float, float]] = {
    Category.MUSIC_SYNC: (8_000, 35_000),
    Category.MUSIC_LIBRARY: (150, 900),
    Category.ARCHIVAL_FOOTAGE: (1_800, 6_500),
    Category.FILM_TV_CLIP: (7_000, 40_000),
    Category.ARTWORK: (1_200, 7_500),
    Category.LITERARY_QUOTE: (900, 5_000),
    Category.PERSON_LIKENESS: (0, 1_500),
    Category.LOCATION: (500, 4_000),
    Category.TRADEMARK: (0, 0),
    Category.SIGNAGE_PRINT: (300, 1_800),
    Category.FONT_TYPEFACE: (200, 1_200),
    Category.SCRIPT_REFERENCE: (0, 0),
}

_PROMINENCE_MULTIPLIER: dict[Prominence, float] = {
    Prominence.INCIDENTAL: 0.35,
    Prominence.BACKGROUND: 0.6,
    Prominence.FEATURED: 1.0,
    Prominence.HERO: 1.6,
    Prominence.PLOT_CRITICAL: 2.2,
}


def _distribution_multiplier(project: ProjectMeta) -> float:
    """Festival-only clearance is a fraction of worldwide-in-perpetuity."""
    intent = project.distribution_intent.lower()
    if "festival" in intent:
        return 0.25
    if "educational" in intent or "non-theatrical" in intent:
        return 0.4
    if "domestic" in intent or "single territory" in intent:
        return 0.6
    if "perpetuity" in intent or "worldwide" in intent or "all media" in intent:
        return 1.0
    return 0.8


def estimate_fee(ctx: RuleContext) -> tuple[float | None, float | None]:
    """Fee band for an item.

    Research-derived comparables beat our table whenever they exist — a real
    licensing precedent found on the web is better evidence than a heuristic.
    """
    if (
        ctx.finding.typical_fee_low_usd is not None
        or ctx.finding.typical_fee_high_usd is not None
    ):
        low = ctx.finding.typical_fee_low_usd
        high = ctx.finding.typical_fee_high_usd
    else:
        base = _BASE_FEES.get(ctx.item.category)
        if base is None:
            return None, None
        low, high = base

    if low is None and high is None:
        return None, None

    multiplier = _PROMINENCE_MULTIPLIER[ctx.item.prominence] * _distribution_multiplier(
        ctx.project
    )
    scaled_low = round(low * multiplier, -2) if low else low
    scaled_high = round(high * multiplier, -2) if high else high
    return scaled_low, scaled_high


# ---------------------------------------------------------------------------
# Rule registry
# ---------------------------------------------------------------------------

RULES: list[Rule] = []


def rule(
    rule_id: str,
    description: str,
    applies_to: Iterable[Category] | None = None,
) -> Callable[[Callable[[RuleContext], Proposal | None]], Callable]:
    def decorator(fn: Callable[[RuleContext], Proposal | None]) -> Callable:
        RULES.append(
            Rule(
                rule_id=rule_id,
                description=description,
                applies_to=tuple(applies_to) if applies_to else None,
                evaluate=fn,
            )
        )
        return fn

    return decorator


# --- Music -----------------------------------------------------------------


@rule(
    "MUS-001",
    "A song in picture needs two separate licences: synchronisation from the "
    "publisher and master use from the recording owner.",
    [Category.MUSIC_SYNC],
)
def mus_001(ctx: RuleContext) -> Proposal | None:
    low, high = estimate_fee(ctx)
    return Proposal(
        rule_id="MUS-001",
        description=(
            "Song in picture: synchronisation licence (publisher) and master "
            "use licence (recording owner) both required."
        ),
        tier=RiskTier.HIGH,
        action=Action.OBTAIN_LICENSE,
        documents=(Document.SYNC_LICENSE, Document.MASTER_USE_LICENSE),
        fee_low=low,
        fee_high=high,
        lead_time_days=30,
    )


@rule(
    "MUS-002",
    "A composition falling into the public domain does not free the recording. "
    "Pre-1930 compositions are PD in the US, but the specific master is a "
    "separate copyright.",
    [Category.MUSIC_SYNC],
)
def mus_002(ctx: RuleContext) -> Proposal | None:
    if not ctx.finding.public_domain:
        return None
    year = ctx.finding.first_publication_year
    masters = ctx.finding.holders_by_role("master owner", "label", "recording owner")
    if masters or (year is not None and year >= 1930):
        return Proposal(
            rule_id="MUS-002",
            description=(
                "Composition is public domain but the sound recording is a "
                "separate copyright — master use licence still required."
            ),
            tier=RiskTier.MEDIUM,
            action=Action.OBTAIN_LICENSE,
            documents=(Document.MASTER_USE_LICENSE, Document.PUBLIC_DOMAIN_MEMO),
            lead_time_days=21,
        )
    return Proposal(
        rule_id="MUS-002",
        description=(
            "Both composition and recording predate the copyright window; "
            "public domain memo sufficient."
        ),
        tier=RiskTier.LOW,
        action=Action.MONITOR,
        documents=(Document.PUBLIC_DOMAIN_MEMO,),
        dispositive=True,
    )


@rule(
    "MUS-003",
    "Music has no meaningful de minimis defence. Even a few audible seconds of "
    "a commercial recording is actionable, so duration does not mitigate.",
    [Category.MUSIC_SYNC],
)
def mus_003(ctx: RuleContext) -> Proposal | None:
    if not ctx.item.audible_in_clear:
        return None
    return Proposal(
        rule_id="MUS-003",
        description=(
            "Recording is audible in the clear — no de minimis mitigation "
            "available for sound recordings."
        ),
        tier=RiskTier.HIGH,
        action=Action.OBTAIN_LICENSE,
    )


@rule(
    "MUS-004",
    "Production library music is pre-cleared at source; the obligation is a "
    "correct cue sheet, not a negotiation.",
    [Category.MUSIC_LIBRARY],
)
def mus_004(ctx: RuleContext) -> Proposal | None:
    low, high = estimate_fee(ctx)
    return Proposal(
        rule_id="MUS-004",
        description=(
            "Production library cue — pre-cleared; file the licence and list it "
            "on the cue sheet."
        ),
        tier=RiskTier.LOW,
        action=Action.MONITOR,
        documents=(Document.SYNC_LICENSE,),
        fee_low=low,
        fee_high=high,
        lead_time_days=5,
        terminal=True,
    )


# --- Archival and clips ----------------------------------------------------


@rule(
    "ARC-001",
    "Archival footage requires a licence from the holding archive, and the "
    "archive's licence rarely covers the people or music inside the clip.",
    [Category.ARCHIVAL_FOOTAGE],
)
def arc_001(ctx: RuleContext) -> Proposal | None:
    low, high = estimate_fee(ctx)
    return Proposal(
        rule_id="ARC-001",
        description=(
            "Archival footage licence required; check whether nested rights "
            "(persons, music, underlying works) are included or excluded."
        ),
        tier=RiskTier.HIGH,
        action=Action.OBTAIN_LICENSE,
        documents=(Document.ARCHIVAL_FOOTAGE_LICENSE,),
        fee_low=low,
        fee_high=high,
        lead_time_days=21,
    )


@rule(
    "CLIP-001",
    "A film or television clip playing on screen is among the most expensive "
    "and slowest clearances: studio, talent and guild residuals can all attach.",
    [Category.FILM_TV_CLIP],
)
def clip_001(ctx: RuleContext) -> Proposal | None:
    low, high = estimate_fee(ctx)
    return Proposal(
        rule_id="CLIP-001",
        description=(
            "On-screen film/TV clip: studio licence plus potential talent and "
            "guild residual obligations."
        ),
        tier=RiskTier.HIGH,
        action=Action.OBTAIN_LICENSE,
        documents=(Document.CLIP_LICENSE,),
        fee_low=low,
        fee_high=high,
        lead_time_days=45,
    )


# --- Trademark -------------------------------------------------------------


@rule(
    "TM-001",
    "Incidental depiction of branded goods in a narrative work is ordinarily "
    "protected expressive use; no clearance is required by default.",
    [Category.TRADEMARK],
)
def tm_001(ctx: RuleContext) -> Proposal | None:
    if ctx.is_exposed or ctx.item.depicted_negatively:
        return None
    return Proposal(
        rule_id="TM-001",
        description=(
            "Brand appears in a narrative context without hero framing — "
            "expressive use, no implied endorsement; no release required."
        ),
        tier=RiskTier.LOW,
        action=Action.NO_ACTION,
        terminal=True,
    )


@rule(
    "TM-002",
    "A brand the camera rests on, or that the plot turns on, risks implying "
    "endorsement or sponsorship.",
    [Category.TRADEMARK],
)
def tm_002(ctx: RuleContext) -> Proposal | None:
    if not (ctx.is_exposed and ctx.is_recognisable):
        return None
    return Proposal(
        rule_id="TM-002",
        description=(
            "Brand is hero-framed and clearly legible — implied endorsement "
            "exposure; obtain a release or dress the shot."
        ),
        tier=RiskTier.MEDIUM,
        action=Action.OBTAIN_RELEASE,
        documents=(Document.TRADEMARK_RELEASE,),
        lead_time_days=14,
    )


@rule(
    "TM-003",
    "Brands shown in a disparaging light lose the expressive-use comfort and "
    "attract tarnishment and trade-libel claims.",
    [Category.TRADEMARK],
)
def tm_003(ctx: RuleContext) -> Proposal | None:
    if not ctx.item.depicted_negatively:
        return None
    return Proposal(
        rule_id="TM-003",
        description=(
            "Mark depicted unfavourably — tarnishment/trade-libel exposure; "
            "counsel should review before locking picture."
        ),
        tier=RiskTier.HIGH,
        action=Action.LEGAL_REVIEW,
        lead_time_days=10,
    )


# --- Artwork ---------------------------------------------------------------


@rule(
    "ART-001",
    "Artwork visible in shot is a separate copyrighted work owned by the "
    "artist, not by the owner of the physical object.",
    [Category.ARTWORK],
)
def art_001(ctx: RuleContext) -> Proposal | None:
    if ctx.is_unattributable:
        # There is no one to obtain a release from. ART-002 states the real
        # options instead of sending the producer after a phantom licensor.
        return None
    low, high = estimate_fee(ctx)
    if ctx.item.prominence in (Prominence.INCIDENTAL, Prominence.BACKGROUND) and not ctx.is_recognisable:
        return Proposal(
            rule_id="ART-001",
            description=(
                "Artwork appears only incidentally and is not reproduced "
                "legibly — monitor, no licence required."
            ),
            tier=RiskTier.LOW,
            action=Action.MONITOR,
        )
    return Proposal(
        rule_id="ART-001",
        description=(
            "Artwork is legibly reproduced — the artist's copyright is "
            "implicated; obtain an artwork release."
        ),
        tier=RiskTier.HIGH,
        action=Action.OBTAIN_RELEASE,
        documents=(Document.ARTWORK_RELEASE,),
        fee_low=low,
        fee_high=high,
        lead_time_days=21,
    )


@rule(
    "ART-002",
    "Unattributed artwork cannot be cleared. If research cannot name the "
    "artist, the practical remedy is to replace or obscure it.",
    [Category.ARTWORK],
)
def art_002(ctx: RuleContext) -> Proposal | None:
    if not ctx.is_unattributable:
        return None
    if not ctx.is_recognisable:
        return None
    if ctx.item.prominence is Prominence.INCIDENTAL:
        # Fleeting and unfocused; DEM-001 may clear it on its own terms.
        return None

    if ctx.item.prominence is Prominence.BACKGROUND:
        # Real but contained. Blurring a wall in the background is a day's
        # work in post, where reshooting is not.
        return Proposal(
            rule_id="ART-002",
            description=(
                "Artwork is legible but the artist cannot be identified, so no "
                "licence can be obtained. It sits in the background — obscure "
                "it in post, or accept the exposure on written advice."
            ),
            tier=RiskTier.MEDIUM,
            action=Action.OBSCURE_OR_BLUR,
            lead_time_days=5,
        )

    return Proposal(
        rule_id="ART-002",
        description=(
            "Artwork is featured and the artist cannot be identified — it "
            "cannot be licensed as shot. Replace it, obscure it, or reshoot."
        ),
        tier=RiskTier.BLOCKING,
        action=Action.REPLACE_ASSET,
        eo_blocking=True,
        lead_time_days=7,
    )


# --- Persons ---------------------------------------------------------------


@rule(
    "PER-001",
    "Anyone recognisable on screen needs an appearance release; crowd members "
    "who cannot be identified do not.",
    [Category.PERSON_LIKENESS],
)
def per_001(ctx: RuleContext) -> Proposal | None:
    if not ctx.is_recognisable:
        return Proposal(
            rule_id="PER-001",
            description=(
                "Person is not identifiable in frame — no appearance release "
                "required."
            ),
            tier=RiskTier.CLEARED,
            action=Action.NO_ACTION,
            terminal=True,
        )
    return Proposal(
        rule_id="PER-001",
        description="Identifiable person on camera — appearance release required.",
        tier=RiskTier.MEDIUM,
        action=Action.OBTAIN_RELEASE,
        documents=(Document.APPEARANCE_RELEASE,),
        lead_time_days=7,
    )


@rule(
    "PER-002",
    "Depicting a real, named individual in dramatised or unflattering "
    "material raises defamation and right-of-publicity exposure that a "
    "standard release does not cure.",
    [Category.PERSON_LIKENESS, Category.SCRIPT_REFERENCE],
)
def per_002(ctx: RuleContext) -> Proposal | None:
    if not ctx.item.depicted_negatively:
        return None
    return Proposal(
        rule_id="PER-002",
        description=(
            "Real individual portrayed unfavourably — defamation and right of "
            "publicity review required before lock."
        ),
        tier=RiskTier.HIGH,
        action=Action.LEGAL_REVIEW,
        lead_time_days=14,
    )


@rule(
    "SCR-001",
    "A neutral mention of a real entity in dialogue is ordinarily fine.",
    [Category.SCRIPT_REFERENCE],
)
def scr_001(ctx: RuleContext) -> Proposal | None:
    if ctx.item.depicted_negatively:
        return None
    return Proposal(
        rule_id="SCR-001",
        description="Neutral reference to a real entity in dialogue — no action.",
        tier=RiskTier.LOW,
        action=Action.NO_ACTION,
        terminal=True,
    )


# --- Location, print, type -------------------------------------------------


@rule(
    "LOC-001",
    "Filming identifiable private property requires the owner's permission; "
    "public thoroughfares generally do not.",
    [Category.LOCATION],
)
def loc_001(ctx: RuleContext) -> Proposal | None:
    low, high = estimate_fee(ctx)
    if not ctx.is_recognisable:
        return Proposal(
            rule_id="LOC-001",
            description="Location is not identifiable — no agreement required.",
            tier=RiskTier.CLEARED,
            action=Action.NO_ACTION,
            terminal=True,
        )
    return Proposal(
        rule_id="LOC-001",
        description=(
            "Identifiable private property — location agreement should be on "
            "file for delivery."
        ),
        tier=RiskTier.MEDIUM,
        action=Action.OBTAIN_RELEASE,
        documents=(Document.LOCATION_AGREEMENT,),
        fee_low=low,
        fee_high=high,
        lead_time_days=10,
    )


@rule(
    "LIT-001",
    "Publishers pursue quoted song lyrics aggressively; there is effectively "
    "no safe quantity.",
    [Category.LITERARY_QUOTE],
)
def lit_001(ctx: RuleContext) -> Proposal | None:
    low, high = estimate_fee(ctx)
    return Proposal(
        rule_id="LIT-001",
        description=(
            "Quoted literary or lyric text — licence from the publisher, or a "
            "written fair-use opinion, required."
        ),
        tier=RiskTier.MEDIUM,
        action=Action.OBTAIN_LICENSE,
        documents=(Document.SYNC_LICENSE, Document.FAIR_USE_OPINION),
        fee_low=low,
        fee_high=high,
        lead_time_days=21,
    )


@rule(
    "SGN-001",
    "Legible newspaper, magazine or poster copy in frame reproduces someone "
    "else's editorial and design work.",
    [Category.SIGNAGE_PRINT],
)
def sgn_001(ctx: RuleContext) -> Proposal | None:
    if not ctx.is_recognisable or not ctx.is_exposed:
        # Two different reasons to let it go, and they need different
        # sentences. Saying "not legible" about copy the spotter graded
        # clearly identifiable contradicts the evidence printed beside it.
        reason = (
            "Print material not legible in frame"
            if not ctx.is_recognisable
            else "Legible print material, but not the subject of the shot; "
            "reproduced as part of a real environment"
        )
        return Proposal(
            rule_id="SGN-001",
            description=f"{reason} — no action.",
            tier=RiskTier.LOW,
            action=Action.NO_ACTION,
            terminal=True,
        )
    low, high = estimate_fee(ctx)
    return Proposal(
        rule_id="SGN-001",
        description=(
            "Legible third-party print material reproduced on screen — clear "
            "or replace with production-made art."
        ),
        tier=RiskTier.MEDIUM,
        action=Action.REPLACE_ASSET,
        fee_low=low,
        fee_high=high,
        lead_time_days=7,
    )


@rule(
    "FONT-001",
    "Typeface licences for print or web rarely extend to broadcast titles and "
    "motion graphics.",
    [Category.FONT_TYPEFACE],
)
def font_001(ctx: RuleContext) -> Proposal | None:
    low, high = estimate_fee(ctx)
    return Proposal(
        rule_id="FONT-001",
        description=(
            "Typeface used in titles or graphics — confirm the licence covers "
            "film and broadcast use."
        ),
        tier=RiskTier.LOW,
        action=Action.MONITOR,
        documents=(Document.FONT_LICENSE,),
        fee_low=low,
        fee_high=high,
        lead_time_days=5,
    )


# --- Cross-cutting ---------------------------------------------------------


#: A rationale is the one line a producer reads before deciding what to do
#: about an item. Research can return several hundred words of gap analysis per
#: field; concatenated verbatim it produces a paragraph nobody finishes. The
#: full text is never lost — it is listed under "what nobody could establish".
_GAP_CHARS = 150


def _summarise_gaps(gaps: list[str]) -> str:
    """One readable sentence naming the first gap, and how many follow."""
    cleaned = [g.strip().rstrip(".") for g in gaps if g.strip()]
    if not cleaned:
        return "ownership could not be established."

    lead = cleaned[0]
    if len(lead) > _GAP_CHARS:
        cut = lead[:_GAP_CHARS].rsplit(" ", 1)[0]
        lead = f"{cut}…"

    remaining = len(cleaned) - 1
    if remaining:
        return f"{lead} (and {remaining} further gap{'s' if remaining > 1 else ''})."
    return f"{lead}."


@rule(
    "CHAIN-001",
    "A break in the chain of title is a delivery blocker: the carrier cannot "
    "bind and the distributor cannot accept delivery.",
)
def chain_001(ctx: RuleContext) -> Proposal | None:
    if not ctx.unresolved:
        return None
    if ctx.item.category not in LICENCE_CATEGORIES:
        return None
    # Two different findings were being reported in one sentence. "We named the
    # owner but cannot prove who holds the right today" and "nobody can say who
    # made this" are opposite outcomes with the same consequence, and stating
    # them identically made every researched item read as a failure. The legal
    # position is unchanged either way — same tier, same action, same lead time;
    # only the description distinguishes them.
    if ctx.has_holders:
        described = (
            f"Owner identified, but the chain to today is incomplete — "
            f"{_summarise_gaps(ctx.finding.unresolved)} "
            "Cannot be cleared until the current holder is confirmed."
        )
    else:
        described = (
            f"No owner could be identified — "
            f"{_summarise_gaps(ctx.finding.unresolved)} "
            "Cannot be cleared until ownership is established."
        )
    return Proposal(
        rule_id="CHAIN-001",
        description=described,
        tier=RiskTier.BLOCKING,
        action=Action.LEGAL_REVIEW,
        eo_blocking=True,
        lead_time_days=45,
    )


@rule(
    "RES-001",
    "An item that was never researched cannot be represented as cleared.",
)
def res_001(ctx: RuleContext) -> Proposal | None:
    if ctx.finding.researched:
        return None
    if not ctx.finding.applicable:
        # Triaged out on purpose — the web was never going to identify a
        # background extra. The category rules govern this item instead.
        return None
    return Proposal(
        rule_id="RES-001",
        description=(
            "No rights research completed for this item — status unknown, "
            "manual review required."
        ),
        tier=RiskTier.MEDIUM,
        action=Action.LEGAL_REVIEW,
    )


@rule(
    "RES-002",
    "Low-confidence research is a prompt for human verification, not a "
    "conclusion.",
)
def res_002(ctx: RuleContext) -> Proposal | None:
    if not ctx.finding.researched or ctx.finding.confidence is not Confidence.LOW:
        return None
    if not ctx.has_holders:
        return None
    return Proposal(
        rule_id="RES-002",
        description=(
            "Ownership identified with low confidence — verify against the "
            "cited sources before relying on it."
        ),
        tier=RiskTier.MEDIUM,
        action=Action.LEGAL_REVIEW,
    )


@rule(
    "ID-001",
    "Music that cannot be named cannot be cleared or delivered — but the "
    "answer is a cue sheet from the production, not a web search.",
    [Category.MUSIC_SYNC],
)
def id_001(ctx: RuleContext) -> Proposal | None:
    if ctx.item.known_work or ctx.has_holders:
        return None
    return Proposal(
        rule_id="ID-001",
        description=(
            "Music is unidentified. A cut cannot be delivered with a cue that "
            "nobody can name — obtain the cue sheet from the composer or "
            "picture editor, then clear against it."
        ),
        tier=RiskTier.BLOCKING,
        action=Action.IDENTIFY_SOURCE,
        eo_blocking=True,
        lead_time_days=7,
    )


@rule(
    "ID-002",
    "A one-off physical object has no public owner to find. It is cleared by "
    "the location agreement covering the shoot, or by changing the shot — not "
    "by research.",
)
def id_002(ctx: RuleContext) -> Proposal | None:
    if not ctx.item.no_published_identity:
        return None
    if ctx.item.category is Category.ARTWORK:
        # A mural is still somebody's copyright even when nobody can say
        # whose. ART-002 already reaches the right answer — replace or
        # obscure it — and must not be softened here.
        return None
    if ctx.item.category is Category.FILM_TV_CLIP:
        # Footage with no public identity was shot for this production, so
        # there is no third-party licence to obtain. The people in it still
        # need releases, which PER-001 governs separately.
        return Proposal(
            rule_id="ID-002",
            description=(
                "Footage has no published identity — production-originated "
                "material, so no third-party clip licence applies. Confirm it "
                "came from the production's own rushes."
            ),
            tier=RiskTier.LOW,
            action=Action.MONITOR,
            dispositive=True,
            lead_time_days=3,
        )
    return Proposal(
        rule_id="ID-002",
        description=(
            "One-off physical object with no public identity; no rights holder "
            "is discoverable. Ordinarily covered by the location agreement for "
            "the shoot — confirm that agreement extends to set dressing."
        ),
        tier=RiskTier.LOW,
        action=Action.MONITOR,
        documents=(Document.LOCATION_AGREEMENT,),
        terminal=True,
        lead_time_days=5,
    )


@rule(
    "DEM-001",
    "De minimis: a visual work that is fleeting, unfocused and unidentifiable "
    "is not a reproduction. Never applies to sound recordings.",
)
def dem_001(ctx: RuleContext) -> Proposal | None:
    if not ctx.is_visual:
        return None
    if ctx.item.prominence is not Prominence.INCIDENTAL:
        return None
    if ctx.item.identifiability is Identifiability.CLEARLY_IDENTIFIABLE:
        return None
    duration = ctx.item.duration_seconds
    if duration is not None and duration > 3:
        return None
    return Proposal(
        rule_id="DEM-001",
        description=(
            "Fleeting, unfocused and unidentifiable in frame — de minimis; no "
            "clearance required."
        ),
        tier=RiskTier.CLEARED,
        action=Action.NO_ACTION,
        terminal=True,
    )


@rule(
    "PD-001",
    "Material in the public domain needs a memo recording why, not a licence.",
)
def pd_001(ctx: RuleContext) -> Proposal | None:
    if not ctx.finding.public_domain:
        return None
    if ctx.item.category is Category.MUSIC_SYNC:
        return None  # MUS-002 handles the split composition/recording case
    return Proposal(
        rule_id="PD-001",
        description=(
            "Public domain — "
            + (ctx.finding.public_domain_rationale or "no subsisting copyright")
            + "; file a public domain memo."
        ),
        tier=RiskTier.CLEARED,
        action=Action.NO_ACTION,
        documents=(Document.PUBLIC_DOMAIN_MEMO,),
        dispositive=True,
    )


@rule(
    "DOC-001",
    "Paperwork already on file closes the item.",
)
def doc_001(ctx: RuleContext) -> Proposal | None:
    if not ctx.item.release_on_file:
        return None
    return Proposal(
        rule_id="DOC-001",
        description="Signed paperwork already on file for this item.",
        tier=RiskTier.CLEARED,
        action=Action.NO_ACTION,
        dispositive=True,
    )


# ---------------------------------------------------------------------------
# Engine
# ---------------------------------------------------------------------------


def adjudicate(
    item: RiskItem,
    finding: RightsFinding | None,
    project: ProjectMeta,
) -> ClearanceVerdict:
    """Reduce an item plus its research to a single defensible position."""
    finding = finding or RightsFinding(item_id=item.id, researched=False)
    ctx = RuleContext(item=item, finding=finding, project=project)

    proposals: list[Proposal] = []
    for r in RULES:
        if r.applies_to is not None and item.category not in r.applies_to:
            continue
        proposal = r.evaluate(ctx)
        if proposal is not None:
            proposals.append(proposal)

    if not proposals:
        return ClearanceVerdict(
            item_id=item.id,
            tier=RiskTier.MEDIUM,
            action=Action.LEGAL_REVIEW,
            rationale=(
                "No rule matched this item. Treated as unresolved pending "
                "manual review."
            ),
            fired_rules=[
                FiredRule(
                    rule_id="FALLBACK",
                    description="No category rule matched; defaulted to review.",
                )
            ],
        )

    fired = [FiredRule(rule_id=p.rule_id, description=p.description) for p in proposals]

    escalating = [p for p in proposals if not (p.terminal or p.dispositive)]
    terminal = [p for p in proposals if p.terminal and not p.dispositive]
    dispositive = [p for p in proposals if p.dispositive]

    blocking = any(p.eo_blocking for p in escalating)
    highest_escalating = max(
        (p.tier for p in escalating if p.tier is not None),
        key=lambda t: t.rank,
        default=None,
    )

    # A dispositive fact — the work is out of copyright, or the release is
    # signed — outranks any escalation short of a chain of title we cannot
    # establish. A judgement call (de minimis, expressive use) is weaker: it
    # only holds when nothing serious escalated against it.
    collapsing = dispositive or (
        terminal
        if not blocking
        and (
            highest_escalating is None
            or highest_escalating.rank <= RiskTier.MEDIUM.rank
        )
        else []
    )

    if collapsing and not blocking:
        winner = min(collapsing, key=lambda p: p.tier.rank if p.tier else 99)
        documents = _union_documents(collapsing)
        return ClearanceVerdict(
            item_id=item.id,
            tier=winner.tier or RiskTier.CLEARED,
            action=winner.action or Action.NO_ACTION,
            documents=documents,
            eo_blocking=False,
            rationale=winner.description,
            fired_rules=fired,
            fee_low_usd=winner.fee_low,
            fee_high_usd=winner.fee_high,
            lead_time_days=winner.lead_time_days,
        )

    tier = highest_escalating or RiskTier.MEDIUM
    action = max(
        (p.action for p in escalating if p.action is not None),
        key=lambda a: _ACTION_PRIORITY[a],
        default=Action.LEGAL_REVIEW,
    )
    documents = _union_documents(escalating)

    fee_low = _max_optional(p.fee_low for p in escalating)
    fee_high = _max_optional(p.fee_high for p in escalating)
    lead_time = _max_optional(p.lead_time_days for p in escalating)

    driving = [p for p in escalating if p.tier is tier] or escalating
    rationale = " ".join(p.description for p in driving[:2])

    return ClearanceVerdict(
        item_id=item.id,
        tier=tier,
        action=action,
        documents=documents,
        eo_blocking=blocking,
        rationale=rationale,
        fired_rules=fired,
        fee_low_usd=fee_low,
        fee_high_usd=fee_high,
        lead_time_days=int(lead_time) if lead_time is not None else None,
    )


def _union_documents(proposals: Iterable[Proposal]) -> list[Document]:
    seen: dict[Document, None] = {}
    for proposal in proposals:
        for doc in proposal.documents:
            seen.setdefault(doc, None)
    return [d for d in seen if d is not Document.NONE]


def _max_optional(values: Iterable[float | int | None]) -> float | None:
    present = [v for v in values if v is not None]
    return max(present) if present else None


def rule_catalogue() -> list[dict[str, str]]:
    """Every rule, for the UI's 'why did it decide that' panel."""
    return [
        {
            "rule_id": r.rule_id,
            "description": r.description,
            "applies_to": ", ".join(c.value for c in r.applies_to)
            if r.applies_to
            else "all categories",
        }
        for r in RULES
    ]
