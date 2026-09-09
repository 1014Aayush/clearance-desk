"""The ADK agent surface.

What the agent is, and what it is not
------------------------------------
The agent is a way to *talk to* a clearance pass — to ask what is blocking
delivery, why a particular item was tiered the way it was, what a letter would
say. It is not the thing that decides those questions.

That distinction is deliberate and load-bearing. Every tool below either
retrieves a stored fact or invokes the deterministic pipeline; none of them ask
a model to form a legal position. If the agent is asked whether something is
cleared, it can only report what the rules engine concluded and name the rules
that produced it. An agent that could talk itself into a clearance would be
worse than useless, because its output would look exactly as authoritative as
one that could not.

Deploy to Agent Engine with ``python -m clearance_desk.deploy``, or serve the
same agent locally with ``adk web clearance_desk``.
"""

from __future__ import annotations

import logging
from typing import Any

from .config import get_settings
from .demo import demo_items, demo_project
from .drafter import next_actions
from .models import ProjectMeta, RiskTier
from .pipeline import ClearancePipeline, RunStore
from .report import render_markdown
from .rules import rule_catalogue

logger = logging.getLogger(__name__)

#: Shared with the HTTP layer when both run in one process.
STORE = RunStore()


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------


def run_clearance_pass(
    title: str,
    cut_label: str = "rough cut",
    gcs_uri: str = "",
    script_text: str = "",
    distribution_intent: str = "worldwide, all media, in perpetuity",
    runtime_seconds: int = 0,
) -> dict[str, Any]:
    """Run a full clearance pass over a cut and/or a script.

    Spots clearable material with Gemini, researches the chain of title behind
    each item with Parallel, and adjudicates the result with the deterministic
    rules engine.

    Args:
        title: The production's title.
        cut_label: Which cut this is, e.g. 'rough cut v4'.
        gcs_uri: gs:// URI of the video to analyse. Empty to skip picture.
        script_text: Screenplay text to analyse. Empty to skip the script.
        distribution_intent: Rights being sought. Drives fee estimates —
            'festival only' is a fraction of 'worldwide, all media, in
            perpetuity'.
        runtime_seconds: Runtime of the cut, used to plan analysis windows.

    Returns:
        The run identifier and a headline summary of the outcome.
    """
    project = ProjectMeta(
        title=title,
        cut_label=cut_label,
        distribution_intent=distribution_intent,
        runtime_seconds=runtime_seconds or None,
    )
    pipeline = ClearancePipeline()
    report = pipeline.run(
        project,
        gcs_uri=gcs_uri or None,
        script_text=script_text or None,
        on_event=STORE.append_event,
    )
    STORE.set_report(report.run_id, report)

    low, high = report.estimated_cost_range
    return {
        "run_id": report.run_id,
        "status": report.status.value,
        "items_found": len(report.entries),
        "blocking_count": len(report.blocking_entries),
        "eo_ready": report.eo_ready,
        "estimated_cost_usd": [low, high],
        "longest_lead_days": report.longest_lead_time_days,
        "sources_cited": report.total_citations,
        "summary": report.summary,
        "error": report.error,
    }


def run_demo_clearance_pass() -> dict[str, Any]:
    """Run the worked example over a reel of the fictional feature 'Nightshift'.

    Uses pre-spotted material, so it needs no video upload. Useful for showing
    the full pipeline including live chain-of-title research.

    Returns:
        The run identifier and a headline summary of the outcome.
    """
    pipeline = ClearancePipeline()
    report = pipeline.run(
        demo_project(), items=demo_items(), on_event=STORE.append_event
    )
    STORE.set_report(report.run_id, report)
    low, high = report.estimated_cost_range
    return {
        "run_id": report.run_id,
        "status": report.status.value,
        "items_found": len(report.entries),
        "blocking_count": len(report.blocking_entries),
        "eo_ready": report.eo_ready,
        "estimated_cost_usd": [low, high],
        "summary": report.summary,
    }


