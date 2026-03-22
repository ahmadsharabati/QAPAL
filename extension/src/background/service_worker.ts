/**
 * Service worker — background orchestrator for the QAPAL extension.
 *
 * Handles:
 * 1. Quick Scan: injects content script, maps results to Report
 * 2. Deep Scan: forwards to backend API
 * 3. Job polling (periodic GET /v1/jobs/{id})
 * 4. Sending progress updates to the popup
 *
 * No persistent state — everything is in chrome.storage.local.
 */

import type { ExtensionMessage, Job, JobCreateRequest, Report, Issue } from "../types";

const API_BASE = "http://localhost:8000";
const POLL_INTERVAL_MS = 3000;
const CLIENT_SCANS_KEY = "qapal_client_scans";
const MAX_CLIENT_SCANS = 50;

// Active polling timers: jobId → intervalId
const _pollingJobs = new Map<string, ReturnType<typeof setInterval>>();

// ── Helpers ─────────────────────────────────────────────────────────────

async function getToken(): Promise<string | null> {
  const result = await chrome.storage.local.get("qapal_token");
  return result.qapal_token || null;
}

async function apiRequest<T>(
  path: string,
  options: RequestInit = {}
): Promise<T> {
  const token = await getToken();
  const headers: Record<string, string> = {
    "Content-Type": "application/json",
    ...(options.headers as Record<string, string>),
  };
  if (token) {
    headers["Authorization"] = `Bearer ${token}`;
  }

  const response = await fetch(`${API_BASE}${path}`, { ...options, headers });

  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    throw new Error((body as { detail?: string }).detail || response.statusText);
  }

  if (response.status === 204) return undefined as T;
  return response.json();
}

function generateId(): string {
  return "qs-" + Date.now().toString(36) + "-" + Math.random().toString(36).slice(2, 8);
}

// ── Quick Scan (client-side) ────────────────────────────────────────────

interface RawScanResult {
  issues: Array<{
    ruleId: string;
    severity: "critical" | "major" | "medium" | "minor";
    category: string;
    title: string;
    description: string;
    selector?: string;
    element?: string;
  }>;
  pageUrl: string;
  pageTitle: string;
  duration_ms: number;
  checksRun: number;
}

// Severity mapping: content script uses major/minor, Report uses high/low
function mapSeverity(sev: string): "critical" | "high" | "medium" | "low" {
  switch (sev) {
    case "critical": return "critical";
    case "major":    return "high";
    case "medium":   return "medium";
    case "minor":    return "low";
    default:         return "medium";
  }
}

function buildReport(scanResult: RawScanResult): Report {
  const issues: Issue[] = scanResult.issues.map((raw, i) => ({
    id: `${raw.ruleId}-${i}`,
    severity: mapSeverity(raw.severity),
    rule: raw.ruleId,
    message: `${raw.title}. ${raw.description}`,
    page: scanResult.pageUrl,
    element: raw.selector || null,
  }));

  // Calculate score: 100 minus deductions, capped per rule.
  // Repeated instances of the same rule (e.g. 200x missing alt) count as ONE
  // problem for scoring — the first hit takes full weight, extras add only 0.5 each,
  // capped at 3x the base weight per rule. This prevents one category from
  // dominating the score on large pages.
  const baseWeights: Record<string, number> = { critical: 15, high: 8, medium: 3, low: 1 };
  const ruleHits = new Map<string, number>();
  let deductions = 0;
  for (const issue of issues) {
    const w = baseWeights[issue.severity] ?? 0;
    const hits = (ruleHits.get(issue.rule) ?? 0) + 1;
    ruleHits.set(issue.rule, hits);
    const maxPerRule = w * 3; // cap: 3x the base weight
    const currentTotal = hits === 1 ? w : 0.5; // first hit = full, extras = 0.5
    const ruleSoFar = w + (hits - 1) * 0.5;
    if (ruleSoFar <= maxPerRule) {
      deductions += currentTotal;
    }
    // else: already at cap for this rule, no more deductions
  }
  const score = Math.max(0, Math.round(100 - deductions));

  const counts = { critical: 0, high: 0, medium: 0, low: 0 };
  for (const issue of issues) {
    counts[issue.severity]++;
  }

  const report: Report = {
    summary: `Quick Scan found ${issues.length} issue${issues.length !== 1 ? "s" : ""} on ${scanResult.pageTitle}`,
    score,
    issues,
    critical_count: counts.critical,
    high_count: counts.high,
    medium_count: counts.medium,
    pages_crawled: 1,
    actions_taken: 0,
    duration_ms: scanResult.duration_ms,
    engine_version: "client-1.0",
    generated_at: new Date().toISOString(),
  };

  return report;
}

