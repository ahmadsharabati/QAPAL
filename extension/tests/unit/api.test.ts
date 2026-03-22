/**
 * API client unit tests.
 *
 * Mocks fetch to test the API layer without a real backend.
 */

import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import {
  healthCheck,
  createJob,
  getJob,
  listJobs,
  deleteJob,
  getUserProfile,
  getQuota,
  setToken,
  clearToken,
  setApiBaseForTests,
  ApiError,
} from "../../src/popup/api";

// ── Mock chrome.storage ─────────────────────────────────────────────────

const mockStorage: Record<string, unknown> = {};

vi.stubGlobal("chrome", {
  storage: {
    local: {
      get: vi.fn(async (key: string) => ({ [key]: mockStorage[key] })),
      set: vi.fn(async (obj: Record<string, unknown>) => {
        Object.assign(mockStorage, obj);
      }),
      remove: vi.fn(async (key: string) => {
        delete mockStorage[key];
      }),
    },
  },
});

// ── Mock fetch ──────────────────────────────────────────────────────────

const mockFetch = vi.fn();
vi.stubGlobal("fetch", mockFetch);

function mockResponse(status: number, body: unknown) {
  return {
    ok: status >= 200 && status < 300,
    status,
    statusText: status === 200 ? "OK" : "Error",
    json: async () => body,
  };
}

// ── Setup ───────────────────────────────────────────────────────────────

beforeEach(() => {
  mockFetch.mockReset();
  setApiBaseForTests("http://test-api:8000");
  clearToken();
});

// ── Tests ───────────────────────────────────────────────────────────────

describe("healthCheck", () => {
  it("returns health response", async () => {
    mockFetch.mockResolvedValueOnce(
      mockResponse(200, { status: "ok", db: "ok", version: "1.0.0" })
    );

    const result = await healthCheck();
    expect(result.status).toBe("ok");
    expect(result.db).toBe("ok");
  });
});

describe("createJob", () => {
  it("sends POST with URL and returns job", async () => {
    await setToken("dev-test");

    mockFetch.mockResolvedValueOnce(
      mockResponse(201, {
        id: "job-1",
        state: "queued",
        progress: 0,
        url: "https://example.com",
      })
    );

    const result = await createJob({ url: "https://example.com" });
    expect(result.id).toBe("job-1");
    expect(result.state).toBe("queued");

    // Verify fetch was called with correct args
    const [url, opts] = mockFetch.mock.calls[0];
    expect(url).toBe("http://test-api:8000/v1/jobs");
    expect(opts.method).toBe("POST");
    expect(JSON.parse(opts.body)).toEqual({ url: "https://example.com" });
    expect(opts.headers["Authorization"]).toBe("Bearer dev-test");
  });
});

describe("getJob", () => {
  it("fetches job by ID", async () => {
    mockFetch.mockResolvedValueOnce(
      mockResponse(200, {
        id: "job-1",
        state: "complete",
        progress: 100,
        url: "https://example.com",
        report: { summary: "Done", score: 85 },
      })
    );

    const result = await getJob("job-1");
    expect(result.state).toBe("complete");
    expect(result.report?.score).toBe(85);
  });
});

describe("listJobs", () => {
  it("returns paginated job list", async () => {
    mockFetch.mockResolvedValueOnce(
      mockResponse(200, {
        jobs: [{ id: "j1" }, { id: "j2" }],
        total: 2,
        page: 1,
        per_page: 20,
      })
    );

    const result = await listJobs();
    expect(result.total).toBe(2);
    expect(result.jobs).toHaveLength(2);
  });
});

describe("deleteJob", () => {
  it("sends DELETE and handles 204", async () => {
    mockFetch.mockResolvedValueOnce(mockResponse(204, undefined));
    await expect(deleteJob("job-1")).resolves.toBeUndefined();
  });
});

describe("getUserProfile", () => {
  it("returns profile with quota", async () => {
    mockFetch.mockResolvedValueOnce(
      mockResponse(200, {
        id: "u1",
        email: "test@dev.local",
        tier: "free",
        quota_remaining: 5,
      })
    );

    const result = await getUserProfile();
    expect(result.tier).toBe("free");
    expect(result.quota_remaining).toBe(5);
  });
});

describe("getQuota", () => {
  it("returns quota info", async () => {
    mockFetch.mockResolvedValueOnce(
      mockResponse(200, { used: 2, limit: 5, resets_at: "2026-04-01" })
    );

    const result = await getQuota();
    expect(result.used).toBe(2);
    expect(result.limit).toBe(5);
  });
});

describe("error handling", () => {
  it("throws ApiError on non-OK response", async () => {
    mockFetch.mockResolvedValueOnce(
      mockResponse(403, { detail: "Quota exceeded" })
    );

    await expect(createJob({ url: "https://example.com" })).rejects.toThrow(
      ApiError
    );
  });

  it("includes status and detail in ApiError", async () => {
    mockFetch.mockResolvedValueOnce(
      mockResponse(401, { detail: "Invalid token" })
    );

    try {
      await getUserProfile();
      expect.fail("Should have thrown");
    } catch (e) {
      expect(e).toBeInstanceOf(ApiError);
      expect((e as ApiError).status).toBe(401);
      expect((e as ApiError).detail).toBe("Invalid token");
    }
  });
});

describe("auth token", () => {
  it("sends Authorization header when token is set", async () => {
    await setToken("my-token");
    mockFetch.mockResolvedValueOnce(
      mockResponse(200, { status: "ok", db: "ok", version: "1.0.0" })
    );

    await healthCheck();
    const headers = mockFetch.mock.calls[0][1].headers;
    expect(headers["Authorization"]).toBe("Bearer my-token");
  });

  it("omits Authorization header when no token", async () => {
    await clearToken();
    mockFetch.mockResolvedValueOnce(
      mockResponse(200, { status: "ok", db: "ok", version: "1.0.0" })
    );

    await healthCheck();
    const headers = mockFetch.mock.calls[0][1].headers;
    expect(headers["Authorization"]).toBeUndefined();
  });
});
