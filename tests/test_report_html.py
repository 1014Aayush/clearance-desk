"""Tests for the printable clearance report.

This is the artefact that leaves the building — printed, attached to an email,
read by someone who has never seen the tool. So the tests are about what a
reader must not be able to miss, and about not leaking markup.
"""

from __future__ import annotations

from html import escape

import pytest

from clearance_desk.config import Settings
from clearance_desk.demo import demo_items, demo_project
from clearance_desk.drafter import deterministic_summary, template_draft
from clearance_desk.models import ClearanceReport
from clearance_desk.pipeline import ClearancePipeline
from clearance_desk.report_html import render_html
from clearance_desk.research import FixtureResearcher


class StubDrafter:
    def draft_outreach(self, item, finding, verdict, project):  # noqa: ANN001
        return template_draft(item, finding, verdict, project)

    def summarise(self, report):  # noqa: ANN001
        return deterministic_summary(report)


@pytest.fixture(scope="module")
def report() -> ClearanceReport:
    pipeline = ClearancePipeline(
        settings=Settings(use_fixtures=True, google_cloud_project=""),
        provider=FixtureResearcher(),
        drafter=StubDrafter(),
    )
    return pipeline.run(demo_project(), items=demo_items())


@pytest.fixture(scope="module")
def html(report: ClearanceReport) -> str:
    return render_html(report)


def test_is_a_complete_document(html: str) -> None:
    assert html.startswith("<!doctype html>")
    assert html.rstrip().endswith("</html>")
    assert "<title>" in html


def test_the_verdict_cannot_be_missed(html: str) -> None:
    """A reader must never have to infer whether the film can be delivered."""
    assert "Not cleared for delivery" in html
    assert 'class="stamp"' in html


def test_a_clear_report_says_so(monkeypatch: pytest.MonkeyPatch, report: ClearanceReport) -> None:
    clean = report.model_copy(deep=True)
    for entry in clean.entries:
        if entry.verdict:
            entry.verdict.eo_blocking = False
    html = render_html(clean)
    assert "Clear to proceed" in html
    assert "stamp ok" in html


def test_every_item_appears(html: str, report: ClearanceReport) -> None:
    for entry in report.entries:
        # Titles are HTML-escaped on the way in ("&" -> "&amp;"), so compare
        # against the escaped form rather than the raw one.
        assert escape(entry.item.research_subject()) in html


def test_sources_are_linked_and_quoted(html: str, report: ClearanceReport) -> None:
    assert report.total_citations > 0
    assert "<blockquote>" in html
    assert 'href="https://' in html


def test_rules_are_named_against_items(html: str) -> None:
    assert "Rules applied:" in html


def test_provenance_is_stated(html: str) -> None:
    assert "deterministic rule engine" in html
    assert "not legal advice" in html.lower()


def test_records_what_is_required_not_what_is_held(html: str) -> None:
    assert "not what has been obtained" in html


def test_replay_is_disclosed(report: ClearanceReport) -> None:
    replayed = report.model_copy(deep=True)
    replayed.replay_of = "run_original"
    replayed.replay_recorded_at = report.created_at
    html = render_html(replayed)
    assert "reproduces a clearance pass" in html
    assert "run_original" in html


def test_a_live_report_carries_no_replay_notice(html: str) -> None:
    assert "reproduces a clearance pass" not in html


# ---------------------------------------------------------------------------
# Escaping — item titles and research text come from the open web
# ---------------------------------------------------------------------------


def test_hostile_content_is_escaped(report: ClearanceReport) -> None:
    poisoned = report.model_copy(deep=True)
    poisoned.entries[0].item.title = "<script>alert('xss')</script>"
    poisoned.entries[0].item.known_work = None
    poisoned.entries[0].item.description = "<img src=x onerror=alert(1)>"
    html = render_html(poisoned)
    # The payload survives as inert text; what matters is that no live tag or
    # attribute reaches the document.
    assert "<script>alert" not in html
    assert "&lt;script&gt;" in html
    assert "<img src=x" not in html
    assert "&lt;img src=x" in html


def test_hostile_citation_titles_are_escaped(report: ClearanceReport) -> None:
    poisoned = report.model_copy(deep=True)
    for entry in poisoned.entries:
        if entry.finding and entry.finding.basis:
            for basis in entry.finding.basis:
                for citation in basis.citations:
                    citation.title = "<b>not bold</b>"
            break
    html = render_html(poisoned)
    assert "<b>not bold</b>" not in html


def test_empty_report_still_renders(report: ClearanceReport) -> None:
    bare = report.model_copy(deep=True)
    bare.entries = []
    html = render_html(bare)
    assert "<!doctype html>" in html
    assert "Schedule of items" in html


def test_the_report_states_how_many_owners_were_found() -> None:
    """Every other figure counts a problem.

    A pass that traced an owner for everything it researched used to render as
    nothing but blockers, exposure and lead time — which reads as the search
    having failed rather than succeeded.
    """
    from clearance_desk.models import (
        Category,
        ClearanceEntry,
        ClearanceReport,
        ProjectMeta,
        RightsFinding,
        RightsHolder,
        RiskItem,
    )

    def entry(item_id: str, holder: str | None, researched: bool) -> ClearanceEntry:
        return ClearanceEntry(
            item=RiskItem(
                id=item_id,
                title=item_id,
                description=item_id,
                category=Category.ARTWORK,
            ),
            finding=RightsFinding(
                item_id=item_id,
                researched=researched,
                holders=(
                    [RightsHolder(name=holder, role="owner")] if holder else []
                ),
            ),
        )

    report = ClearanceReport(
        run_id="run_x",
        project=ProjectMeta(title="X"),
        entries=[
            entry("a", "Leon Carr", True),
            entry("b", "Unidentified mural artist(s)", True),
            entry("c", None, False),
        ],
    )

    # b names a placeholder, not a party; c was never researched at all.
    assert report.ownership_found == (1, 2)
    assert "Owners found" in render_html(report)
