"""Tests for the research layer.

The parsing tests matter because Parallel returns free-form strings inside a
structured envelope — "yes", "$12,000", "1971" — and a sloppy coercion here
would feed the rules engine garbage while looking like it worked.
"""

from __future__ import annotations

import pytest

from clearance_desk.models import (
    Category,
    Confidence,
    Identifiability,
    Prominence,
    ProjectMeta,
    RiskItem,
)
from clearance_desk.research import (
    FixtureResearcher,
    _as_bool,
    _as_float,
    _clean,
    build_task_input,
    finding_from_output,
    needs_research,
    parse_basis,
    processor_for,
    research_items,
)
from clearance_desk.config import Settings


@pytest.fixture
def project() -> ProjectMeta:
    return ProjectMeta(title="Nightshift")


@pytest.fixture
def settings() -> Settings:
    return Settings(
        parallel_processor_standard="core",
        parallel_processor_deep="pro",
        use_fixtures=True,
    )


def item(**overrides) -> RiskItem:
    base = dict(
        id="itm_1",
        category=Category.MUSIC_SYNC,
        title="Rhapsody in Blue",
        # Named by default: an unnamed cue is deliberately not researched, so
        # the default here has to be a work research can actually look up.
        known_work="Rhapsody in Blue",
        description="Plays on a car radio",
    )
    base.update(overrides)
    return RiskItem(**base)


# ---------------------------------------------------------------------------
# Coercion
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw,expected",
    [("yes", True), ("YES", True), ("no", False), ("unclear", None), ("", None)],
)
def test_bool_coercion(raw: str, expected: bool | None) -> None:
    assert _as_bool(raw) is expected


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("$12,000", 12000.0),
        ("12000", 12000.0),
        ("10k", 10000.0),
        ("1.5m", 1500000.0),
        ("45,000 USD", 45000.0),
        ("not a number", None),
        ("", None),
    ],
)
def test_fee_coercion(raw: str, expected: float | None) -> None:
    assert _as_float(raw) == expected


@pytest.mark.parametrize("raw", ["unknown", "n/a", "none", "  ", "N/A"])
def test_placeholder_strings_become_none(raw: str) -> None:
    assert _clean(raw) is None


# ---------------------------------------------------------------------------
# Triage — the cost-control path
# ---------------------------------------------------------------------------


def test_hard_chain_of_title_gets_the_deep_processor(settings: Settings) -> None:
    assert processor_for(item(category=Category.MUSIC_SYNC), settings) == "pro"
    assert processor_for(item(category=Category.ARTWORK), settings) == "pro"


def test_simple_categories_use_the_standard_processor(settings: Settings) -> None:
    assert processor_for(item(category=Category.TRADEMARK), settings) == "core"
    assert processor_for(item(category=Category.LOCATION), settings) == "core"


def test_anonymous_extra_is_not_researched() -> None:
    """The web cannot identify a background extra — do not pay to ask."""
    assert not needs_research(
        item(category=Category.PERSON_LIKENESS, title="Man at counter")
    )


def test_item_with_paperwork_is_not_researched() -> None:
    assert not needs_research(item(release_on_file=True))


def test_library_cue_is_not_researched() -> None:
    assert not needs_research(item(category=Category.MUSIC_LIBRARY, title="Bed 04"))


def test_a_named_song_is_researched() -> None:
    assert needs_research(
        item(category=Category.MUSIC_SYNC, known_work="Ain't No Sunshine")
    )


def test_an_unnamed_cue_is_not_researched() -> None:
    """Observed on a real run: a deep-tier call on an unidentifiable cue came
    back with Copyright Office circulars and an ASCAP FAQ — a lecture on
    copyright law, billed at the highest rate, proving nothing about the cue.
    By this point ID-001 has already blocked it and asked for the cue sheet."""
    assert not needs_research(item(category=Category.MUSIC_SYNC, known_work=None))


def test_one_off_physical_object_is_not_researched() -> None:
    """No registry lists the neon sign in one particular alley."""
    assert not needs_research(
        item(
            category=Category.SIGNAGE_PRINT,
            title="Diary neon sign",
            no_published_identity=True,
        )
    )


def test_a_recognised_brand_is_still_researched() -> None:
    """The exclusion must not swallow mass-produced or published things."""
    assert needs_research(
        item(category=Category.TRADEMARK, title="Kirin Brewery Company")
    )
    assert needs_research(item(category=Category.ARTWORK, title="The Starry Night"))


# ---------------------------------------------------------------------------
# Task input
# ---------------------------------------------------------------------------


def test_task_input_carries_distribution_context(project: ProjectMeta) -> None:
    """Festival clearance and worldwide clearance are different questions."""
    payload = build_task_input(item(), project)
    assert "Nightshift" in payload["intended_use"]
    assert project.distribution_intent in payload["intended_use"]
    assert payload["material_type"] == "music_sync"


def test_music_brief_demands_the_publishing_master_split(
    project: ProjectMeta,
) -> None:
    payload = build_task_input(item(category=Category.MUSIC_SYNC), project)
    objective = payload["objective"].lower()
    assert "composition" in objective and "master" in objective


def test_known_work_and_year_reach_the_researcher(project: ProjectMeta) -> None:
    payload = build_task_input(
        item(title="radio song", known_work="Rhapsody in Blue", known_year=1924),
        project,
    )
    assert payload["material"] == "Rhapsody in Blue (1924)"


