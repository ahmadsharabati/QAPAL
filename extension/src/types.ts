/**
 * Shared types — mirrors the backend API schemas.
 */

// ── Job ────────────────────────────────────────────────────────────────

export type JobState = "queued" | "running" | "complete" | "failed" | "deleted";

export interface Job {
  id: string;
  state: JobState;
  progress: number;
  message: string | null;
  url: string;
  report: Report | null;
  error: string | null;
  created_at: string | null;
  started_at: string | null;
  completed_at: string | null;
}

export interface JobListResponse {
  jobs: Job[];
  total: number;
  page: number;
  per_page: number;
}

export interface JobCreateRequest {
  url: string;
  options?: {
    scan_mode?: "quick" | "standard" | "deep";
    max_pages?: number;
  };
}

// ── Report ─────────────────────────────────────────────────────────────

export interface Report {
  summary: string;
  score: number;
  issues: Issue[];
  critical_count: number;
  high_count: number;
  medium_count: number;
  pages_crawled: number;
  actions_taken: number;
  duration_ms: number;
  engine_version: string;
  generated_at: string;
}

export interface Issue {
  id: string;
  severity: "critical" | "high" | "medium" | "low";
  rule: string;
  message: string;
  page: string;
  element: string | null;
}

// ── User ───────────────────────────────────────────────────────────────

export interface UserProfile {
  id: string;
  email: string;
  tier: "free" | "starter" | "pro";
  quota_remaining: number;
}

export interface QuotaInfo {
  used: number;
  limit: number;
  resets_at: string;
}

// ── Health ──────────────────────────────────────────────────────────────

export interface HealthResponse {
  status: string;
  db: string;
  version: string;
}

// ── Messages (popup ↔ service worker) ──────────────────────────────────

export type MessageType =
  | "START_SCAN"
  | "POLL_JOB"
  | "STOP_POLLING"
  | "JOB_UPDATE"
  | "GET_AUTH"
  | "SET_AUTH"
  | "CLEAR_AUTH";

export interface ExtensionMessage {
  type: MessageType;
  payload?: unknown;
}

export interface JobUpdatePayload {
  jobId: string;
  job: Job;
}
