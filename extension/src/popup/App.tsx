/**
 * App — main popup UI.
 *
 * State flow:
 * 1. Not authenticated → show token input
 * 2. Authenticated → show scan form + quota + job history
 * 3. Active job → show progress + results
 */

import React, { useEffect, useState, useCallback } from "react";
import type { Job, UserProfile, QuotaInfo } from "../types";
import {
  getToken,
  setToken,
  clearToken,
  getUserProfile,
  getQuota,
  listJobs,
  createJob,
  getJob,
  ApiError,
} from "./api";
import { JobForm } from "../components/JobForm";
import { JobStatus } from "../components/JobStatus";
import { QuotaBadge } from "../components/QuotaBadge";
import { JobList } from "../components/JobList";

type View = "loading" | "auth" | "main" | "detail";

export function App() {
  const [view, setView] = useState<View>("loading");
  const [tokenInput, setTokenInput] = useState("");
  const [profile, setProfile] = useState<UserProfile | null>(null);
  const [quota, setQuota] = useState<QuotaInfo | null>(null);
  const [jobs, setJobs] = useState<Job[]>([]);
  const [activeJob, setActiveJob] = useState<Job | null>(null);
  const [error, setError] = useState("");
  const [submitting, setSubmitting] = useState(false);

  // ── Init: check for existing token ────────────────────────────────
  useEffect(() => {
    (async () => {
      const token = await getToken();
      if (token) {
        await loadUserData();
      } else {
        setView("auth");
      }
    })();
  }, []);

  // ── Poll active job ───────────────────────────────────────────────
  useEffect(() => {
    if (!activeJob || activeJob.state === "complete" || activeJob.state === "failed") {
      return;
    }

    const interval = setInterval(async () => {
      try {
        const updated = await getJob(activeJob.id);
        setActiveJob(updated);
        if (updated.state === "complete" || updated.state === "failed") {
          // Refresh job list
          loadJobs();
        }
      } catch {
        // Backend unreachable — keep trying
      }
    }, 3000);

    return () => clearInterval(interval);
  }, [activeJob?.id, activeJob?.state]);

  // ── Listen for service worker updates ─────────────────────────────
  useEffect(() => {
    const listener = (message: { type: string; payload?: { jobId: string; job: Job } }) => {
      if (message.type === "JOB_UPDATE" && message.payload) {
        const { job } = message.payload;
        setActiveJob((prev) => (prev && prev.id === job.id ? job : prev));
        // Refresh list if terminal
        if (job.state === "complete" || job.state === "failed") {
          loadJobs();
        }
      }
    };

    try {
      chrome.runtime.onMessage.addListener(listener);
      return () => chrome.runtime.onMessage.removeListener(listener);
    } catch {
      // Not in extension context
      return;
    }
  }, []);

  // ── Data loaders ──────────────────────────────────────────────────
  const loadUserData = useCallback(async () => {
    try {
      const [p, q] = await Promise.all([getUserProfile(), getQuota()]);
      setProfile(p);
      setQuota(q);
      await loadJobs();
      setView("main");
    } catch (e) {
      if (e instanceof ApiError && (e.status === 401 || e.status === 403)) {
        await clearToken();
        setView("auth");
      } else {
        setError("Could not connect to backend");
        setView("auth");
      }
    }
  }, []);

  const loadJobs = async () => {
    try {
      const result = await listJobs();
      setJobs(result.jobs);
    } catch {
      // Non-critical
    }
  };

  // ── Handlers ──────────────────────────────────────────────────────
  const handleLogin = async () => {
    setError("");
    const trimmed = tokenInput.trim();
    if (!trimmed) {
      setError("Please enter a token");
      return;
    }
    await setToken(trimmed);
    await loadUserData();
  };

  const handleLogout = async () => {
    await clearToken();
    setProfile(null);
    setQuota(null);
    setJobs([]);
    setActiveJob(null);
    setView("auth");
  };

  const handleSubmitScan = async (url: string) => {
    setError("");
    setSubmitting(true);
    try {
      const job = await createJob({ url });
      setActiveJob(job);
      setView("detail");
      // Refresh quota
      getQuota().then(setQuota).catch(() => {});
    } catch (e) {
      if (e instanceof ApiError) {
        setError(e.detail);
      } else {
        setError("Failed to start scan");
      }
    } finally {
      setSubmitting(false);
    }
  };

  const handleSelectJob = async (jobId: string) => {
    try {
      const job = await getJob(jobId);
      setActiveJob(job);
      setView("detail");
    } catch {
      setError("Could not load job");
    }
  };

  const handleBack = () => {
    setActiveJob(null);
    setView("main");
    loadJobs();
  };

  // ── Render ────────────────────────────────────────────────────────
  if (view === "loading") {
    return (
      <div style={styles.container}>
        <p style={styles.loading}>Loading...</p>
      </div>
    );
  }

  if (view === "auth") {
    return (
      <div style={styles.container}>
        <h1 style={styles.title}>QAPAL</h1>
        <p style={styles.subtitle}>Sign in to start testing</p>
        <div style={styles.authForm}>
          <input
            type="text"
            value={tokenInput}
            onChange={(e) => setTokenInput(e.target.value)}
            placeholder="Enter your token (e.g. dev-myname)"
            style={styles.input}
            onKeyDown={(e) => e.key === "Enter" && handleLogin()}
            aria-label="Auth token"
          />
          <button onClick={handleLogin} style={styles.primaryButton}>
            Sign In
          </button>
        </div>
        {error && <p style={styles.error}>{error}</p>}
      </div>
    );
  }

  if (view === "detail" && activeJob) {
    return (
      <div style={styles.container}>
        <div style={styles.topBar}>
          <button onClick={handleBack} style={styles.backButton}>
            &larr; Back
          </button>
          {profile && quota && (
            <QuotaBadge quota={quota} tier={profile.tier} />
          )}
        </div>
        <JobStatus job={activeJob} />
      </div>
    );
  }

  // Main view
  return (
    <div style={styles.container}>
      <div style={styles.topBar}>
        <h1 style={styles.titleSmall}>QAPAL</h1>
        <div style={styles.topRight}>
          {profile && quota && (
            <QuotaBadge quota={quota} tier={profile.tier} />
          )}
          <button onClick={handleLogout} style={styles.logoutButton}>
            Sign Out
          </button>
        </div>
      </div>

      <JobForm
        onSubmit={handleSubmitScan}
        disabled={submitting}
        quotaRemaining={quota ? quota.limit - quota.used : undefined}
      />

      {error && <p style={styles.error}>{error}</p>}

      <JobList jobs={jobs} onSelect={handleSelectJob} />
    </div>
  );
}

const styles: Record<string, React.CSSProperties> = {
  container: { padding: 16 },
  loading: { textAlign: "center", color: "#9ca3af", padding: 40 },
  title: { fontSize: 22, fontWeight: 700, marginBottom: 4 },
  titleSmall: { fontSize: 16, fontWeight: 700 },
  subtitle: { fontSize: 14, color: "#6b7280", marginBottom: 16 },
  authForm: { display: "flex", flexDirection: "column", gap: 8 },
  input: {
    padding: "8px 12px",
    border: "1px solid #ddd",
    borderRadius: 6,
    fontSize: 14,
    outline: "none",
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
  },
  error: { color: "#dc2626", fontSize: 12, marginTop: 8 },
  topBar: {
    display: "flex",
    justifyContent: "space-between",
    alignItems: "center",
    marginBottom: 16,
  },
  topRight: { display: "flex", alignItems: "center", gap: 8 },
  backButton: {
    background: "none",
    border: "none",
    color: "#2563eb",
    fontSize: 14,
    cursor: "pointer",
    padding: 0,
  },
  logoutButton: {
    background: "none",
    border: "none",
    color: "#6b7280",
    fontSize: 12,
    cursor: "pointer",
    padding: 0,
  },
};
