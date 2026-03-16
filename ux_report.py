"""
ux_report.py — QAPal UX Audit Report Generator
=================================================
Generates rich HTML reports from UX audit results and exploration traces.

Features:
  - Severity-ranked findings with annotated screenshots
  - UX score and letter grade per page and overall
  - Heuristic category breakdown (pie/bar charts via inline SVG)
  - Exportable JSON for CI/CD integration
  - Finding cards with screenshot thumbnails

Usage:
    from ux_report import generate_ux_report
    html_path = generate_ux_report(audit_result, output_dir="reports/")
"""

import json
from collections import Counter
from datetime import datetime, timezone
from html import escape as _esc
from pathlib import Path
from string import Template
from typing import Optional


# ── HTML template ────────────────────────────────────────────────────

_UX_REPORT_TEMPLATE = """\
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8"/>
<title>QAPal UX Audit — $title</title>
<style>
 :root { --pass: #22c55e; --fail: #ef4444; --warn: #f59e0b; --info: #3b82f6;
         --high: #ef4444; --medium: #f59e0b; --low: #6b7280; }
 * { box-sizing: border-box; }
 body { font-family: system-ui, -apple-system, sans-serif; margin: 0;
        background: #f8fafc; color: #1e293b; line-height: 1.5; }
 header { background: linear-gradient(135deg, #1e1b4b 0%, #312e81 100%);
          color: #fff; padding: 1.5rem 2rem; }
 header h1 { margin: 0; font-size: 1.4rem; font-weight: 600; }
 header .subtitle { opacity: 0.7; font-size: 0.85rem; margin-top: 0.25rem; }

 .grade-ring { display: inline-flex; align-items: center; justify-content: center;
               width: 56px; height: 56px; border-radius: 50%; font-size: 1.6rem;
               font-weight: 700; border: 3px solid; }
 .grade-A { color: var(--pass); border-color: var(--pass); }
 .grade-B { color: #22d3ee; border-color: #22d3ee; }
 .grade-C { color: var(--warn); border-color: var(--warn); }
 .grade-D { color: #f97316; border-color: #f97316; }
 .grade-F { color: var(--fail); border-color: var(--fail); }

 main { padding: 1.5rem 2rem; max-width: 1200px; margin: 0 auto; }

 .summary-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(140px, 1fr));
                  gap: 1rem; margin-bottom: 2rem; }
 .stat-card { background: #fff; border-radius: 0.5rem; padding: 1rem 1.25rem;
              box-shadow: 0 1px 3px rgba(0,0,0,.06); }
 .stat-card .n { font-size: 1.8rem; font-weight: 700; line-height: 1; }
 .stat-card .l { font-size: 0.75rem; color: #64748b; margin-top: 0.25rem;
                 text-transform: uppercase; letter-spacing: 0.05em; }

 .section-title { font-size: 1.1rem; font-weight: 600; margin: 1.5rem 0 0.75rem;
                  padding-bottom: 0.5rem; border-bottom: 2px solid #e2e8f0; }

 .severity-bar { display: flex; height: 8px; border-radius: 4px; overflow: hidden;
                 margin-bottom: 1.5rem; background: #e2e8f0; }
 .severity-bar .seg { transition: width 0.3s; }
 .seg-high   { background: var(--high); }
 .seg-medium { background: var(--medium); }
 .seg-low    { background: var(--low); }

 .finding-card { background: #fff; border-radius: 0.5rem; padding: 1rem 1.25rem;
                 margin-bottom: 0.75rem; box-shadow: 0 1px 3px rgba(0,0,0,.06);
                 border-left: 4px solid var(--low); display: grid;
                 grid-template-columns: 1fr auto; gap: 1rem; align-items: start; }
 .finding-card.sev-high   { border-left-color: var(--high); }
 .finding-card.sev-medium { border-left-color: var(--medium); }
 .finding-card.sev-low    { border-left-color: var(--low); }
 .finding-card .badge { display: inline-block; padding: 0.15rem 0.5rem;
                        border-radius: 999px; font-size: 0.7rem; font-weight: 600;
                        text-transform: uppercase; letter-spacing: 0.03em; }
 .badge-high   { background: #fef2f2; color: var(--high); }
 .badge-medium { background: #fffbeb; color: #b45309; }
 .badge-low    { background: #f1f5f9; color: var(--low); }
 .badge-rule   { background: #eff6ff; color: var(--info); }
 .badge-vision { background: #f5f3ff; color: #7c3aed; }
 .finding-meta { font-size: 0.75rem; color: #94a3b8; margin-top: 0.35rem; }
 .finding-desc { font-size: 0.85rem; margin: 0.25rem 0; }
 .finding-thumb { width: 120px; height: 80px; object-fit: cover; border-radius: 0.375rem;
                  border: 1px solid #e2e8f0; }

 .category-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(200px, 1fr));
                  gap: 0.75rem; margin-bottom: 1.5rem; }
 .cat-card { background: #fff; border-radius: 0.375rem; padding: 0.75rem 1rem;
             box-shadow: 0 1px 2px rgba(0,0,0,.04); font-size: 0.85rem; }
 .cat-card .count { font-weight: 700; font-size: 1.2rem; }

 .page-section { margin-bottom: 2rem; }
 .page-url { font-family: monospace; font-size: 0.8rem; color: #475569;
             background: #f1f5f9; padding: 0.25rem 0.5rem; border-radius: 0.25rem; }

 footer { text-align: center; padding: 1.5rem; color: #94a3b8; font-size: 0.75rem; }
 a { color: var(--info); text-decoration: none; }
</style>
</head>
<body>
<header>
 <div style="display:flex;align-items:center;gap:1.25rem">
  <div class="grade-ring grade-$grade">$grade</div>
  <div>
   <h1>QAPal UX Audit Report</h1>
   <div class="subtitle">$subtitle</div>
  </div>
  <div style="margin-left:auto;text-align:right;font-size:.8rem;opacity:.7">
   Score: $score / 100<br>$generated_at
  </div>
 </div>
</header>
<main>

 <div class="summary-grid">
  <div class="stat-card"><div class="n" style="color:var(--high)">$high_count</div><div class="l">High</div></div>
  <div class="stat-card"><div class="n" style="color:var(--medium)">$medium_count</div><div class="l">Medium</div></div>
  <div class="stat-card"><div class="n" style="color:var(--low)">$low_count</div><div class="l">Low</div></div>
  <div class="stat-card"><div class="n">$total_findings</div><div class="l">Total</div></div>
  <div class="stat-card"><div class="n">$pages_count</div><div class="l">Pages</div></div>
  <div class="stat-card"><div class="n">$vision_calls</div><div class="l">Vision Calls</div></div>
  <div class="stat-card"><div class="n">$duration</div><div class="l">Duration</div></div>
 </div>

 <div class="severity-bar">$severity_bar</div>

 <div class="section-title">Findings by Category</div>
 <div class="category-grid">$category_cards</div>

 $page_sections

</main>
<footer>Generated by QAPal UX Audit Engine &bull; $generated_at</footer>
</body>
</html>
"""


