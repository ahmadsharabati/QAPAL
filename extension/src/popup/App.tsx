/**
 * App — main popup UI.
 *
 * State flow:
 * 1. Always show Quick Scan (no login required)
 * 2. Authenticated → also show Deep Scan + quota + server job history
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

const CLIENT_SCANS_KEY = "qapal_client_scans";

type View = "loading" | "main" | "detail";

export function App() {
  const [view, setView] = useState<View>("loading");
  const [tokenInput, setTokenInput] = useState("");
  const [showLogin, setShowLogin] = useState(false);
  const [profile, setProfile] = useState<UserProfile | null>(null);
  const [quota, setQuota] = useState<QuotaInfo | null>(null);
  const [serverJobs, setServerJobs] = useState<Job[]>([]);
  const [clientScans, setClientScans] = useState<Job[]>([]);
  const [activeJob, setActiveJob] = useState<Job | null>(null);
  const [error, setError] = useState("");
  const [submitting, setSubmitting] = useState(false);

  const isAuthenticated = !!profile;

  // ── Init ─────────────────────────────────────────────────────────
  useEffect(() => {
    (async () => {
      // Load client scans from local storage
      await loadClientScans();

      // Try loading user data if token exists
      const token = await getToken();
      if (token) {
        await loadUserData();
      }
      setView("main");
    })();
  }, []);

  // ── Poll active job (deep scan only) ────────────────────────────
  useEffect(() => {
    if (!activeJob || activeJob.state === "complete" || activeJob.state === "failed") {
      return;
    }
    // Client scans are always complete immediately
    if (activeJob.id.startsWith("qs-")) return;

    const interval = setInterval(async () => {
      try {
        const updated = await getJob(activeJob.id);
        setActiveJob(updated);
        if (updated.state === "complete" || updated.state === "failed") {
          loadServerJobs();
        }
      } catch {
        // Backend unreachable — keep trying
      }
    }, 3000);

    return () => clearInterval(interval);
  }, [activeJob?.id, activeJob?.state]);

  // ── Listen for service worker updates ───────────────────────────
  useEffect(() => {
    const listener = (message: { type: string; payload?: { jobId: string; job: Job } }) => {
      if (message.type === "JOB_UPDATE" && message.payload) {
        const { job } = message.payload;
        setActiveJob((prev) => (prev && prev.id === job.id ? job : prev));
        if (job.state === "complete" || job.state === "failed") {
          loadServerJobs();
        }
      }
    };

    try {
      chrome.runtime.onMessage.addListener(listener);
      return () => chrome.runtime.onMessage.removeListener(listener);
    } catch {
      return;
    }
  }, []);

  // ── Data loaders ────────────────────────────────────────────────
  const loadUserData = useCallback(async () => {
    try {
      const [p, q] = await Promise.all([getUserProfile(), getQuota()]);
      setProfile(p);
      setQuota(q);
      await loadServerJobs();
    } catch (e) {
      if (e instanceof ApiError && (e.status === 401 || e.status === 403)) {
        await clearToken();
        setProfile(null);
        setQuota(null);
      }
      // Non-fatal: Quick Scan still works
    }
  }, []);

  const loadServerJobs = async () => {
    try {
      const result = await listJobs();
      setServerJobs(result.jobs);
    } catch {
      // Non-critical
    }
  };

  const loadClientScans = async () => {
    try {
      const result = await chrome.storage.local.get(CLIENT_SCANS_KEY);
      setClientScans(result[CLIENT_SCANS_KEY] || []);
    } catch {
      // Not in extension context
    }
  };

  // Merge server jobs and client scans, sorted by date (newest first)
  const allJobs = [...serverJobs, ...clientScans].sort((a, b) => {
    const da = a.created_at ? new Date(a.created_at).getTime() : 0;
    const db = b.created_at ? new Date(b.created_at).getTime() : 0;
    return db - da;
  });

  // ── Handlers ────────────────────────────────────────────────────
  const handleLogin = async () => {
    setError("");
    const trimmed = tokenInput.trim();
    if (!trimmed) {
      setError("Please enter a token");
      return;
    }
    await setToken(trimmed);
    await loadUserData();
    setShowLogin(false);
    setTokenInput("");
  };

  const handleLogout = async () => {
    await clearToken();
    setProfile(null);
    setQuota(null);
    setServerJobs([]);
  };

  const handleQuickScan = async () => {
    setError("");
    setSubmitting(true);
    try {
      const response = await chrome.runtime.sendMessage({ type: "START_CLIENT_SCAN" });
      if (response?.error) {
        setError(response.error);
      } else {
        setActiveJob(response as Job);
        setView("detail");
        // Refresh local scans
        await loadClientScans();
      }
    } catch (e) {
      setError("Quick Scan failed. Make sure you're on a web page.");
    } finally {
      setSubmitting(false);
    }
  };

  const handleDeepScan = async (url: string) => {
    setError("");
    setSubmitting(true);
    try {
      const job = await createJob({ url });
      setActiveJob(job);
      setView("detail");
      getQuota().then(setQuota).catch(() => {});
    } catch (e) {
      if (e instanceof ApiError) {
        setError(e.errorMessage);
      } else {
        setError("Failed to start scan");
      }
    } finally {
      setSubmitting(false);
    }
  };

  const handleSelectJob = async (jobId: string) => {
    // Check if it's a client scan
    const clientJob = clientScans.find((j) => j.id === jobId);
    if (clientJob) {
      setActiveJob(clientJob);
      setView("detail");
      return;
    }

    // Otherwise fetch from server
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
    loadServerJobs();
    loadClientScans();
  };

  // ── Render ──────────────────────────────────────────────────────
  if (view === "loading") {
    return (
      <div style={styles.container}>
        <p style={styles.loading}>Loading...</p>
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
          {isAuthenticated && quota && (
            <QuotaBadge quota={quota} tier={profile!.tier} />
          )}
        </div>
        <JobStatus job={activeJob} />
      </div>
    );
  }

  // Main view — always accessible
  return (
    <div style={styles.container}>
      <div style={styles.topBar}>
        <h1 style={styles.titleSmall}>QAPAL</h1>
        <div style={styles.topRight}>
          {isAuthenticated && quota && (
            <QuotaBadge quota={quota} tier={profile!.tier} />
          )}
          {isAuthenticated ? (
            <button onClick={handleLogout} style={styles.logoutButton}>
              Sign Out
            </button>
          ) : (
            <button onClick={() => setShowLogin(!showLogin)} style={styles.signInButton}>
              Sign In
            </button>
          )}
        </div>
      </div>

      {/* Login form (collapsible) */}
      {showLogin && !isAuthenticated && (
        <div style={styles.loginSection}>
          <div style={styles.loginRow}>
            <input
              type="text"
              value={tokenInput}
              onChange={(e) => setTokenInput(e.target.value)}
              placeholder="Enter token (e.g. dev-myname)"
              style={styles.loginInput}
              onKeyDown={(e) => e.key === "Enter" && handleLogin()}
              aria-label="Auth token"
            />
            <button onClick={handleLogin} style={styles.loginButton}>
              Go
            </button>
          </div>
        </div>
      )}

      <JobForm
        onQuickScan={handleQuickScan}
        onDeepScan={handleDeepScan}
        disabled={false}
        scanning={submitting}
        quotaRemaining={quota ? quota.limit - quota.used : undefined}
        isAuthenticated={isAuthenticated}
      />

      {error && <p style={styles.error}>{error}</p>}

      <JobList jobs={allJobs} onSelect={handleSelectJob} />
    </div>
  );
}

const styles: Record<string, React.CSSProperties> = {
  container: { padding: 16 },
  loading: { textAlign: "center", color: "#9ca3af", padding: 40 },
  titleSmall: { fontSize: 16, fontWeight: 700, margin: 0 },
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
  signInButton: {
    background: "none",
    border: "1px solid #d1d5db",
    color: "#374151",
    fontSize: 12,
    cursor: "pointer",
    padding: "4px 10px",
    borderRadius: 4,
  },
  loginSection: {
    marginBottom: 12,
    padding: 10,
    background: "#f9fafb",
    borderRadius: 6,
    border: "1px solid #e5e7eb",
  },
  loginRow: {
    display: "flex",
    gap: 6,
  },
  loginInput: {
    flex: 1,
    padding: "6px 10px",
    border: "1px solid #d1d5db",
    borderRadius: 4,
    fontSize: 13,
    outline: "none",
  },
  loginButton: {
    padding: "6px 12px",
    background: "#2563eb",
    color: "#fff",
    border: "none",
    borderRadius: 4,
    fontSize: 13,
    fontWeight: 600,
    cursor: "pointer",
  },
};
