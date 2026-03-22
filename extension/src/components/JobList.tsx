/**
 * JobList — shows past scan jobs with status.
 */

import React from "react";
import type { Job } from "../types";

interface JobListProps {
  jobs: Job[];
  onSelect: (jobId: string) => void;
}

export function JobList({ jobs, onSelect }: JobListProps) {
  if (jobs.length === 0) {
    return <p style={styles.empty}>No scans yet. Start your first scan above.</p>;
  }

  return (
    <div>
      <h3 style={styles.heading}>Recent Scans</h3>
      {jobs.map((job) => (
        <button
          key={job.id}
          onClick={() => onSelect(job.id)}
          style={styles.row}
          aria-label={`View scan for ${job.url}`}
        >
          <div style={styles.rowLeft}>
            <span style={styles.hostname}>
              {tryHostname(job.url)}
            </span>
            <span style={styles.date}>
              {job.created_at ? formatDate(job.created_at) : ""}
            </span>
          </div>
          <span style={{ ...styles.state, ...stateColor(job.state) }}>
            {job.state}
          </span>
        </button>
      ))}
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
  rowLeft: { display: "flex", flexDirection: "column", gap: 2 },
  hostname: { fontWeight: 500, color: "#1a1a1a" },
  date: { fontSize: 11, color: "#9ca3af" },
  state: { fontSize: 12, fontWeight: 500 },
};
