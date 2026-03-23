/**
 * QAPAL GitHub Action — src/index.js
 *
 * Pure Node.js, zero npm dependencies. Uses only built-in modules:
 * https, fs, path. Compatible with actions/runner node20.
 *
 * Flow:
 *   1. Read inputs
 *   2. POST /v1/jobs  → job_id
 *   3. Poll GET /v1/jobs/{id} until state = complete | failed | timeout
 *   4. Fetch report, write annotations, set outputs
 *   5. Exit 0 (pass) or 1 (fail) based on fail_on threshold
 */

"use strict";

const https = require("https");
const http = require("http");
const fs = require("fs");
const path = require("path");

// ── GitHub Actions toolkit (pure-JS subset) ────────────────────────────────

function getInput(name, required = false) {
  const val = process.env[`INPUT_${name.toUpperCase().replace(/ /g, "_")}`] || "";
  if (required && !val.trim()) {
    setFailed(`Input '${name}' is required but was not provided.`);
    process.exit(1);
  }
  return val.trim();
}

function setOutput(name, value) {
  // GitHub Actions output via workflow commands
  const filePath = process.env.GITHUB_OUTPUT;
  if (filePath) {
    fs.appendFileSync(filePath, `${name}=${value}\n`);
  } else {
    // Fallback for older runners
    console.log(`::set-output name=${name}::${value}`);
  }
}

function info(message) {
  console.log(message);
}

function warning(message) {
  console.log(`::warning::${message}`);
}

function error(message) {
  console.log(`::error::${message}`);
}

function notice(message) {
  console.log(`::notice::${message}`);
}

function setFailed(message) {
  console.log(`::error::${message}`);
  process.exitCode = 1;
}

function startGroup(name) {
  console.log(`::group::${name}`);
}

function endGroup() {
  console.log(`::endgroup::`);
}

function rankOf(severity) {
  const s = (severity || "low").toLowerCase();
  if (s === "critical") return 4;
  if (s === "major" || s === "high") return 3;
  if (s === "medium") return 2;
  if (s === "minor" || s === "low") return 1;
  if (s === "none") return 0;
  return 1;
}

// ── HTTP client (built-in only) ─────────────────────────────────────────────

function request(method, urlStr, body, headers) {
  return new Promise((resolve, reject) => {
    const url = new URL(urlStr);
    const isHttps = url.protocol === "https:";
    const lib = isHttps ? https : http;

    const bodyStr = body ? JSON.stringify(body) : null;
    const reqHeaders = {
      "Content-Type": "application/json",
      "User-Agent": "qapal-action/1.0",
      ...headers,
    };
    if (bodyStr) {
      reqHeaders["Content-Length"] = Buffer.byteLength(bodyStr);
    }

    const options = {
      hostname: url.hostname,
      port: url.port || (isHttps ? 443 : 80),
      path: url.pathname + url.search,
      method,
      headers: reqHeaders,
    };

    const req = lib.request(options, (res) => {
      let data = "";
      res.on("data", (chunk) => (data += chunk));
      res.on("end", () => {
        try {
          resolve({ status: res.statusCode, body: JSON.parse(data) });
        } catch {
          resolve({ status: res.statusCode, body: data });
        }
      });
    });

    req.on("error", reject);
    if (bodyStr) req.write(bodyStr);
    req.end();
  });
}

// ── QAPAL API client ────────────────────────────────────────────────────────

async function createJob(backendUrl, token, url, prdPath) {
  const payload = { url };

  if (prdPath && fs.existsSync(prdPath)) {
    payload.prd_content = fs.readFileSync(prdPath, "utf8");
    payload.options = { 
      max_pages: 5,
      credentials: {
        username: getInput("test_user"),
        password: getInput("test_pass")
      }
    };
    info(`  PRD spec loaded: ${prdPath}`);
  }

  const res = await request("POST", `${backendUrl}/v1/jobs`, payload, {
    Authorization: `Bearer ${token}`,
  });

  if (res.status === 401) throw new Error("Invalid QAPAL token. Check your QAPAL_TOKEN secret.");
  if (res.status === 403) {
    const detail = res.body?.detail;
    const msg = typeof detail === "object" ? detail.message : detail;
    throw new Error(`Quota exceeded: ${msg || "upgrade your QAPAL plan"}`);
  }
  if (res.status === 429) throw new Error("Rate limited by QAPAL API. Retry after a moment.");
  if (res.status !== 201) throw new Error(`Failed to create job: HTTP ${res.status}`);

  return res.body;
}

