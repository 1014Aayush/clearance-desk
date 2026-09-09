"""Tests for the deterministic clearance engine.

These carry more weight than usual: the product's central claim is that the
same evidence produces the same verdict and that every verdict names the rules
behind it. That is a testable property, so it is tested.
"""

from __future__ import annotations

import pytest

from clearance_desk.models import (
    Action,
    Category,
    Confidence,
    Document,
    Identifiability,
    Prominence,
    ProjectMeta,
    RightsFinding,
    RightsHolder,
    RiskItem,
    RiskTier,
    format_timecode,
    parse_timecode,
)
from clearance_desk.rules import adjudicate, rule_catalogue


@pytest.fixture
def project() -> ProjectMeta:
    return ProjectMeta(title="Nightshift", cut_label="rough cut v4")


def item(**overrides) -> RiskItem:
    base = dict(
        id="itm_1",
        category=Category.TRADEMARK,
        title="Test item",
        description="A thing in the frame",
        prominence=Prominence.BACKGROUND,
        identifiability=Identifiability.PARTIAL,
    )
    base.update(overrides)
    return RiskItem(**base)


def researched(**overrides) -> RightsFinding:
    base = dict(item_id="itm_1", researched=True, confidence=Confidence.HIGH)
    base.update(overrides)
    return RightsFinding(**base)


# ---------------------------------------------------------------------------
# Timecode round-tripping
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text,seconds",
    [("00:14", 14), ("1:23", 83), ("12:05", 725), ("1:02:03", 3723), ("0:00", 0)],
)
def test_timecode_parses(text: str, seconds: int) -> None:
    assert parse_timecode(text) == seconds


def test_timecode_round_trip() -> None:
    assert format_timecode(parse_timecode("1:02:03")) == "1:02:03"


def test_bad_timecode_rejected() -> None:
    with pytest.raises(ValueError):
        parse_timecode("not a timecode")


# ---------------------------------------------------------------------------
# The determinism claim
# ---------------------------------------------------------------------------


def test_same_inputs_produce_identical_verdicts(project: ProjectMeta) -> None:
    subject = item(category=Category.MUSIC_SYNC, audible_in_clear=True)
    finding = researched(
        holders=[
            RightsHolder(name="Interior Music", role="publisher"),
            RightsHolder(name="Sussex Records", role="master owner"),
        ]
    )
    first = adjudicate(subject, finding, project)
    second = adjudicate(subject, finding, project)
    assert first.model_dump() == second.model_dump()


def test_every_verdict_cites_its_rules(project: ProjectMeta) -> None:
    for category in Category:
        verdict = adjudicate(
            item(category=category), researched(), project
        )
        assert verdict.fired_rules, f"{category} produced a verdict with no audit trail"
        assert verdict.rationale.strip()


# ---------------------------------------------------------------------------
# Music
# ---------------------------------------------------------------------------


def test_song_needs_both_licences(project: ProjectMeta) -> None:
    verdict = adjudicate(
        item(category=Category.MUSIC_SYNC, title="Ain't No Sunshine"),
        researched(holders=[RightsHolder(name="Interior Music", role="publisher")]),
        project,
    )
    assert verdict.action is Action.OBTAIN_LICENSE
    assert Document.SYNC_LICENSE in verdict.documents
    assert Document.MASTER_USE_LICENSE in verdict.documents


def test_public_domain_composition_does_not_free_the_master(
    project: ProjectMeta,
) -> None:
    """The nuance that catches real productions out."""
    verdict = adjudicate(
        item(category=Category.MUSIC_SYNC, title="Rhapsody in Blue"),
        researched(
            public_domain=True,
            first_publication_year=1924,
            holders=[RightsHolder(name="Columbia", role="master owner")],
        ),
        project,
    )
    assert Document.MASTER_USE_LICENSE in verdict.documents
    assert verdict.action is Action.OBTAIN_LICENSE
    assert verdict.tier is not RiskTier.CLEARED


def test_music_gets_no_de_minimis_relief(project: ProjectMeta) -> None:
    """DEM-001 must not reach sound recordings, however brief."""
    verdict = adjudicate(
        item(
            category=Category.MUSIC_SYNC,
            prominence=Prominence.INCIDENTAL,
            identifiability=Identifiability.PARTIAL,
            start_seconds=10,
            end_seconds=12,
            audible_in_clear=True,
        ),
        researched(holders=[RightsHolder(name="Label", role="master owner")]),
        project,
    )
    assert verdict.tier is RiskTier.HIGH
    assert "DEM-001" not in {r.rule_id for r in verdict.fired_rules}


