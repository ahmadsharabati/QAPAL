/**
 * JobList — shows past scan jobs with status.
 * Merges Quick Scan (local) and Deep Scan (server) results.
 */

import React from "react";
import type { Job } from "../types";

interface JobListProps {
  jobs: Job[];
  onSelect: (jobId: string) => void;
}

export function JobList({ jobs, onSelect }: JobListProps) {
  if (jobs.length === 0) {
    return <p style={styles.empty}>No scans yet. Run a Quick Scan to get started.</p>;
  }

  return (
    <div>
      <h3 style={styles.heading}>Recent Scans</h3>
      {jobs.slice(0, 20).map((job) => {
        const isQuick = job.id.startsWith("qs-");
        return (
          <button
            key={job.id}
            onClick={() => onSelect(job.id)}
            style={styles.row}
            aria-label={`View scan for ${job.url}`}
          >
            <div style={styles.rowLeft}>
              <div style={styles.hostRow}>
                <span style={styles.hostname}>
                  {tryHostname(job.url)}
                </span>
                <span style={isQuick ? styles.quickLabel : styles.deepLabel}>
                  {isQuick ? "Quick" : "Deep"}
                </span>
              </div>
              <span style={styles.date}>
                {job.created_at ? formatDate(job.created_at) : ""}
              </span>
            </div>
            <div style={styles.rowRight}>
              {job.report && (
                <span style={styles.score}>{job.report.score}</span>
              )}
              <span style={{ ...styles.state, ...stateColor(job.state) }}>
                {job.state}
              </span>
            </div>
          </button>
        );
      })}
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

function formatDate(iso: string): string {
  try {
    const d = new Date(iso);
    return d.toLocaleDateString(undefined, { month: "short", day: "numeric" });
  } catch {
    return "";
  }
}

function stateColor(state: string): React.CSSProperties {
  switch (state) {
    case "complete": return { color: "#166534" };
    case "failed":   return { color: "#991b1b" };
    case "running":  return { color: "#1e40af" };
    default:         return { color: "#6b7280" };
  }
}

const styles: Record<string, React.CSSProperties> = {
  heading: { fontSize: 14, fontWeight: 600, marginBottom: 8, color: "#374151" },
  empty: { fontSize: 13, color: "#9ca3af", textAlign: "center", padding: 20 },
  row: {
    display: "flex",
    justifyContent: "space-between",
    alignItems: "center",
    width: "100%",
    padding: "8px 12px",
    background: "#fff",
    border: "1px solid #e5e7eb",
    borderRadius: 6,
    marginBottom: 4,
    cursor: "pointer",
    textAlign: "left",
    fontSize: 13,
  },
  rowLeft: { display: "flex", flexDirection: "column", gap: 2, flex: 1, minWidth: 0 },
  rowRight: { display: "flex", alignItems: "center", gap: 8, flexShrink: 0 },
  hostRow: { display: "flex", alignItems: "center", gap: 6 },
  hostname: {
    fontWeight: 500,
    color: "#1a1a1a",
    overflow: "hidden",
    textOverflow: "ellipsis",
    whiteSpace: "nowrap",
  },
  quickLabel: {
    fontSize: 10,
    fontWeight: 600,
    color: "#2563eb",
    background: "#dbeafe",
    padding: "1px 5px",
    borderRadius: 3,
    flexShrink: 0,
  },
  deepLabel: {
    fontSize: 10,
    fontWeight: 600,
    color: "#7c3aed",
    background: "#ede9fe",
    padding: "1px 5px",
    borderRadius: 3,
    flexShrink: 0,
  },
  date: { fontSize: 11, color: "#9ca3af" },
  score: {
    fontSize: 14,
    fontWeight: 700,
    color: "#374151",
  },
  state: { fontSize: 12, fontWeight: 500 },
};
