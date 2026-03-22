/**
 * Types for the client-side Quick Scan content script.
 */

export interface RawIssue {
  ruleId: string;        // e.g. "a11y/img-alt", "seo/meta-description"
  severity: "critical" | "major" | "medium" | "minor";
  category: string;      // "accessibility" | "seo" | "forms" | "links" | "performance"
  title: string;
  description: string;
  selector?: string;     // CSS selector to the offending element
  element?: string;      // outerHTML snippet (truncated to 120 chars)
}

export interface ScanResult {
  issues: RawIssue[];
  pageUrl: string;
  pageTitle: string;
  duration_ms: number;
  checksRun: number;
}
