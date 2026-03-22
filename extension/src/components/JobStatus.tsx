/**
 * JobStatus — shows current job progress and result.
 */

import React from "react";
import type { Job } from "../types";

interface JobStatusProps {
  job: Job;
}

export function JobStatus({ job }: JobStatusProps) {
  return (
    <div style={styles.container}>
      <div style={styles.header}>
        <span style={styles.url} title={job.url}>
          {new URL(job.url).hostname}
        </span>
        <span style={{ ...styles.badge, ...badgeColor(job.state) }}>
          {job.state}
        </span>
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

      {/* Error */}
      {job.state === "failed" && job.error && (
        <p style={styles.error}>{job.error}</p>
      )}

      {/* Report summary */}
      {job.state === "complete" && job.report && (
        <div style={styles.report}>
          <div style={styles.scoreRow}>
            <span style={styles.scoreLabel}>Score</span>
            <span style={styles.scoreValue}>{job.report.score}/100</span>
          </div>
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
              {job.report.issues.slice(0, 5).map((issue) => (
                <div key={issue.id} style={styles.issue}>
                  <span style={styles.issueSeverity}>{issue.severity}</span>
                  <span style={styles.issueMessage}>{issue.message}</span>
                </div>
              ))}
              {job.report.issues.length > 5 && (
                <p style={styles.moreIssues}>
                  +{job.report.issues.length - 5} more issues
                </p>
              )}
            </div>
          )}
        </div>
      )}
    </div>
  );
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
    maxWidth: 220,
  },
  badge: {
    padding: "2px 8px",
    borderRadius: 12,
    fontSize: 12,
    fontWeight: 500,
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
  error: { fontSize: 13, color: "#dc2626" },
  report: { marginTop: 8 },
  scoreRow: {
    display: "flex",
    justifyContent: "space-between",
    marginBottom: 8,
  },
  scoreLabel: { fontSize: 13, color: "#6b7280" },
  scoreValue: { fontSize: 18, fontWeight: 700, color: "#1a1a1a" },
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
    marginBottom: 4,
  },
  issueSeverity: {
    fontSize: 11,
    fontWeight: 600,
    textTransform: "uppercase",
    color: "#6b7280",
    minWidth: 50,
  },
  issueMessage: { fontSize: 13, color: "#374151" },
  moreIssues: { fontSize: 12, color: "#6b7280", marginTop: 4 },
};
