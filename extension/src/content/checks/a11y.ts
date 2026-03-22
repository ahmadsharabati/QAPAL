/**
 * Accessibility checks — runs against the current page DOM.
 *
 * Rules:
 *   a11y/img-alt        — Images missing alt text
 *   a11y/form-label     — Form inputs without labels
 *   a11y/heading-order  — Skipped heading levels
 *   a11y/aria-valid     — Invalid ARIA roles
 *   a11y/contrast       — Low contrast text (simplified)
 *   a11y/focus          — Interactive elements not focusable
 *   a11y/landmark       — Missing landmark regions
 *   a11y/lang           — Missing html lang attribute
 */

import type { RawIssue } from "../types";
import { getSelector, snippetHTML } from "../utils";

const CATEGORY = "accessibility";

// Valid ARIA roles per WAI-ARIA 1.2
const VALID_ROLES = new Set([
  "alert", "alertdialog", "application", "article", "banner", "button",
  "cell", "checkbox", "columnheader", "combobox", "complementary",
  "contentinfo", "definition", "dialog", "directory", "document",
  "feed", "figure", "form", "grid", "gridcell", "group", "heading",
  "img", "link", "list", "listbox", "listitem", "log", "main",
  "marquee", "math", "menu", "menubar", "menuitem", "menuitemcheckbox",
  "menuitemradio", "meter", "navigation", "none", "note", "option",
  "presentation", "progressbar", "radio", "radiogroup", "region",
  "row", "rowgroup", "rowheader", "scrollbar", "search", "searchbox",
  "separator", "slider", "spinbutton", "status", "switch", "tab",
  "table", "tablist", "tabpanel", "term", "textbox", "timer",
  "toolbar", "tooltip", "tree", "treegrid", "treeitem",
]);

export function checkImagesAltText(): RawIssue[] {
  const issues: RawIssue[] = [];
  const images = document.querySelectorAll("img");

  for (const img of images) {
    // Skip decorative images (role="presentation" or role="none")
    const role = img.getAttribute("role");
    if (role === "presentation" || role === "none") continue;

    // Skip images with explicit empty alt (intentionally decorative)
    if (img.hasAttribute("alt") && img.alt === "" && !role) continue;

    if (!img.hasAttribute("alt")) {
      issues.push({
        ruleId: "a11y/img-alt",
        severity: "major",
        category: CATEGORY,
        title: "Image missing alt text",
        description: `Image has no alt attribute. Screen readers cannot describe this image.`,
        selector: getSelector(img),
        element: snippetHTML(img),
      });
    }
  }
  return issues;
}

export function checkFormLabels(): RawIssue[] {
  const issues: RawIssue[] = [];
  const inputs = document.querySelectorAll(
    "input:not([type=hidden]):not([type=submit]):not([type=button]):not([type=reset]):not([type=image]), select, textarea"
  );

  for (const input of inputs) {
    const el = input as HTMLInputElement;

    // Check for associated label
    const hasLabel =
      el.getAttribute("aria-label") ||
      el.getAttribute("aria-labelledby") ||
      el.getAttribute("title") ||
      el.placeholder ||
      (el.id && document.querySelector(`label[for="${CSS.escape(el.id)}"]`)) ||
      el.closest("label");

    if (!hasLabel) {
      issues.push({
        ruleId: "a11y/form-label",
        severity: "critical",
        category: CATEGORY,
        title: "Form input missing label",
        description: `Input element has no accessible label. Add an aria-label, aria-labelledby, or <label> element.`,
        selector: getSelector(el),
        element: snippetHTML(el),
      });
    }
  }
  return issues;
}

export function checkHeadingOrder(): RawIssue[] {
  const issues: RawIssue[] = [];
  const headings = document.querySelectorAll("h1, h2, h3, h4, h5, h6");

  let prevLevel = 0;
  for (const h of headings) {
    const level = parseInt(h.tagName[1], 10);
    if (prevLevel > 0 && level > prevLevel + 1) {
      issues.push({
        ruleId: "a11y/heading-order",
        severity: "medium",
        category: CATEGORY,
        title: `Heading level skipped: h${prevLevel} to h${level}`,
        description: `Heading jumps from h${prevLevel} to h${level}, skipping h${prevLevel + 1}. This confuses screen reader navigation.`,
        selector: getSelector(h),
        element: snippetHTML(h),
      });
    }
    prevLevel = level;
  }
  return issues;
}

export function checkAriaRoles(): RawIssue[] {
  const issues: RawIssue[] = [];
  const elements = document.querySelectorAll("[role]");

  for (const el of elements) {
    const role = el.getAttribute("role")!.trim().toLowerCase();
    if (!VALID_ROLES.has(role)) {
      issues.push({
        ruleId: "a11y/aria-valid",
        severity: "major",
        category: CATEGORY,
        title: `Invalid ARIA role: "${role}"`,
        description: `The role "${role}" is not a valid WAI-ARIA role. Screen readers may ignore or misinterpret this element.`,
        selector: getSelector(el),
        element: snippetHTML(el),
      });
    }
  }
  return issues;
}