def list_blocking_items(run_id: str) -> dict[str, Any]:
    """List the items preventing an E&O policy from binding on this cut.

    Args:
        run_id: Identifier returned by a clearance pass.

    Returns:
        The blocking items with their location, position and lead time.
    """
    report = STORE.get(run_id)
    if report is None:
        return {"error": f"no run named {run_id}"}
    return {
        "run_id": run_id,
        "eo_ready": report.eo_ready,
        "blocking": [
            {
                "item_id": e.item.id,
                "work": e.item.research_subject(),
                "category": e.item.category.value,
                "location": e.item.timecode or e.item.scene,
                "action": e.verdict.action.value if e.verdict else None,
                "why": e.verdict.rationale if e.verdict else None,
                "lead_time_days": e.verdict.lead_time_days if e.verdict else None,
                "unresolved": e.finding.unresolved if e.finding else [],
            }
            for e in report.blocking_entries
        ],
        "next_actions": next_actions(report),
    }


def explain_verdict(run_id: str, item_id: str) -> dict[str, Any]:
    """Explain exactly why one item was given the position it was given.

    Returns the rules that fired and the cited sources behind the ownership
    findings, so the conclusion can be checked rather than trusted.

    Args:
        run_id: Identifier returned by a clearance pass.
        item_id: Identifier of the item to explain.

    Returns:
        The verdict, the rules that produced it, and the supporting citations.
    """
    report = STORE.get(run_id)
    if report is None:
        return {"error": f"no run named {run_id}"}
    entry = next((e for e in report.entries if e.item.id == item_id), None)
    if entry is None:
        return {"error": f"no item named {item_id} in {run_id}"}

    verdict, finding = entry.verdict, entry.finding
    return {
        "work": entry.item.research_subject(),
        "location": entry.item.timecode or entry.item.scene,
        "on_screen": entry.item.description,
        "prominence": entry.item.prominence.value,
        "identifiability": entry.item.identifiability.value,
        "tier": verdict.tier.value if verdict else None,
        "action": verdict.action.value if verdict else None,
        "documents": [d.value for d in verdict.documents] if verdict else [],
        "rationale": verdict.rationale if verdict else None,
        "rules_applied": (
            [{"id": r.rule_id, "text": r.description} for r in verdict.fired_rules]
            if verdict
            else []
        ),
        "rights_holders": (
            [{"name": h.name, "role": h.role, "contact": h.contact_url or h.contact}
             for h in finding.holders]
            if finding
            else []
        ),
        "unresolved": finding.unresolved if finding else [],
        "research_confidence": finding.confidence.value if finding else None,
        "sources": (
            [
                {"url": c.url, "title": c.title, "excerpts": c.excerpts[:2]}
                for c in finding.all_citations
            ]
            if finding
            else []
        ),
        "note": (
            "Tier, action and blocking status were assigned by the "
            "deterministic rules engine listed above, not by a language model."
        ),
    }


def get_outreach_draft(run_id: str, item_id: str) -> dict[str, Any]:
    """Return the drafted rights request for an item, if one applies.

    Args:
        run_id: Identifier returned by a clearance pass.
        item_id: Identifier of the item.

    Returns:
        The draft letter and its intended recipient.
    """
    report = STORE.get(run_id)
    if report is None:
        return {"error": f"no run named {run_id}"}
    entry = next((e for e in report.entries if e.item.id == item_id), None)
    if entry is None:
        return {"error": f"no item named {item_id} in {run_id}"}
    if not entry.outreach_draft:
        return {
            "work": entry.item.research_subject(),
            "draft": None,
            "reason": (
                "No outreach applies — either nothing is required, or research "
                "found no contactable rights holder to write to."
            ),
        }
    holders = entry.finding.holders if entry.finding else []
    return {
        "work": entry.item.research_subject(),
        "recipient": holders[0].name if holders else "unidentified rights holder",
        "draft": entry.outreach_draft,
    }


