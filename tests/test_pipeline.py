"""End-to-end pipeline tests, run entirely offline.

These exercise the real orchestration, the real rules engine and the real
report rollups against recorded research, with drafting stubbed. They are the
regression net for the behaviour a judge or a producer actually sees.
"""

from __future__ import annotations

import pytest

from clearance_desk.config import Settings
from clearance_desk.demo import demo_items, demo_project
from clearance_desk.drafter import (
    deterministic_summary,
    entry_is_actionable,
    next_actions,
    template_draft,
)
from clearance_desk.models import (
    Action,
    Category,
    ClearanceReport,
    Document,
    ProgressEvent,
    RiskTier,
    RunStatus,
)
from clearance_desk.pipeline import ClearancePipeline, RunStore
from clearance_desk.research import FixtureResearcher


class StubDrafter:
    """Drafting without Vertex AI — exercises the deterministic fallbacks."""

    def draft_outreach(self, item, finding, verdict, project):  # noqa: ANN001
        return template_draft(item, finding, verdict, project)

    def summarise(self, report):  # noqa: ANN001
        return deterministic_summary(report)


@pytest.fixture
def pipeline() -> ClearancePipeline:
    return ClearancePipeline(
        settings=Settings(use_fixtures=True, google_cloud_project=""),
        provider=FixtureResearcher(),
        drafter=StubDrafter(),
    )


@pytest.fixture
def report(pipeline: ClearancePipeline) -> ClearanceReport:
    return pipeline.run(demo_project(), items=demo_items())


# ---------------------------------------------------------------------------
# The run completes and covers everything
# ---------------------------------------------------------------------------


def test_run_completes(report: ClearanceReport) -> None:
    assert report.status is RunStatus.COMPLETE
    assert report.error is None
    assert report.completed_at is not None


def test_every_item_is_adjudicated(report: ClearanceReport) -> None:
    assert len(report.entries) == len(demo_items())
    assert all(e.verdict is not None for e in report.entries)


def test_every_stage_is_timed(report: ClearanceReport) -> None:
    assert set(report.stage_timings_ms) == {"spot", "research", "adjudicate", "draft"}


def test_report_records_its_provenance(report: ClearanceReport) -> None:
    """A report must be able to say how it was produced."""
    assert report.research_provider == "fixtures"
    assert report.spotter_model


# ---------------------------------------------------------------------------
# The specific judgements the demo exists to show
# ---------------------------------------------------------------------------


def _entry(report: ClearanceReport, item_id: str):  # noqa: ANN202
    return next(e for e in report.entries if e.item.id == item_id)


def test_pd_composition_still_requires_the_master(report: ClearanceReport) -> None:
    verdict = _entry(report, "itm_demo_01").verdict
    assert verdict is not None
    assert Document.MASTER_USE_LICENSE in verdict.documents
    assert verdict.tier is not RiskTier.CLEARED


def test_background_brand_needs_nothing(report: ClearanceReport) -> None:
    verdict = _entry(report, "itm_demo_02").verdict
    assert verdict is not None
    assert verdict.action is Action.NO_ACTION


def test_same_brand_hero_framed_and_disparaged_escalates(
    report: ClearanceReport,
) -> None:
    """The distinction the product exists to make."""
    background = _entry(report, "itm_demo_02").verdict
    hero = _entry(report, "itm_demo_03").verdict
    assert background is not None and hero is not None
    assert hero.tier.rank > background.tier.rank
    assert hero.action is Action.LEGAL_REVIEW


def test_public_domain_artwork_clears_despite_hero_framing(
    report: ClearanceReport,
) -> None:
    verdict = _entry(report, "itm_demo_04").verdict
    assert verdict is not None
    assert verdict.tier is RiskTier.CLEARED


def test_unattributable_mural_blocks_delivery(report: ClearanceReport) -> None:
    verdict = _entry(report, "itm_demo_06").verdict
    assert verdict is not None
    assert verdict.eo_blocking is True
    assert verdict.action is Action.REPLACE_ASSET


def test_unidentifiable_extra_clears_and_identifiable_one_does_not(
    report: ClearanceReport,
) -> None:
    assert _entry(report, "itm_demo_07").verdict.tier is RiskTier.CLEARED
    assert _entry(report, "itm_demo_08").verdict.action is Action.OBTAIN_RELEASE


