/**
 * JobForm — dual-mode scan form.
 *
 * Quick Scan: scans the current tab instantly (free, no login needed).
 * Deep Scan:  submits URL to backend for full Playwright analysis (premium).
 */

import React, { useState } from "react";
import type { ScanTier } from "../types";

interface JobFormProps {
  onDeepScan: (url: string) => void;
  onQuickScan: () => void;
  disabled?: boolean;
  quotaRemaining?: number;
  isAuthenticated: boolean;
  scanning?: boolean;
}

export function JobForm({
  onDeepScan,
  onQuickScan,
  disabled,
  quotaRemaining,
  isAuthenticated,
  scanning,
}: JobFormProps) {
  const [tier, setTier] = useState<ScanTier>("quick");
  const [url, setUrl] = useState("");
  const [error, setError] = useState("");

  const handleDeepSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    setError("");

    const trimmed = url.trim();
    if (!trimmed) {
      setError("Please enter a URL");
      return;
    }
    if (!trimmed.startsWith("http://") && !trimmed.startsWith("https://")) {
      setError("URL must start with http:// or https://");
      return;
    }

    onDeepScan(trimmed);
    setUrl("");
  };

  const handleQuickScan = () => {
    setError("");
    onQuickScan();
  };

  const deepDisabled =
    disabled ||
    scanning ||
    !isAuthenticated ||
    (quotaRemaining !== undefined && quotaRemaining <= 0);

  return (
    <div style={styles.container}>
      {/* Tier toggle */}
      <div style={styles.toggle}>
        <button
          style={{
            ...styles.toggleBtn,
            ...(tier === "quick" ? styles.toggleActive : {}),
          }}
          onClick={() => setTier("quick")}
          aria-pressed={tier === "quick"}
        >
          Quick Scan
        </button>
        <button
          style={{
            ...styles.toggleBtn,
            ...(tier === "deep" ? styles.toggleActiveDeep : {}),
          }}
          onClick={() => setTier("deep")}
          aria-pressed={tier === "deep"}
        >
          Deep Scan
        </button>
      </div>

      {/* Quick Scan mode */}
      {tier === "quick" && (
        <div style={styles.modeContent}>
          <p style={styles.modeDesc}>
            Scans the current tab for accessibility, SEO, and quality issues.
          </p>
          <button
            onClick={handleQuickScan}
            style={{
              ...styles.primaryButton,
              ...(scanning ? styles.buttonDisabled : {}),
            }}
            disabled={scanning || disabled}
          >
            {scanning ? "Scanning..." : "Scan This Page"}
          </button>
          <p style={styles.freeLabel}>Free &middot; Instant &middot; No login required</p>
        </div>
      )}

      {/* Deep Scan mode */}
      {tier === "deep" && (
        <div style={styles.modeContent}>
          {!isAuthenticated ? (
            <p style={styles.lockMessage}>
              Sign in to unlock Deep Scan — multi-page crawling, test execution, and full reports.
            </p>
          ) : (
            <>
              <p style={styles.modeDesc}>
                Full multi-page crawl with Playwright engine. Tests flows, validates behavior.
              </p>
              <form onSubmit={handleDeepSubmit}>
                <div style={styles.inputRow}>
                  <input
                    type="text"
                    value={url}
                    onChange={(e) => setUrl(e.target.value)}
                    placeholder="https://example.com"
                    style={styles.input}
                    disabled={deepDisabled}
                    aria-label="Site URL"
                  />
                  <button
                    type="submit"
                    style={{
                      ...styles.deepButton,
                      ...(deepDisabled ? styles.buttonDisabled : {}),
                    }}
                    disabled={deepDisabled}
                  >
                    {scanning ? "Scanning..." : "Scan"}
                  </button>
                </div>
              </form>
              {quotaRemaining !== undefined && quotaRemaining <= 0 && (
                <div style={styles.quotaBox}>
                  <p style={styles.error}>Monthly scan quota exceeded</p>
                  <a href="#" style={styles.upgradeLink} onClick={(e) => e.preventDefault()}>
                    Upgrade to Starter (50/mo) &rarr;
                  </a>
                </div>
              )}
            </>
          )}
        </div>
      )}

      {error && <p style={styles.error}>{error}</p>}
    </div>
  );
}

const styles: Record<string, React.CSSProperties> = {
  container: { marginBottom: 16 },
  toggle: {
    display: "flex",
    background: "#f3f4f6",
    borderRadius: 8,
    padding: 3,
    marginBottom: 12,
  },
  toggleBtn: {
    flex: 1,
    padding: "6px 12px",
    border: "none",
    borderRadius: 6,
    fontSize: 13,
    fontWeight: 500,
    cursor: "pointer",
    background: "transparent",
    color: "#6b7280",
    transition: "all 0.15s ease",
  },
  toggleActive: {
    background: "#2563eb",
    color: "#fff",
    fontWeight: 600,
  },
  toggleActiveDeep: {
    background: "#7c3aed",
    color: "#fff",
    fontWeight: 600,
  },
  modeContent: {
    display: "flex",
    flexDirection: "column",
    gap: 8,
  },
  modeDesc: {
    fontSize: 12,
    color: "#6b7280",
    margin: 0,
  },
  primaryButton: {
    padding: "10px 16px",
    background: "#2563eb",
    color: "#fff",
    border: "none",
    borderRadius: 6,
    fontSize: 14,
    fontWeight: 600,
    cursor: "pointer",
    width: "100%",
  },
  deepButton: {
    padding: "8px 16px",
    background: "#7c3aed",
    color: "#fff",
    border: "none",
    borderRadius: 6,
    fontSize: 14,
    fontWeight: 600,
    cursor: "pointer",
    whiteSpace: "nowrap",
  },
  buttonDisabled: {
    background: "#94a3b8",
    cursor: "not-allowed",
  },
  inputRow: { display: "flex", gap: 8 },
  input: {
    flex: 1,
    padding: "8px 12px",
    border: "1px solid #ddd",
    borderRadius: 6,
    fontSize: 14,
    outline: "none",
  },
  error: {
    color: "#dc2626",
    fontSize: 12,
    marginTop: 4,
    margin: 0,
  },
  freeLabel: {
    fontSize: 11,
    color: "#9ca3af",
    textAlign: "center",
    margin: 0,
  },
  lockMessage: {
    fontSize: 13,
    color: "#6b7280",
    textAlign: "center",
    padding: "12px 0",
    margin: 0,
  },
  quotaBox: {
    padding: 8,
    background: "#fef2f2",
    borderRadius: 6,
    border: "1px solid #fee2e2",
    marginTop: 8,
  },
  upgradeLink: {
    fontSize: 11,
    color: "#7c3aed",
    textDecoration: "none",
    fontWeight: 600,
  },
};
