"""Renders the deliverable.

The output is shaped like the clearance report a production's legal department
and E&O carrier expect: a status line they can act on, an item-by-item schedule
with timecodes, and — the part that distinguishes this from a summary — the
sources behind every ownership claim, quoted and linked.
"""

from __future__ import annotations

from .drafter import next_actions
from .models import ClearanceEntry, ClearanceReport, RiskTier

_TIER_MARK: dict[RiskTier, str] = {
    RiskTier.BLOCKING: "BLOCKING",
    RiskTier.HIGH: "HIGH",
    RiskTier.MEDIUM: "MEDIUM",
    RiskTier.LOW: "LOW",
    RiskTier.CLEARED: "CLEARED",
}


def _humanise(value: str) -> str:
    return value.replace("_", " ")


def _entry_section(entry: ClearanceEntry, index: int) -> list[str]:
    item, finding, verdict = entry.item, entry.finding, entry.verdict
    where = item.timecode or item.scene or "not located"
    lines: list[str] = []

    tier = _TIER_MARK[verdict.tier] if verdict else "UNADJUDICATED"
    lines.append(f"### {index}. {item.research_subject()} — {tier}")
    lines.append("")
    lines.append(f"- **Category:** {_humanise(item.category.value)}")
    lines.append(f"- **Location:** {where} ({item.source.value})")
    lines.append(
        f"- **On screen:** {item.description} "
        f"({_humanise(item.prominence.value)}, "
        f"{_humanise(item.identifiability.value)})"
    )

    if verdict:
        lines.append(f"- **Action:** {_humanise(verdict.action.value)}")
        if verdict.documents:
            lines.append(
                "- **Documents required:** "
                + ", ".join(_humanise(d.value) for d in verdict.documents)
            )
        if verdict.fee_band:
            lines.append(f"- **Estimated fee:** {verdict.fee_band}")
        if verdict.lead_time_days:
            lines.append(f"- **Lead time:** {verdict.lead_time_days} days")
        if verdict.eo_blocking:
            lines.append(
                "- **E&O:** blocks binding until resolved"
            )
        lines.append("")
        lines.append(f"**Position.** {verdict.rationale}")

    if finding and finding.holders:
        lines.append("")
        lines.append("**Rights holders identified.**")
        lines.append("")
        for holder in finding.holders:
            bits = [f"*{holder.role}*"]
            if holder.territory:
                bits.append(holder.territory)
            if holder.share_percent is not None:
                bits.append(f"{holder.share_percent:g}%")
            contact = holder.contact_url or holder.contact
            if contact:
                bits.append(contact)
            lines.append(f"- **{holder.name}** — {', '.join(bits)}")
            if holder.notes:
                lines.append(f"  - {holder.notes}")

    if finding and finding.unresolved:
        lines.append("")
        lines.append("**Unresolved.**")
        lines.append("")
        for gap in finding.unresolved:
            lines.append(f"- {gap}")

    if finding and finding.licensing_precedent:
        lines.append("")
        lines.append(f"**Precedent.** {finding.licensing_precedent}")

    # The evidence trail. This is the section that makes the report reviewable
    # rather than merely readable.
    if finding and finding.basis:
        lines.append("")
        lines.append(
            f"**Sources** ({finding.citation_count} cited, "
            f"research confidence {finding.confidence.value}"
            + (f", {finding.provider}/{finding.processor}" if finding.processor else "")
            + ")"
        )
        lines.append("")
        for basis in finding.basis:
            if not basis.citations:
                continue
            lines.append(f"- `{basis.field}` — {basis.confidence.value} confidence")
            if basis.reasoning:
                lines.append(f"  - {basis.reasoning}")
            for citation in basis.citations:
                title = citation.title or citation.url
                lines.append(f"  - [{title}]({citation.url})")
                for excerpt in citation.excerpts[:2]:
                    lines.append(f"    > {excerpt}")

    if verdict and verdict.fired_rules:
        lines.append("")
        lines.append(
            "**Rules applied.** "
            + ", ".join(r.rule_id for r in verdict.fired_rules)
        )

    if entry.outreach_draft:
        lines.append("")
        lines.append("<details><summary>Draft request</summary>")
        lines.append("")
        lines.append("```")
        lines.append(entry.outreach_draft)
        lines.append("```")
        lines.append("")
        lines.append("</details>")

    lines.append("")
    return lines


