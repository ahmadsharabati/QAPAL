"""
ux_evaluator.py — QAPal UX Heuristic Evaluator
=================================================
Evaluates web pages against established UX heuristics using a hybrid
approach: rule-based DOM checks (free) + VLM visual analysis (targeted).

Heuristic framework based on:
  - Nielsen's 10 Usability Heuristics
  - WCAG 2.1 AA accessibility guidelines
  - Common UX anti-patterns

The evaluator operates in two modes:
  1. Static audit   — evaluates already-crawled pages from the locator DB
  2. Live audit     — opens pages in a browser, runs full DOM + vision checks

Usage:
    evaluator = UXEvaluator(db, vision_client)
    report    = await evaluator.audit_url("https://app.com")
    report    = evaluator.audit_static(url)  # no browser, DB-only

Env vars:
    QAPAL_UX_MIN_CONTRAST   — minimum contrast ratio (default: 4.5 per WCAG AA)
    QAPAL_UX_MIN_TAP_TARGET — minimum tap target size in px (default: 44)
"""

import json
import os
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from locator_db import LocatorDB, _normalize_url

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass


# ── Config ────────────────────────────────────────────────────────────

MIN_CONTRAST   = float(os.getenv("QAPAL_UX_MIN_CONTRAST", "4.5"))
MIN_TAP_TARGET = int(os.getenv("QAPAL_UX_MIN_TAP_TARGET", "44"))


# ── Heuristic definitions ────────────────────────────────────────────

@dataclass
class UXFinding:
    """A single UX issue found during evaluation."""
    heuristic:       str                # which heuristic was violated
    severity:        str                # "high", "medium", "low"
    category:        str                # "accessibility", "layout", "forms", etc.
    description:     str                # human-readable description
    url:             str     = ""
    element:         str     = ""       # which element is affected
    selector:        str     = ""       # CSS/testid selector for the element
    location:        str     = ""       # where on the page
    screenshot_path: str     = ""       # annotated screenshot if available
    source:          str     = "rule"   # "rule" (DOM check) or "vision" (VLM)
    wcag_criterion:  str     = ""       # WCAG criterion if applicable


@dataclass
class UXAuditResult:
    """Complete UX audit for a single page or set of pages."""
    urls:            list[str]
    findings:        list[UXFinding]    = field(default_factory=list)
    score:           float              = 100.0  # 0-100, deducted per finding
    audited_at:      str                = ""
    duration_ms:     int                = 0
    vision_calls:    int                = 0
    pages_audited:   int                = 0

    @property
    def severity_counts(self) -> dict:
        counts = {"high": 0, "medium": 0, "low": 0}
        for f in self.findings:
            counts[f.severity] = counts.get(f.severity, 0) + 1
        return counts

    @property
    def grade(self) -> str:
        if self.score >= 90:
            return "A"
        if self.score >= 80:
            return "B"
        if self.score >= 70:
            return "C"
        if self.score >= 60:
            return "D"
        return "F"


# ── Heuristic IDs ────────────────────────────────────────────────────
# Based on Nielsen's 10 + WCAG + additional UX anti-patterns

HEURISTICS = {
    "N1_VISIBILITY":      "Visibility of system status",
    "N2_MATCH":           "Match between system and real world",
    "N3_CONTROL":         "User control and freedom",
    "N4_CONSISTENCY":     "Consistency and standards",
    "N5_ERROR_PREVENTION":"Error prevention",
    "N6_RECOGNITION":     "Recognition rather than recall",
    "N7_FLEXIBILITY":     "Flexibility and efficiency of use",
    "N8_AESTHETIC":       "Aesthetic and minimalist design",
    "N9_ERROR_RECOVERY":  "Help users recognise and recover from errors",
    "N10_HELP":           "Help and documentation",
    "WCAG_CONTRAST":      "WCAG 2.1 AA — Colour contrast",
    "WCAG_TAP_TARGET":    "WCAG 2.1 AA — Target size",
    "WCAG_LABELS":        "WCAG 2.1 AA — Form labels",
    "WCAG_ALT_TEXT":      "WCAG 2.1 AA — Image alt text",
    "WCAG_HEADING":       "WCAG 2.1 AA — Heading hierarchy",
    "UX_ORPHAN_FORM":     "Form without visible submit button",
    "UX_DEAD_LINK":       "Link with no href or empty href",
    "UX_MISSING_FOCUS":   "Interactive element without focus indicator",
}


