"""HTML renderer for the all-research Research Atlas."""

from __future__ import annotations

import html
import json
import os
from pathlib import Path
from typing import Any

from factor_lab.alpha.research_portfolio import ROLES, build_portfolio
from factor_lab.core import config


CSS = """
:root{--ink:#11161d;--muted:#5b6472;--line:#dfe4ea;--bg:#f4f6f9;--card:#fff;--blue:#1f5eff;--blue-soft:#eef3ff;--green:#0b7a53;--green-soft:#e9f7f0;--amber:#9a6400;--amber-soft:#fff5dd;--red:#a4262c;--red-soft:#fdeeee;--violet:#5b3ea8;--violet-soft:#f1ecff;--mono:Consolas,monospace;--sans:"Segoe UI","PingFang SC","Microsoft YaHei",system-ui,sans-serif}
*{box-sizing:border-box}html{scroll-behavior:smooth}body{margin:0;background:var(--bg);color:var(--ink);font-family:var(--sans);line-height:1.62;font-size:15px}a{color:var(--blue);text-decoration:none}a:hover{text-decoration:underline}
.hero{background:#0d1522;color:#e9eef7;padding:46px 28px 32px}.wrap{max-width:1200px;margin:0 auto}.eyebrow{font-size:12px;letter-spacing:.16em;text-transform:uppercase;color:#7fa6ff;font-weight:700}h1{font-size:38px;line-height:1.1;letter-spacing:-.02em;margin:12px 0}.lead{color:#b9c6dc;max-width:1080px;margin:0}.meta{color:#8194b0;font-size:12px;margin-top:13px}
.kpis{display:grid;grid-template-columns:repeat(6,minmax(0,1fr));gap:10px;margin-top:22px}.kpi{background:#131f30;border:1px solid #24344b;border-radius:12px;padding:11px 13px}.kpi b{display:block;font-size:23px;line-height:1.1;font-variant-numeric:tabular-nums}.kpi span{font-size:11.5px;color:#9db0cb}
.questions{display:grid;grid-template-columns:repeat(6,minmax(0,1fr));gap:10px;max-width:1200px;margin:22px auto 0;padding:0 28px}.question{background:#fff;border:1px solid var(--line);border-radius:12px;padding:12px 13px;font-size:12.5px;color:#39445a}.question b{display:block;color:var(--ink);font-size:14px;margin-bottom:2px}
.layout{max-width:1200px;margin:26px auto 0;padding:0 28px 70px;display:grid;grid-template-columns:220px minmax(0,1fr);gap:28px}nav.toc{position:sticky;top:18px;align-self:start;background:#fff;border:1px solid var(--line);border-radius:14px;padding:15px}.toc h2{font-size:11px;letter-spacing:.13em;text-transform:uppercase;color:var(--muted);margin:0 0 9px}.toc a{display:block;padding:5px 7px;border-radius:7px;color:#39445a;font-size:12.5px}.toc a:hover{background:var(--blue-soft);text-decoration:none}main{min-width:0}
section{background:#fff;border:1px solid var(--line);border-radius:16px;padding:24px 26px;margin:0 0 20px;box-shadow:0 2px 10px rgba(16,24,40,.03)}section h2{font-size:22px;letter-spacing:-.01em;margin:0 0 5px}.sub{color:var(--muted);font-size:13.3px;margin:0 0 14px}
.grid{display:grid;gap:12px}.g4{grid-template-columns:repeat(4,minmax(0,1fr))}.g2{grid-template-columns:repeat(2,minmax(0,1fr))}.g3{grid-template-columns:repeat(3,minmax(0,1fr))}
.card{border:1px solid var(--line);border-radius:12px;padding:13px 15px;background:#fcfcfd}.card b{display:block;font-size:14px}.card p{margin:4px 0 0;color:var(--muted);font-size:12.5px}.big{font-size:26px;font-weight:800;font-variant-numeric:tabular-nums;line-height:1.1}
.tag{display:inline-block;font-size:10.5px;font-weight:800;padding:2px 7px;border-radius:6px;white-space:nowrap;margin:1px 2px 1px 0}.pass{background:var(--green-soft);color:var(--green)}.fail{background:var(--red-soft);color:var(--red)}.warn{background:var(--amber-soft);color:var(--amber)}.info{background:var(--blue-soft);color:var(--blue)}.violet{background:var(--violet-soft);color:var(--violet)}.muted{color:var(--muted)}
.flow{display:flex;flex-wrap:wrap;gap:6px;align-items:center;margin:14px 0}.flow span{font-size:12px;background:#f3f5f8;border:1px solid var(--line);border-radius:8px;padding:5px 9px}.flow i{color:#9aa4b2;font-style:normal}
.campaign{border:1px solid var(--line);border-radius:14px;background:#fcfcfd;margin:0 0 13px;overflow:hidden}.campaign-head{display:flex;gap:12px;align-items:flex-start;padding:14px 16px 8px}.phase{font-family:var(--mono);font-size:11px;font-weight:800;color:#fff;background:#24344b;border-radius:7px;padding:4px 6px;white-space:nowrap}.campaign h3{font-size:15.5px;margin:0}.campaign .mini{color:var(--muted);font-size:12px}.campaign .objective{padding:0 16px;margin:4px 0 8px;color:#3c4556;font-size:13px}.round-chain{display:flex;flex-wrap:wrap;gap:5px;padding:0 16px 9px}.round{font-family:var(--mono);font-size:10.5px;border:1px solid var(--line);background:#fff;border-radius:6px;padding:3px 6px;color:#3c4556}.round.good{border-color:#b8e4d1;background:var(--green-soft)}.round.bad{border-color:#f3c7c9;background:var(--red-soft)}.round.stop{border-color:#f1d9a6;background:var(--amber-soft)}.summary-line{display:flex;flex-wrap:wrap;gap:14px;padding:8px 16px;border-top:1px solid var(--line);border-bottom:1px solid var(--line);background:#fff;font-size:12px}.summary-line b{font-variant-numeric:tabular-nums}.campaign .conclusion{padding:9px 16px;font-size:12.6px;color:#374151}.campaign details{padding:0 16px 14px}.campaign summary{cursor:pointer;color:var(--blue);font-size:12.5px;padding:4px 0}
.idea{border-left:3px solid #dbe3ee;padding:7px 10px;margin:7px 0;background:#fff;border-radius:0 8px 8px 0}.idea .top{font-size:12.5px;font-weight:700}.idea .detail{color:var(--muted);font-size:12.2px;margin-top:2px}.idea code{font-family:var(--mono);font-size:11.5px;background:#f1f4f8;padding:1px 4px;border-radius:4px;word-break:break-all}
table.data,.matrix{width:100%;border-collapse:collapse;font-size:12.1px}table.data th,table.data td,.matrix th,.matrix td{border-bottom:1px solid var(--line);padding:7px 8px;text-align:left;vertical-align:top}table.data th,.matrix th{background:#f3f5f8;font-size:11px;color:#3c4556}table.data tr:last-child td,.matrix tr:last-child td{border-bottom:0}.matrix td.agent-yes{background:#f1fbf7}.matrix td.agent-no{color:#a0a8b5;background:#fbfcfd}.num{font-variant-numeric:tabular-nums;white-space:nowrap}code{font-family:var(--mono);font-size:11.8px;background:#eef1f5;padding:1px 5px;border-radius:4px;word-break:break-all}
.callout{border-left:4px solid var(--blue);background:var(--blue-soft);border-radius:0 10px 10px 0;padding:11px 14px;margin:12px 0;font-size:13.2px}.callout.good{border-color:var(--green);background:var(--green-soft)}.callout.warn{border-color:var(--amber);background:var(--amber-soft)}.callout.bad{border-color:var(--red);background:var(--red-soft)}
.filters{display:flex;flex-wrap:wrap;gap:7px;align-items:center;margin:10px 0 14px}.filter{border:1px solid var(--line);background:#fff;border-radius:999px;padding:5px 11px;font-size:12px;cursor:pointer}.filter.active{background:#0d1522;color:#fff;border-color:#0d1522}input.search{border:1px solid var(--line);border-radius:9px;padding:8px 11px;font:inherit;font-size:12.5px;min-width:220px;background:#fff}details.block{border:1px solid var(--line);border-radius:10px;padding:10px 12px;margin:9px 0;background:#fff}details.block summary{cursor:pointer;font-weight:600;font-size:13px;color:#2e3a4d}footer{max-width:1200px;margin:0 auto;padding:0 28px 50px;color:var(--muted);font-size:12px}
@media(max-width:1050px){.kpis{grid-template-columns:repeat(3,1fr)}.questions{grid-template-columns:repeat(3,1fr)}.layout{grid-template-columns:1fr;padding:20px}nav.toc{position:static}.g4{grid-template-columns:repeat(2,1fr)}}@media(max-width:650px){.kpis,.questions,.g4,.g2,.g3{grid-template-columns:1fr}h1{font-size:29px}}@media print{body{background:#fff}nav.toc{display:none}.layout{display:block;padding:0}section{box-shadow:none;break-inside:avoid}}
"""