def test_executed_location_agreement_closes_the_item(
    report: ClearanceReport,
) -> None:
    verdict = _entry(report, "itm_demo_12").verdict
    assert verdict is not None
    assert verdict.tier is RiskTier.CLEARED


def test_every_verdict_carries_an_audit_trail(report: ClearanceReport) -> None:
    for entry in report.entries:
        assert entry.verdict is not None
        assert entry.verdict.fired_rules


# ---------------------------------------------------------------------------
# Rollups a producer reads first
# ---------------------------------------------------------------------------


def test_report_is_not_eo_ready_with_blockers(report: ClearanceReport) -> None:
    assert report.blocking_entries
    assert report.eo_ready is False


def test_tier_counts_add_up(report: ClearanceReport) -> None:
    assert sum(report.tier_counts.values()) == len(report.entries)


def test_cost_range_is_ordered(report: ClearanceReport) -> None:
    low, high = report.estimated_cost_range
    assert 0 <= low <= high


def test_citations_are_carried_into_the_report(report: ClearanceReport) -> None:
    assert report.total_citations > 0


def test_sorting_puts_the_dangerous_items_first(report: ClearanceReport) -> None:
    tiers = [e.verdict.tier.rank for e in report.sorted_entries() if e.verdict]
    assert tiers == sorted(tiers, reverse=True)


def test_summary_states_the_blocking_position(report: ClearanceReport) -> None:
    assert report.summary
    assert "errors-and-omissions" in report.summary


def test_next_actions_are_ordered_and_bounded(report: ClearanceReport) -> None:
    actions = next_actions(report)
    assert actions
    assert len(actions) <= 10
    assert actions[0].startswith("[BLOCKING]")


# ---------------------------------------------------------------------------
# Outreach drafting
# ---------------------------------------------------------------------------


def test_only_items_worth_writing_about_get_a_draft(
    report: ClearanceReport,
) -> None:
    drafted = [e for e in report.entries if e.outreach_draft]
    assert drafted
    for entry in report.entries:
        assert bool(entry.outreach_draft) == entry_is_actionable(entry)


def test_no_draft_when_there_is_nobody_to_write_to(
    report: ClearanceReport,
) -> None:
    """The unattributable mural blocks delivery, but no letter would help."""
    entry = _entry(report, "itm_demo_06")
    assert entry.verdict is not None and entry.verdict.eo_blocking
    assert entry.outreach_draft is None


def test_no_letter_is_addressed_to_a_legal_status(
    report: ClearanceReport,
) -> None:
    """You cannot post a licence request to 'public domain'."""
    entry = _entry(report, "itm_demo_01")
    assert entry.verdict is not None and entry.verdict.eo_blocking
    assert entry.outreach_draft is None
    for other in report.entries:
        if other.outreach_draft:
            assert "Dear Public domain" not in other.outreach_draft


def test_draft_names_the_work_and_the_use(report: ClearanceReport) -> None:
    entry = _entry(report, "itm_demo_03")
    assert entry.outreach_draft
    assert "Coca-Cola" in entry.outreach_draft
    assert "Nightshift" in entry.outreach_draft
    assert "0:45:11" in entry.outreach_draft


def test_draft_addresses_the_named_holder(report: ClearanceReport) -> None:
    draft = _entry(report, "itm_demo_03").outreach_draft
    assert draft and draft.startswith("Dear The Coca-Cola Company,")


def test_draft_falls_back_to_a_neutral_salutation(report: ClearanceReport) -> None:
    draft = _entry(report, "itm_demo_08").outreach_draft
    assert draft and draft.startswith("To whom it may concern,")


def test_music_draft_flags_the_two_sided_split() -> None:
    """Unit-tested directly: the demo's song has no publisher to write to."""
    from clearance_desk.models import RightsFinding, RightsHolder
    from clearance_desk.rules import adjudicate

    song = next(i for i in demo_items() if i.category is Category.MUSIC_SYNC)
    finding = RightsFinding(
        item_id=song.id,
        researched=True,
        holders=[RightsHolder(name="Example Music Publishing", role="publisher")],
    )
    project = demo_project()
    draft = template_draft(song, finding, adjudicate(song, finding, project), project)
    assert "controlled separately" in draft
    assert draft.startswith("Dear Example Music Publishing,")


