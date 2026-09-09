"""Tests for the ADK tool surface.

The tools are plain functions, so they are tested directly without standing up
an agent runtime. What matters here is that every tool degrades honestly on a
bad identifier and that the explanation tool really does expose the rule trail
— an agent that cannot show its work would undermine the whole design.
"""

from __future__ import annotations

import pytest

from clearance_desk import agent as agent_module
from clearance_desk.agent import (
    TOOLS,
    explain_verdict,
    get_clearance_report,
    get_outreach_draft,
    list_blocking_items,
    list_clearance_rules,
    run_demo_clearance_pass,
    summarise_run,
)


@pytest.fixture(scope="module")
def run_id() -> str:
    result = run_demo_clearance_pass()
    assert result["status"] == "complete"
    return result["run_id"]


def test_demo_pass_reports_its_outcome(run_id: str) -> None:
    summary = summarise_run(run_id)
    assert summary["items"]
    assert summary["eo_ready"] is False
    assert sum(summary["tier_counts"].values()) == len(summary["items"])


def test_blocking_items_come_with_reasons(run_id: str) -> None:
    result = list_blocking_items(run_id)
    assert result["blocking"]
    for item in result["blocking"]:
        assert item["why"]
        assert item["location"]
    assert result["next_actions"]


def test_explain_verdict_exposes_the_rule_trail(run_id: str) -> None:
    blocking = list_blocking_items(run_id)["blocking"][0]
    detail = explain_verdict(run_id, blocking["item_id"])
    assert detail["rules_applied"]
    assert all(r["id"] and r["text"] for r in detail["rules_applied"])
    assert "deterministic" in detail["note"]


def test_explain_verdict_carries_citations_where_research_ran(run_id: str) -> None:
    summary = summarise_run(run_id)
    cited = [
        explain_verdict(run_id, i["item_id"]) for i in summary["items"]
    ]
    assert any(d["sources"] for d in cited)
    for detail in cited:
        for source in detail["sources"]:
            assert source["url"].startswith("http")


def test_report_tool_returns_markdown(run_id: str) -> None:
    markdown = get_clearance_report(run_id)["markdown"]
    assert markdown.startswith("# Clearance Report")
    assert "Rules applied" in markdown


def test_draft_tool_explains_itself_when_there_is_no_draft(run_id: str) -> None:
    summary = summarise_run(run_id)
    results = [get_outreach_draft(run_id, i["item_id"]) for i in summary["items"]]
    assert any(r.get("draft") for r in results)
    for result in results:
        if not result.get("draft"):
            assert result["reason"]


def test_rule_catalogue_is_exposed() -> None:
    rules = list_clearance_rules()["rules"]
    assert len(rules) > 10
    assert all(r["rule_id"] and r["description"] for r in rules)


@pytest.mark.parametrize(
    "tool,args",
    [
        (summarise_run, ("run_nope",)),
        (list_blocking_items, ("run_nope",)),
        (get_clearance_report, ("run_nope",)),
        (explain_verdict, ("run_nope", "itm_nope")),
        (get_outreach_draft, ("run_nope", "itm_nope")),
    ],
)
def test_unknown_identifiers_return_an_error_not_an_exception(tool, args) -> None:  # noqa: ANN001
    assert "error" in tool(*args)


def test_unknown_item_in_a_real_run_is_reported(run_id: str) -> None:
    assert "error" in explain_verdict(run_id, "itm_does_not_exist")


def test_every_tool_is_documented_for_the_model() -> None:
    """ADK derives tool schemas from signatures and docstrings."""
    for tool in TOOLS:
        assert tool.__doc__, f"{tool.__name__} has no docstring"
        assert "Returns:" in tool.__doc__, f"{tool.__name__} documents no return"


def test_agent_builds() -> None:
    agent = agent_module.build_agent()
    assert agent.name == "clearance_desk"
    assert len(agent.tools) == len(TOOLS)
    assert "not a lawyer" in agent.instruction