def test_library_music_is_pre_cleared(project: ProjectMeta) -> None:
    verdict = adjudicate(
        item(category=Category.MUSIC_LIBRARY, title="Tension Bed 04"),
        researched(),
        project,
    )
    assert verdict.tier is RiskTier.LOW
    assert verdict.action is Action.MONITOR


# ---------------------------------------------------------------------------
# Trademark
# ---------------------------------------------------------------------------


def test_incidental_brand_needs_no_release(project: ProjectMeta) -> None:
    verdict = adjudicate(
        item(category=Category.TRADEMARK, prominence=Prominence.BACKGROUND),
        researched(),
        project,
    )
    assert verdict.action is Action.NO_ACTION
    assert verdict.tier is RiskTier.LOW


def test_hero_brand_escalates(project: ProjectMeta) -> None:
    verdict = adjudicate(
        item(
            category=Category.TRADEMARK,
            prominence=Prominence.HERO,
            identifiability=Identifiability.CLEARLY_IDENTIFIABLE,
        ),
        researched(),
        project,
    )
    assert verdict.tier is RiskTier.MEDIUM
    assert Document.TRADEMARK_RELEASE in verdict.documents


def test_disparaged_brand_goes_to_counsel(project: ProjectMeta) -> None:
    verdict = adjudicate(
        item(
            category=Category.TRADEMARK,
            prominence=Prominence.HERO,
            identifiability=Identifiability.CLEARLY_IDENTIFIABLE,
            depicted_negatively=True,
        ),
        researched(),
        project,
    )
    assert verdict.tier is RiskTier.HIGH
    assert verdict.action is Action.LEGAL_REVIEW


# ---------------------------------------------------------------------------
# Blocking conditions
# ---------------------------------------------------------------------------


def test_broken_chain_of_title_blocks_eo(project: ProjectMeta) -> None:
    verdict = adjudicate(
        item(category=Category.MUSIC_SYNC, title="Orphan Recording"),
        researched(
            holders=[RightsHolder(name="Unknown", role="publisher")],
            unresolved=["1975 label sale left the master chain unrecorded"],
        ),
        project,
    )
    assert verdict.tier is RiskTier.BLOCKING
    assert verdict.eo_blocking is True


def test_a_placeholder_holder_does_not_count_as_finding_the_artist(
    project: ProjectMeta,
) -> None:
    """Live research names the gap rather than returning nothing.

    Parallel returned "Unidentified mural artist(s)" for an unattributable
    mural. Treating that as an owner suppressed ART-002 and downgraded the
    remedy from 'replace the asset' to 'ask a lawyer', which is not a remedy.
    """
    verdict = adjudicate(
        item(
            category=Category.ARTWORK,
            prominence=Prominence.HERO,
            identifiability=Identifiability.CLEARLY_IDENTIFIABLE,
        ),
        researched(holders=[RightsHolder(name="Unidentified mural artist(s)", role="artist")]),
        project,
    )
    assert verdict.action is Action.REPLACE_ASSET
    assert "ART-002" in {r.rule_id for r in verdict.fired_rules}


@pytest.mark.parametrize(
    "name,identified",
    [
        ("Warner Chappell Music, Inc.", True),
        ("Friedrich-Wilhelm-Murnau-Stiftung", True),
        ("Unidentified mural artist(s)", False),
        ("Public domain (United States)", False),
        ("Unknown", False),
        ("Unattributed", False),
        ("", False),
    ],
)
def test_placeholder_holder_detection(name: str, identified: bool) -> None:
    assert RightsHolder(name=name, role="artist").is_identified is identified


def test_a_verdict_rationale_stays_readable(project: ProjectMeta) -> None:
    """Seen on a real run: three concatenated gap essays in the one line a
    producer reads first. The detail belongs in the gaps list, not here."""
    essay = (
        "Composition chain is missing: songwriter credits and shares, PRO data, "
        "publisher or administrator, sub-publishers, publishing agreements, "
        "assignments, catalogue sales, reversion notices, and the current party "
        "authorised to grant the worldwide sync licence."
    )
    verdict = adjudicate(
        item(category=Category.MUSIC_SYNC, known_work="Some Song"),
        researched(unresolved=[essay, essay, essay]),
        project,
    )
    assert len(verdict.rationale) < 400
    assert "further gap" in verdict.rationale


