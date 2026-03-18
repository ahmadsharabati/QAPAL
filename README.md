# QAPAL

**Locator intelligence engine for Playwright.**
Analyze, fix, and heal broken test selectors — automatically.

```
pip install qapal
playwright install chromium
```

---

## The Problem

Playwright tests break when selectors go stale. A renamed CSS class, a removed `data-testid`, a changed button label — and your entire CI pipeline goes red. Teams spend hours manually hunting down which selector broke and what to replace it with.

## The Solution

QAPAL probes your selectors against the live page, scores them by resilience, and replaces weak ones with validated alternatives. No AI in the loop at runtime. Pure locator resolution + scoring.

```bash
# Find every weak selector in your test suite
qapal analyze tests/ --url https://staging.myapp.com

# Auto-fix them
qapal fix tests/ --url https://staging.myapp.com --apply

# CI self-healing: tests fail -> QAPAL patches -> opens a PR
qapal heal --test-results results.json --url https://staging.myapp.com --pr
```

---

## Quick Start

### Analyze selector health

```bash
$ qapal analyze tests/login.spec.ts --url https://myapp.com/login

File                            Line  Type         Value                          Found      Grade
-------------------------------------------------------------------------------------------------
login.spec.ts                      9  placeholder  Email address                    YES [A - 0.83]
login.spec.ts                     10  placeholder  Password                         YES [A - 0.83]
login.spec.ts                     12  css          .btn-submit                      YES [B - 0.70]
login.spec.ts                     15  testid       nonexistent                       NO [F - 0.00]

--- Summary ---
Total: 4  |  Strong: 2  |  Weak: 1  |  Broken: 1
```

Grades: **A** (>0.8) rock-solid, **B** (>0.6) acceptable, **C** (>0.4) fragile, **D/F** replace immediately.

### Fix weak selectors

```bash
$ qapal fix tests/login.spec.ts --url https://myapp.com/login --dry-run

Found 1 selector replacement(s):

  login.spec.ts:12  page.locator(".btn-submit")  ->  page.getByRole("button", { name: "Sign In" })
                    [A - 0.88]  Replaced css with role (confidence: 0.88)

--- Diff Preview (--dry-run) ---

--- a/login.spec.ts
+++ b/login.spec.ts
@@ -9,7 +9,7 @@
   await page.getByPlaceholder('Email address').fill('user@test.com');
   await page.getByPlaceholder('Password').fill('secret');

-  await page.locator('.btn-submit').click();
+  await page.getByRole('button', { name: 'Sign In' }).click();
```

Happy with it? Apply:

```bash
qapal fix tests/login.spec.ts --url https://myapp.com/login --apply
```

Or send a PR directly:

```bash
qapal fix tests/ --url https://myapp.com --pr
```

### Probe a single selector

```bash
$ qapal probe "page.getByTestId('email')" --url https://myapp.com/login

Selector: page.getByTestId('email')
Type:     testid
Value:    email
Probing https://myapp.com/login...

Found:       YES
Count:       1
Visible:     True
Enabled:     True
In viewport: True
Confidence:  [A - 0.95]
Strategy:    testid
```

### Generate a test scaffold

```bash
$ qapal generate --url https://myapp.com/login --language python

Probing https://myapp.com/login...
Discovered 6 interactive elements.
Scaffold written to: tests/generated/test_login.py
```

Output:

```python
"""Auto-generated scaffold by QAPAL"""
from playwright.sync_api import Page, expect

# === Validated elements on https://myapp.com/login ===
#
# Textbox "Email"        -> page.get_by_test_id("email")           [A - 0.95]
# Textbox "Password"     -> page.get_by_test_id("password")        [A - 0.95]
# Button "Sign In"       -> page.get_by_role("button", name=...)   [A - 0.88]
# Link "Forgot password" -> page.get_by_role("link", name=...)     [B - 0.72]

def test_login(page: Page):
    page.goto("https://myapp.com/login", wait_until="domcontentloaded")

    # TODO: Write your test logic using the validated selectors above
    pass
```

### CI Self-Healing

```bash
# In your CI pipeline, after tests fail:
qapal heal --test-results results.json --url $STAGING_URL --pr
```

QAPAL reads the failure report, finds which selectors broke, probes for working alternatives, patches the files, and opens a PR.

---

## How It Works

QAPAL has a 4-step locator resolution chain:

