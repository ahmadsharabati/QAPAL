/**
 * JobStatus — shows current job progress and result.
 * Works identically for both Quick Scan and Deep Scan results.
 *
 * Displays:
 * - Progress bar during scan
 * - Score + severity breakdown on completion
 * - AI narration (if available)
 * - Failure diagnostics (failure_stage, error details)
 * - Issue list (capped at 8 with overflow indicator)
 */

import React from "react";
import type { Job } from "../types";

interface JobStatusProps {
  job: Job;
}

export function JobStatus({ job }: JobStatusProps) {
  const isQuick = job.report?.engine_version?.startsWith("client-");

  return (
    <div style={styles.container}>
      <div style={styles.header}>
        <span style={styles.url} title={job.url}>
          {tryHostname(job.url)}
        </span>
        <div style={styles.badges}>
          {isQuick !== undefined && (
            <span style={isQuick ? styles.quickBadge : styles.deepBadge}>
              {isQuick ? "Quick" : "Deep"}
            </span>
          )}
          <span style={{ ...styles.badge, ...badgeColor(job.state) }}>
            {job.state}
          </span>
        </div>
      </div>

      {/* Progress bar */}
      {(job.state === "queued" || job.state === "running") && (
        <div style={styles.progressTrack}>
          <div
            style={{ ...styles.progressBar, width: `${job.progress}%` }}
            role="progressbar"
            aria-valuenow={job.progress}
            aria-valuemin={0}
            aria-valuemax={100}
          />
        </div>
      )}

      {/* Status message */}
      {job.message && (
        <p style={styles.message}>{job.message}</p>
      )}

      {/* Error with failure diagnostics */}
      {job.state === "failed" && (
        <div style={styles.errorSection}>
          {job.failure_stage && (
            <p style={styles.failureStage}>
              Failed during: <strong>{job.failure_stage}</strong>
            </p>
          )}
          {job.error && <p style={styles.error}>{job.error}</p>}
        </div>
      )}

      {/* Report summary */}
      {job.state === "complete" && job.report && (
        <div style={styles.report}>
          <div style={styles.scoreRow}>
            <span style={styles.scoreLabel}>Score</span>
            <span style={{
              ...styles.scoreValue,
              color: job.report.score >= 80 ? "#166534" : job.report.score >= 50 ? "#854d0e" : "#991b1b",
            }}>
              {job.report.score}/100
            </span>
          </div>

          {/* AI Narration */}
          {job.report.narration && (
            <div style={styles.narrationBox}>
              <p style={styles.narration}>{job.report.narration}</p>
            </div>
          )}

          <p style={styles.summary}>{job.report.summary}</p>
          <div style={styles.counts}>
            {job.report.critical_count > 0 && (
              <span style={{ ...styles.countBadge, background: "#fecaca", color: "#991b1b" }}>
                {job.report.critical_count} critical
              </span>
            )}
            {job.report.high_count > 0 && (
              <span style={{ ...styles.countBadge, background: "#fed7aa", color: "#9a3412" }}>
                {job.report.high_count} high
              </span>
            )}
            {job.report.medium_count > 0 && (
              <span style={{ ...styles.countBadge, background: "#fef08a", color: "#854d0e" }}>
                {job.report.medium_count} medium
              </span>
            )}
          </div>
          {job.report.issues.length > 0 && (
            <div style={styles.issues}>
              {job.report.issues.slice(0, 8).map((issue) => (
                <div key={issue.id} style={styles.issue}>
                  <span style={{
                    ...styles.issueSeverity,
                    color: severityColor(issue.severity),
                  }}>
                    {issue.severity}
                  </span>
                  <div style={styles.issueContent}>
                    <span style={styles.issueRule}>{issue.rule}</span>
                    <span style={styles.issueMessage}>{issue.message}</span>
                  </div>
                </div>
              ))}
              {job.report.issues.length > 8 && (
                <p style={styles.moreIssues}>
                  +{job.report.issues.length - 8} more issues
                </p>
              )}
            </div>
          )}
          {job.report.issues.length === 0 && (
            <p style={styles.noIssues}>No issues found! This page looks great.</p>
          )}

          {/* Stats footer */}
          <div style={styles.statsRow}>
            <span style={styles.stat}>{job.report.pages_crawled} pages</span>
            <span style={styles.statDivider}>&middot;</span>
            <span style={styles.stat}>{job.report.actions_taken} actions</span>
            <span style={styles.statDivider}>&middot;</span>
            <span style={styles.stat}>{formatDuration(job.report.duration_ms)}</span>
          </div>
          <p style={styles.meta}>
            {job.report.engine_version}
          </p>
        </div>
      )}

      {/* Partial report on failed jobs */}
      {job.state === "failed" && job.report && (
        <div style={styles.report}>
          <p style={styles.partialLabel}>Partial results (scan failed)</p>
          <div style={styles.scoreRow}>
            <span style={styles.scoreLabel}>Partial Score</span>
            <span style={{ ...styles.scoreValue, color: "#6b7280" }}>
              {job.report.score}/100
            </span>
          </div>
          {job.report.narration && (
            <div style={styles.narrationBox}>
              <p style={styles.narration}>{job.report.narration}</p>
            </div>
          )}
          {job.report.issues.length > 0 && (
            <p style={styles.summary}>
              Found {job.report.issues.length} issue(s) before failure
            </p>
          )}
        </div>
      )}
    </div>
  );
}

