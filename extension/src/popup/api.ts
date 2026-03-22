/**
 * API client — thin wrapper over fetch for the QAPAL backend.
 *
 * All calls go through this module so the popup never talks to
 * the backend directly from component code.
 */

import type {
  Job,
  JobListResponse,
  JobCreateRequest,
  UserProfile,
  QuotaInfo,
  HealthResponse,
} from "../types";

// Default to localhost in dev; override via chrome.storage
const DEFAULT_API_BASE = "http://localhost:8000";

// ── Token storage ───────────────────────────────────────────────────────

let _cachedToken: string | null = null;

export async function getToken(): Promise<string | null> {
  if (_cachedToken) return _cachedToken;
  try {
    const result = await chrome.storage.local.get("qapal_token");
    _cachedToken = result.qapal_token || null;
    return _cachedToken;
  } catch {
    // Not in extension context (e.g., tests)
    return _cachedToken;
  }
}

export async function setToken(token: string): Promise<void> {
  _cachedToken = token;
  try {
    await chrome.storage.local.set({ qapal_token: token });
  } catch {
    // Not in extension context
  }
}

export async function clearToken(): Promise<void> {
  _cachedToken = null;
  try {
    await chrome.storage.local.remove("qapal_token");
  } catch {
    // Not in extension context
  }
}

// ── API base ────────────────────────────────────────────────────────────

let _apiBase = DEFAULT_API_BASE;

export async function getApiBase(): Promise<string> {
  try {
    const result = await chrome.storage.local.get("qapal_api_base");
    if (result.qapal_api_base) {
      _apiBase = result.qapal_api_base;
    }
  } catch {
    // Not in extension context
  }
  return _apiBase;
}

export function setApiBaseForTests(base: string): void {
  _apiBase = base;
}

// ── Fetch helper ────────────────────────────────────────────────────────

async function apiFetch<T>(
  path: string,
  options: RequestInit = {}
): Promise<T> {
  const base = await getApiBase();
  const token = await getToken();

  const headers: Record<string, string> = {
    "Content-Type": "application/json",
    ...(options.headers as Record<string, string>),
  };

  if (token) {
    headers["Authorization"] = `Bearer ${token}`;
  }

  const response = await fetch(`${base}${path}`, {
    ...options,
    headers,
  });

  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    const detail = (body as { detail?: string }).detail || response.statusText;
    throw new ApiError(response.status, detail);
  }

  // 204 No Content
  if (response.status === 204) {
    return undefined as T;
  }

  return response.json();
}

export class ApiError extends Error {
  constructor(
    public status: number,
    public detail: string
  ) {
    super(`API ${status}: ${detail}`);
    this.name = "ApiError";
  }
}

// ── Endpoints ───────────────────────────────────────────────────────────

export async function healthCheck(): Promise<HealthResponse> {
  return apiFetch<HealthResponse>("/v1/health");
}

export async function createJob(
  request: JobCreateRequest
): Promise<Job> {
  return apiFetch<Job>("/v1/jobs", {
    method: "POST",
    body: JSON.stringify(request),
  });
}

export async function getJob(jobId: string): Promise<Job> {
  return apiFetch<Job>(`/v1/jobs/${jobId}`);
}

export async function listJobs(
  page = 1,
  perPage = 20
): Promise<JobListResponse> {
  return apiFetch<JobListResponse>(
    `/v1/jobs?page=${page}&per_page=${perPage}`
  );
}

export async function deleteJob(jobId: string): Promise<void> {
  return apiFetch<void>(`/v1/jobs/${jobId}`, { method: "DELETE" });
}

export async function getUserProfile(): Promise<UserProfile> {
  return apiFetch<UserProfile>("/v1/user/profile");
}

export async function getQuota(): Promise<QuotaInfo> {
  return apiFetch<QuotaInfo>("/v1/user/quota");
}
