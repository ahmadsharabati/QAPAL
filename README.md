# QAPal — AI-Powered Test Automation Framework

AI plans once. Code executes deterministically. No AI in the execution loop.

---

## How to Run

### 1. Install dependencies

```bash
pip install -r requirements.txt
playwright install chromium
```

### 2. Configure `.env`

```bash
# Claude (recommended)
QAPAL_AI_PROVIDER=anthropic
ANTHROPIC_API_KEY=sk-ant-...
QAPAL_AI_MODEL=claude-sonnet-4-6

# OR Groq (free tier, fast)
# QAPAL_AI_PROVIDER=openai
# QAPAL_AI_BASE_URL=https://api.groq.com/openai/v1
# OPENAI_API_KEY=gsk_...
# QAPAL_AI_MODEL=meta-llama/llama-4-scout-17b-16e-instruct
```

### 3. Create credentials file

```json
// toolbox_creds.json
{
  "url": "https://practicesoftwaretesting.com/auth/login",
  "username": "customer2@practicesoftwaretesting.com",
  "password": "welcome01"
}
```

### 4. Run commands

```bash
# Full pipeline: crawl → generate plans from PRD → execute all tests
python main.py prd-run --prd toolbox.md --url https://practicesoftwaretesting.com --credentials-file toolbox_creds.json

# Re-crawl (force fresh locators, e.g. after product ULIDs change)
python main.py prd-run --prd toolbox.md --url https://practicesoftwaretesting.com --credentials-file toolbox_creds.json --force

# Run existing plan files directly (skip regeneration)
python main.py run --plans plans/TC001_login_plan.json plans/TC002_add_to_cart_plan.json --credentials-file toolbox_creds.json

# Crawl only
python main.py crawl --urls https://practicesoftwaretesting.com/category/hand-tools --credentials-file toolbox_creds.json

# Check locator DB status
python main.py status
```

---

## Architecture

```
PRD (markdown)
     │
     ▼
[1] Crawler ──────────────────────► LocatorDB (locators.json / TinyDB)
     Playwright A11Y extraction           │
     DOM visibility check                 │
     StateGraph nav transitions           │
                                          ▼
[2] Generator ◄── PRD + locators + nav_graph
     AI call (temperature=0)              │
     _fix_url_assertions post-proc        │
     5 test plan JSONs                    │
                                          ▼
[3] Executor ─────────────────────────────┘
     Deterministic Playwright
     OR-locator (data-testid|data-test|data-cy|data-qa)
     scroll_into_view_if_needed
     Self-healing fallback selectors
     Zero AI calls during execution
```

### Key files

| File | Purpose |
|------|---------|
| `main.py` | CLI: `crawl`, `run`, `prd-run`, `status`, `graph` |
| `crawler.py` | Playwright A11Y crawler, writes to LocatorDB |
| `generator.py` | PRD → 5 JSON test plans via AI |
| `executor.py` | Deterministic test execution |
| `planner.py` | Manual test case → execution plan via AI |
| `state_graph.py` | Nav graph (URL transitions, reachable paths) |
| `locator_db.py` | TinyDB wrapper for element locators |
| `ai_client.py` | AI provider abstraction (Anthropic / OpenAI-compatible) |

---

## Plan File Format

```json
{
  "test_id": "TC001_login",
  "name": "User can log in",
  "steps": [
    { "action": "navigate", "url": "https://..." },
    { "action": "fill", "selector": {"strategy": "testid", "value": "email"}, "value": "user@test.com" },
    { "action": "click", "selector": {"strategy": "testid", "value": "login-submit"} }
  ],
  "assertions": [
    { "type": "url_contains", "value": "/account" }
  ]
}
```

### Selector strategies

| Strategy | Example |
|----------|---------|
| `testid` | `{"strategy": "testid", "value": "login-submit"}` — matches `data-testid`, `data-test`, `data-cy`, `data-qa` |
| `testid_prefix` | `{"strategy": "testid_prefix", "value": "product-", "index": 0}` — first element whose testid starts with prefix |
| `role` | `{"strategy": "role", "value": {"role": "button", "name": "Add to cart"}}` |
| `css` | `{"strategy": "css", "value": "form > button.primary"}` |

### Assertion types

`url_contains`, `url_equals`, `element_visible`, `element_exists`, `element_text_contains`, `element_text_equals`, `element_enabled`, `element_disabled`

### Step options

```json
{ "action": "fill", "selector": {...}, "value": "...", "timeout": 30000 }
```

---

## Recent Updates (Session Log)

### Executor (`executor.py`)
- **OR-locator for testid** — `data-testid | data-test | data-cy | data-qa` so any test-id convention works
- **Removed `set_test_id_attribute("data-test")`** — was globally breaking default Playwright testid resolution
- **`scroll_into_view_if_needed()`** before every interaction (click, fill, check) — eliminates viewport failures
- **`count = 0` init** before selector block — prevents `UnboundLocalError` crash
- **Fallback selector guard** — only returns fallback locator if `count >= 1` (clearer error on not-found)
- **Per-step timeout** — `timeout` field in step JSON is passed all the way to `_verify_actionable()`
- **Auto-coerce string `value` → testid selector** for `element_visible` assertions