function tryHostname(url: string): string {
  try {
    return new URL(url).hostname;
  } catch {
    return url;
  }
}

function formatDuration(ms: number): string {
  if (ms < 1000) return `${ms}ms`;
  if (ms < 60000) return `${(ms / 1000).toFixed(1)}s`;
  return `${Math.floor(ms / 60000)}m ${Math.floor((ms % 60000) / 1000)}s`;
}

function badgeColor(state: string): React.CSSProperties {
  switch (state) {
    case "complete": return { background: "#dcfce7", color: "#166534" };
    case "failed":   return { background: "#fecaca", color: "#991b1b" };
    case "running":  return { background: "#dbeafe", color: "#1e40af" };
    case "queued":   return { background: "#f3f4f6", color: "#374151" };
    default:         return { background: "#f3f4f6", color: "#374151" };
  }
}

function severityColor(severity: string): string {
  switch (severity) {
    case "critical": return "#991b1b";
    case "high":     return "#9a3412";
    case "medium":   return "#854d0e";
    case "low":      return "#6b7280";
    default:         return "#6b7280";
  }
}

const styles: Record<string, React.CSSProperties> = {
  container: {
    padding: 12,
    background: "#fff",
    border: "1px solid #e5e7eb",
    borderRadius: 8,
    marginBottom: 12,
  },
  header: {
    display: "flex",
    justifyContent: "space-between",
    alignItems: "center",
    marginBottom: 8,
  },
  url: {
    fontSize: 14,
    fontWeight: 600,
    overflow: "hidden",
    textOverflow: "ellipsis",
    whiteSpace: "nowrap",
    maxWidth: 180,
  },
  badges: { display: "flex", gap: 6, alignItems: "center" },
  badge: {
    padding: "2px 8px",
    borderRadius: 12,
    fontSize: 12,
    fontWeight: 500,
  },
  quickBadge: {
    padding: "2px 6px",
    borderRadius: 3,
    fontSize: 10,
    fontWeight: 600,
    color: "#2563eb",
    background: "#dbeafe",
  },
  deepBadge: {
    padding: "2px 6px",
    borderRadius: 3,
    fontSize: 10,
    fontWeight: 600,
    color: "#7c3aed",
    background: "#ede9fe",
  },
  progressTrack: {
    height: 4,
    background: "#e5e7eb",
    borderRadius: 2,
    overflow: "hidden",
    marginBottom: 8,
  },
  progressBar: {
    height: "100%",
    background: "#2563eb",
    borderRadius: 2,
    transition: "width 0.3s ease",
  },
  message: { fontSize: 13, color: "#6b7280", marginBottom: 4 },
  errorSection: {
    background: "#fef2f2",
    border: "1px solid #fecaca",
    borderRadius: 6,
    padding: 10,
    marginBottom: 8,
  },
  failureStage: {
    fontSize: 12,
    color: "#991b1b",
    margin: "0 0 4px 0",
  },
  error: { fontSize: 13, color: "#dc2626", margin: 0 },
  report: { marginTop: 8 },
  scoreRow: {
    display: "flex",
    justifyContent: "space-between",
    marginBottom: 8,
  },
  scoreLabel: { fontSize: 13, color: "#6b7280" },
  scoreValue: { fontSize: 18, fontWeight: 700 },
  narrationBox: {
    background: "#f0f9ff",
    border: "1px solid #bae6fd",
    borderRadius: 6,
    padding: "8px 10px",
    marginBottom: 10,
  },
  narration: {
    fontSize: 13,
    color: "#0c4a6e",
    lineHeight: "1.4",
    margin: 0,
  },
  summary: { fontSize: 13, color: "#374151", marginBottom: 8 },
  counts: { display: "flex", gap: 6, flexWrap: "wrap", marginBottom: 8 },
  countBadge: {
    padding: "2px 8px",
    borderRadius: 10,
    fontSize: 11,
    fontWeight: 500,
  },
  issues: { borderTop: "1px solid #e5e7eb", paddingTop: 8 },
  issue: {
    display: "flex",
    gap: 8,
    alignItems: "baseline",
    marginBottom: 6,
  },
  issueSeverity: {
    fontSize: 10,
    fontWeight: 700,
    textTransform: "uppercase",
    minWidth: 50,
    flexShrink: 0,
  },
  issueContent: {
    display: "flex",
    flexDirection: "column",
    gap: 1,
    minWidth: 0,
  },
  issueRule: {
    fontSize: 11,
    color: "#6b7280",
    fontFamily: "monospace",
  },
  issueMessage: {
    fontSize: 12,
    color: "#374151",
    lineHeight: "1.3",
  },
  moreIssues: { fontSize: 12, color: "#6b7280", marginTop: 4 },
  noIssues: { fontSize: 13, color: "#166534", textAlign: "center", padding: 12 },
  statsRow: {
    display: "flex",
    alignItems: "center",
    gap: 6,
    marginTop: 8,
    paddingTop: 6,
    borderTop: "1px solid #f3f4f6",
  },
  stat: { fontSize: 12, color: "#6b7280" },
  statDivider: { fontSize: 12, color: "#d1d5db" },
  meta: {
    fontSize: 11,
    color: "#9ca3af",
    marginTop: 4,
  },
  partialLabel: {
    fontSize: 11,
    color: "#991b1b",
    fontWeight: 600,
    textTransform: "uppercase",
    marginBottom: 6,
  },
};
