/**
 * Service worker — background orchestrator for the QAPAL extension.
 *
 * Handles:
 * 1. Job creation (forwards to backend)
 * 2. Job polling (periodic GET /v1/jobs/{id})
 * 3. Sending progress updates to the popup
 *
 * No persistent state — everything is in chrome.storage.local.
 */

import type { ExtensionMessage, Job, JobCreateRequest } from "../types";

const API_BASE = "http://localhost:8000";
const POLL_INTERVAL_MS = 3000;

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