### Generator (`generator.py`)
- **`temperature=0`** — same PRD produces identical plans every run
- **Nav graph `min_count=2`** — filters one-off noise edges, keeps reliable transitions only
- **Landing URL lookup** — uses `_SUBMIT_LABELS` set to find correct post-login URL (not admin redirects)
- **Rule 12 strengthened** — AI must not assert a URL that has no outgoing nav-graph edge
- **RULE D added** — product links must use testid, never role+name
- **NOT ACTIONABLE filter** — locators with `actionable=False` excluded from AI prompt
- **`_fix_url_assertions()` post-processor** — simulates URL state through steps, auto-corrects wrong URL assertions
- **`max_tokens=4096`** — reduced from 8192 to respect Groq free-tier limit

### Planner (`planner.py`)
- **Chain-of-thought prompt** — AI writes reasoning before JSON (STEP 1 / STEP 2 format)
- **Retry loop (3 attempts)** — detects hallucinated `element_id` values, feeds bad IDs back, retries
- **Improved `_parse_plan`** — regex `json` block extraction, skips `navigate` steps in ID validation

### Crawler (`crawler.py`)
- **DOM visibility check** — `el.offsetWidth > 0 && el.offsetHeight > 0` in A11Y JS; mobile-only hidden buttons get `actionable=False`

### State Graph (`state_graph.py`)
- **`min_count` filter** in `format_for_prompt()` — removes noise transitions
- **Filters `navigate` trigger actions** — test-runner noise, not real user interactions
- **Prefix URL matching** — `url.startswith(base + "/")` instead of exact match
- **Reachable paths** — capped at 12, `/admin/*` skipped

### LocatorDB (`locator_db.py`)
- **Upsert guard fix** — allows updating existing records to `actionable=False` on re-crawl (previously blocked all non-actionable writes)

### AI Client (`ai_client.py`)
- **Default model updated** — `claude-sonnet-4-6` (was `claude-sonnet-4-5`)
- **`temperature=0`** added to all `complete()` / `acomplete()` signatures

---

## Known Bugs & Limitations

### 1. Account lockout (practicesoftwaretesting.com)
The demo site locks accounts after ~5 failed login attempts. Current working account: `customer2@practicesoftwaretesting.com / welcome01`. If locked, rotate to `customer@` (and back when it unlocks). **Never run `prd-run --force` in a loop with wrong credentials.**

### 2. TC004 checkout — stateful flow (IN PROGRESS)
The checkout test requires: login → add-to-cart → nav-cart → proceed-1 → proceed-2 → fill billing → proceed-3 → finish. AI-generated plans navigate directly to `/checkout` (empty cart). The manually-written `plans/TC004_checkout_plan.json` has the correct flow but `prd-run` overwrites it on each generation. **Workaround: use `run` command with existing plan files.**

### 3. Product ULIDs change periodically
Product URLs contain ULIDs (`/product/01KK...`) that change when the site resets. When `testid_prefix` tests fail, re-crawl category pages with `--force`.

### 4. Country field is a textbox, not a select
The checkout country field (`data-test="country"`) is an Angular autocomplete textbox, not a `<select>`. Use `action: fill` not `action: select`.

### 5. Checkout wizard step numbering
The 4-step Angular checkout wizard:
- `proceed-1` → confirms cart (step 1)
- `proceed-2` → proceeds past sign-in (step 2, even when already logged in)
- `proceed-3` → submits billing address (step 3)
- `finish` → confirms payment (step 4)

### 6. `prd-run` always regenerates plans
`prd-run` overwrites plan JSON files on every run. Manually edited plans are lost. **Use `run --plans` to execute specific plan files without regenerating.**

---

## Current Test Status (practicesoftwaretesting.com)

Run with:
```bash
python main.py run \
  --plans plans/TC001_login_plan.json \
          plans/TC002_add_to_cart_plan.json \
          plans/TC003_register_plan.json \
          plans/TC004_checkout_plan.json \
          plans/TC005_product_info_plan.json \
  --credentials-file toolbox_creds.json
```

| Test | Status | Notes |
|------|--------|-------|
| TC001_login | ✓ pass | login → assert url=/account |
| TC002_add_to_cart | ✓ pass | hand-tools → product → add-to-cart |
| TC003_register | ✓ pass | register form → assert url=/auth/register |
| TC004_checkout | ✗ in-progress | Full flow in manual plan; proceed-1→2→3→finish |
| TC005_product_info | ✓ pass | hand-tools → product → assert url=/product/ + add-to-cart visible |

---

## Environment Variables Reference

| Variable | Required | Description |
|----------|----------|-------------|
| `QAPAL_AI_PROVIDER` | yes | `anthropic` \| `openai` \| `grok` |
| `ANTHROPIC_API_KEY` | if anthropic | Anthropic API key |
| `OPENAI_API_KEY` | if openai/grok | OpenAI or Groq API key |
| `QAPAL_AI_MODEL` | no | Override model (default: `claude-sonnet-4-6`) |
| `QAPAL_AI_BASE_URL` | no | Custom OpenAI-compatible endpoint (e.g. Groq) |