def _e(value: Any) -> str:
    return html.escape("" if value is None else str(value))


def _tag(label: Any, tone: str) -> str:
    return f'<span class="tag {tone}">{_e(label)}</span>'


def _num(value: Any, digits: int = 4) -> str:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return "—"
    if number != number:
        return "—"
    if abs(number) >= 1000:
        return f"{number:,.1f}"
    if abs(number) >= 1:
        return f"{number:.{digits}g}"
    return f"{number:.{digits}f}"


def _link(path: Any, base: Path, label: str | None = None) -> str:
    if not path:
        return ""
    try:
        target = os.path.relpath(str(path), start=str(base)).replace("\\", "/")
    except Exception:  # noqa: BLE001
        target = str(path)
    return f'<a href="{_e(target)}">{_e(label or Path(str(path)).name)}</a>'


def _verdict_tag(verdict: Any) -> str:
    text = str(verdict or "")
    if text == "pass":
        return _tag("pass", "pass")
    if text in {"fail", "admission_fail"}:
        return _tag(text, "fail")
    return _tag(text or "—", "info")


def _round_chip(row: dict[str, Any]) -> str:
    metrics = row.get("metrics") or {}
    verdict = str(row.get("verdict") or "")
    cls = "good" if verdict == "pass" else "bad" if verdict == "fail" else "stop" if row.get("decision") == "stop" else ""
    net_t = metrics.get("net_t")
    suffix = f" · t {_num(net_t, 3)}" if net_t is not None else ""
    schema = str(row.get("schema") or "").replace("factor_lab.alpha.", "").replace(".v1", "")
    return f'<span class="round {cls}">{_e(row.get("round_id"))} · {_e(schema[:26])}{_e(suffix)}</span>'


