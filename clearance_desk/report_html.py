"""The clearance report, rendered as a document.

The web tool is an instrument — dark, dense, built for someone scrubbing a
timeline. This is the other half: the thing that gets printed, attached to an
email, and read by a lawyer who has never seen the tool and does not care about
it. So it is set as paper. Serif text, generous measure, a real title block, and
a schedule of items that survives being printed in black and white.

Two conventions borrowed from actual delivery paperwork:

* the status is stated as a **stamp** at the top, not buried in a summary — a
  reader must not have to infer whether the film can be delivered;
* every ownership claim carries its source underneath it, quoted. A clearance
  report whose assertions cannot be checked is not worth attaching.
"""

from __future__ import annotations

from html import escape

from .drafter import next_actions
from .models import ClearanceEntry, ClearanceReport, RiskTier

_TIER_LABEL = {
    RiskTier.BLOCKING: "Blocks delivery",
    RiskTier.HIGH: "High risk",
    RiskTier.MEDIUM: "Needs attention",
    RiskTier.LOW: "Low risk",
    RiskTier.CLEARED: "Cleared",
}

_ACTION_PLAIN = {
    "no_action": "No action required",
    "monitor": "File the paperwork",
    "obtain_release": "Obtain a signed release",
    "obtain_license": "Obtain a licence",
    "obscure_or_blur": "Obscure in post",
    "identify_source": "Identify from production records",
    "legal_review": "Refer to counsel",
    "replace_asset": "Replace or reshoot",
    "remove": "Remove from the cut",
}