# ── Public API ───────────────────────────────────────────────────────

def generate_ux_report(
    audit_result,            # UXAuditResult
    output_dir:  str = "reports",
    trace        = None,     # Optional ExplorationTrace
) -> tuple[Path, Path]:
    """
    Generate HTML and JSON UX audit reports.
    Returns (html_path, json_path).
    """
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)

    ts       = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    json_path = out / f"ux_audit_{ts}.json"
    html_path = out / f"ux_audit_{ts}.html"

    # Build JSON report
    json_data = _build_json(audit_result, trace)
    json_path.write_text(json.dumps(json_data, indent=2), encoding="utf-8")

    # Build HTML report
    html = _build_html(audit_result, trace)
    html_path.write_text(html, encoding="utf-8")

    return html_path, json_path


def generate_exploration_report(
    trace,                   # ExplorationTrace
    output_dir: str = "reports",
) -> tuple[Path, Path]:
    """
    Generate HTML and JSON reports from an exploration trace.
    Returns (html_path, json_path).
    """
    from ux_evaluator import UXAuditResult, UXFinding

    # Convert trace findings to UXFinding objects
    findings = []
    for f in trace.ux_findings:
        findings.append(UXFinding(
            heuristic       = f.get("heuristic", "N8_AESTHETIC"),
            severity        = f.get("severity", "low"),
            category        = f.get("category", "layout"),
            description     = f.get("description", ""),
            url             = f.get("url", trace.start_url),
            location        = f.get("location", ""),
            screenshot_path = f.get("screenshot_path", ""),
            source          = f.get("source", "vision"),
        ))

    from ux_evaluator import UXEvaluator
    score = UXEvaluator.compute_score(findings)

    audit = UXAuditResult(
        urls          = list({s.url for s in trace.steps}),
        findings      = findings,
        score         = score,
        audited_at    = trace.finished_at,
        duration_ms   = trace.duration_ms,
        vision_calls  = trace.vision_calls,
        pages_audited = trace.pages_visited,
    )

    return generate_ux_report(audit, output_dir, trace=trace)


# ── Internal builders ────────────────────────────────────────────────

