/**
 * JobForm — URL input + submit button for starting a scan.
 */

import React, { useState } from "react";

interface JobFormProps {
  onSubmit: (url: string) => void;
  disabled?: boolean;
  quotaRemaining?: number;
}

export function JobForm({ onSubmit, disabled, quotaRemaining }: JobFormProps) {
  const [url, setUrl] = useState("");
  const [error, setError] = useState("");

  const handleSubmit = (e: React.FormEvent) => {
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

    onSubmit(trimmed);
    setUrl("");
  };

  const isDisabled = disabled || (quotaRemaining !== undefined && quotaRemaining <= 0);

  return (
    <form onSubmit={handleSubmit} style={styles.form}>
      <div style={styles.inputRow}>
        <input
          type="text"
          value={url}
          onChange={(e) => setUrl(e.target.value)}
          placeholder="https://example.com"
          style={styles.input}
          disabled={isDisabled}
          aria-label="Site URL"
        />
        <button
          type="submit"
          style={{
            ...styles.button,
            ...(isDisabled ? styles.buttonDisabled : {}),
          }}
          disabled={isDisabled}
        >
          Scan
        </button>
      </div>
      {error && <p style={styles.error}>{error}</p>}
      {quotaRemaining !== undefined && quotaRemaining <= 0 && (
        <p style={styles.error}>Monthly scan quota exceeded</p>
      )}
    </form>
  );
}

const styles: Record<string, React.CSSProperties> = {
  form: { marginBottom: 16 },
  inputRow: { display: "flex", gap: 8 },
  input: {
    flex: 1,
    padding: "8px 12px",
    border: "1px solid #ddd",
    borderRadius: 6,
    fontSize: 14,
    outline: "none",
  },
  button: {
    padding: "8px 16px",
    background: "#2563eb",
    color: "#fff",
    border: "none",
    borderRadius: 6,
    fontSize: 14,
    fontWeight: 600,
    cursor: "pointer",
  },
  buttonDisabled: {
    background: "#94a3b8",
    cursor: "not-allowed",
  },
  error: {
    color: "#dc2626",
    fontSize: 12,
    marginTop: 4,
  },
};