_CSS = """
@import url('https://fonts.googleapis.com/css2?family=Newsreader:ital,opsz,wght@0,6..72,400;0,6..72,600;1,6..72,400&family=Courier+Prime:wght@400;700&display=swap');

:root{
  --paper:#f6f3ec; --paper-2:#efeade; --ink:#1a1815; --ink-2:#514c44; --ink-3:#8a8378;
  --rule:#d8d1c2; --rule-dark:#b9b09c;
  --blocking:#a3341f; --high:#a86a1c; --medium:#8a7320; --low:#3f5f7d; --cleared:#2f6b4f;
  --serif:"Newsreader",Georgia,"Times New Roman",serif;
  --mono:"Courier Prime",Courier,monospace;
}
*{box-sizing:border-box}
body{
  margin:0;background:#e6e1d6;color:var(--ink);
  font-family:var(--serif);font-size:16px;line-height:1.62;
  -webkit-font-smoothing:antialiased;padding:36px 18px;
}
.sheet{
  max-width:920px;margin:0 auto;background:var(--paper);
  padding:60px 66px 76px;box-shadow:0 2px 34px rgba(60,50,35,.16);
}

/* ── title block ───────────────────────────────────────── */
.masthead{border-bottom:2px solid var(--ink);padding-bottom:18px;margin-bottom:26px}
.kicker{
  font-family:var(--mono);font-size:10.5px;letter-spacing:.26em;text-transform:uppercase;
  color:var(--ink-3);margin-bottom:12px;
}
h1{font-size:44px;line-height:1.06;margin:0 0 4px;font-weight:600;letter-spacing:-.015em}
.subtitle{font-size:19px;color:var(--ink-2);font-style:italic;margin-bottom:18px}
.docket{
  display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:10px 26px;
  font-family:var(--mono);font-size:11.5px;
}
.docket div span{display:block;color:var(--ink-3);font-size:9.5px;letter-spacing:.16em;text-transform:uppercase;margin-bottom:2px}

/* ── the stamp ─────────────────────────────────────────── */
.stamp{
  border:2.5px solid var(--blocking);color:var(--blocking);
  padding:16px 22px;margin:0 0 12px;display:flex;gap:20px;align-items:baseline;
  flex-wrap:wrap;
}
.stamp.ok{border-color:var(--cleared);color:var(--cleared)}
.stamp .verdict{
  font-family:var(--mono);font-weight:700;font-size:16px;
  letter-spacing:.16em;text-transform:uppercase;
}
.stamp .detail{font-size:15px;color:var(--ink-2);font-style:italic}
.standfirst{font-size:18px;line-height:1.55;color:var(--ink-2);margin:18px 0 30px}

/* ── figures ───────────────────────────────────────────── */
.figures{
  display:grid;grid-template-columns:repeat(auto-fit,minmax(112px,1fr));
  border-top:1px solid var(--rule-dark);border-bottom:1px solid var(--rule-dark);
  margin-bottom:34px;
}
.figures div{padding:13px 14px;border-right:1px solid var(--rule)}
.figures div:last-child{border-right:0}
.figures .k{
  font-family:var(--mono);font-size:9px;letter-spacing:.15em;text-transform:uppercase;
  color:var(--ink-3);display:block;margin-bottom:4px;
}
.figures .v{font-family:var(--mono);font-size:19px}
.figures .v.alarm{color:var(--blocking)}

h2{
  font-size:13px;font-family:var(--mono);letter-spacing:.2em;text-transform:uppercase;
  color:var(--ink-3);font-weight:400;margin:40px 0 16px;
  padding-bottom:7px;border-bottom:1px solid var(--rule-dark);
}

/* ── actions ───────────────────────────────────────────── */
ol.actions{margin:0 0 10px;padding-left:22px}
ol.actions li{margin-bottom:9px;font-size:16px}
ol.actions .tag{
  font-family:var(--mono);font-size:10px;letter-spacing:.1em;text-transform:uppercase;
  border:1px solid currentColor;padding:1px 6px;margin-right:8px;vertical-align:2px;
}
.t-blocking{color:var(--blocking)} .t-high{color:var(--high)}
.t-medium{color:var(--medium)} .t-low{color:var(--low)} .t-cleared{color:var(--cleared)}

/* ── item ──────────────────────────────────────────────── */
.item{border-top:1px solid var(--rule);padding:26px 0 6px;break-inside:avoid}
.item-head{display:flex;gap:16px;align-items:baseline;flex-wrap:wrap;margin-bottom:4px}
.item-no{font-family:var(--mono);font-size:12px;color:var(--ink-3)}
.item h3{font-size:25px;font-weight:600;margin:0;flex:1;min-width:220px;letter-spacing:-.01em}
.badge{
  font-family:var(--mono);font-size:10px;letter-spacing:.13em;text-transform:uppercase;
  border:1.5px solid currentColor;padding:3px 9px;white-space:nowrap;
}
.slugline{
  font-family:var(--mono);font-size:11.5px;color:var(--ink-3);margin-bottom:14px;
  letter-spacing:.04em;
}
.position{
  background:var(--paper-2);border-left:3px solid var(--rule-dark);
  padding:13px 18px;margin-bottom:14px;font-size:16px;
}
.position.blocking{border-left-color:var(--blocking)}
.position.high{border-left-color:var(--high)}
.position.medium{border-left-color:var(--medium)}
.position.cleared{border-left-color:var(--cleared)}
.todo{font-family:var(--mono);font-size:12.5px;margin-top:10px;color:var(--ink)}
.todo b{letter-spacing:.06em;text-transform:uppercase;font-size:10px;color:var(--ink-3);display:block;margin-bottom:3px}

.block{margin:14px 0}
.block > .lbl{
  font-family:var(--mono);font-size:9.5px;letter-spacing:.16em;text-transform:uppercase;
  color:var(--ink-3);margin-bottom:7px;
}
.holder{margin-bottom:9px;padding-left:14px;border-left:2px solid var(--rule)}
.holder .n{font-weight:600}
.holder .r{font-family:var(--mono);font-size:11px;color:var(--ink-3);text-transform:uppercase;letter-spacing:.08em}
.holder .note{font-size:14.5px;color:var(--ink-2);margin-top:3px}
.gap{border-left:2px solid var(--blocking);padding:6px 0 6px 14px;margin-bottom:8px;font-size:15px}

.source{margin-bottom:13px;font-size:14.5px}
.source .field{font-family:var(--mono);font-size:11px;color:var(--ink-3)}
.source a{color:#2b4a63;text-decoration:none;border-bottom:1px solid #b9cbd8}
.source blockquote{
  margin:6px 0 0;padding-left:15px;border-left:2px solid var(--rule);
  font-style:italic;color:var(--ink-2);font-size:15px;
}
.rules{font-family:var(--mono);font-size:11px;color:var(--ink-3);margin-top:12px;letter-spacing:.06em}
details.draft{margin-top:14px}
details.draft summary{
  font-family:var(--mono);font-size:11px;letter-spacing:.14em;text-transform:uppercase;
  color:var(--ink-3);cursor:pointer;
}
details.draft pre{
  margin:11px 0 0;padding:16px 18px;background:var(--paper-2);border:1px solid var(--rule);
  font-family:var(--mono);font-size:12.5px;line-height:1.62;white-space:pre-wrap;color:var(--ink-2);
}

.colophon{
  margin-top:52px;padding-top:20px;border-top:2px solid var(--ink);
  font-size:14.5px;color:var(--ink-2);
}
.colophon ul{padding-left:20px;margin:10px 0}
.disclaimer{
  margin-top:20px;padding:14px 18px;background:var(--paper-2);
  border-left:3px solid var(--ink-3);font-style:italic;font-size:15px;
}
.replay{
  border:1px dashed var(--medium);color:var(--ink-2);padding:12px 16px;
  margin-bottom:22px;font-size:14.5px;font-style:italic;
}

@media print{
  body{background:#fff;padding:0}
  .sheet{box-shadow:none;max-width:none;padding:0}
  .item{break-inside:avoid}
  a{color:inherit}
}
@media(max-width:680px){ .sheet{padding:32px 24px} h1{font-size:32px} }
"""