async function runClientScan(): Promise<Job> {
  // Get the active tab
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  if (!tab?.id || !tab.url) {
    throw new Error("No active tab found");
  }

  // Don't scan extension pages, chrome://, etc.
  if (!tab.url.startsWith("http://") && !tab.url.startsWith("https://")) {
    throw new Error("Quick Scan only works on web pages (http/https)");
  }

  // Inject the content script
  const results = await chrome.scripting.executeScript({
    target: { tabId: tab.id },
    files: ["src/content/scanner.js"],
  });

  if (!results || !results[0]?.result) {
    throw new Error("Content script returned no results");
  }

  const scanResult = results[0].result as RawScanResult;
  const report = buildReport(scanResult);

  // Create a local Job object
  const job: Job = {
    id: generateId(),
    state: "complete",
    progress: 100,
    message: null,
    url: scanResult.pageUrl,
    report,
    error: null,
    failure_stage: null,
    trace_path: null,
    created_at: new Date().toISOString(),
    started_at: new Date().toISOString(),
    completed_at: new Date().toISOString(),
  };

  // Persist to local storage
  await saveClientScan(job);

  return job;
}

async function saveClientScan(job: Job): Promise<void> {
  const result = await chrome.storage.local.get(CLIENT_SCANS_KEY);
  const scans: Job[] = result[CLIENT_SCANS_KEY] || [];
  scans.unshift(job);
  // Keep only last N
  if (scans.length > MAX_CLIENT_SCANS) {
    scans.length = MAX_CLIENT_SCANS;
  }
  await chrome.storage.local.set({ [CLIENT_SCANS_KEY]: scans });
}

// ── Job polling ─────────────────────────────────────────────────────────

function startPolling(jobId: string): void {
  if (_pollingJobs.has(jobId)) return; // already polling

  const poll = async () => {
    try {
      const job = await apiRequest<Job>(`/v1/jobs/${jobId}`);

      // Broadcast update to popup
      chrome.runtime.sendMessage({
        type: "JOB_UPDATE",
        payload: { jobId, job },
      }).catch(() => {
        // Popup not open — that's fine
      });

      // Stop polling when job reaches terminal state
      if (job.state === "complete" || job.state === "failed") {
        stopPollingJob(jobId);
      }
    } catch (e) {
      console.error(`[QAPAL] Poll failed for job ${jobId}:`, e);
    }
  };

  // Poll immediately, then on interval
  poll();
  const intervalId = setInterval(poll, POLL_INTERVAL_MS);
  _pollingJobs.set(jobId, intervalId);
}

function stopPollingJob(jobId: string): void {
  const intervalId = _pollingJobs.get(jobId);
  if (intervalId) {
    clearInterval(intervalId);
    _pollingJobs.delete(jobId);
  }
}

// ── Message handler ─────────────────────────────────────────────────────

chrome.runtime.onMessage.addListener(
  (message: ExtensionMessage, _sender, sendResponse) => {
    const handle = async () => {
      switch (message.type) {
        case "START_CLIENT_SCAN": {
          const job = await runClientScan();
          return job;
        }

        case "START_SCAN": {
          const { url, options } = message.payload as {
            url: string;
            options?: JobCreateRequest["options"];
          };
          const job = await apiRequest<Job>("/v1/jobs", {
            method: "POST",
            body: JSON.stringify({ url, options }),
          });
          // Start polling for this job
          startPolling(job.id);
          return job;
        }

        case "POLL_JOB": {
          const { jobId } = message.payload as { jobId: string };
          const job = await apiRequest<Job>(`/v1/jobs/${jobId}`);
          // Ensure polling is active
          if (job.state === "queued" || job.state === "running") {
            startPolling(jobId);
          }
          return job;
        }

        case "STOP_POLLING": {
          const { jobId } = message.payload as { jobId: string };
          stopPollingJob(jobId);
          return;
        }

        case "GET_AUTH": {
          const token = await getToken();
          return { token };
        }

        case "SET_AUTH": {
          const { token } = message.payload as { token: string };
          await chrome.storage.local.set({ qapal_token: token });
          return;
        }

        case "CLEAR_AUTH": {
          await chrome.storage.local.remove("qapal_token");
          return;
        }

        default:
          console.warn(`[QAPAL] Unknown message type: ${message.type}`);
      }
    };

    handle()
      .then((result) => sendResponse(result))
      .catch((err) => sendResponse({ error: err.message }));

    // Return true to indicate async response
    return true;
  }
);

// ── Startup ─────────────────────────────────────────────────────────────

console.log("[QAPAL] Service worker started");
