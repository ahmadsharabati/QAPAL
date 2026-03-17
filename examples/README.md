# QAPAL — Usage Examples

Three ready-to-use GitHub Actions workflows. Copy the one that matches your stack into `.github/workflows/` and add the required secrets.

---

## Which workflow should I use?

| Your stack | File to copy |
|------------|-------------|
| TypeScript + Playwright | [`typescript-playwright.yml`](./typescript-playwright.yml) |
| Python + pytest-playwright | [`python-playwright.yml`](./python-playwright.yml) |
| Any stack — auto-heal on CI failure | [`self-heal-on-failure.yml`](./self-heal-on-failure.yml) |

---

## 1. TypeScript / Playwright — Selector Health Check

**File:** `typescript-playwright.yml`

**What it does:**
- Runs on every push/PR and weekly
- Scans all `tests/**/*.spec.ts` files
- Posts a selector health score as a PR comment
- Fails the job if any broken selectors are detected

**Setup:**
```
1. Copy typescript-playwright.yml → .github/workflows/
2. Add repo secret: APP_URL = https://your-staging-url.com
3. (Optional) Add CREDENTIALS_FILE_CONTENT = '{"email":"...","password":"..."}'
```

**PR comment looks like:**
```
🔴 QAPAL Selector Health Report
| Metric           | Count |
|------------------|-------|
| Total selectors  | 47    |
| 💪 Strong (A/B)  | 43    |
| ⚠️  Weak  (C/D)  | 2     |
| 💥 Broken (F)    | 2     |
| Health score     | 91%   |

⚡ Run `qapal fix tests/**/*.spec.ts --url $APP_URL --apply` to auto-fix 2 broken selector(s).
```

---

## 2. Python / Playwright — Selector Health Check

**File:** `python-playwright.yml`

**What it does:** Same as above but for Python test files (`tests/**/*.py`).

**Setup:**
```
1. Copy python-playwright.yml → .github/workflows/
2. Add repo secret: APP_URL = https://your-staging-url.com
```

---

## 3. Self-Healing CI — Full Auto-Heal Pipeline

**File:** `self-heal-on-failure.yml`

**What it does:**
- Phase 1: Runs your Playwright tests normally
- Phase 2: **Only if tests fail** — QAPAL probes broken selectors, generates patches, applies them, opens a PR with the fixes, then retries the tests
- The retry result is the final CI status — broken selectors never permanently block your pipeline

**Setup:**
```
1. Copy self-heal-on-failure.yml → .github/workflows/
2. Add repo secrets:
   - APP_URL = https://your-staging-url.com
   - GH_TOKEN = github PAT with contents:write + pull-requests:write
3. Adjust the "Run Playwright tests" step for your test command
```

**Flow:**
```
push → tests run → PASS ✅  →  done
                → FAIL ❌  →  QAPAL heals selectors
                              → patches applied
                              → PR opened: "fix: auto-heal N selectors"
                              → tests retry → PASS ✅
```

---

## Secrets Reference

| Secret | Required by | Description |
|--------|-------------|-------------|
| `APP_URL` | all | Base URL of your app (staging/preview) |
| `GH_TOKEN` | self-heal | GitHub PAT — `contents:write` + `pull-requests:write` |
| `CREDENTIALS_FILE_CONTENT` | optional | JSON with login credentials for auth-gated pages |

### Credentials file format
```json
{
  "email": "testuser@example.com",
  "password": "s3cr3t",
  "base_url": "https://staging.example.com"
}
```

---

## Local usage (same commands, no CI)

```bash
pip install qapal
playwright install chromium

# Analyze — see selector health report
qapal analyze tests/**/*.spec.ts --url https://staging.example.com

# Fix — apply patches automatically
qapal fix tests/**/*.spec.ts --url https://staging.example.com --apply

# Heal — fix selectors that caused test failures
qapal heal --test-results test-results/results.json --url https://staging.example.com

# Generate — scaffold new test file with pre-validated selectors
qapal generate --url https://staging.example.com --out tests/generated/
```