```
1. DB chain lookup     (cached selectors from previous crawls)
2. Primary selector    (the one in your test file)
3. Fallback selector   (testid-prefix matching, OR-locator for testid variants)
4. AI rediscovery      (one-shot AI call using accessibility snapshot -- optional)
```

### Scoring Model

Each selector gets a confidence score (0.0 - 1.0) based on weighted factors:

| Factor | Weight | What it measures |
|--------|--------|-----------------|
| Strategy | 35% | `testid` > `role` > `text` > `css` |
| Uniqueness | 30% | Does it match exactly 1 element? |
| Visibility | 15% | Is the element visible and in viewport? |
| Interactability | 10% | Is the element enabled? |
| History | 10% | Past success/failure rate |

Strategy scores:

| Strategy | Score | Why |
|----------|-------|-----|
| `testid` | 1.0 | Explicit test contract, never changes accidentally |
| `id` | 0.9 | Stable but may conflict |
| `role` | 0.8 | Semantic, accessible, resilient |
| `aria-label` | 0.75 | Good but may be localized |
| `label` | 0.7 | Tied to form structure |
| `placeholder` | 0.65 | Can change with UX copy |
| `text` | 0.5 | Fragile to copy changes |
| `css` | 0.3 | Breaks on any style refactor |
| `xpath` | 0.2 | Breaks on any DOM change |

---

## CLI Reference

```
qapal analyze <files> --url <url> [--format table|json|github]
qapal fix     <files> --url <url> [--dry-run|--apply|--pr] [--min-confidence 0.8] [--ai-fallback]
qapal generate        --url <url> [--output dir] [--language python|typescript]
qapal probe   "<sel>" --url <url>
qapal heal    --test-results <json> --url <url> [--pr] [--ai-fallback]
```

### Global Options

| Flag | Description |
|------|-------------|
| `--headless` | Run browser headlessly (default) |
| `--headed` | Show browser window |
| `--device` | Playwright device preset (e.g. `"iPhone 12"`) |
| `--credentials-file` | JSON file with login credentials |
| `--timeout` | Action timeout in ms (default: 10000) |
| `--db-path` | Path to locator DB (default: `locators.json`) |
| `--ai-fallback` | Enable LLM inference for selectors with no semantic hint (requires `ANTHROPIC_API_KEY` or `OPENAI_API_KEY`) |

### `--ai-fallback` — LLM-Powered Recovery

By default QAPAL is **fully deterministic**: it only upgrades selectors it can semantically match against the live accessibility tree.
When a selector has completely degraded into an unreadable CSS path with zero accessible name, the deterministic engine correctly refuses to guess.

Pass `--ai-fallback` to unlock one-shot LLM inference for those cases:

```bash
qapal fix tests/ --url https://github.com --apply --ai-fallback
```

```
# Before (unreadable CSS path — deterministic engine refuses to touch it)
page.locator("qbsearch-input > div.search-input > button > span.flex-1")

# After --ai-fallback (LLM deduced intent from accessibility snapshot)
page.get_by_role("button", name="Search or jump to…")   [B — 0.79]
```

> **Safety contract:** `--ai-fallback` is opt-in and scoped to selectors the deterministic engine explicitly cannot resolve. It never overrides a selector that already has a confident match.

### GitHub Actions Output

```bash
qapal analyze tests/ --url $STAGING_URL --format github
```

Outputs GitHub-compatible annotations:

```
::error file=tests/login.spec.ts,line=19::Broken selector: page.getByTestId('nonexistent') - element not found
::warning file=tests/login.spec.ts,line=12::Weak selector: page.locator('.btn') (confidence: 0.30)
```

---

## GitHub Action

Add to your CI workflow:

```yaml
name: Playwright Tests + QAPAL Healing

on: [push, pull_request]

jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: '3.12'

      - name: Install dependencies
        run: |
          pip install qapal[ai]
          playwright install chromium --with-deps

      - name: Run Playwright tests
        id: tests
        continue-on-error: true
        run: |
          pytest tests/ --json-report --json-report-file=results.json

      - name: QAPAL Analyze
        if: always()
        run: |
          qapal analyze tests/ --url ${{ vars.STAGING_URL }} --format github

      - name: QAPAL Heal (on failure)
        if: steps.tests.outcome == 'failure'
        env:
          GH_TOKEN: ${{ secrets.GITHUB_TOKEN }}
        run: |
          qapal heal --test-results results.json --url ${{ vars.STAGING_URL }} --pr
```

---

## Authentication

For apps behind login, provide a credentials file:

```json
{
  "url": "https://myapp.com/login",
  "username": "test@example.com",
  "password": "testpass123",
  "username_field": "email",
  "password_field": "password",
  "submit_button": "sign-in"
}
```

```bash
qapal analyze tests/ --url https://myapp.com/dashboard --credentials-file creds.json
```

---

## Python + TypeScript

QAPAL parses both languages:

**Python** (`pytest-playwright`):
```python
page.get_by_test_id("email")
page.get_by_role("button", name="Submit")
page.locator(".css-selector")
```

**TypeScript** (`@playwright/test`):
```typescript
page.getByTestId('email')
page.getByRole('button', { name: 'Submit' })
page.locator('.css-selector')
```

Fixes are generated in the correct language for each file.

---

## Installation

```bash
pip install qapal
playwright install chromium
```

### Optional extras

```bash
pip install "qapal[ai]"       # AI rediscovery (anthropic + openai)
pip install "qapal[all]"      # Everything
```

### From source

```bash
git clone https://github.com/ahmadsharabati/QAPAL.git
cd QAPAL
pip install -e ".[dev]"
playwright install chromium
```

---

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `QAPAL_HEADLESS` | `true` | Run browser headlessly |
| `QAPAL_DB_PATH` | `locators.json` | Locator database path |
| `QAPAL_ACTION_TIMEOUT` | `10000` | Timeout per action (ms) |
| `QAPAL_AI_REDISCOVERY` | `true` | Enable AI fallback for missing locators |
| `QAPAL_AI_PROVIDER` | `anthropic` | AI provider: `anthropic`, `openai`, `grok` |
| `ANTHROPIC_API_KEY` | - | Required for AI rediscovery with Claude |
| `OPENAI_API_KEY` | - | Required for AI rediscovery with GPT-4 |

---

## Battle-Tested: Real-World Validation

The following results were produced against live, public websites to validate correctness and safety of the locator engine.

### ✅ TodoMVC — CSS → Semantic upgrade

```
Site: demo.playwright.dev/todomvc/
```

```python
# Before — brittle CSS class
page.locator(".new-todo").fill("Buy milk")

# After qapal fix --apply — semantic, resilient
page.get_by_role("textbox", name="What needs to be done?").fill("Buy milk")
```

Grade improvement: `[B — 0.70]` → `[A — 0.88]`

---

### ✅ SauceDemo — Auth-gated app

```
Site: saucedemo.com (login required)
```

| Selector | Before | After |
|----------|--------|-------|
| `.input_error.form_input` | `[F — 0.00]` broken | `page.get_by_test_id("username")` `[A — 0.95]` |
| `input[type='password']` | `[D — 0.35]` fragile | `page.get_by_test_id("password")` `[A — 0.95]` |
| `.btn_inventory` (post-login button, probed from homepage) | `[F — 0.00]` correctly refused | no false-positive patch generated |

QAPAL correctly identified that `.btn_inventory` is a **valid element but unreachable** from the homepage URL — it refused to patch rather than guess.

---

### ✅ BooksToScrape — Deep DOM hierarchy

```
Site: books.toscrape.com (multi-page, no auth)
```

```python
# Before — structurally fragile, breaks on any nav reorder
page.locator(".nav-list > li > ul > li:nth-child(3) > a")

# After — semantic link, stable across DOM changes
page.get_by_role("link", name="Historical Fiction")   [A — 0.88]
```

A downstream `#content_inner > article > p` content selector was left **untouched** — QAPAL understood it was outside the semantic domain of the probe URL and avoided false-positive remediation.

---

### 🛡️ Safety validation — GitHub & Wikipedia

To verify the engine does not hallucinate fixes, completely unreadable CSS paths with zero accessible names were tested:

```python
# GitHub — structurally opaque path
page.locator("qbsearch-input > div.search-input > button > span.flex-1")
# Result: [F — 0.00] — correctly refused to patch (no semantic hint)

# Wikipedia — positional path
page.locator("div#mw-content-text > div.mw-content-ltr > div > ul:nth-child(5) > li:nth-child(2) > a")
# Result: [F — 0.00] — correctly refused to patch (no semantic hint)
```

With `--ai-fallback` enabled, the LLM recovered both:

```python
# GitHub → page.get_by_role("button", name="Search or jump to…")   [B — 0.79]
# Wikipedia → page.get_by_role("link", name="County Cavan")         [B — 0.79]
```

This validates the safety contract: **determinism prevents false positives; `--ai-fallback` recovers what determinism deliberately ignores.**

---

## License

MIT
