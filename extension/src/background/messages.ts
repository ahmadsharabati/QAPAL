/**
 * Message protocol — popup ↔ service worker communication.
 *
 * The popup sends commands (START_SCAN, POLL_JOB, etc.).
 * The service worker replies with results or sends JOB_UPDATE messages.
 */

import type { ExtensionMessage, Job, JobCreateRequest } from "../types";

// ── Send from popup to service worker ───────────────────────────────────

export function sendMessage(
  message: ExtensionMessage
): Promise<unknown> {
  return chrome.runtime.sendMessage(message);
}

export function startScan(url: string, options?: JobCreateRequest["options"]): Promise<Job> {
  return sendMessage({
    type: "START_SCAN",
    payload: { url, options },
  }) as Promise<Job>;
}

export function pollJob(jobId: string): Promise<Job> {
  return sendMessage({
    type: "POLL_JOB",
    payload: { jobId },
  }) as Promise<Job>;
}

export function stopPolling(jobId: string): Promise<void> {
  return sendMessage({
    type: "STOP_POLLING",
    payload: { jobId },
  }) as Promise<void>;
}

export function startClientScan(): Promise<Job> {
  return sendMessage({ type: "START_CLIENT_SCAN" }) as Promise<Job>;
}

// ── Listen for updates from service worker ──────────────────────────────

export function onJobUpdate(
  callback: (jobId: string, job: Job) => void
): () => void {
  const listener = (message: ExtensionMessage) => {
    if (message.type === "JOB_UPDATE") {
      const { jobId, job } = message.payload as { jobId: string; job: Job };
      callback(jobId, job);
    }
  };

  chrome.runtime.onMessage.addListener(listener);
  return () => chrome.runtime.onMessage.removeListener(listener);
}