def test_draft_has_no_doubled_punctuation(report: ClearanceReport) -> None:
    for entry in report.entries:
        if entry.outreach_draft:
            assert ".." not in entry.outreach_draft


def test_draft_never_claims_rights_were_granted(report: ClearanceReport) -> None:
    for entry in report.entries:
        if not entry.outreach_draft:
            continue
        lowered = entry.outreach_draft.lower()
        assert "has been cleared" not in lowered
        assert "we have secured" not in lowered


# ---------------------------------------------------------------------------
# Progress reporting
# ---------------------------------------------------------------------------


def test_progress_events_walk_the_stages(pipeline: ClearancePipeline) -> None:
    events: list[ProgressEvent] = []
    pipeline.run(demo_project(), items=demo_items(), on_event=events.append)
    stages = [e.stage for e in events]
    for stage in (
        RunStatus.RESEARCHING,
        RunStatus.ADJUDICATING,
        RunStatus.DRAFTING,
        RunStatus.COMPLETE,
    ):
        assert stage in stages
    assert events[-1].kind == "done"


def test_a_broken_progress_sink_cannot_fail_the_run(
    pipeline: ClearancePipeline,
) -> None:
    """Display is not allowed to destroy already-billed research.

    A Windows console that cannot encode an arrow once killed a five-minute
    live run on its way out of the research stage.
    """

    def hostile(event: ProgressEvent) -> None:
        raise UnicodeEncodeError("charmap", "→", 0, 1, "not encodable")

    report = pipeline.run(demo_project(), items=demo_items(), on_event=hostile)
    assert report.status is RunStatus.COMPLETE
    assert report.error is None
    assert len(report.entries) == len(demo_items())


def test_no_material_completes_cleanly(pipeline: ClearancePipeline) -> None:
    report = pipeline.run(demo_project(), items=[])
    assert report.status is RunStatus.COMPLETE
    assert report.entries == []
    assert report.summary


def test_a_failing_stage_fails_the_run_visibly(pipeline: ClearancePipeline) -> None:
    class Exploding:
        name = "exploding"

        def research(self, item, project):  # noqa: ANN001
            raise RuntimeError("nope")

    broken = ClearancePipeline(
        settings=Settings(use_fixtures=True),
        provider=Exploding(),
        drafter=StubDrafter(),
    )
    report = broken.run(demo_project(), items=demo_items())

    # Per-item failures are contained: the run still produces a usable report.
    assert report.status is RunStatus.COMPLETE
    assert len(report.entries) == len(demo_items())

    # Nothing that needs a licence may be presented as cleared on the strength
    # of research that never came back. (Items that are de minimis on the face
    # of the picture may still clear — that judgement does not depend on
    # knowing who owns the material.)
    licence_categories = {
        Category.MUSIC_SYNC,
        Category.ARCHIVAL_FOOTAGE,
        Category.FILM_TV_CLIP,
        Category.ARTWORK,
    }
    checked = 0
    for entry in report.entries:
        if entry.item.category not in licence_categories:
            continue
        assert entry.finding is not None
        if not entry.finding.applicable:
            # Triaged out before the provider was ever called — the one-off
            # mural has no public owner to look up, so there was nothing to
            # fail. It is still not cleared; ART-002 handles it.
            assert entry.verdict is not None
            assert entry.verdict.tier is not RiskTier.CLEARED
            continue
        assert entry.finding.error
        assert entry.verdict is not None
        assert entry.verdict.tier is not RiskTier.CLEARED
        checked += 1
    assert checked, "expected the demo to contain licensable material"


# ---------------------------------------------------------------------------
# Run store
# ---------------------------------------------------------------------------


def test_run_store_round_trips(report: ClearanceReport) -> None:
    store = RunStore()
    store.create(report.run_id, report)
    store.append_event(
        ProgressEvent(run_id=report.run_id, stage=RunStatus.COMPLETE, message="done")
    )
    assert store.get(report.run_id) is report
    assert len(store.events(report.run_id)) == 1
    assert store.events(report.run_id, since=1) == []
    assert store.list_runs()[0].run_id == report.run_id


def test_report_survives_serialisation(report: ClearanceReport) -> None:
    """The API hands this to a browser; it has to survive the trip."""
    restored = ClearanceReport.model_validate_json(report.model_dump_json())
    assert len(restored.entries) == len(report.entries)
    assert restored.tier_counts == report.tier_counts
