/**
 * Link quality checks — validates href values, link text, security.
 *
 * Rules:
 *   links/broken-href   — Empty or invalid href attributes
 *   links/generic-text  — Generic link text ("click here", "read more")
 *   links/noopener      — External links missing rel="noopener"
 */

import type { RawIssue } from "../types";
import { getSelector, snippetHTML } from "../utils";

const CATEGORY = "links";

const GENERIC_LINK_TEXT = new Set([
  "click here",
  "here",
  "read more",
  "more",
  "learn more",
  "link",
  "this",
  "go",
  "see more",
  "details",
]);

export function checkBrokenHrefs(): RawIssue[] {
  const issues: RawIssue[] = [];
  const links = document.querySelectorAll("a");

  for (const a of links) {
    const href = a.getAttribute("href");

    // No href at all
    if (href === null || href === undefined) {
      // Skip anchors without href that have role="button" etc.
      if (!a.getAttribute("role")) {
        issues.push({
          ruleId: "links/broken-href",
          severity: "major",
          category: CATEGORY,
          title: "Link missing href attribute",
          description: "Anchor element has no href attribute. This makes it inaccessible to keyboard users and assistive technology.",
          selector: getSelector(a),
          element: snippetHTML(a),
        });
      }
      continue;
    }

    const trimmed = href.trim();

    // Empty href
    if (trimmed === "" || trimmed === "#") {
      // Skip if it looks like an SPA navigation handler
      if (a.getAttribute("onclick") || a.getAttribute("role") === "button") continue;

      issues.push({
        ruleId: "links/broken-href",
        severity: "major",
        category: CATEGORY,
        title: "Link has empty or # href",
        description: `Link has href="${trimmed}" which navigates nowhere. Use a <button> for interactive actions or provide a real URL.`,
        selector: getSelector(a),
        element: snippetHTML(a),
      });
      continue;
    }

    // javascript: void links
    if (trimmed.startsWith("javascript:")) {
      issues.push({
        ruleId: "links/broken-href",
        severity: "major",
        category: CATEGORY,
        title: "Link uses javascript: href",
        description: "Link uses javascript: protocol in href. This is a bad practice. Use a <button> with an event handler instead.",
        selector: getSelector(a),
        element: snippetHTML(a),
      });
    }
  }

  return issues;
}

export function checkGenericLinkText(): RawIssue[] {
  const issues: RawIssue[] = [];
  const links = document.querySelectorAll("a[href]");

  for (const a of links) {
    // Get accessible name: text content or aria-label
    const ariaLabel = a.getAttribute("aria-label");
    const text = (ariaLabel || a.textContent || "").trim().toLowerCase();

    if (!text) continue;

    if (GENERIC_LINK_TEXT.has(text)) {
      issues.push({
        ruleId: "links/generic-text",
        severity: "medium",
        category: CATEGORY,
        title: `Generic link text: "${text}"`,
        description: `Link text "${text}" is not descriptive. Screen reader users navigate by links and need meaningful text to understand where a link goes.`,
        selector: getSelector(a),
        element: snippetHTML(a),
      });
    }
  }

  return issues;
}

export function checkNoopener(): RawIssue[] {
  const issues: RawIssue[] = [];
  const externalLinks = document.querySelectorAll('a[target="_blank"]');

  for (const a of externalLinks) {
    const rel = (a.getAttribute("rel") || "").toLowerCase();
    const hasNoopener = rel.includes("noopener") || rel.includes("noreferrer");

    if (!hasNoopener) {
      issues.push({
        ruleId: "links/noopener",
        severity: "medium",
        category: CATEGORY,
        title: "External link missing rel=\"noopener\"",
        description: `Link with target="_blank" is missing rel="noopener noreferrer". This is a security risk as the opened page can access window.opener.`,
        selector: getSelector(a),
        element: snippetHTML(a),
      });
    }
  }

  return issues;
}

// ── Export all checks ──────────────────────────────────────────────────

export function runLinksChecks(): RawIssue[] {
  return [
    ...checkBrokenHrefs(),
    ...checkGenericLinkText(),
    ...checkNoopener(),
  ];
}