async function pollJob(backendUrl, token, jobId, pollIntervalSec, timeoutSec) {
  const deadline = Date.now() + timeoutSec * 1000;
  let lastProgress = -1;

  while (Date.now() < deadline) {
    const res = await request("GET", `${backendUrl}/v1/jobs/${jobId}`, null, {
      Authorization: `Bearer ${token}`,
    });

    if (res.status !== 200) throw new Error(`Failed to poll job ${jobId}: HTTP ${res.status}`);

    const job = res.body;
    const progress = job.progress || 0;
    const state = job.state;
    const message = job.message || "";

    // Only log progress changes to avoid spamming the CI log
    if (progress !== lastProgress) {
      info(`  [${progress}%] ${message}`);
      lastProgress = progress;
    }

    if (state === "complete") return job;
    if (state === "failed") {
      throw new Error(
        `Scan failed at ${progress}%: ${message}` +
          (job.failure_stage ? ` (stage: ${job.failure_stage})` : "")
      );
    }

    await new Promise((r) => setTimeout(r, pollIntervalSec * 1000));
  }

  throw new Error(`Scan timed out after ${timeoutSec}s. Job ${jobId} may still be running.`);
}

async function getReport(backendUrl, token, jobId) {
  const res = await request("GET", `${backendUrl}/v1/jobs/${jobId}/report`, null, {
    Authorization: `Bearer ${token}`,
  });
  if (res.status === 404) throw new Error(`Report not found for job ${jobId}.`);
  if (res.status !== 200) throw new Error(`Failed to fetch report: HTTP ${res.status}`);
  return res.body;
}

// ── Severity helpers ────────────────────────────────────────────────────────

const SEVERITY_RANK = { critical: 4, major: 3, medium: 2, minor: 1, none: 0 };

function rankOf(severity) {
  return SEVERITY_RANK[severity?.toLowerCase()] ?? 0;
}

// ── Annotation helpers ─────────────────────────────────────────────────────

/**
 * Write a GitHub Actions annotation for each issue.
 * Annotations appear inline in PR diffs and the Actions log.
 */
function annotateIssues(issues, failOnRank) {
  for (const issue of issues) {
    const rank = rankOf(issue.severity);
    const text =
      `[${issue.ruleId}] ${issue.title}` +
      (issue.message ? ` — ${issue.message}` : "") +
      (issue.selector ? ` (selector: ${issue.selector})` : "");

    if (rank >= 4) {
      error(text);
    } else if (rank >= 3) {
      warning(text);
    } else {
      notice(text);
    }
  }
}

function printSummaryTable(report, issues) {
  const counts = { critical: 0, major: 0, medium: 0, minor: 0 };
  for (const issue of issues) {
    const sev = issue.severity?.toLowerCase();
    if (sev in counts) counts[sev]++;
  }

  info("");
  info("┌─────────────────────────────────────────────────┐");
  info(`│  QAPAL Scan Results                             │`);
  info("├──────────────────┬──────────────────────────────┤");
  info(`│  Score           │  ${String(report.score ?? "N/A").padEnd(28)}│`);
  info(`│  Total Issues    │  ${String(issues.length).padEnd(28)}│`);
  info(`│  Critical        │  ${String(counts.critical).padEnd(28)}│`);
  info(`│  Major           │  ${String(counts.major).padEnd(28)}│`);
  info(`│  Medium          │  ${String(counts.medium).padEnd(28)}│`);
  info(`│  Minor           │  ${String(counts.minor).padEnd(28)}│`);
  info(`│  Pages Crawled   │  ${String(report.pages_crawled ?? 1).padEnd(28)}│`);
  info(`│  Duration        │  ${String((report.duration_ms ?? 0) + "ms").padEnd(28)}│`);
  info("└──────────────────┴──────────────────────────────┘");
  info("");

  if (report.narration) {
    info(`📝 ${report.narration}`);
    info("");
  }
}

async function postPullRequestComment(report, issues) {
  const token = process.env.GITHUB_TOKEN;
  if (!token) {
    info("  [CI] GITHUB_TOKEN not set. Skipping PR comment.");
    return;
  }

  const repo = process.env.GITHUB_REPOSITORY;
  const eventPath = process.env.GITHUB_EVENT_PATH;
  if (!repo || !eventPath) return;

  let prNumber;
  try {
    const event = JSON.parse(fs.readFileSync(eventPath, "utf8"));
    prNumber = event.pull_request?.number || event.number;
  } catch (e) {
    info("  [CI] Could not determine PR number from event path.");
    return;
  }

  if (!prNumber) {
    info("  [CI] Not a pull_request event. Skipping PR comment.");
    return;
  }

  const commentTag = "<!-- QAPAL_REPORT -->";
  const counts = { critical: 0, major: 0, medium: 0, minor: 0 };
  for (const issue of issues) {
    const sev = issue.severity?.toLowerCase();
    if (sev in counts) counts[sev]++;
  }

  const statusEmoji = issues.length === 0 ? "✅" : (counts.critical > 0 ? "❌" : "⚠️");
  const markdown = [
    `### ${statusEmoji} QAPAL Scan Results ${commentTag}`,
    "",
    "| Metric | Value |",
    "| :--- | :--- |",
    `| **Score** | ${report.score ?? "N/A"} |`,
    `| **Findings** | ${issues.length} |`,
    `| **Critical** | ${counts.critical} |`,
    `| **Major** | ${counts.major} |`,
    `| **Pages** | ${report.pages_crawled ?? 1} |`,
    "",
    report.narration ? `> 📝 ${report.narration}` : "",
    "",
    report.screenshot ? `![Failure Screenshot](${report.screenshot})` : "",
    "",
    `[Full Report](${process.env.GITHUB_SERVER_URL}/${repo}/actions/runs/${process.env.GITHUB_RUN_ID})`,
  ].join("\n");

  const apiUrl = `https://api.github.com/repos/${repo}/issues/${prNumber}/comments`;
  const commonHeaders = {
    Authorization: `token ${token}`,
    Accept: "application/vnd.github.v3+json",
    "User-Agent": "qapal-action",
  };

  try {
    // 1. Find existing comment
    const listRes = await request("GET", apiUrl, null, commonHeaders);
    const existing = Array.isArray(listRes.body) 
      ? listRes.body.find(c => c.body?.includes(commentTag)) 
      : null;

    if (existing) {
      info(`  [CI] Updating existing PR comment: ${existing.id}`);
      await request("PATCH", `https://api.github.com/repos/${repo}/issues/comments/${existing.id}`, 
        { body: markdown }, commonHeaders);
    } else {
      info(`  [CI] Creating new PR comment`);
      await request("POST", apiUrl, { body: markdown }, commonHeaders);
    }
  } catch (err) {
    warning(`Failed to post PR comment: ${err.message}`);
  }
}

