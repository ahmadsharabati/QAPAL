/**
 * SEO checks — validates meta tags, headings, structured data.
 *
 * Rules:
 *   seo/title           — Missing or too-long page title
 *   seo/meta-desc       — Missing meta description
 *   seo/h1              — Missing or multiple h1 elements
 *   seo/og-tags         — Missing Open Graph tags
 *   seo/canonical       — Missing canonical URL
 *   seo/structured-data — No JSON-LD structured data
 */

import type { RawIssue } from "../types";

const CATEGORY = "seo";

export function checkTitle(): RawIssue[] {
  const issues: RawIssue[] = [];
  const title = document.title?.trim();

  if (!title) {
    issues.push({
      ruleId: "seo/title",
      severity: "critical",
      category: CATEGORY,
      title: "Missing page title",
      description: "The page has no <title> element. Search engines use the title as the primary ranking signal and it appears in search results.",
    });
  } else if (title.length > 60) {
    issues.push({
      ruleId: "seo/title",
      severity: "minor",
      category: CATEGORY,
      title: `Page title too long (${title.length} chars)`,
      description: `Title is ${title.length} characters. Search engines typically display only the first 60 characters.`,
      selector: "title",
    });
  }

  return issues;
}

export function checkMetaDescription(): RawIssue[] {
  const meta = document.querySelector('meta[name="description"]');
  if (!meta) {
    return [
      {
        ruleId: "seo/meta-desc",
        severity: "major",
        category: CATEGORY,
        title: "Missing meta description",
        description: 'No <meta name="description"> found. Search engines display this in results. Add a 150-160 character description.',
      },
    ];
  }

  const content = meta.getAttribute("content")?.trim();
  if (!content) {
    return [
      {
        ruleId: "seo/meta-desc",
        severity: "major",
        category: CATEGORY,
        title: "Empty meta description",
        description: "Meta description exists but has no content. Add a meaningful 150-160 character description.",
        selector: 'meta[name="description"]',
      },
    ];
  }

  if (content.length > 160) {
    return [
      {
        ruleId: "seo/meta-desc",
        severity: "minor",
        category: CATEGORY,
        title: `Meta description too long (${content.length} chars)`,
        description: `Meta description is ${content.length} characters. Search engines typically display only the first 160 characters.`,
        selector: 'meta[name="description"]',
      },
    ];
  }

  return [];
}

export function checkH1(): RawIssue[] {
  const issues: RawIssue[] = [];
  const h1s = document.querySelectorAll("h1");

  if (h1s.length === 0) {
    issues.push({
      ruleId: "seo/h1",
      severity: "medium",
      category: CATEGORY,
      title: "Missing h1 heading",
      description: "No <h1> element found. Every page should have exactly one h1 that describes the main content.",
    });
  } else if (h1s.length > 1) {
    issues.push({
      ruleId: "seo/h1",
      severity: "medium",
      category: CATEGORY,
      title: `Multiple h1 headings (${h1s.length})`,
      description: `Found ${h1s.length} h1 elements. Best practice is exactly one h1 per page for clear content hierarchy.`,
    });
  }

  return issues;
}

export function checkOpenGraph(): RawIssue[] {
  const issues: RawIssue[] = [];
  const requiredOg = ["og:title", "og:description", "og:image"];

  for (const prop of requiredOg) {
    const meta = document.querySelector(
      `meta[property="${prop}"], meta[name="${prop}"]`
    );
    if (!meta || !meta.getAttribute("content")?.trim()) {
      issues.push({
        ruleId: "seo/og-tags",
        severity: "minor",
        category: CATEGORY,
        title: `Missing Open Graph tag: ${prop}`,
        description: `No ${prop} meta tag found. Social media platforms use Open Graph tags for rich link previews.`,
      });
    }
  }

  return issues;
}

export function checkCanonical(): RawIssue[] {
  const link = document.querySelector('link[rel="canonical"]');
  if (!link || !link.getAttribute("href")?.trim()) {
    return [
      {
        ruleId: "seo/canonical",
        severity: "medium",
        category: CATEGORY,
        title: "Missing canonical URL",
        description: 'No <link rel="canonical"> found. Canonical URLs prevent duplicate content issues in search engines.',
      },
    ];
  }
  return [];
}

export function checkStructuredData(): RawIssue[] {
  const scripts = document.querySelectorAll('script[type="application/ld+json"]');
  if (scripts.length === 0) {
    return [
      {
        ruleId: "seo/structured-data",
        severity: "minor",
        category: CATEGORY,
        title: "No structured data (JSON-LD)",
        description: "No JSON-LD structured data found. Structured data helps search engines understand page content and enables rich results.",
      },
    ];
  }
  return [];
}

// ── Export all checks ──────────────────────────────────────────────────

export function runSeoChecks(): RawIssue[] {
  return [
    ...checkTitle(),
    ...checkMetaDescription(),
    ...checkH1(),
    ...checkOpenGraph(),
    ...checkCanonical(),
    ...checkStructuredData(),
  ];
}