def _campaign_datasets(campaign: dict[str, Any]) -> str:
    seen = {}
    for manifest in campaign.get("dataset_manifests") or []:
        if isinstance(manifest, dict):
            key = (str(manifest.get("start")), str(manifest.get("end")), int(manifest.get("codes") or 0), len(manifest.get("fields") or []))
            seen[key] = manifest
    if not seen:
        return '<p class="muted">该 campaign 没有记录 data_manifest。</p>'
    rows = "".join(
        f"<tr><td>{_e(m.get('start'))}..{_e(m.get('end'))}</td><td class='num'>{_e(m.get('codes'))}</td><td class='num'>{len(m.get('fields') or [])}</td><td>{_e(', '.join(m.get('services') or []))}</td></tr>"
        for m in seen.values()
    )
    return '<table class="data"><thead><tr><th>Range</th><th>Codes</th><th>Fields</th><th>Services</th></tr></thead><tbody>' + rows + "</tbody></table>"


def _candidate_table(campaign: dict[str, Any]) -> str:
    rows = campaign.get("candidates") or []
    if not rows:
        return '<p class="muted">没有独立 cycle candidate metrics；ML admission 见 campaign 摘要。</p>'
    body = []
    for row in rows:
        metrics = row.get("metrics") or {}
        fdr = row.get("program_fdr")
        if fdr is True:
            fdr_html = _tag("pass", "pass")
        elif fdr is False:
            fdr_html = _tag(f"fail p={_num(row.get('program_p'), 3)}", "fail")
        else:
            fdr_html = "—"
        body.append(
            "<tr>"
            f"<td><code>{_e(row.get('factor_id') or '—')}</code></td>"
            f"<td>{_e(row.get('family') or '—')}</td>"
            f"<td>{_verdict_tag(row.get('verdict'))}</td>"
            f"<td class='num'>{_num(metrics.get('net_spread'))}</td>"
            f"<td class='num'>{_num(metrics.get('net_t'))}</td>"
            f"<td class='num'>{_num(metrics.get('coverage'))}</td>"
            f"<td class='num'>{_num(row.get('p_one_sided_positive'), 4)} / {_num(row.get('p_two_sided'), 4)}</td>"
            f"<td>{fdr_html}</td>"
            f"<td>{_e(row.get('round_id') or '—')}</td>"
            "</tr>"
        )
    return '<table class="data"><thead><tr><th>Candidate</th><th>Family</th><th>Gate</th><th>net</th><th>net_t</th><th>coverage</th><th>p(pos/two)</th><th>Program FDR</th><th>Round</th></tr></thead><tbody>' + "".join(body) + "</tbody></table>"


def _idea_block(campaign: dict[str, Any], base: Path) -> str:
    ideas = campaign.get("ideas") or []
    if not ideas:
        return '<p class="muted">没有 team artifact 或可解析 idea。</p>'
    out = []
    for idea in ideas:
        out.append(
            '<div class="idea">'
            f'<div class="top">{_tag(idea.get("agent") or "—", "info")} {_tag(idea.get("kind") or "idea", "violet")} {_e(idea.get("title"))}</div>'
            + (f'<div class="detail">{_e(idea.get("detail"))}</div>' if idea.get("detail") else "")
            + (f'<div class="detail">{_link(idea.get("source"), base)}</div>' if idea.get("source") else "")
            + "</div>"
        )
    return "".join(out)


def _agent_evidence_block(campaign: dict[str, Any], base: Path) -> str:
    out = []
    for role, label in ROLES:
        evidence = (campaign.get("agents") or {}).get(role) or {}
        if not evidence.get("used"):
            continue
        files = evidence.get("files") or []
        tasks = evidence.get("tasks") or []
        out.append(
            f'<div class="idea"><div class="top">{_tag(label, "info")} {_e(evidence.get("status") or "")} '
            + (f'{_e(", ".join(tasks))}' if tasks else "artifact evidence")
            + "</div>"
            + (f'<div class="detail">{_link(files[0], base)}</div>' if files else "")
            + "</div>"
        )
    return "".join(out) or '<p class="muted">该 campaign 没有 team artifact；只有 deterministic records。</p>'