# ── DOM-based rule checks (free — no AI) ─────────────────────────────

# JavaScript executed in the browser to collect UX metrics
_UX_AUDIT_JS = r"""
() => {
    const results = {
        forms_without_labels: [],
        small_tap_targets: [],
        missing_alt_text: [],
        empty_links: [],
        heading_hierarchy: [],
        orphan_forms: [],
        inputs_without_labels: [],
        contrast_issues: [],
        missing_landmarks: false,
        page_has_h1: false,
        focusable_without_indicator: [],
    };

    // Check for images without alt text
    document.querySelectorAll('img').forEach(img => {
        const altAttr = img.getAttribute('alt');
        const role = img.getAttribute('role');
        // alt="" is intentional for decorative images; role="presentation"/"none" also marks decorative
        const isDecorative = altAttr === '' || role === 'presentation' || role === 'none';
        if (altAttr === null && !isDecorative && img.offsetWidth > 1) {
            const testid = img.getAttribute('data-testid');
            // Safely build selector — avoid injecting raw src into CSS selector
            let selector;
            if (testid) {
                selector = `[data-testid="${testid.replace(/"/g, '\\"')}"]`;
            } else {
                const filename = (img.src || '').split('/').pop().substring(0, 30).replace(/["\]\\]/g, '');
                selector = filename ? `img[src*="${filename}"]` : 'img';
            }
            results.missing_alt_text.push({ src: img.src ? img.src.substring(0, 100) : '', selector });
        }
    });

    // Check for small tap targets
    const minTap = __MIN_TAP_TARGET__;
    document.querySelectorAll('button, a, input, select, [role="button"], [role="link"]').forEach(el => {
        const rect = el.getBoundingClientRect();
        if (rect.width > 0 && rect.height > 0 &&
            (rect.width < minTap || rect.height < minTap) &&
            el.offsetParent !== null) {
            results.small_tap_targets.push({
                tag: el.tagName.toLowerCase(),
                text: (el.textContent || '').trim().substring(0, 50),
                width: Math.round(rect.width),
                height: Math.round(rect.height),
                selector: el.getAttribute('data-testid')
                    ? `[data-testid="${el.getAttribute('data-testid')}"]`
                    : '',
            });
        }
    });

    // Check for empty/dead links
    document.querySelectorAll('a').forEach(a => {
        const href = a.getAttribute('href');
        if (!href || href === '#' || href === 'javascript:void(0)' || href === 'javascript:;') {
            const text = (a.textContent || '').trim();
            if (text) {
                results.empty_links.push({
                    text: text.substring(0, 50),
                    href: href || '(none)',
                });
            }
        }
    });

    // Check heading hierarchy
    const headings = [];
    document.querySelectorAll('h1, h2, h3, h4, h5, h6').forEach(h => {
        headings.push({
            level: parseInt(h.tagName[1]),
            text: (h.textContent || '').trim().substring(0, 60),
        });
    });
    results.heading_hierarchy = headings;
    results.page_has_h1 = headings.some(h => h.level === 1);

    // Check for inputs without associated labels
    document.querySelectorAll('input, select, textarea').forEach(input => {
        if (input.type === 'hidden' || input.type === 'submit' || input.type === 'button') return;
        const hasLabel = input.labels && input.labels.length > 0;
        const hasAriaLabel = input.getAttribute('aria-label');
        const hasAriaLabelledby = input.getAttribute('aria-labelledby');
        const hasPlaceholder = input.getAttribute('placeholder');
        const hasTitle = input.getAttribute('title');

        if (!hasLabel && !hasAriaLabel && !hasAriaLabelledby) {
            results.inputs_without_labels.push({
                tag: input.tagName.toLowerCase(),
                type: input.type || '',
                name: input.name || '',
                placeholder: hasPlaceholder || '',
                has_placeholder: !!hasPlaceholder,
                has_title: !!hasTitle,
                selector: input.getAttribute('data-testid')
                    ? `[data-testid="${input.getAttribute('data-testid')}"]`
                    : '',
            });
        }
    });

    // Check for forms without visible submit buttons
    document.querySelectorAll('form').forEach(form => {
        const hasSubmit = form.querySelector(
            'button[type="submit"], input[type="submit"], button:not([type])'
        );
        if (!hasSubmit) {
            results.orphan_forms.push({
                action: form.action || '',
                fields: form.querySelectorAll('input, select, textarea').length,
            });
        }
    });

    // Check for ARIA landmarks
    const landmarks = document.querySelectorAll(
        'main, nav, header, footer, aside, [role="main"], [role="navigation"], [role="banner"], [role="contentinfo"]'
    );
    results.missing_landmarks = landmarks.length === 0;

    return results;
}"""