def get_clearance_report(run_id: str) -> dict[str, Any]:
    """Return the full clearance report as markdown.

    Args:
        run_id: Identifier returned by a clearance pass.

    Returns:
        The rendered report.
    """
    report = STORE.get(run_id)
    if report is None:
        return {"error": f"no run named {run_id}"}
    return {"run_id": run_id, "markdown": render_markdown(report)}


def list_clearance_rules() -> dict[str, Any]:
    """List every rule the adjudication engine can apply, with its text.

    Returns:
        The full rule catalogue.
    """
    return {"rules": rule_catalogue()}


def summarise_run(run_id: str) -> dict[str, Any]:
    """Return the headline position for a run.

    Args:
        run_id: Identifier returned by a clearance pass.

    Returns:
        Counts by risk tier, cost range, lead time and blocking status.
    """
    report = STORE.get(run_id)
    if report is None:
        return {"error": f"no run named {run_id}"}
    low, high = report.estimated_cost_range
    return {
        "run_id": run_id,
        "title": report.project.title,
        "cut": report.project.cut_label,
        "status": report.status.value,
        "eo_ready": report.eo_ready,
        "tier_counts": report.tier_counts,
        "estimated_cost_usd": [low, high],
        "longest_lead_days": report.longest_lead_time_days,
        "sources_cited": report.total_citations,
        "summary": report.summary,
        "items": [
            {
                "item_id": e.item.id,
                "work": e.item.research_subject(),
                "location": e.item.timecode or e.item.scene,
                "tier": e.verdict.tier.value if e.verdict else None,
                "action": e.verdict.action.value if e.verdict else None,
            }
            for e in report.sorted_entries()
        ],
    }


TOOLS = [
    run_clearance_pass,
    run_demo_clearance_pass,
    summarise_run,
    list_blocking_items,
    explain_verdict,
    get_outreach_draft,
    get_clearance_report,
    list_clearance_rules,
]


# ---------------------------------------------------------------------------
# Agent
# ---------------------------------------------------------------------------

INSTRUCTION = """\
You are Clearance Desk, assisting a film production with rights clearance.

You talk to a clearance pass; you do not perform one yourself. Tier, required
documents and blocking status are produced by a deterministic rules engine and
are returned to you by the tools. Report them. Never reason your way to a
different conclusion, and never soften or harden one.

How to work:

- Start from what the producer is trying to do. "Can we deliver?" is answered
  by `list_blocking_items`, not by a tour of every item.
- Lead with the position, then the reason. "Three items block E&O. The largest
  is the alley mural at 48:10 — the artist could not be identified, so it
  cannot be licensed as shot."
- When you state that something is or is not cleared, name the rule that
  decided it. `explain_verdict` gives you the rule identifiers and the cited
  sources.
- Cite sources when ownership is in question. A claim about who owns a work is
  only useful if the producer can check it.
- Be exact about timecodes, money and lead times. These drive schedules.

Boundaries:

- You are not a lawyer and this is not legal advice. Say so plainly if asked to
  approve, sign off, or guarantee anything, then give the position the rules
  engine reached and suggest counsel review the blocking items.
- Never claim rights have been obtained. The system tracks what is *required*,
  not what has been secured.
- If research returned low confidence or an unresolved chain of title, say so
  rather than presenting a tentative finding as settled. Uncertainty here is
  the most valuable thing you can communicate: it is what tells a producer to
  start a conversation eight weeks early.
"""


def build_agent(model: str | None = None):  # noqa: ANN201
    """Construct the ADK agent."""
    from google.adk.agents import Agent

    settings = get_settings()
    return Agent(
        name="clearance_desk",
        model=model or settings.spotter_model,
        description=(
            "Rights clearance for film and television: spots clearable material "
            "in a cut, researches chain of title with citations, and reports a "
            "deterministic clearance position."
        ),
        instruction=INSTRUCTION,
        tools=TOOLS,
    )


try:  # pragma: no cover - exercised by `adk web` / Agent Engine, not by tests
    root_agent = build_agent()
except Exception:  # noqa: BLE001
    logger.warning(
        "ADK agent not constructed at import time; call build_agent() directly.",
        exc_info=True,
    )
    root_agent = None