def _campaign_card(campaign: dict[str, Any], base: Path) -> str:
    best = campaign.get("best") or {}
    metrics = best.get("metrics") or {}
    audit = campaign.get("protocol_audit")
    audit_html = _tag("audit ok", "pass") if isinstance(audit, dict) and audit.get("ok") else _tag("audit FAILED", "fail") if isinstance(audit, dict) else ""
    holdout = campaign.get("holdout")
    holdout_html = _tag(f"holdout {holdout}", "warn") if holdout else ""
    rounds = "".join(_round_chip(row) for row in (campaign.get("rounds") or [])[:18])
    ml = campaign.get("ml_summary")
    ml_html = ""
    if ml:
        m = ml.get("metrics") or {}
        admission = ml.get("admission") or {}
        ml_html = f'<div class="idea"><div class="top">{_tag("ML admission", "violet")}</div><div class="detail">OOS IC {_num(m.get("oos_rank_ic_mean"))} / t {_num(m.get("oos_rank_ic_t"))} · passed={_e(admission.get("passed"))} · blocked={_e(admission.get("blocked_reasons"))}</div></div>'
    return f"""
    <article class="campaign" data-kind="{_e(campaign.get('kind'))}" data-search="{_e(campaign.get('campaign_id'))} {_e(campaign.get('label'))} {_e(campaign.get('objective'))}">
      <div class="campaign-head"><span class="phase">P{campaign.get('phase'):02d}</span><div><h3>{_e(campaign.get('label'))}</h3>
      <div class="mini">{_e(campaign.get('campaign_id'))} · {_e(campaign.get('kind_label'))} · {_e(campaign.get('state'))} · {_e(campaign.get('created_at'))}</div>
      <div class="mini">{audit_html} {holdout_html}</div></div></div>
      <p class="objective">{_e(campaign.get('objective'))}</p>
      <div class="round-chain">{rounds}</div>
      <div class="summary-line"><span>rounds <b>{_e(campaign.get('n_rounds'))}</b></span><span>candidates <b>{_e(len(campaign.get('candidates') or []))}</b></span><span>gate pass <b>{_e(campaign.get('gate_pass'))}</b></span><span>best net_t <b>{_num(metrics.get('net_t'))}</b></span><span>FDR pass <b>{_e(len(campaign.get('fdr_pass') or []))}</b></span><span>cards <b>{_e(campaign.get('edge_status'))}</b></span></div>
      <div class="conclusion"><b>结论：</b>{_e(campaign.get('conclusion'))}</div>
      <details><summary>展开：idea / agents / results / datasets</summary>
        <h4>Idea provenance</h4>{_idea_block(campaign, base)}
        <h4>Agent evidence</h4>{_agent_evidence_block(campaign, base)}
        <h4>Candidate results</h4>{_candidate_table(campaign)}
        {ml_html}
        <h4>Datasets</h4>{_campaign_datasets(campaign)}
      </details>
    </article>
    """


def _ideas_section(campaigns: list[dict[str, Any]], base: Path) -> str:
    out = []
    for c in campaigns:
        if c.get("kind") == "calibration" or not c.get("ideas"):
            continue
        out.append(f'<details class="block"><summary>P{c.get("phase"):02d} · {_e(c.get("label"))} ({len(c.get("ideas") or [])} ideas)</summary>{_idea_block(c, base)}</details>')
    return "".join(out) or '<p class="muted">没有想法记录。</p>'


def _agent_matrix(campaigns: list[dict[str, Any]]) -> str:
    head = "".join(f"<th>{_e(label)}</th>" for _role, label in ROLES)
    rows = []
    for c in campaigns:
        cells = []
        for role, _label in ROLES:
            evidence = (c.get("agents") or {}).get(role) or {}
            if not evidence.get("used"):
                cells.append('<td class="agent-no">—</td>')
            else:
                bits = [str(evidence.get("status") or "")]
                if evidence.get("tasks"):
                    bits.append(", ".join(evidence["tasks"]))
                elif evidence.get("files"):
                    bits.append(f"{len(evidence['files'])} files")
                cells.append(f'<td class="agent-yes">{"<br>".join(_e(bit) for bit in bits if bit)}</td>')
        rows.append(f"<tr><td><b>{_e(c.get('label'))}</b><br><span class='muted'>{_e(c.get('kind_label'))}</span></td>{''.join(cells)}</tr>")
    return f'<table class="matrix"><thead><tr><th>Campaign</th>{head}</tr></thead><tbody>{"".join(rows)}</tbody></table>'


