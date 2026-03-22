/**
 * Score calculator — converts raw issues into a 0-100 page health score.
 *
 * Scoring: start at 100, deduct by severity. Floor at 0.
 */

import type { RawIssue } from "./types";

const WEIGHTS: Record<string, number> = {
  critical: 15,
  major: 8,
  medium: 3,
  minor: 1,
};

export function calculateScore(issues: RawIssue[]): number {
  let deductions = 0;
  for (const issue of issues) {
    deductions += WEIGHTS[issue.severity] ?? 0;
  }
  return Math.max(0, 100 - deductions);
}

export function countBySeverity(issues: RawIssue[]) {
  const counts = { critical: 0, major: 0, medium: 0, minor: 0 };
  for (const issue of issues) {
    if (issue.severity in counts) {
      counts[issue.severity as keyof typeof counts]++;
    }
  }
  return counts;
}