# ---------------------------------------------------------------------------
# Basis preservation — the legal defensibility path
# ---------------------------------------------------------------------------


def test_citations_survive_parsing() -> None:
    basis = parse_basis(
        [
            {
                "field": "rights_holders.0.name",
                "reasoning": "Because the registry says so",
                "confidence": "high",
                "citations": [
                    {
                        "url": "https://example.org/registry",
                        "title": "Registry",
                        "excerpts": ["Owned by Example Music"],
                    }
                ],
            }
        ]
    )
    assert basis[0].confidence is Confidence.HIGH
    assert basis[0].citations[0].excerpts == ["Owned by Example Music"]


@pytest.mark.parametrize(
    "raw_title,url,expected",
    [
        ("Public Domain Day 2020", "https://web.law.duke.edu/x", "Public Domain Day 2020"),
        ("Fetched web page", "https://www.coca-colacompany.com/policies/ip.pdf", "coca-colacompany.com"),
        ("", "https://tmsearch.uspto.gov/search", "tmsearch.uspto.gov"),
        (None, "http://investors.coca-colacompany.com/about", "investors.coca-colacompany.com"),
    ],
)
def test_generic_citation_titles_fall_back_to_the_domain(
    raw_title, url: str, expected: str
) -> None:  # noqa: ANN001
    basis = parse_basis(
        [{"field": "x", "confidence": "high", "citations": [{"url": url, "title": raw_title}]}]
    )
    assert basis[0].citations[0].title == expected


def test_citations_without_a_url_are_dropped() -> None:
    """A citation you cannot open is not a citation."""
    basis = parse_basis(
        [{"field": "x", "citations": [{"title": "no url here"}], "confidence": "high"}]
    )
    assert basis[0].citations == []


def test_confidence_is_governed_by_the_holder_fields() -> None:
    """Being sure about a publication year is not being sure about ownership."""
    finding = finding_from_output(
        item(),
        {"rights_holders": [{"name": "Example Music", "role": "publisher"}]},
        parse_basis(
            [
                {"field": "first_publication_year", "confidence": "high", "citations": []},
                {"field": "rights_holders.0.name", "confidence": "low", "citations": []},
            ]
        ),
        processor="pro",
        run_id="run_1",
        provider="parallel",
    )
    assert finding.confidence is Confidence.LOW


def test_missing_owner_is_recorded_as_an_unresolved_gap() -> None:
    """Silence must not read as 'nothing to clear'."""
    finding = finding_from_output(
        item(),
        {"rights_holders": [], "unresolved_issues": []},
        [],
        processor="pro",
        run_id="run_1",
        provider="parallel",
    )
    assert finding.unresolved
    assert "rights holder" in finding.unresolved[0].lower()


def test_publishing_and_master_holders_are_addressable_by_role() -> None:
    finding = finding_from_output(
        item(),
        {
            "rights_holders": [
                {"name": "Interior Music", "role": "publisher"},
                {"name": "Sussex Records", "role": "master owner"},
            ]
        },
        [],
        processor="pro",
        run_id="run_1",
        provider="parallel",
    )
    assert finding.holders_by_role("master owner")[0].name == "Sussex Records"
    assert finding.holders_by_role("publisher")[0].name == "Interior Music"


# ---------------------------------------------------------------------------
# Fixture provider and fan-out
# ---------------------------------------------------------------------------


def test_fixture_provider_returns_real_citations(project: ProjectMeta) -> None:
    finding = FixtureResearcher().research(item(title="Rhapsody in Blue"), project)
    assert finding.researched
    assert finding.public_domain is True
    assert finding.citation_count > 0
    assert all(c.url.startswith("http") for c in finding.all_citations)


def test_unknown_fixture_degrades_honestly(project: ProjectMeta) -> None:
    finding = FixtureResearcher().research(
        item(title="Some Unrecorded Song", known_work="Some Unrecorded Song"), project
    )
    assert finding.unresolved
    assert finding.confidence is Confidence.LOW


def test_fanout_covers_every_item_including_skipped(project: ProjectMeta) -> None:
    items = [
        item(id="a", title="Rhapsody in Blue"),
        item(id="b", category=Category.PERSON_LIKENESS, title="Extra in diner"),
        item(id="c", category=Category.TRADEMARK, title="Coca-Cola"),
    ]
    findings = research_items(items, project, FixtureResearcher())
    assert set(findings) == {"a", "b", "c"}
    assert findings["b"].researched is False  # triaged out, not researched
    assert findings["a"].researched is True


def test_fanout_reports_progress(project: ProjectMeta) -> None:
    seen: list[str] = []
    research_items(
        [item(id="a", title="Rhapsody in Blue")],
        project,
        FixtureResearcher(),
        on_result=lambda i, f: seen.append(i.id),
    )
    assert seen == ["a"]


def test_a_crashing_provider_does_not_lose_the_item(project: ProjectMeta) -> None:
    class Exploding:
        name = "exploding"

        def research(self, item, project):  # noqa: ANN001
            raise RuntimeError("upstream on fire")

    findings = research_items([item(id="a")], project, Exploding())
    assert findings["a"].researched is False
    assert "upstream on fire" in (findings["a"].error or "")