def _results_table(campaigns: list[dict[str, Any]]) -> str:
    rows = []
    for c in campaigns:
        best = c.get("best") or {}
        metrics = best.get("metrics") or {}
        rows.append(
            "<tr>"
            f"<td><b>{_e(c.get('label'))}</b><br><span class='muted'>{_e(c.get('kind_label'))}</span></td>"
            f"<td class='num'>{len(c.get('candidates') or [])}</td>"
            f"<td class='num'>{_e(c.get('gate_pass'))}</td>"
            f"<td class='num'>{_num(metrics.get('net_t'))}</td>"
            f"<td><code>{_e(best.get('factor_id') or '—')}</code></td>"
            f"<td class='num'>{len(c.get('fdr_pass') or [])}</td>"
            f"<td>{_e(c.get('edge_status'))}</td>"
            "</tr>"
        )
    return '<table class="data"><thead><tr><th>Campaign</th><th>Evaluated</th><th>Gate pass</th><th>Best net_t</th><th>Best factor</th><th>FDR pass</th><th>Edge Cards</th></tr></thead><tbody>' + "".join(rows) + "</tbody></table>"


def _axes_table(axes: dict[str, Any]) -> str:
    fields = axes.get("fields") or {}
    if not fields:
        return '<p class="muted">没有 P0/P1 数据轴覆盖文件。</p>'
    rows = []
    for name, value in sorted(fields.items()):
        rows.append(f"<tr><td>{_tag(str(value.get('group', '')).upper(), 'info')}</td><td><code>{_e(name)}</code></td><td>{_e(value.get('axis'))}</td><td class='num'>{_num(value.get('coverage'))}</td><td class='num'>{_num(value.get('nonzero'))}</td></tr>")
    return '<table class="data"><thead><tr><th>Group</th><th>Field</th><th>Axis</th><th>Coverage</th><th>Nonzero</th></tr></thead><tbody>' + "".join(rows) + "</tbody></table>"


def _axis_usage_table(portfolio: dict[str, Any]) -> str:
    rows = portfolio.get("axis_usage") or []
    if not rows:
        return '<p class="muted">没有 candidate expression 可映射到数据轴。</p>'
    body = "".join(
        f"<tr><td>{_tag(str(row.get('group', '')).upper(), 'info')}</td><td>{_e(row.get('label'))}</td><td class='num'>{len(row.get('fields') or [])}</td><td class='num'>{_e(row.get('candidates'))}</td><td>{_e(', '.join(row.get('campaigns') or [])) or '—'}</td></tr>"
        for row in rows
    )
    return '<table class="data"><thead><tr><th>Group</th><th>Axis</th><th>Fields</th><th>Candidate uses</th><th>Campaigns</th></tr></thead><tbody>' + body + "</tbody></table>"