def _fmt_money(value: float | None) -> str:
    return "—" if value is None else f"${value:,.0f}"


def _item_html(entry: ClearanceEntry, index: int) -> str:
    item, finding, verdict = entry.item, entry.finding, entry.verdict
    tier = verdict.tier if verdict else RiskTier.MEDIUM
    where = item.timecode or item.scene or "not located"

    out = ['<section class="item">']
    out.append('<div class="item-head">')
    out.append(f'<span class="item-no">{index:02d}</span>')
    out.append(f"<h3>{escape(item.research_subject())}</h3>")
    out.append(
        f'<span class="badge t-{tier.value}">{escape(_TIER_LABEL[tier])}</span>'
    )
    out.append("</div>")

    out.append(
        f'<div class="slugline">{escape(where)} &nbsp;·&nbsp; '
        f'{escape(item.category.value.replace("_", " "))} &nbsp;·&nbsp; '
        f'{escape(item.prominence.value.replace("_", " "))} in frame</div>'
    )

    if verdict:
        out.append(f'<div class="position {tier.value}">')
        out.append(f"<div>{escape(verdict.rationale)}</div>")
        bits = [_ACTION_PLAIN.get(verdict.action.value, verdict.action.value)]
        if verdict.documents:
            bits.append(
                "Documents: "
                + ", ".join(d.value.replace("_", " ") for d in verdict.documents)
            )
        if verdict.fee_band:
            bits.append(f"Estimated fee {verdict.fee_band}")
        if verdict.lead_time_days:
            bits.append(f"Allow {verdict.lead_time_days} days")
        out.append(
            '<div class="todo"><b>Action</b>' + escape(" · ".join(bits)) + "</div>"
        )
        out.append("</div>")

    out.append(
        f'<div class="block"><div class="lbl">On screen</div>{escape(item.description)}</div>'
    )

    if finding and finding.holders:
        out.append('<div class="block"><div class="lbl">Rights holders identified</div>')
        for holder in finding.holders:
            meta = " · ".join(
                filter(None, [holder.role, holder.territory,
                              f"{holder.share_percent:g}%" if holder.share_percent else None])
            )
            out.append('<div class="holder">')
            out.append(f'<div class="n">{escape(holder.name)}</div>')
            out.append(f'<div class="r">{escape(meta)}</div>')
            if holder.notes:
                out.append(f'<div class="note">{escape(holder.notes)}</div>')
            contact = holder.contact_url or holder.contact
            if contact:
                out.append(f'<div class="note">{escape(contact)}</div>')
            out.append("</div>")
        out.append("</div>")

    if finding and finding.unresolved:
        out.append('<div class="block"><div class="lbl">Unresolved</div>')
        for gap in finding.unresolved:
            out.append(f'<div class="gap">{escape(gap)}</div>')
        out.append("</div>")

    if finding and finding.basis:
        cited = [b for b in finding.basis if b.citations]
        if cited:
            out.append(
                f'<div class="block"><div class="lbl">Sources · '
                f"{finding.citation_count} cited · {finding.confidence.value} confidence</div>"
            )
            for basis in cited:
                out.append('<div class="source">')
                out.append(f'<div class="field">{escape(basis.field)}</div>')
                if basis.reasoning:
                    out.append(f"<div>{escape(basis.reasoning)}</div>")
                for citation in basis.citations:
                    title = escape(citation.title or citation.url)
                    out.append(
                        f'<div><a href="{escape(citation.url)}">{title}</a></div>'
                    )
                    for excerpt in citation.excerpts[:2]:
                        out.append(f"<blockquote>{escape(excerpt)}</blockquote>")
                out.append("</div>")
            out.append("</div>")

    if verdict and verdict.fired_rules:
        out.append(
            '<div class="rules">Rules applied: '
            + escape(", ".join(r.rule_id for r in verdict.fired_rules))
            + "</div>"
        )

    if entry.outreach_draft:
        out.append("<details class='draft'><summary>Draft request</summary>")
        out.append(f"<pre>{escape(entry.outreach_draft)}</pre></details>")

    out.append("</section>")
    return "\n".join(out)