def test_a_single_short_gap_is_quoted_whole(project: ProjectMeta) -> None:
    verdict = adjudicate(
        item(category=Category.ARTWORK, known_work="A Work"),
        researched(unresolved=["the artist's estate could not be traced"]),
        project,
    )
    assert "estate could not be traced" in verdict.rationale
    assert "further gap" not in verdict.rationale


def test_unattributable_artwork_blocks(project: ProjectMeta) -> None:
    verdict = adjudicate(
        item(
            category=Category.ARTWORK,
            prominence=Prominence.HERO,
            identifiability=Identifiability.CLEARLY_IDENTIFIABLE,
        ),
        researched(holders=[]),
        project,
    )
    assert verdict.eo_blocking is True
    assert verdict.action is Action.REPLACE_ASSET


def test_blocking_beats_de_minimis(project: ProjectMeta) -> None:
    """A terminal rule must never mask a blocker."""
    verdict = adjudicate(
        item(
            category=Category.ARTWORK,
            prominence=Prominence.INCIDENTAL,
            identifiability=Identifiability.NOT_IDENTIFIABLE,
            start_seconds=0,
            end_seconds=2,
        ),
        researched(unresolved=["artist estate not traced"]),
        project,
    )
    assert verdict.eo_blocking is True
    assert verdict.tier is RiskTier.BLOCKING


def test_unresearched_item_is_never_cleared(project: ProjectMeta) -> None:
    verdict = adjudicate(
        item(category=Category.ARCHIVAL_FOOTAGE),
        RightsFinding(item_id="itm_1", researched=False),
        project,
    )
    assert verdict.tier.rank >= RiskTier.MEDIUM.rank
    assert "RES-001" in {r.rule_id for r in verdict.fired_rules}


# ---------------------------------------------------------------------------
# Mitigation
# ---------------------------------------------------------------------------


def test_de_minimis_clears_a_fleeting_visual(project: ProjectMeta) -> None:
    verdict = adjudicate(
        item(
            category=Category.SIGNAGE_PRINT,
            prominence=Prominence.INCIDENTAL,
            identifiability=Identifiability.NOT_IDENTIFIABLE,
            start_seconds=30,
            end_seconds=32,
        ),
        researched(),
        project,
    )
    assert verdict.tier is RiskTier.CLEARED


def test_paperwork_on_file_closes_the_item(project: ProjectMeta) -> None:
    verdict = adjudicate(
        item(
            category=Category.PERSON_LIKENESS,
            identifiability=Identifiability.CLEARLY_IDENTIFIABLE,
            release_on_file=True,
        ),
        researched(),
        project,
    )
    assert verdict.tier is RiskTier.CLEARED
    assert verdict.action is Action.NO_ACTION


def test_unidentifiable_crowd_member_is_cleared(project: ProjectMeta) -> None:
    verdict = adjudicate(
        item(
            category=Category.PERSON_LIKENESS,
            identifiability=Identifiability.NOT_IDENTIFIABLE,
        ),
        researched(),
        project,
    )
    assert verdict.tier is RiskTier.CLEARED


# ---------------------------------------------------------------------------
# Material the web cannot identify
#
# Found by running the real pipeline over real footage: six of eighteen items
# blocked because research was asked to identify a particular neon sign in a
# particular alley. It answered honestly — "cannot identify this from a
# description" — which then read as a broken chain of title. The questions were
# unanswerable and cost money to ask.
# ---------------------------------------------------------------------------


def test_one_off_object_is_not_treated_as_a_broken_chain(
    project: ProjectMeta,
) -> None:
    verdict = adjudicate(
        item(
            category=Category.SIGNAGE_PRINT,
            title="Diary neon sign",
            prominence=Prominence.FEATURED,
            identifiability=Identifiability.CLEARLY_IDENTIFIABLE,
            no_published_identity=True,
        ),
        RightsFinding(item_id="itm_1", researched=False, applicable=False),
        project,
    )
    assert verdict.eo_blocking is False
    assert verdict.tier is RiskTier.LOW
    assert Document.LOCATION_AGREEMENT in verdict.documents


def test_an_unattributable_mural_is_still_replaced(project: ProjectMeta) -> None:
    """ID-002 must not soften artwork: a mural is somebody's copyright."""
    verdict = adjudicate(
        item(
            category=Category.ARTWORK,
            title="Untitled alley mural",
            prominence=Prominence.HERO,
            identifiability=Identifiability.CLEARLY_IDENTIFIABLE,
            no_published_identity=True,
        ),
        RightsFinding(item_id="itm_1", researched=False, applicable=False),
        project,
    )
    assert verdict.action is Action.REPLACE_ASSET
    assert verdict.eo_blocking is True