def render_portfolio(portfolio: dict[str, Any]) -> str:
    base = Path(portfolio.get("base_dir") or "output/deliverables")
    campaigns = portfolio.get("campaigns") or []
    kpis = portfolio.get("kpis") or {}
    meaningful = [c for c in campaigns if c.get("kind") != "calibration"]
    datasets = portfolio.get("datasets") or []
    holdout = portfolio.get("holdout") or {}
    probes = portfolio.get("probes") or []
    latest = probes[-1]["payload"] if probes else {}
    probe = latest.get("conclusion") or {}
    audit_ok = sum(1 for c in campaigns if isinstance(c.get("protocol_audit"), dict) and c["protocol_audit"].get("ok"))
    audit_fail = sum(1 for c in campaigns if isinstance(c.get("protocol_audit"), dict) and not c["protocol_audit"].get("ok"))
    cards = "".join(
        f'<div class="card"><b>{_e(d.get("codes"))} codes · {_e(d.get("start"))}..{_e(d.get("end"))}</b><p>{len(d.get("fields") or [])} fields · {_e(", ".join(d.get("services") or []))} · hash {_e(d.get("hash"))}</p><p>campaigns: {_e(", ".join(d.get("campaigns") or []))}</p></div>'
        for d in datasets
    )
    dataset_table = '<table class="data"><thead><tr><th>Range</th><th>Window</th><th>Codes</th><th>Rows</th><th>Fields</th><th>Services</th><th>Campaigns</th></tr></thead><tbody>' + "".join(
        f"<tr><td>{_e(d.get('start'))}..{_e(d.get('end'))}</td><td>{_e(d.get('window_type') or 'dev')}</td><td class='num'>{_e(d.get('codes'))}</td><td class='num'>{_e(d.get('rows'))}</td><td class='num'>{len(d.get('fields') or [])}</td><td>{_e(', '.join(d.get('services') or []))}</td><td>{_e(', '.join(d.get('campaigns') or []))}</td></tr>"
        for d in datasets
    ) + "</tbody></table>"
    conclusion_cards = "".join(f'<div class="card"><b>{_e(c.get("title"))}</b><p>{_e(c.get("body"))}</p></div>' for c in portfolio.get("portfolio_conclusions") or [])
    conclusion_rows = "".join(f'<div class="idea"><div class="top">{_e(c.get("label"))} {_tag(c.get("kind_label"), "info")}</div><div class="detail">{_e(c.get("conclusion"))}</div><div class="detail">next: {_e(c.get("next_step"))}</div></div>' for c in meaningful)
    return f"""<!DOCTYPE html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Factor Lab · Research Atlas</title><style>{CSS}</style></head><body>
<header class="hero"><div class="wrap"><div class="eyebrow">Factor Lab · research portfolio · structured-data aggregation</div><h1>Research Atlas</h1>
<p class="lead">所有研究 campaign 的迭代、想法来源、agent 参与、测试结果、数据集与结论。只读聚合；不引入新的 LLM 判断。</p>
<div class="meta">generated {_e(portfolio.get('generated_at'))} · {_e(kpis.get('campaigns'))} campaigns · {_e(kpis.get('attempts'))} attempts · {_e(kpis.get('ledger_trials'))} ledger trials · holdout locked={_e(holdout.get('locked'))} · holdout uses={_e(kpis.get('holdout_uses'))} · trial events={_e(kpis.get('trial_events'))} · Alpha fees buy/sell={_e((portfolio.get('cost_policy') or {}).get('cost', {}).get('buy_fee_bps'))}/{_e((portfolio.get('cost_policy') or {}).get('cost', {}).get('sell_fee_bps'))}bps<br>git {_e(str(portfolio.get('git_commit') or 'unknown')[:12])} · policy {_e(str((portfolio.get('policy') or {}).get('policy_hash') or '')[:12])} · fdr rank1 t={_e((portfolio.get('fdr_population') or {}).get('rank1_t'))}</div>
<div class="kpis"><div class="kpi"><b>{_e(kpis.get('campaigns'))}</b><span>campaigns</span></div><div class="kpi"><b>{_e(kpis.get('attempts'))}</b><span>attempts</span></div><div class="kpi"><b>{_e(kpis.get('ledger_trials'))}</b><span>ledger trials</span></div><div class="kpi"><b>{_e(kpis.get('edge_cards'))}</b><span>Edge Cards</span></div><div class="kpi"><b>{_e(kpis.get('validated'))}</b><span>validated</span></div><div class="kpi"><b>{_num(kpis.get('best_net_t'))}</b><span>best dev net_t</span></div></div></div></header>
<div class="questions"><a class="question" href="#evolution"><b>01 迭代</b>研究如何推进</a><a class="question" href="#ideas"><b>02 想法</b>问题/地图/预注册</a><a class="question" href="#agents"><b>03 Agent</b>谁参与、产出</a><a class="question" href="#results"><b>04 测试</b>Gate/net_t/FDR</a><a class="question" href="#datasets"><b>05 数据</b>窗口/字段/覆盖</a><a class="question" href="#conclusions"><b>06 结论</b>现在知道什么</a></div>
<div class="layout"><nav class="toc"><h2>Atlas</h2><a href="#snapshot">研究快照</a><a href="#evolution">01 研究迭代</a><a href="#ideas">02 想法是怎么来的</a><a href="#agents">03 Agent 调用</a><a href="#results">04 测试结果</a><a href="#datasets">05 数据集</a><a href="#conclusions">06 结论</a><a href="#method">方法与边界</a></nav><main>
<section id="snapshot"><h2>研究快照</h2><p class="sub">先看当前状态，再逐层展开证据。</p>
<div class="grid g4"><div class="card"><div class="big">{_e(kpis.get('campaigns'))}</div><p>campaigns（{_e(kpis.get('meaningful_campaigns'))} 个研究/discovery/ML；{_e(kpis.get('calibration_campaigns'))} 个 L0 smoke）</p></div><div class="card"><div class="big">{_e(kpis.get('edge_cards'))}</div><p>Edge Cards：{_e(kpis.get('killed'))} killed / {_e(kpis.get('research_cards'))} research / {_e(kpis.get('validated'))} validated</p></div><div class="card"><div class="big">{_e(kpis.get('agents_used'))} / 5</div><p>model-facing roles；{_e(kpis.get('audited_campaigns'))}/{_e(kpis.get('campaigns'))} audited（{audit_ok} ok / {audit_fail} failed）</p></div><div class="card"><div class="big">{_num(kpis.get('best_net_t'))}</div><p>历史 best dev net_t（{_e(kpis.get('best_factor'))}）；不是 freeze 证据</p></div></div>
<div class="callout warn"><b>读法：</b>Gate pass ≠ freeze；family FDR pass ≠ program FDR pass。holdout 仍 {_e('locked' if holdout.get('locked') else 'not locked')}；没有 validated/active 卡。</div>
<div class="callout"><b>P0 probe：</b>{_e(latest.get('probe_id'))} · {_e(probe.get('code'))} · instrument_trusted={_e(probe.get('instrument_trusted'))}</div></section>

<section id="evolution"><h2>01 · 研究是如何迭代的</h2><p class="sub">按时间顺序展开 campaign；round chain 显示 decision 与关键 net_t。点击展开可看 idea、agents、candidates、datasets。</p>
<div class="filters"><button class="filter active" data-filter-kind="all">全部</button><button class="filter" data-filter-kind="research">研究</button><button class="filter" data-filter-kind="discovery">Discovery</button><button class="filter" data-filter-kind="ml">ML</button><button class="filter" data-filter-kind="calibration">L0 / smoke</button><input class="search" id="search" placeholder="搜索 campaign / objective / ID"></div>
<div class="flow"><span>P0 probes</span><i>→</i><span>MAX/lottery</span><i>→</i><span>baby</span><i>→</i><span>post-kill search</span><i>→</i><span>attribution</span><i>→</i><span>low-price/value</span><i>→</i><span>P1/P1.5 FDR</span><i>→</i><span>P3 discovery</span><i>→</i><span>P2 ML</span><i>→</i><span>P0/P1 batch</span><i>→</i><span>holder robustness</span></div>
{''.join(_campaign_card(c, base) for c in campaigns)}</section>

<section id="ideas"><h2>02 · 想法是如何产生的</h2><p class="sub">PathFinder 问题 / Web Gateway leads / ML 计划 / pre-registration / FactorBuilder artifact；文本直接来自 team artifact。</p>
<div class="flow"><span>PathFinder question</span><i>→</i><span>Gateway / data axes</span><i>→</i><span>pre-registration</span><i>→</i><span>FactorBuilder artifact</span><i>→</i><span>Gate</span><i>→</i><span>program FDR</span><i>→</i><span>conclusion</span></div>
{_ideas_section(campaigns, base)}</section>

<section id="agents"><h2>03 · 调用了哪些 Agent</h2><p class="sub">role × campaign 矩阵；绿色格表示有 team artifact / round provenance / artifact spec 证据。老 campaign 没有 team 目录时显示 —。</p>{_agent_matrix(campaigns)}
<div class="callout warn"><b>注意：</b>— 表示没有留下 team 证据，不等于没有研究；新 campaign 必须记录 round agents/team_tasks 并通过 <code>alpha campaign-audit</code>。</div></section>

<section id="results"><h2>04 · 测试结果</h2><p class="sub">Gate / metrics / Edge Cards / program FDR。候选表只展示有独立 metrics 的 cycle；ML admission 单独展示。</p>{_results_table(campaigns)}
{_ml_summary(campaigns, base)}
<div class="card"><b>Program FDR population</b><p>n_trials {_e((portfolio.get('fdr_population') or {}).get('n_trials'))} · n_unique {_e((portfolio.get('fdr_population') or {}).get('n_unique'))} · n_eff {_e((portfolio.get('fdr_population') or {}).get('n_eff'))} · rank1 p {_e((portfolio.get('fdr_population') or {}).get('rank1_p'))} · rank1 |t| {_e((portfolio.get('fdr_population') or {}).get('rank1_t'))}</p></div>
<div class="grid g2" style="margin-top:14px"><div class="card"><b>全局 observed best</b><p>{_e(kpis.get('best_factor'))} · net_t {_num(kpis.get('best_net_t'))}<br>dev evidence only；不是 freeze。</p></div><div class="card"><b>FDR 纪律</b><p>program FDR pass={_e('yes' if kpis.get('fdr_any') else 'no')}；killed cards={_e(kpis.get('killed'))}；validated/active={_e(kpis.get('validated'))}；unattributed cards={_e(kpis.get('unattributed_cards'))}。</p></div></div>
<details class="block"><summary>展开 candidate 明细</summary>{''.join(f'<h4>P{c.get("phase"):02d} · {_e(c.get("label"))}</h4>{_candidate_table(c)}' for c in meaningful)}</details></section>

<section id="datasets"><h2>05 · 用到了怎样的数据集</h2><p class="sub">来自 data_manifest / P0/P1 data axes / holdout policy；不推断未记录的数据。</p>
<div class="grid g2">{cards or '<div class="card"><b>无 data_manifest</b><p>没有 campaign 留下 data manifest。</p></div>'}</div>
<h3>唯一 dataset manifests</h3>{dataset_table}
<h3>P0/P1 数据轴覆盖（dev panel）</h3>{_axes_table(portfolio.get('axes') or {})}
<h3>数据轴在 candidate 中的实际使用</h3>{_axis_usage_table(portfolio)}
<div class="callout warn"><b>使用 vs 可用：</b>P0/P1 字段进入了 Panel/AlphaContext；默认 <code>alpha search</code> 的字段池现已包含这些轴。campaign 中是否使用，要看 candidate expression 的字段；这张表是实际使用证据，不是可用性声明。</div>
<div class="callout warn"><b>Holdout 审计：</b>历史 manifest 中出现 {_e(kpis.get('historical_holdout_datasets'))} 个 non-dev window；它们是历史 holdout 证据，不代表当前研究可以使用。当前 policy 仍 locked={_e(holdout.get('locked'))}；除历史 confirm 外，当前 campaign 只使用 dev。</div>
<div class="callout"><b>Holdout policy：</b>{_e(holdout)}</div></section>

<section id="conclusions"><h2>06 · 结论</h2><p class="sub">全局结论由结构化状态生成；campaign 结论来自 stop/summary round 或 team artifact。</p><div class="grid g2">{conclusion_cards}</div><h3>Campaign 结论</h3>{conclusion_rows}</section>

<section id="method"><h2>方法与边界</h2><ul><li>只读聚合 campaign rounds、team/、memory、families、factor library、probes、data_axes、holdout policy；不读取市场数据、不运行 gate/search。</li><li>campaign 按 created_at 排序；round metrics/decision 是原始记录，不重新解释。</li><li>文本直接来自结构化 artifact；损坏的历史文本只标记，不猜测补全。</li><li>Gate pass / net_t 不是 freeze 或 allocation。</li></ul><p class="muted">重新生成：<code>python -m factor_lab alpha report</code>。</p></section>
</main></div><footer>Factor Lab · Research Atlas · {_e(portfolio.get('generated_at'))} · structured-data aggregation, not investment advice.</footer>
<script>
(function(){{
 const cards=Array.from(document.querySelectorAll('.campaign'));const filters=Array.from(document.querySelectorAll('[data-filter-kind]'));const search=document.getElementById('search');let kind='all';
 function apply(){{const q=(search&&search.value||'').toLowerCase().trim();cards.forEach(card=>{{const okKind=kind==='all'||card.dataset.kind===kind;const okSearch=!q||(card.dataset.search||'').toLowerCase().includes(q);card.style.display=(okKind&&okSearch)?'':'none';}});}}
 filters.forEach(btn=>btn.addEventListener('click',()=>{{filters.forEach(b=>b.classList.remove('active'));btn.classList.add('active');kind=btn.dataset.filterKind;apply();}}));
 if(search)search.addEventListener('input',apply);
}})();
</script></body></html>"""