// ── Main ────────────────────────────────────────────────────────────────────

async function run() {
  try {
    const url = getInput("url", true);
    const token = getInput("token", true);
    const testUser = getInput("test_user");
    const testPass = getInput("test_pass");
    const backendUrl = getInput("backend_url") || "https://api.qapal.dev";
    const failOn = (getInput("fail_on") || "major").toLowerCase();
    const prdPath = getInput("prd") || "";
    const pollInterval = parseInt(getInput("poll_interval") || "5", 10);
    const timeout = parseInt(getInput("timeout") || "300", 10);

    const failOnRank = rankOf(failOn);
    if (failOnRank === undefined) {
      setFailed(`Invalid fail_on value: "${failOn}". Must be one of: critical, major, medium, minor, none`);
      return;
    }

    info(`\n🔍 QAPAL Scan`);
    info(`   URL:       ${url}`);
    info(`   Fail on:   ${failOn} and above`);
    info(`   Backend:   ${backendUrl}`);
    if (prdPath) info(`   PRD spec:  ${prdPath}`);
    info("");

    // ── 1. Create job ──────────────────────────────────────────────────────
    startGroup("Creating scan job");
    const job = await createJob(backendUrl, token, url, prdPath);
    info(`  Job ID: ${job.id}`);
    info(`  Type:   ${prdPath ? "Deep Scan (AI behavioral)" : "Quick Scan"}`);
    endGroup();

    setOutput("job_id", job.id);

    // ── 2. Poll until complete ─────────────────────────────────────────────
    startGroup("Waiting for scan to complete");
    const completedJob = await pollJob(backendUrl, token, job.id, pollInterval, timeout);
    endGroup();

    // ── 3. Fetch report ────────────────────────────────────────────────────
    startGroup("Processing results");
    const report = await getReport(backendUrl, token, job.id);
    const issues = report.issues || [];

    setOutput("score", String(report.score ?? 0));
    setOutput("issues_count", String(issues.length));
    const critCount = issues.filter((i) => i.severity?.toLowerCase() === "critical").length;
    setOutput("critical_count", String(critCount));
    setOutput("report_url", `${backendUrl}/v1/jobs/${job.id}/report`);

    printSummaryTable(report, issues);
    await postPullRequestComment(report, issues);

    if (report.reproduce_test) {
      fs.writeFileSync("reproduce_test.ts", report.reproduce_test);
      info("  [Artifact] Playwright reproduction script saved to: reproduce_test.ts");
    }

    endGroup();

    // ── 4. Write annotations ───────────────────────────────────────────────
    if (issues.length > 0) {
      startGroup(`Scan findings (${issues.length} issues)`);
      annotateIssues(issues, failOnRank);
      endGroup();
    }

    // ── 5. Pass / fail (Task 4.4 & 4.5) ───────────────────────────────────
    const failingIssues = issues.filter((i) => rankOf(i.severity) >= failOnRank);

    if (failOnRank === 0) {
      info(`✅ QAPAL scan complete (fail_on: none — build always passes)`);
    } else if (failingIssues.length > 0) {
      setFailed(
        `QAPAL found ${failingIssues.length} issue(s) at "${failOn}" severity or above. ` +
          `Fix these before merging. Run QAPAL locally with: npx qapal-scan ${url}`
      );
    } else {
      info(`✅ QAPAL scan passed. No ${failOn}+ severity issues found.`);
    }
  } catch (err) {
    setFailed(`QAPAL action failed: ${err.message}`);
  }
}

run();