class UXEvaluator:
    """
    Evaluates web pages against UX heuristics.

    Two-layer approach:
      1. DOM-based rules (free): tap target size, alt text, labels, heading hierarchy
      2. VLM visual analysis (targeted): layout quality, readability, visual hierarchy
    """

    def __init__(
        self,
        db:            LocatorDB,
        vision_client  = None,
    ):
        self._db     = db
        self._vision = vision_client

    # ── Live audit (browser) ─────────────────────────────────────────

    async def audit_url(
        self,
        page,              # Playwright Page object
        url:  str = "",
        screenshot_bytes: Optional[bytes] = None,
    ) -> list[UXFinding]:
        """
        Run full UX audit on a live Playwright page.
        Returns list of UXFinding objects.
        """
        url = url or _normalize_url(page.url)
        findings: list[UXFinding] = []

        # Layer 1: DOM-based rule checks
        try:
            js = _UX_AUDIT_JS.replace("__MIN_TAP_TARGET__", str(int(MIN_TAP_TARGET)))
            dom_results = await page.evaluate(js)
            findings.extend(self._evaluate_dom_results(dom_results, url))
        except Exception as e:
            findings.append(UXFinding(
                heuristic="N1_VISIBILITY", severity="low", category="errors",
                description=f"DOM audit script failed: {e}", url=url, source="rule",
            ))

        # Layer 2: Vision-based analysis (if available)
        if self._vision and screenshot_bytes:
            vision_findings = await self._vision_audit(screenshot_bytes, url)
            findings.extend(vision_findings)

        return findings

    # ── Static audit (DB only, no browser) ───────────────────────────

    def audit_static(self, url: str) -> list[UXFinding]:
        """
        Audit a page using only data already in the locator DB.
        No browser, no AI — just rule-based checks on stored locators.
        """
        url      = _normalize_url(url)
        findings = []
        locators = self._db.get_all(url, valid_only=True)

        if not locators:
            findings.append(UXFinding(
                heuristic="N1_VISIBILITY", severity="low", category="errors",
                description="No locators found in DB — page has not been crawled",
                url=url, source="rule",
            ))
            return findings

        # Check for buttons without accessible names
        buttons = [l for l in locators if (l.get("identity") or {}).get("role") == "button"]
        for b in buttons:
            name = (b.get("identity") or {}).get("name", "")
            if not name or name.strip() == "":
                findings.append(UXFinding(
                    heuristic="WCAG_LABELS", severity="medium",
                    category="accessibility",
                    description="Button without accessible name",
                    url=url, element="button", source="rule",
                    wcag_criterion="4.1.2",
                ))

        # Check for form inputs without labels (from DB)
        textboxes = [l for l in locators if (l.get("identity") or {}).get("role") in ("textbox", "searchbox", "combobox")]
        for t in textboxes:
            name = (t.get("identity") or {}).get("name", "")
            if not name or name.strip() == "":
                findings.append(UXFinding(
                    heuristic="WCAG_LABELS", severity="high",
                    category="accessibility",
                    description="Form input without label or accessible name",
                    url=url, element="textbox", source="rule",
                    wcag_criterion="1.3.1",
                ))

        # Check for links without text
        links = [l for l in locators if (l.get("identity") or {}).get("role") == "link"]
        for lnk in links:
            name = (lnk.get("identity") or {}).get("name", "")
            if not name or name.strip() == "":
                findings.append(UXFinding(
                    heuristic="UX_DEAD_LINK", severity="medium",
                    category="navigation",
                    description="Link without visible text or accessible name",
                    url=url, element="link", source="rule",
                ))

        return findings

    # ── DOM result evaluation ────────────────────────────────────────

    def _evaluate_dom_results(self, data: dict, url: str) -> list[UXFinding]:
        """Convert raw DOM audit results into structured UX findings."""
        findings = []

        # Missing alt text
        for img in data.get("missing_alt_text", []):
            findings.append(UXFinding(
                heuristic="WCAG_ALT_TEXT", severity="medium",
                category="accessibility",
                description=f"Image missing alt text: {img.get('src', '')[:60]}",
                url=url, selector=img.get("selector", ""), source="rule",
                wcag_criterion="1.1.1",
            ))

        # Small tap targets
        for el in data.get("small_tap_targets", [])[:10]:  # cap output
            w, h = el.get("width", 0), el.get("height", 0)
            if w < MIN_TAP_TARGET or h < MIN_TAP_TARGET:
                findings.append(UXFinding(
                    heuristic="WCAG_TAP_TARGET", severity="medium",
                    category="accessibility",
                    description=f"Tap target too small: {el.get('tag', '?')} "
                                f"\"{el.get('text', '')[:30]}\" is {w}x{h}px "
                                f"(minimum: {MIN_TAP_TARGET}x{MIN_TAP_TARGET}px)",
                    url=url, selector=el.get("selector", ""), source="rule",
                    wcag_criterion="2.5.5",
                ))

        # Dead/empty links
        for lnk in data.get("empty_links", []):
            findings.append(UXFinding(
                heuristic="UX_DEAD_LINK", severity="low",
                category="navigation",
                description=f"Dead link: \"{lnk.get('text', '')[:40]}\" → {lnk.get('href', '')}",
                url=url, source="rule",
            ))

        # Heading hierarchy
        headings = data.get("heading_hierarchy", [])
        if headings:
            if not data.get("page_has_h1"):
                findings.append(UXFinding(
                    heuristic="WCAG_HEADING", severity="medium",
                    category="accessibility",
                    description="Page missing H1 heading",
                    url=url, source="rule",
                    wcag_criterion="1.3.1",
                ))
            # Check for skipped levels (e.g. H1 → H3 with no H2)
            for i in range(1, len(headings)):
                prev_level = headings[i - 1]["level"]
                curr_level = headings[i]["level"]
                if curr_level > prev_level + 1:
                    findings.append(UXFinding(
                        heuristic="WCAG_HEADING", severity="low",
                        category="accessibility",
                        description=f"Heading level skipped: H{prev_level} → H{curr_level} "
                                    f"(\"{headings[i]['text'][:30]}\")",
                        url=url, source="rule",
                        wcag_criterion="1.3.1",
                    ))

        # Inputs without labels
        for inp in data.get("inputs_without_labels", []):
            sev = "low" if inp.get("has_placeholder") else "high"
            findings.append(UXFinding(
                heuristic="WCAG_LABELS", severity=sev,
                category="forms",
                description=f"Input without label: <{inp.get('tag', '?')}> "
                            f"type={inp.get('type', '?')} name=\"{inp.get('name', '')}\"" +
                            (f" (has placeholder: \"{inp.get('placeholder', '')}\")" if inp.get("has_placeholder") else ""),
                url=url, selector=inp.get("selector", ""), source="rule",
                wcag_criterion="1.3.1",
            ))

        # Orphan forms
        for form in data.get("orphan_forms", []):
            findings.append(UXFinding(
                heuristic="UX_ORPHAN_FORM", severity="medium",
                category="forms",
                description=f"Form without visible submit button ({form.get('fields', 0)} fields)",
                url=url, source="rule",
            ))

        # Missing landmarks
        if data.get("missing_landmarks"):
            findings.append(UXFinding(
                heuristic="WCAG_LABELS", severity="low",
                category="accessibility",
                description="Page has no ARIA landmarks (no <main>, <nav>, <header>, <footer>)",
                url=url, source="rule",
                wcag_criterion="1.3.1",
            ))

        return findings

    # ── Vision-based evaluation ──────────────────────────────────────

    _VISION_UX_PROMPT = """\
Examine this web page screenshot and identify UX issues.

Evaluate against these specific criteria:
1. LAYOUT: Are elements properly aligned? Any overlapping content? Broken grid?
2. READABILITY: Is text large enough? Sufficient contrast? Any truncated text?
3. VISUAL HIERARCHY: Is the primary action clear? Are there competing CTAs?
4. CONSISTENCY: Consistent spacing, fonts, icon styles?
5. NAVIGATION: Clear where to click? Breadcrumbs present if needed?
6. WHITESPACE: Too cramped or too sparse?
7. ERROR STATES: If errors are shown, are they clear and actionable?

Respond with ONLY valid JSON:
{
    "findings": [
        {
            "severity": "high" | "medium" | "low",
            "category": "layout" | "readability" | "hierarchy" | "consistency" | "navigation" | "whitespace" | "errors",
            "heuristic": "N8_AESTHETIC" | "N4_CONSISTENCY" | "N1_VISIBILITY" | "N2_MATCH" | "N6_RECOGNITION",
            "description": "specific, actionable description",
            "location": "where on the page"
        }
    ]
}"""

    async def _vision_audit(self, screenshot_bytes: bytes, url: str) -> list[UXFinding]:
        """Use VLM to evaluate visual/layout aspects of the page."""
        try:
            raw = await self._vision.aanalyze_screenshot(
                screenshot_bytes,
                self._VISION_UX_PROMPT,
                max_tokens=2048,
            )
            data = json.loads(_extract_json(raw))
            valid_severities = {"high", "medium", "low"}
            findings = []
            for f in data.get("findings", []):
                severity = f.get("severity", "low")
                if severity not in valid_severities:
                    severity = "low"
                heuristic = f.get("heuristic", "N8_AESTHETIC")
                if heuristic not in HEURISTICS:
                    heuristic = "N8_AESTHETIC"
                findings.append(UXFinding(
                    heuristic   = heuristic,
                    severity    = severity,
                    category    = f.get("category", "layout"),
                    description = f.get("description", ""),
                    url         = url,
                    location    = f.get("location", ""),
                    source      = "vision",
                ))
            return findings
        except Exception as e:
            return [UXFinding(
                heuristic="N1_VISIBILITY", severity="low", category="errors",
                description=f"Vision audit failed: {e}", url=url, source="vision",
            )]

    # ── Scoring ──────────────────────────────────────────────────────

    @staticmethod
    def compute_score(findings: list[UXFinding]) -> float:
        """Compute a 0-100 UX score. Deductions based on severity."""
        score = 100.0
        deductions = {"high": 8.0, "medium": 3.0, "low": 1.0}
        for f in findings:
            score -= deductions.get(f.severity, 1.0)
        return max(0.0, round(score, 1))


# ── Helpers ──────────────────────────────────────────────────────────

def _extract_json(text: str) -> str:
    """Extract JSON object from text that may contain markdown fences."""
    text = text.strip()
    if text.startswith("```"):
        lines = text.split("\n")
        lines = [l for l in lines if not l.strip().startswith("```")]
        text  = "\n".join(lines)
    # Find the first '{' and match its closing '}' by tracking brace depth
    start = text.find("{")
    if start == -1:
        return text
    depth = 0
    in_string = False
    escape = False
    for i in range(start, len(text)):
        c = text[i]
        if escape:
            escape = False
            continue
        if c == '\\' and in_string:
            escape = True
            continue
        if c == '"' and not escape:
            in_string = not in_string
            continue
        if in_string:
            continue
        if c == '{':
            depth += 1
        elif c == '}':
            depth -= 1
            if depth == 0:
                return text[start:i + 1]
    # Fallback: use first { to last }
    end = text.rfind("}")
    if end != -1:
        return text[start:end + 1]
    return text