def write_portfolio_report(
    out: str | Path | None = None,
    *,
    root: str | Path | None = None,
    json_out: str | Path | None = None,
) -> dict[str, Any]:
    lab_root = Path(root) if root is not None else Path(config.LAB_ROOT)
    target = Path(out) if out is not None else lab_root.parent / "deliverables" / "research_portfolio.html"
    json_target = Path(json_out) if json_out is not None else target.with_suffix(".json")
    portfolio = build_portfolio(lab_root, base_dir=target.parent)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(render_portfolio(portfolio), encoding="utf-8")
    def _jsonable(value: Any) -> Any:
        if isinstance(value, Path):
            return str(value)
        if isinstance(value, dict):
            return {str(k): _jsonable(v) for k, v in value.items()}
        if isinstance(value, (list, tuple, set)):
            return [_jsonable(v) for v in value]
        return value
    json_target.write_text(json.dumps(_jsonable({k: v for k, v in portfolio.items() if k != "base_dir"}), ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    return {"schema": "factor_lab.alpha.research_portfolio_output.v1", "ok": True, "path": str(target), "json_path": str(json_target), "n_campaigns": len(portfolio.get("campaigns") or []), "generated_at": portfolio.get("generated_at")}


def _ml_summary(campaigns: list[dict[str, Any]], base: Path) -> str:
    rows = []
    for campaign in campaigns:
        ml = campaign.get("ml_summary")
        if not ml:
            continue
        metrics = ml.get("metrics") or {}
        admission = ml.get("admission") or {}
        rows.append(
            f'<div class="card"><b>{_e(campaign.get("label"))} · ML</b>'
            f'<p>OOS rank IC {_num(metrics.get("oos_rank_ic_mean"))} / t {_num(metrics.get("oos_rank_ic_t"))} · '
            f'admission passed={_e(admission.get("passed"))} · blocked={_e(admission.get("blocked_reasons"))}</p>'
            f'<p>{_e(ml.get("conclusion") or "")}</p>'
            + (f'<p>cycle={_e((ml.get("cycle") or {}).get("metrics"))}</p>' if ml.get("cycle") else "")
            + (f'<p>weights={_e(ml.get("standardized_weights") or ml.get("weights"))}</p>' if ml.get("weights") else "")
            + "</div>"
        )
    if not rows:
        return '<div class="callout warn"><b>ML：</b>本 portfolio 没有 ML summary 记录。</div>'
    return '<h3>ML 研究（独立分支）</h3><div class="grid g2">' + "".join(rows) + '</div><div class="callout warn"><b>为什么 ML 没有进入后续 factor 结果：</b>ML 先经过 pre-registered plan → admission；本次 P2 admission 未通过，且没有产生 distilled artifact，所以它没有进入后续 Edge Card / FDR / holdout 路径。这不是“ML 被禁用”，而是“没有合格 ML 实验进入 Factor Library”。</div>'
