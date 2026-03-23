/**
 * Quick Scan — client-side content script entry point.
 *
 * Injected via chrome.scripting.executeScript from the service worker.
 * Runs all check modules against the current page DOM and returns results.
 *
 * This file is bundled by Vite as a standalone script (no React, no extension APIs).
 */

import type { ScanResult } from "./types";
import { runA11yChecks } from "./checks/a11y";
import { runSeoChecks } from "./checks/seo";
import { runFormsChecks } from "./checks/forms";
import { runLinksChecks } from "./checks/links";
import { runPerformanceChecks } from "./checks/performance";

export async function runQapalScan(): Promise<ScanResult> {
  const start = performance.now();

  const issues = [
    ...runA11yChecks(),
    ...runSeoChecks(),
    ...runFormsChecks(),
    ...runLinksChecks(),
    ...runPerformanceChecks(),
  ];

  const duration_ms = Math.round(performance.now() - start);

  const result: ScanResult = {
    issues,
    pageUrl: location.href,
    pageTitle: document.title || "(untitled)",
    duration_ms,
    checksRun: 26,
  };

  return result;
}

// Expose to window for CLI / Playwright injection
(window as any).runQapalScan = runQapalScan;

// For extension usage (executeScript returns the last expression)
runQapalScan();