def test_unidentified_music_blocks_and_asks_the_production(
    project: ProjectMeta,
) -> None:
    """The remedy is a cue sheet, not more searching."""
    verdict = adjudicate(
        item(category=Category.MUSIC_SYNC, title="Unnamed electronic score"),
        researched(),
        project,
    )
    assert verdict.tier is RiskTier.BLOCKING
    assert verdict.action is Action.IDENTIFY_SOURCE
    assert verdict.eo_blocking is True
    assert "cue sheet" in verdict.rationale


def test_named_music_is_not_flagged_for_identification(
    project: ProjectMeta,
) -> None:
    verdict = adjudicate(
        item(
            category=Category.MUSIC_SYNC,
            title="Rhapsody in Blue",
            known_work="Rhapsody in Blue",
        ),
        researched(holders=[RightsHolder(name="Warner Chappell", role="publisher")]),
        project,
    )
    assert "ID-001" not in {r.rule_id for r in verdict.fired_rules}
    assert verdict.action is Action.OBTAIN_LICENSE


# ---------------------------------------------------------------------------
# Dispositive facts vs judgement calls
# ---------------------------------------------------------------------------


def test_public_domain_beats_hero_prominence(project: ProjectMeta) -> None:
    """No amount of screen time creates a right that does not exist."""
    verdict = adjudicate(
        item(
            category=Category.FILM_TV_CLIP,
            title="Nosferatu",
            prominence=Prominence.PLOT_CRITICAL,
            identifiability=Identifiability.CLEARLY_IDENTIFIABLE,
        ),
        researched(public_domain=True, public_domain_rationale="Released 1922"),
        project,
    )
    assert verdict.tier is RiskTier.CLEARED
    assert Document.PUBLIC_DOMAIN_MEMO in verdict.documents


def test_de_minimis_yields_to_a_serious_escalation(project: ProjectMeta) -> None:
    """A judgement call is weaker than a fact and must not mask exposure."""
    verdict = adjudicate(
        item(
            category=Category.TRADEMARK,
            prominence=Prominence.INCIDENTAL,
            identifiability=Identifiability.NOT_IDENTIFIABLE,
            start_seconds=0,
            end_seconds=2,
            depicted_negatively=True,
        ),
        researched(),
        project,
    )
    assert verdict.tier is RiskTier.HIGH
    assert verdict.action is Action.LEGAL_REVIEW


def test_public_domain_still_blocks_when_the_work_cannot_be_pinned_down(
    project: ProjectMeta,
) -> None:
    """We cannot call a work public domain if we cannot say which work it is."""
    verdict = adjudicate(
        item(category=Category.FILM_TV_CLIP, title="Nosferatu"),
        researched(
            public_domain=True,
            unresolved=["Which restoration was used is not established"],
        ),
        project,
    )
    assert verdict.eo_blocking is True
    assert verdict.tier is RiskTier.BLOCKING


# ---------------------------------------------------------------------------
# Fee scaling
# ---------------------------------------------------------------------------


def test_festival_only_is_cheaper_than_worldwide() -> None:
    subject = item(category=Category.MUSIC_SYNC, prominence=Prominence.FEATURED)
    finding = researched(holders=[RightsHolder(name="Pub", role="publisher")])

    festival = adjudicate(
        subject, finding, ProjectMeta(title="X", distribution_intent="festival only")
    )
    worldwide = adjudicate(
        subject,
        finding,
        ProjectMeta(title="X", distribution_intent="worldwide, all media, in perpetuity"),
    )
    assert festival.fee_low_usd < worldwide.fee_low_usd


def test_research_comparables_override_the_heuristic_table(
    project: ProjectMeta,
) -> None:
    verdict = adjudicate(
        item(category=Category.MUSIC_SYNC, prominence=Prominence.FEATURED),
        researched(
            holders=[RightsHolder(name="Pub", role="publisher")],
            typical_fee_low_usd=100.0,
            typical_fee_high_usd=200.0,
        ),
        project,
    )
    assert verdict.fee_high_usd is not None
    assert verdict.fee_high_usd < 1_000


# ---------------------------------------------------------------------------
# Catalogue
# ---------------------------------------------------------------------------


def test_rule_ids_are_unique() -> None:
    ids = [r["rule_id"] for r in rule_catalogue()]
    assert len(ids) == len(set(ids))