def render_markdown(report: ClearanceReport) -> str:
    """The full clearance report."""
    project = report.project
    low, high = report.estimated_cost_range
    counts = report.tier_counts

    lines: list[str] = [
        f"# Clearance Report — {project.title}",
        "",
        f"**Cut:** {project.cut_label}  ",
        f"**Rights sought:** {project.distribution_intent}  ",
        f"**Territories:** {', '.join(project.territories)}  ",
        f"**Generated:** {report.created_at:%Y-%m-%d %H:%M UTC}  ",
        f"**Run:** `{report.run_id}`",
        "",
    ]

    if report.is_replay:
        recorded = (
            f"{report.replay_recorded_at:%Y-%m-%d %H:%M UTC}"
            if report.replay_recorded_at
            else "an earlier date"
        )
        lines += [
            f"> **Replay.** This is the recorded output of a live pass run on "
            f"{recorded} (`{report.replay_of}`) — real research, real "
            f"citations — served again rather than re-billed per visitor. "
            f"Trigger a fresh pass with `?live=true`.",
            "",
        ]

    lines += [
        "---",
        "",
        "## Status",
        "",
    ]

    if report.eo_ready:
        lines.append(
            "**No item currently blocks errors-and-omissions cover on this cut.**"
        )
    else:
        blocking = len(report.blocking_entries)
        lines.append(
            f"**{blocking} item{'s' if blocking != 1 else ''} would prevent an "
            "errors-and-omissions policy binding on this cut.**"
        )

    lines += [
        "",
        report.summary or "",
        "",
        "| | |",
        "|---|---|",
        f"| Items identified | {len(report.entries)} |",
        f"| Blocking | {counts[RiskTier.BLOCKING.value]} |",
        f"| High / Medium / Low | {counts[RiskTier.HIGH.value]} / "
        f"{counts[RiskTier.MEDIUM.value]} / {counts[RiskTier.LOW.value]} |",
        f"| Cleared | {counts[RiskTier.CLEARED.value]} |",
        f"| Estimated exposure | ${low:,.0f}–${high:,.0f} |",
        f"| Longest lead time | {report.longest_lead_time_days} days |",
        f"| Sources cited | {report.total_citations} |",
        "",
    ]

    actions = next_actions(report)
    if actions:
        lines += ["## Next actions", ""]
        lines += [f"{i}. {a}" for i, a in enumerate(actions, 1)]
        lines.append("")

    lines += ["---", "", "## Schedule of items", ""]
    for index, entry in enumerate(report.sorted_entries(), 1):
        lines += _entry_section(entry, index)

    lines += [
        "---",
        "",
        "## How this report was produced",
        "",
        f"- Material spotted by `{report.spotter_model}` on Vertex AI.",
        f"- Chain of title researched via `{report.research_provider}`.",
        "- Risk tiers, required documents and blocking status assigned by a "
        "deterministic rule engine — no model output sits between the cited "
        "evidence and the position stated above. Rule identifiers are listed "
        "against every item.",
        "",
        "> This report is a research and triage instrument for a production's "
        "clearance workflow. It is not legal advice and does not replace "
        "review by qualified counsel or a clearance attorney.",
        "",
    ]

    if report.stage_timings_ms:
        timings = ", ".join(
            f"{stage} {ms}ms" for stage, ms in report.stage_timings_ms.items()
        )
        lines += [f"*Stage timings: {timings}.*", ""]

    return "\n".join(lines)
