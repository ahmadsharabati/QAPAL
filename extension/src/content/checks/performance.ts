/**
 * Performance checks — lightweight hints about page performance.
 *
 * Rules:
 *   perf/img-dimensions     — Images missing width/height (CLS)
 *   perf/img-oversized      — Images much larger than displayed
 *   perf/lazy-load          — Below-fold images missing lazy loading
 *   perf/render-blocking    — Render-blocking resources in <head>
 */

import type { RawIssue } from "../types";
import { getSelector, snippetHTML } from "../utils";

const CATEGORY = "performance";

export function checkImageDimensions(): RawIssue[] {
  const issues: RawIssue[] = [];
  const images = document.querySelectorAll("img");

  for (const img of images) {
    const hasWidth = img.hasAttribute("width") || img.style.width;
    const hasHeight = img.hasAttribute("height") || img.style.height;

    if (!hasWidth || !hasHeight) {
      // Skip tiny/invisible images (likely tracking pixels)
      if (img.naturalWidth <= 1 || img.naturalHeight <= 1) continue;
      // Skip SVGs
      if (img.src?.endsWith(".svg")) continue;

      issues.push({
        ruleId: "perf/img-dimensions",
        severity: "medium",
        category: CATEGORY,
        title: "Image missing explicit dimensions",
        description: "Image has no width/height attributes. This causes Cumulative Layout Shift (CLS) as the browser doesn't know the image size before loading.",
        selector: getSelector(img),
        element: snippetHTML(img),
      });
    }
  }

  return issues;
}

export function checkOversizedImages(): RawIssue[] {
  const issues: RawIssue[] = [];
  const images = document.querySelectorAll("img");

  for (const img of images) {
    // Skip images that haven't loaded
    if (!img.naturalWidth || !img.complete) continue;

    const displayWidth = img.clientWidth;
    const displayHeight = img.clientHeight;

    // Skip hidden images
    if (displayWidth === 0 || displayHeight === 0) continue;

    const widthRatio = img.naturalWidth / displayWidth;
    const heightRatio = img.naturalHeight / displayHeight;

    // Flag if natural size is more than 2x the display size
    if (widthRatio > 2 && heightRatio > 2) {
      const wastedKB = Math.round(
        (img.naturalWidth * img.naturalHeight - displayWidth * displayHeight) *
          3 / 1024 // rough estimate: 3 bytes per pixel
      );

      issues.push({
        ruleId: "perf/img-oversized",
        severity: "minor",
        category: CATEGORY,
        title: `Oversized image: ${img.naturalWidth}x${img.naturalHeight} displayed at ${displayWidth}x${displayHeight}`,
        description: `Image is ${widthRatio.toFixed(1)}x larger than displayed. Serving a properly sized image could save ~${wastedKB}KB.`,
        selector: getSelector(img),
        element: snippetHTML(img),
      });
    }
  }

  return issues;
}

export function checkLazyLoading(): RawIssue[] {
  const issues: RawIssue[] = [];
  const images = document.querySelectorAll("img");
  const viewportHeight = window.innerHeight;

  for (const img of images) {
    // Skip images already using lazy loading
    if (img.loading === "lazy") continue;

    // Skip tiny/invisible images
    if (img.naturalWidth <= 1) continue;

    // Check if image is below the fold
    const rect = img.getBoundingClientRect();
    if (rect.top > viewportHeight * 1.5) {
      issues.push({
        ruleId: "perf/lazy-load",
        severity: "minor",
        category: CATEGORY,
        title: "Below-fold image not lazy loaded",
        description: `Image is ${Math.round(rect.top)}px from top (well below the fold). Add loading="lazy" to defer loading until the user scrolls near it.`,
        selector: getSelector(img),
        element: snippetHTML(img),
      });
    }
  }

  return issues;
}

export function checkRenderBlocking(): RawIssue[] {
  const issues: RawIssue[] = [];

  // Check for render-blocking stylesheets
  const styles = document.querySelectorAll('head link[rel="stylesheet"]');
  // Only flag if there are many blocking stylesheets
  if (styles.length > 3) {
    issues.push({
      ruleId: "perf/render-blocking",
      severity: "medium",
      category: CATEGORY,
      title: `${styles.length} render-blocking stylesheets`,
      description: `Found ${styles.length} stylesheets in <head> that block rendering. Consider inlining critical CSS or using media attributes to reduce blocking.`,
    });
  }

  // Check for blocking scripts without async/defer
  const scripts = document.querySelectorAll("head script[src]");
  for (const script of scripts) {
    const el = script as HTMLScriptElement;
    if (!el.async && !el.defer && el.type !== "module") {
      issues.push({
        ruleId: "perf/render-blocking",
        severity: "medium",
        category: CATEGORY,
        title: "Render-blocking script in <head>",
        description: `Script "${el.src.split("/").pop()}" blocks rendering. Add async or defer attribute to load it non-blocking.`,
        selector: getSelector(el),
        element: snippetHTML(el),
      });
    }
  }

  return issues;
}

// ── Export all checks ──────────────────────────────────────────────────

export function runPerformanceChecks(): RawIssue[] {
  return [
    ...checkImageDimensions(),
    ...checkOversizedImages(),
    ...checkLazyLoading(),
    ...checkRenderBlocking(),
  ];
}