def render_html(report: ClearanceReport) -> str:
    project = report.project
    low, high = report.estimated_cost_range
    counts = report.tier_counts
    blocking = len(report.blocking_entries)

    parts = [
        "<!doctype html>",
        '<html lang="en"><head><meta charset="utf-8">',
        '<meta name="viewport" content="width=device-width,initial-scale=1">',
        f"<title>Clearance Report — {escape(project.title)}</title>",
        f"<style>{_CSS}</style></head><body><div class='sheet'>",
        '<div class="masthead">',
        '<div class="kicker">Clearance Report · Schedule of Third-Party Material</div>',
        f"<h1>{escape(project.title)}</h1>",
        f'<div class="subtitle">{escape(project.cut_label)}</div>',
        '<div class="docket">',
        f"<div><span>Rights sought</span>{escape(project.distribution_intent)}</div>",
        f"<div><span>Territories</span>{escape(', '.join(project.territories))}</div>",
        f"<div><span>Generated</span>{report.created_at:%d %b %Y, %H:%M} UTC</div>",
        f"<div><span>Run</span>{escape(report.run_id)}</div>",
        "</div></div>",
    ]

    if report.is_replay:
        recorded = (
            f"{report.replay_recorded_at:%d %b %Y, %H:%M} UTC"
            if report.replay_recorded_at
            else "an earlier date"
        )
        parts.append(
            f'<div class="replay">This document reproduces a clearance pass run on '
            f"{recorded} (<code>{escape(report.replay_of or '')}</code>). The research "
            "and citations below are from that run, served again rather than repeated.</div>"
        )

    if report.eo_ready:
        parts += [
            '<div class="stamp ok">',
            '<span class="verdict">Clear to proceed</span>',
            '<span class="detail">No item currently blocks errors-and-omissions cover.</span>',
            "</div>",
        ]
    else:
        parts += [
            '<div class="stamp">',
            '<span class="verdict">Not cleared for delivery</span>',
            f'<span class="detail">{blocking} item{"s" if blocking != 1 else ""} '
            "would prevent an errors-and-omissions policy being issued on this cut.</span>",
            "</div>",
        ]

    if report.summary:
        parts.append(f'<p class="standfirst">{escape(report.summary)}</p>')

    named, researched = report.ownership_found
    figures = [
        ("Items", len(report.entries), ""),
        (
            "Owners found",
            f"{named}/{researched}" if researched else "—",
            "good" if named else "",
        ),
        ("Blocking", counts[RiskTier.BLOCKING.value], "alarm" if blocking else ""),
        ("High", counts[RiskTier.HIGH.value], ""),
        ("Medium", counts[RiskTier.MEDIUM.value], ""),
        ("Cleared", counts[RiskTier.CLEARED.value], ""),
        ("Exposure", f"{_fmt_money(low)}–{_fmt_money(high)}", ""),
        ("Longest lead", f"{report.longest_lead_time_days}d", ""),
        ("Sources", report.total_citations, ""),
    ]
    parts.append('<div class="figures">')
    for key, value, cls in figures:
        parts.append(
            f'<div><span class="k">{escape(key)}</span>'
            f'<span class="v {cls}">{value}</span></div>'
        )
    parts.append("</div>")

    actions = next_actions(report)
    if actions:
        parts.append("<h2>Immediate actions</h2><ol class='actions'>")
        for action in actions:
            tier = action.split("]")[0].strip("[").lower()
            body = action.split("] ", 1)[-1]
            parts.append(
                f'<li><span class="tag t-{escape(tier)}">{escape(tier)}</span>'
                f"{escape(body)}</li>"
            )
        parts.append("</ol>")

    parts.append("<h2>Schedule of items</h2>")
    for index, entry in enumerate(report.sorted_entries(), 1):
        parts.append(_item_html(entry, index))

    parts += [
        '<div class="colophon">',
        "<h2>How this report was produced</h2><ul>",
        f"<li>Material identified in picture by <code>{escape(report.spotter_model or 'Gemini')}</code> on Vertex AI.</li>",
        f"<li>Chain of title researched via <code>{escape(report.research_provider or 'n/a')}</code>, "
        f"returning {report.total_citations} cited sources.</li>",
        "<li>Risk tiers, required documents and blocking status assigned by a "
        "deterministic rule engine. No model output sits between the cited evidence "
        "and the positions stated above; the rules applied are named against every item.</li>",
        "</ul>",
        '<div class="disclaimer">This report is a research and triage instrument for a '
        "production's clearance workflow. It is not legal advice and does not replace "
        "review by qualified counsel or a clearance attorney. It records what is "
        "<em>required</em>, not what has been obtained.</div>",
        "</div></div></body></html>",
    ]
    return "\n".join(parts)