export function checkContrast(): RawIssue[] {
  const issues: RawIssue[] = [];

  // Sample text elements (limit to avoid performance issues)
  const textElements = document.querySelectorAll(
    "p, span, a, li, td, th, label, h1, h2, h3, h4, h5, h6, button"
  );

  const checked = new Set<Element>();
  let count = 0;

  for (const el of textElements) {
    if (count >= 50) break; // cap for performance
    if (checked.has(el)) continue;
    if (!el.textContent?.trim()) continue;

    const style = getComputedStyle(el);
    if (style.display === "none" || style.visibility === "hidden") continue;

    const fg = parseColor(style.color);
    const bg = findBackgroundColor(el);

    if (!fg || !bg) continue;
    checked.add(el);
    count++;

    const ratio = contrastRatio(fg, bg);
    const fontSize = parseFloat(style.fontSize);
    const isBold = parseInt(style.fontWeight, 10) >= 700;
    const isLargeText = fontSize >= 24 || (fontSize >= 18.66 && isBold);

    const threshold = isLargeText ? 3.0 : 4.5; // WCAG AA

    if (ratio < threshold) {
      issues.push({
        ruleId: "a11y/contrast",
        severity: "major",
        category: CATEGORY,
        title: `Low contrast ratio: ${ratio.toFixed(1)}:1`,
        description: `Text contrast ratio is ${ratio.toFixed(1)}:1, below WCAG AA minimum of ${threshold}:1. Foreground: ${style.color}, background: ${rgbString(bg)}.`,
        selector: getSelector(el),
        element: snippetHTML(el),
      });
    }
  }
  return issues;
}

export function checkFocusableElements(): RawIssue[] {
  const issues: RawIssue[] = [];
  const interactiveSelectors = 'a[href], button, [onclick], [role="button"], [role="link"], [role="tab"]';
  const elements = document.querySelectorAll(interactiveSelectors);

  for (const el of elements) {
    const tabIndex = el.getAttribute("tabindex");
    if (tabIndex === "-1") {
      issues.push({
        ruleId: "a11y/focus",
        severity: "major",
        category: CATEGORY,
        title: "Interactive element not keyboard-accessible",
        description: `Element has tabindex="-1" making it unreachable by keyboard. Remove tabindex or set it to 0.`,
        selector: getSelector(el),
        element: snippetHTML(el),
      });
    }
  }
  return issues;
}

export function checkLandmarks(): RawIssue[] {
  const issues: RawIssue[] = [];

  if (!document.querySelector("main, [role=main]")) {
    issues.push({
      ruleId: "a11y/landmark",
      severity: "minor",
      category: CATEGORY,
      title: "Missing <main> landmark",
      description: "Page has no <main> element or role=\"main\". Landmarks help screen reader users navigate the page structure.",
    });
  }

  if (!document.querySelector("nav, [role=navigation]")) {
    issues.push({
      ruleId: "a11y/landmark",
      severity: "minor",
      category: CATEGORY,
      title: "Missing <nav> landmark",
      description: "Page has no <nav> element or role=\"navigation\". Navigation landmarks help users find site navigation.",
    });
  }

  return issues;
}

export function checkLangAttribute(): RawIssue[] {
  const lang = document.documentElement.getAttribute("lang");
  if (!lang || !lang.trim()) {
    return [
      {
        ruleId: "a11y/lang",
        severity: "medium",
        category: CATEGORY,
        title: "Missing html lang attribute",
        description:
          'The <html> element has no lang attribute. Add lang="en" (or the appropriate language) so screen readers use the correct pronunciation.',
        selector: "html",
      },
    ];
  }
  return [];
}

// ── Color helpers ──────────────────────────────────────────────────────

type RGB = [number, number, number];

function parseColor(colorStr: string): RGB | null {
  const m = colorStr.match(
    /rgba?\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)/
  );
  if (!m) return null;
  return [parseInt(m[1]), parseInt(m[2]), parseInt(m[3])];
}

function findBackgroundColor(el: Element): RGB | null {
  let current: Element | null = el;
  while (current) {
    const bg = getComputedStyle(current).backgroundColor;
    const parsed = parseColorWithAlpha(bg);
    if (parsed && parsed[3] > 0) {
      return [parsed[0], parsed[1], parsed[2]];
    }
    current = current.parentElement;
  }
  // Default to white
  return [255, 255, 255];
}

function parseColorWithAlpha(colorStr: string): [number, number, number, number] | null {
  const m = colorStr.match(
    /rgba?\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)(?:\s*,\s*([\d.]+))?\s*\)/
  );
  if (!m) return null;
  return [
    parseInt(m[1]),
    parseInt(m[2]),
    parseInt(m[3]),
    m[4] !== undefined ? parseFloat(m[4]) : 1,
  ];
}

function luminance(rgb: RGB): number {
  const [r, g, b] = rgb.map((c) => {
    const s = c / 255;
    return s <= 0.03928 ? s / 12.92 : Math.pow((s + 0.055) / 1.055, 2.4);
  });
  return 0.2126 * r + 0.7152 * g + 0.0722 * b;
}

function contrastRatio(fg: RGB, bg: RGB): number {
  const l1 = luminance(fg);
  const l2 = luminance(bg);
  const lighter = Math.max(l1, l2);
  const darker = Math.min(l1, l2);
  return (lighter + 0.05) / (darker + 0.05);
}

function rgbString(rgb: RGB): string {
  return `rgb(${rgb[0]}, ${rgb[1]}, ${rgb[2]})`;
}

// ── Export all checks ──────────────────────────────────────────────────

export function runA11yChecks(): RawIssue[] {
  return [
    ...checkImagesAltText(),
    ...checkFormLabels(),
    ...checkHeadingOrder(),
    ...checkAriaRoles(),
    ...checkContrast(),
    ...checkFocusableElements(),
    ...checkLandmarks(),
    ...checkLangAttribute(),
  ];
}