def _build_json(audit_result, trace=None) -> dict:
    data = {
        "type":           "ux_audit",
        "score":          audit_result.score,
        "grade":          audit_result.grade,
        "urls":           audit_result.urls,
        "pages_audited":  audit_result.pages_audited,
        "vision_calls":   audit_result.vision_calls,
        "duration_ms":    audit_result.duration_ms,
        "audited_at":     audit_result.audited_at,
        "severity_counts": audit_result.severity_counts,
        "findings": [
            {
                "heuristic":   f.heuristic,
                "severity":    f.severity,
                "category":    f.category,
                "description": f.description,
                "url":         f.url,
                "element":     f.element,
                "selector":    f.selector,
                "location":    f.location,
                "source":      f.source,
                "wcag":        f.wcag_criterion,
            }
            for f in audit_result.findings
        ],
    }
    if trace:
        data["exploration"] = {
            "session_id":    trace.session_id,
            "start_url":     trace.start_url,
            "goal":          trace.goal,
            "total_steps":   len(trace.steps),
            "pages_visited": trace.pages_visited,
        }
    return data


def _build_html(audit_result, trace=None) -> str:
    findings = audit_result.findings
    sev      = audit_result.severity_counts
    total    = len(findings)

    # Severity bar percentages
    bar_parts = []
    if total > 0:
        for sev_name, css_class in [("high", "seg-high"), ("medium", "seg-medium"), ("low", "seg-low")]:
            pct = (sev.get(sev_name, 0) / total) * 100
            if pct > 0:
                bar_parts.append(f'<div class="seg {css_class}" style="width:{pct:.1f}%"></div>')
    severity_bar = "".join(bar_parts) if bar_parts else '<div class="seg seg-low" style="width:100%"></div>'

    # Category cards
    cat_counts = Counter(f.category for f in findings)
    category_cards = ""
    for cat, count in sorted(cat_counts.items(), key=lambda x: -x[1]):
        category_cards += (
            f'<div class="cat-card">'
            f'<span class="count">{count}</span> '
            f'{_esc(cat)}'
            f'</div>'
        )

    # Finding cards grouped by page
    by_page: dict[str, list] = {}
    for f in findings:
        by_page.setdefault(f.url or "unknown", []).append(f)

    page_sections = ""
    for url in sorted(by_page.keys()):
        page_findings = sorted(by_page[url], key=lambda f: {"high": 0, "medium": 1, "low": 2}.get(f.severity, 3))
        cards_html = ""
        for f in page_findings:
            thumb = ""
            if f.screenshot_path and Path(f.screenshot_path).exists():
                thumb = f'<img class="finding-thumb" src="{_esc(f.screenshot_path)}" alt="screenshot"/>'

            cards_html += (
                f'<div class="finding-card sev-{_esc(f.severity)}">'
                f'  <div>'
                f'    <span class="badge badge-{_esc(f.severity)}">{_esc(f.severity)}</span> '
                f'    <span class="badge badge-{_esc(f.source)}">{_esc(f.source)}</span>'
                f'    <p class="finding-desc">{_esc(f.description)}</p>'
                f'    <div class="finding-meta">'
                f'      {_esc(f.heuristic)} · {_esc(f.category)}'
                + (f' · {_esc(f.location)}' if f.location else '')
                + (f' · WCAG {_esc(f.wcag_criterion)}' if f.wcag_criterion else '')
                + (f' · <code>{_esc(f.selector)}</code>' if f.selector else '')
                + f'</div>'
                f'  </div>'
                f'  {thumb}'
                f'</div>\n'
            )

        page_sections += (
            f'<div class="page-section">'
            f'  <div class="section-title">'
            f'    <span class="page-url">{_esc(url)}</span>'
            f'    <span style="float:right;font-size:.8rem;color:#94a3b8">{len(page_findings)} findings</span>'
            f'  </div>'
            f'  {cards_html}'
            f'</div>\n'
        )

    gen_at   = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    subtitle = f"{total} findings across {audit_result.pages_audited} page(s)"
    if trace:
        subtitle += f" · Exploration: {len(trace.steps)} steps"
    duration_str = f"{audit_result.duration_ms // 1000}s" if audit_result.duration_ms else "—"

    return Template(_UX_REPORT_TEMPLATE).substitute(
        title          = f"Score {audit_result.score} ({audit_result.grade})",
        grade          = audit_result.grade,
        subtitle       = subtitle,
        score          = audit_result.score,
        generated_at   = gen_at,
        high_count     = sev.get("high", 0),
        medium_count   = sev.get("medium", 0),
        low_count      = sev.get("low", 0),
        total_findings = total,
        pages_count    = audit_result.pages_audited,
        vision_calls   = audit_result.vision_calls,
        duration        = duration_str,
        severity_bar   = severity_bar,
        category_cards = category_cards,
        page_sections  = page_sections,
    )
