# QAPAL — Full Architecture & Design Reference

> **Core principle:** AI plans once (single call). Code executes deterministically. No AI during execution. Results are reproducible and cheap.

---

## Table of Contents

1. [System Overview](#1-system-overview)
2. [High-Level Data Flow](#2-high-level-data-flow)
3. [Directory Structure](#3-directory-structure)
4. [Module Reference](#4-module-reference)
   - 4.1 [main.py — CLI Orchestrator](#41-mainpy--cli-orchestrator)
   - 4.2 [crawler.py — Element Extraction Engine](#42-crawlerpy--element-extraction-engine)
   - 4.3 [locator_db.py — Locator Registry](#43-locator_dbpy--locator-registry)
   - 4.4 [semantic_extractor.py — Page Understanding](#44-semantic_extractorpy--page-understanding)
   - 4.5 [state_graph.py — Navigation Graph](#45-state_graphpy--navigation-graph)
   - 4.6 [planner.py — Test Plan Generation](#46-plannerpy--test-plan-generation)
   - 4.7 [generator.py — PRD-to-Plan Generator](#47-generatorpy--prd-to-plan-generator)
   - 4.8 [executor.py — Deterministic Test Runner](#48-executorpy--deterministic-test-runner)
   - 4.9 [replanner.py — Self-Healing Recovery](#49-replannerpy--self-healing-recovery)
   - 4.10 [ai_client.py — Provider Abstraction](#410-ai_clientpy--provider-abstraction)
   - 4.11 [actions.py — Action Registry](#411-actionspy--action-registry)
   - 4.12 [assertions.py — Assertion Registry](#412-assertionspy--assertion-registry)
5. [Database Schema](#5-database-schema)
6. [Locator Resolution Strategy](#6-locator-resolution-strategy)
7. [Plan JSON Format](#7-plan-json-format)
8. [End-to-End Execution Walkthrough](#8-end-to-end-execution-walkthrough)
9. [CLI Commands Reference](#9-cli-commands-reference)
10. [Environment Variables](#10-environment-variables)
11. [Key Design Decisions](#11-key-design-decisions)
12. [Credential File Format](#12-credential-file-format)

---

## 1. System Overview

QAPAL is an AI-powered UI test automation framework. It ingests a human-readable Product Requirements Document (PRD) and a live URL, then crawls the app, generates frozen execution plans via one AI call, and runs those plans deterministically with Playwright — with no further AI calls during execution.

```
┌─────────────────────────────────────────────────────────────────┐
│                         QAPAL Pipeline                          │
│                                                                 │
│  PRD (Markdown)          Live App URL                           │
│       │                       │                                 │
│       │              ┌────────▼────────┐                        │
│       │              │    CRAWLER      │  Playwright + JS       │
│       │              │  (A11y + DOM)   │  locator extraction    │
│       │              └────────┬────────┘                        │
│       │                       │                                 │
│       │              ┌────────▼────────┐                        │
│       │              │  LOCATOR DB     │  TinyDB, 5 tables      │
│       │              │ (locators.json) │  elements, sessions,   │
│       │              └────────┬────────┘  states, transitions   │
│       │                       │                                 │
│       └──────────┐   ┌────────▼────────┐                        │
│                  │   │    SEMANTIC     │  Page understanding,   │
│                  │   │   EXTRACTOR     │  a11y tree snapshots   │
│                  │   └────────┬────────┘                        │
│                  │            │                                 │
│                  │   ┌────────▼────────┐                        │
│                  │   │  STATE GRAPH    │  BFS nav graph,        │
│                  │   │                 │  observed transitions  │
│                  │   └────────┬────────┘                        │
│                  │            │                                 │
│          ┌───────▼────────────▼────────┐                        │
│          │        PLANNER / GENERATOR  │  ONE AI call           │
│          │  PRD + locators + context   │  per test case         │
│          └───────────────┬────────────┘                         │
│                          │                                      │
│                 ┌────────▼────────┐                             │
│                 │  EXECUTION PLAN │  Frozen JSON                │
│                 │  (plans/*.json) │  selectors + assertions     │
│                 └────────┬────────┘                             │
│                          │                                      │
│                 ┌────────▼────────┐                             │
│                 │    EXECUTOR     │  Zero AI calls              │
│                 │  (Playwright)   │  deterministic steps        │
│                 └────────┬────────┘                             │
│                          │    ↑ LocatorNotFound?                │
│                          │    └── REPLANNER (1 AI call max)     │
│                          │                                      │
│                 ┌────────▼────────┐                             │
│                 │    RESULTS      │  pass/fail + screenshots    │
│                 │  reports/*.json │                             │
│                 └─────────────────┘                             │
└─────────────────────────────────────────────────────────────────┘
```

---

## 2. High-Level Data Flow

```
User
 │
 ├─ python main.py prd-run --prd toolbox.md --url https://app.com
 │
 ▼
[1] CRAWL
    Playwright launches headless Chromium.
    For each URL:
      - Navigate, wait for networkidle + DOM settle
      - Inject A11y JS extractor → collect role/name/testid/container/domPath
      - Inject DOM fallback JS → capture onclick, tabindex, data-* elements
      - Recursively crawl accessible iframes
      - Upsert all elements into locator_db (5 tables)
      - If --spider: BFS link-following up to --depth N, --max-pages M
    Save auth session (cookies + storage_state) per domain.

[2] SEMANTIC EXTRACTION
    For each discovered URL:
      - Navigate page
      - Call Playwright accessibility.snapshot() (or Crawl4AI if installed)
      - Extract: page title, buttons, links, headings, forms, tables
      - Compute DOM hash (SHA256 of page HTML)
      - Save semantic_context to states table

[3] PLAN GENERATION  (1 AI call per test case)
    Read PRD markdown.
    Build prompt with:
      - PRD content
      - Available locators (filtered by URL, sorted by confidence + hit_count)
      - Semantic context (per page)
      - Navigation graph (observed transitions)
    AI responds with array of frozen test plans (JSON).
    Validate: all element_ids must exist in DB.
    Retry up to 3x if AI invents unknown IDs.
    Save plans to plans/ directory.

[4] EXECUTION  (zero AI calls)
    For each plan:
      - Init browser context (restore session if saved)
      - For each step:
          Resolve primary selector → Playwright locator
          If not found → try fallback selector
          If not found → call REPLANNER (1 AI call, once per test)
          If still not found → screenshot + fail
          Execute action (click, fill, navigate, ...)
          On URL change: record transition in state_graph, crawl new page
      - For each assertion:
          Evaluate against live page
          Collect pass/fail + reason
      - Save results JSON + screenshots

[5] REPORT
    Aggregate: total passed, failed, skipped
    Write reports/<timestamp>_results.json
    Write reports/screenshots/ on failures
```

---

## 3. Directory Structure

```
QAPAL/
├── main.py                  # CLI entry point — 8 commands
├── crawler.py               # Playwright-based locator extraction
├── locator_db.py            # TinyDB element registry (5 tables)
├── semantic_extractor.py    # Page understanding via a11y tree
├── state_graph.py           # Directed page-transition graph
├── planner.py               # Test case → frozen plan (1 AI call)
├── generator.py             # PRD markdown → array of plans (1 AI call)
├── executor.py              # Deterministic step execution (0 AI calls)
├── replanner.py             # Self-healing recovery (1 AI call max)
├── ai_client.py             # Unified Anthropic / OpenAI / xAI interface
├── actions.py               # Action type registry + validation
├── assertions.py            # Assertion type registry + validation
│
├── locators.json            # TinyDB database (auto-created)
│
├── plans/                   # Generated execution plans
│   └── TC001_login_plan.json
│
├── reports/                 # Execution results
│   ├── screenshots/         # Failure screenshots
│   └── run_*.json
│
├── toolbox.md               # Example PRD
├── toolbox_creds.json       # Example credentials file
├── sample_prd.md
├── .env.example             # Environment variable template
│
└── ARCHITECTURE.md          # This file
```

---

## 4. Module Reference

### 4.1 `main.py` — CLI Orchestrator

Parses CLI arguments, wires modules together, and calls them in sequence.

**Commands:**

| Command | What it does |
|---|---|
| `crawl` | Navigate URLs, extract all locators into DB |
| `plan` | Generate execution plans from test-case JSON files |
| `run` | Execute plan files or test-case JSON files |
| `prd-run` | Full pipeline: crawl → generate plans from PRD → execute |
| `semantic` | Extract and store semantic context only (no crawl) |
| `graph-crawl` | Navigate site and record page transitions into state graph |
| `graph` | Query and display the page transition graph |
| `status` | Show DB stats and AI client config |

**Key helpers:**

```python
_load_credentials(args) -> Optional[dict]
    # Reads credentials JSON file, validates required keys

_get_ai_client() -> Optional[AIClient]
    # Builds AIClient from env vars, warns if missing

_load_json_files(patterns) -> List[dict]
    # Loads and merges test-case JSON files (supports glob)
```

**`prd-run` orchestration sequence** (`cmd_prd_run`):

```
1. Crawl URLs → bulk_crawl() or spider_crawl()
2. Semantic extraction → _extract_semantics()
3. Generate plans → generator.generate_plans_from_prd()
4. Execute each plan → Executor.run_plan()
5. Print summary
```

---

### 4.2 `crawler.py` — Element Extraction Engine

Launches Playwright, navigates pages, extracts interactive elements, and persists them to `locator_db`.

#### Three-tier collection strategy

**Tier 1: A11y tree (primary)**

JavaScript is injected into the live page. For every element with a valid ARIA role:

```
role        → button, link, textbox, checkbox, combobox, listbox, ...
name        → aria-label > aria-labelledby > placeholder > button text > title
testid      → data-testid | data-cy | data-qa | data-test
container   → nearest landmark ancestor (dialog, main, nav, form, section, aside)
domPath     → "main>form:nth(1)>input:nth(1)" for repeated elements
actionable  → true if testid OR (role+name+visible+size>0)
```

**Tier 2: DOM fallback**

Catches elements the a11y tree misses:
- Custom click handlers: `[onclick]`
- Keyboard-navigable divs: `[tabindex="0"]`
- Any element with `data-*` attributes not already captured

**Tier 3: iframe recursion**

For each accessible iframe:
- Navigate to `contentDocument`
- Re-run Tier 1 + Tier 2
- Tag elements with `frame.url` so the executor can switch frame context
- Skip cross-origin iframes (security boundary)

#### Session management

```python
_build_context(browser, db, url, credentials)
```

1. Check `sessions` table for saved `storage_state` for this domain
2. If found: restore → `browser.new_context(storage_state=...)`
3. If not found and credentials provided:
   - `page.goto(credentials["url"])` — navigate to login page
   - Auto-detect: `input[type=email]`, `input[type=password]`, `button[type=submit]`
   - Or use explicit selectors from credentials file
   - Save `storage_state` back to DB after successful login

#### Spider crawl

BFS from entry URLs. Deduplicates by normalising dynamic URL segments:
- `/product/01JFAB-ULID` → `/product/{id}` (ULID detection)
- `/user/42/edit` → `/user/{id}/edit` (numeric ID detection)
- `/blog/2026-03-13/post` → `/blog/{date}/post` (date detection)

This prevents crawling thousands of product pages when structure is identical.

#### Key functions

| Function | Description |
|---|---|
| `crawl_page(page, url, db)` | Single-page crawl + DB upsert |
| `Crawler.bulk_crawl(urls, force)` | Concurrent multi-page crawl |
| `Crawler.spider_crawl(urls, depth, max_pages)` | BFS with URL dedup |
| `wait_for_stable(page, timeout)` | `networkidle` + MutationObserver + 200ms flush |
| `_find_selector(page, candidates)` | Returns first visible selector from list |
| `_run_login(page, credentials)` | Auto-login using credentials dict |

---

### 4.3 `locator_db.py` — Locator Registry

TinyDB-backed, thread-safe element registry. All data stored in a single JSON file with five logical tables.

#### Tables

| Table | Purpose |
|---|---|
| `locators` | One document per interactive element per URL |
| `pages` | Metadata per crawled URL (last_crawled, element_count) |
| `sessions` | Auth state (cookies + storage_state) per domain |
| `states` | Semantic context snapshots per URL |
| `transitions` | Directed edges in the page navigation graph |

#### Identity key

Each element is identified by a SHA256 hash of:

```
url | role | normalised_name | container | frame_url | dom_path
```

The `normalised_name` strips dynamic content before hashing:
- Prices: `"Add $99.99 to cart"` → `"Add to cart"`
- Dates: `"Order placed 13 Mar"` → `"Order placed"`
- Counters: `"3 items in cart"` → `"items in cart"`
- ULIDs/UUIDs: removed from names

The raw name is stored separately alongside a `name_pattern` (regex) to match the element back when the value changes.

#### Locator chain (priority order)

```
1. testid      → [data-testid="..."]           highest confidence
2. role+name   → role="button" name="Submit"   verified at runtime
3. role+name+container → scoped to landmark
4. aria-label  → [aria-label="..."]
5. placeholder → [placeholder="..."]           textboxes only
6. css/id      → "#submit-btn"                 lowest confidence
```

#### Soft decay

On re-crawl, if an element is no longer found:
- Increment `miss_count`
- At `MISS_THRESHOLD` (default 3): mark `valid=false`
- Element stays in DB for historical reference / replanning

Hit count is incremented each time the executor successfully uses a locator.

#### Thread safety

All reads and writes pass through `threading.RLock`. `CachingMiddleware` batches disk writes for performance.

---

### 4.4 `semantic_extractor.py` — Page Understanding

Produces a structured description of each page for inclusion in AI prompts.

**Two extraction methods (in preference order):**

1. **Crawl4AI** (if installed): richer markdown extraction, link/table parsing
2. **Playwright a11y snapshot** (always available): fallback, reliable

**Output per page:**

```json
{
  "page": "Login",
  "description": "Login page for the Toolshop application",
  "buttons": ["Sign In", "Forgot Password"],
  "links": ["/register", "/forgot-password"],
  "tables": [],
  "forms": ["Login Form"],
  "headings": ["Welcome back", "Sign In"]
}
```

**DOM hash:** `SHA256(page_html)[:16]`

Used to detect state changes (modal appears, section loads). The semantic extractor only re-runs if the hash has changed since the last snapshot.

**Key functions:**

```python
extract_semantic_context(page, url) -> dict
    # Returns semantic context dict for one page

compute_dom_hash(page) -> str
    # Returns 16-char hex hash of current DOM
```

---

### 4.5 `state_graph.py` — Navigation Graph

Directed graph where nodes are URLs and edges are observed page transitions.

**Built automatically** during execution: whenever the executor detects a URL change after a step, it records the edge.

**Edge document:**

```
from_url + to_url + trigger_action + trigger_label → unique edge
traversal_count incremented on repeat observations
```

**Path-finding (BFS):**

```python
sg.get_path(from_url, to_url) -> List[Edge] | None
```

Returns shortest observed path between two pages, or `None` if unreachable.

**Formatted for AI prompts:**

```
Known page transitions (from observed test runs):
  /auth/login --[click "Sign In"]--> /account  (5x)
  /account --[click "My Orders"]--> /account/orders  (3x)

Reachable from /auth/login:
  /auth/login → /account → /account/orders
    via: click "Sign In" → click "My Orders"
```

**Key methods:**

```python
sg.record_transition(from_url, to_url, trigger, label, session_id)
sg.get_path(from_url, to_url) -> Optional[List[Edge]]
sg.get_reachable(from_url) -> List[str]
sg.format_for_prompt(entry_url) -> str
sg.stats() -> dict
```

---

### 4.6 `planner.py` — Test Plan Generation

Converts a single human-written test case into a frozen execution plan via **one AI call**.

#### Inputs to the AI prompt

```
SYSTEM PROMPT
  Rules:
  - Only use element_ids that exist in the Available Locators list
  - Never invent testids not present in DB
  - Always use absolute URLs for navigate actions
  - Fill ALL form fields, not just mandatory ones
  - For hidden elements, first click the trigger to reveal them
  - Prefer testid > role+name > css

USER PROMPT
  Test case: <title, steps, assertions (human language)>

  Available locators (filtered to relevant URLs):
  [id=a3f92b] textbox "Email" @/auth/login
    chain: testid=email-input | role=textbox name=Email
  [id=b7c21d] button "Sign In" @/auth/login
    chain: testid=sign-in-btn | role=button name=Sign In
  ...

  Semantic context:
  /auth/login: Login Form [buttons: Sign In, Forgot Password]

  Navigation graph:
  /auth/login --[click Sign In]--> /account (3x observed)
```

#### Output

A JSON object with `steps` array and `assertions` array.

#### Validation & retry

The planner validates that every `element_id` in the AI's response actually exists in the DB. If any are hallucinated, it retries the AI call (up to 3 times) with an error hint added to the prompt.

#### Caching

Plans are cached by `test_id`. Re-running the same test case hits the plan file on disk and skips the AI call entirely.

#### Key methods

```python
Planner.create_plan(test_case, cache_key) -> dict
    # One AI call, returns plan dict

_format_locators(locators, max_items, group_by_url) -> str
_prune_list_items(locators) -> List[Locator]
    # Collapses repeated product/row items into one template hint
_format_semantic_contexts(states) -> str
_parse_plan(text, test_id, locator_map) -> dict
    # Extracts JSON from AI response, validates element_ids
```

---

### 4.7 `generator.py` — PRD-to-Plan Generator

Reads a full PRD markdown file and generates **all test plans in one AI call**.

#### Workflow

```
1. Read PRD text
2. Load all locators from DB (filtered, deduped)
3. Load semantic contexts for relevant URLs
4. Load state graph (nav paths)
5. Build combined prompt:
     "Here is a PRD. Generate a comprehensive test suite as JSON array."
6. One AI call
7. Parse JSON array of plans
8. Save each plan to plans/ directory
9. Return plans list for executor
```

#### Key rules enforced via system prompt

- NEVER navigate to URLs with invented ULIDs or UUIDs
- Use only element_ids from the provided locator list
- Generate tests for: happy paths, boundary conditions, error states
- Each test must be independently executable (no shared state)
- Authentication steps should use the provided credentials

---

### 4.8 `executor.py` — Deterministic Test Runner

Runs a frozen plan file against a live browser. **Never calls AI during normal execution.**

#### Step execution loop

```python
for i, step in enumerate(plan["steps"]):

    action = step["action"]

    if action == "navigate":
        page.goto(step["url"])
        wait_for_stable(page)

    else:
        # 1. Resolve selector
        locator = resolve(step["selector"], step.get("fallback"), step.get("element_id"))

        # 2. Execute action
        dispatch_action(page, action, locator, step)

    # 3. Record navigation (if URL changed)
    new_url = page.url
    if new_url != current_url:
        state_graph.record_transition(current_url, new_url, action, label)
        crawl_page(page, new_url, db)   # update locators for new page
        current_url = new_url
```

#### Selector resolution chain

```
resolve(primary, fallback, element_id):
    try:
        loc = build_playwright_locator(page, primary)
        if loc.is_visible(timeout=5000):
            db.increment_hit(element_id)
            return loc
    except:
        pass

    try:
        loc = build_playwright_locator(page, fallback)
        if loc.is_visible(timeout=5000):
            return loc
    except:
        pass

    # Last resort: 1 AI call via replanner
    if replan_count < 1:
        new_steps = replanner.replan(page, failed_step, remaining_steps, db)
        splice new_steps into execution queue
        replan_count += 1
        continue

    # Hard fail
    take_screenshot(page, f"step_{i}_fail.png")
    raise StepExecutionError(f"Could not resolve selector for step {i}")
```

#### Assertion execution

```python
for assertion in plan["assertions"]:
    atype = assertion["type"]

    if atype == "url_contains":
        passed = assertion["value"] in page.url

    elif atype == "element_visible":
        loc = resolve(assertion["selector"], ...)
        passed = loc.is_visible()

    elif atype == "element_text_equals":
        loc = resolve(assertion["selector"], ...)
        passed = loc.inner_text() == assertion["expected"]

    # ... (20+ assertion types, see assertions.py)

    results.append({
        "type": atype,
        "status": "pass" if passed else "fail",
        "expected": assertion.get("expected"),
        "actual": actual_value,
        "reason": error_message if not passed else None
    })
```

#### Result structure

```json
{
  "test_id": "TC001",
  "test_name": "User can log in with valid credentials",
  "status": "pass",
  "duration_ms": 4231,
  "steps": [
    {"action": "navigate", "status": "pass", "url": "https://..."},
    {"action": "fill", "status": "pass", "selector": {}, "value": "user@test.com"}
  ],
  "assertions": [
    {"type": "url_contains", "status": "pass", "expected": "/account"}
  ],
  "screenshots": [],
  "replan_count": 0
}
```

---

### 4.9 `replanner.py` — Self-Healing Recovery

Called only when both the primary and fallback selectors fail to locate an element.

#### Recovery flow

```
1. Extract a11y snapshot of the current live page
2. Build prompt:
     "I was executing step N: [click button 'Add to Cart'].
      The selector failed. Here is the current page state:
      [semantic snapshot]
      Available locators at this URL:
      [locator list]
      Remaining steps to complete:
      [steps N+1 ... end]
      Goal (assertions):
      [assertion list]
      Return replacement steps to complete the test."
3. One AI call
4. Parse replacement step array
5. Executor splices replacement into remaining steps
6. Continue execution
```

**Hard cap:** `replan_count` is checked before each replan. Maximum **1 replan per test**. If recovery also fails, the test is marked `FAIL` with a screenshot.

---

### 4.10 `ai_client.py` — Provider Abstraction

Unified interface for three AI providers. Chosen via `QAPAL_AI_PROVIDER` env var.

```
QAPAL_AI_PROVIDER=anthropic  → _AnthropicClient (claude-sonnet-4-5 default)
QAPAL_AI_PROVIDER=openai     → _OpenAIClient    (gpt-4o-mini default)
QAPAL_AI_PROVIDER=grok       → _OpenAIClient    (grok-2-latest, api.x.ai/v1)
```

Custom endpoint (e.g. Groq, local Ollama, Azure OpenAI):

```
QAPAL_AI_PROVIDER=openai
QAPAL_AI_BASE_URL=https://api.groq.com/openai/v1
QAPAL_AI_MODEL=meta-llama/llama-4-scout-17b-16e-instruct
OPENAI_API_KEY=gsk_...
```

**API surface:**

```python
client = AIClient.from_env()

# Synchronous
response: str = client.complete(
    prompt="...",
    system_prompt="...",
    max_tokens=4096,
    temperature=0.0
)

# Asynchronous (runs sync client in thread executor)
response: str = await client.acomplete(...)
```

---

### 4.11 `actions.py` — Action Registry

Defines 35+ action types as Python dataclasses. Used for:
- AI prompt documentation (what actions are available and how to use them)
- Runtime validation of plan steps
- JSON Schema export for tooling

**Action categories:**

| Category | Actions |
|---|---|
| Navigation | `navigate`, `refresh`, `go_back`, `go_forward` |
| Interaction | `click`, `dblclick`, `hover`, `scroll` |
| Input | `fill`, `type`, `clear`, `press`, `select_option` |
| State | `check`, `uncheck`, `focus`, `blur` |
| Wait | `wait` (ms delay or selector) |
| Utility | `screenshot`, `evaluate` (JS) |

**Validation:**

```python
is_valid, errors = validate_action(step_dict)
is_valid, errors = validate_selector(selector_dict)
```

---

### 4.12 `assertions.py` — Assertion Registry

Defines 20+ assertion types. Covers:

| Category | Assertions |
|---|---|
| URL | `url_equals`, `url_contains`, `url_matches` |
| Page | `title_equals`, `title_contains` |
| Existence | `element_exists`, `element_not_exists` |
| Visibility | `element_visible`, `element_hidden` |
| State | `element_enabled`, `element_disabled`, `element_checked`, `element_unchecked`, `element_focused`, `element_editable` |
| Content | `element_text_equals`, `element_text_contains`, `element_value_equals`, `element_value_contains`, `element_count` |
| Attribute | `element_attribute_equals`, `element_has_class`, `element_has_style` |
| Position | `element_in_viewport` |
| Custom | `javascript` (arbitrary JS returning bool) |

---

## 5. Database Schema

Single file: `locators.json` (TinyDB format, readable JSON).

### `locators` table

```json
{
  "id": "a3f92b1c",
  "url": "https://practicesoftwaretesting.com/#/auth/login",
  "identity": {
    "role": "textbox",
    "name": "Email address",
    "name_pattern": null,
    "tag": "input",
    "container": "form",
    "dom_path": "main>form:nth(0)>input:nth(0)",
    "frame": {
      "type": "main",
      "url": "main",
      "name": "",
      "cross_origin": false,
      "accessible": true
    }
  },
  "locators": {
    "chain": [
      { "strategy": "testid",    "value": "email",                                        "unique": true  },
      { "strategy": "role",      "value": { "role": "textbox", "name": "Email address" }, "unique": null  },
      { "strategy": "role+container", "value": { "role": "textbox", "name": "Email address", "container": "form" }, "unique": null }
    ],
    "confidence": "high",
    "source": "a11y",
    "actionable": true
  },
  "history": {
    "first_seen": "2026-03-13T10:00:00Z",
    "last_seen":  "2026-03-13T10:00:00Z",
    "hit_count":  0,
    "miss_count": 0,
    "valid": true
  },
  "previous_locators": [],
  "warnings": []
}
```

### `pages` table

```json
{
  "url": "https://practicesoftwaretesting.com/#/auth/login",
  "domain": "practicesoftwaretesting.com",
  "last_crawled": "2026-03-13T10:00:00Z",
  "element_count": 12
}
```

### `sessions` table

```json
{
  "domain": "practicesoftwaretesting.com",
  "auth_type": "credentials",
  "cookies": [{ "name": "token", "value": "...", "domain": "..." }],
  "storage_state": {
    "cookies": [...],
    "origins": [{ "origin": "https://...", "localStorage": [...] }]
  },
  "saved_at": "2026-03-13T10:00:00Z"
}
```

### `states` table

```json
{
  "url": "https://practicesoftwaretesting.com/#/auth/login",
  "dom_hash": "a3f92b1c4d5e6f70",
  "semantic_context": {
    "page": "Login",
    "description": "Sign in to your account",
    "buttons": ["Sign in", "Forgot Password?"],
    "links": ["/register"],
    "tables": [],
    "forms": ["Login Form"],
    "headings": ["Sign in"]
  },
  "updated_at": "2026-03-13T10:00:00Z"
}
```

### `transitions` table

```json
{
  "id": "edge123abc",
  "from_url": "https://practicesoftwaretesting.com/#/auth/login",
  "to_url":   "https://practicesoftwaretesting.com/#/account",
  "trigger": {
    "action": "click",
    "label": "Sign in",
    "selector": { "strategy": "role", "value": { "role": "button", "name": "Sign in" } }
  },
  "traversal_count": 3,
  "first_seen": "2026-03-13T10:00:00Z",
  "last_seen":  "2026-03-13T11:00:00Z",
  "session_ids": ["run-001", "run-002"]
}
```

---

## 6. Locator Resolution Strategy

When the executor resolves a selector from a plan step, it uses this strategy mapping:

| `strategy` | Playwright expression | Example |
|---|---|---|
| `testid` | `page.get_by_test_id("...")` | `[data-testid="sign-in-btn"]` |
| `role` | `page.get_by_role(role, name="...")` | `button[name="Sign in"]` |
| `role+container` | `page.locator(container).get_by_role(...)` | Inside `form` → role button |
| `label` | `page.get_by_label("...")` | `<label>Email</label> + input` |
| `placeholder` | `page.get_by_placeholder("...")` | `<input placeholder="Email">` |
| `text` | `page.get_by_text("...")` | Any element with text |
| `aria-label` | `page.locator("[aria-label='...']")` | Hidden icon buttons |
| `css` | `page.locator("...")` | CSS selector, low priority |

**Resolution order within a step:**

```
1. primary selector (from plan)
2. fallback selector (from plan)
3. replanner AI call (1x max per test)
4. hard fail + screenshot
```

---

## 7. Plan JSON Format

```json
{
  "test_id": "TC001",
  "test_name": "User can log in with valid credentials",
  "_meta": {
    "planned_at": "2026-03-13T10:00:00Z",
    "locators_available": 42,
    "ai_model": "claude-sonnet-4-5",
    "attempts": 1,
    "prd_source": "toolbox.md"
  },
  "steps": [
    {
      "action": "navigate",
      "url": "https://practicesoftwaretesting.com/#/auth/login"
    },
    {
      "action": "fill",
      "selector":  { "strategy": "testid", "value": "email" },
      "fallback":  { "strategy": "role",   "value": { "role": "textbox", "name": "Email address" } },
      "element_id": "a3f92b1c",
      "value": "customer@practicesoftwaretesting.com"
    },
    {
      "action": "fill",
      "selector":  { "strategy": "testid", "value": "password" },
      "fallback":  { "strategy": "role",   "value": { "role": "textbox", "name": "Password" } },
      "element_id": "b7c21d4e",
      "value": "welcome01"
    },
    {
      "action": "click",
      "selector":  { "strategy": "testid", "value": "login-submit" },
      "fallback":  { "strategy": "role",   "value": { "role": "button", "name": "Sign in" } },
      "element_id": "c8d32e5f"
    }
  ],
  "assertions": [
    {
      "type": "url_contains",
      "value": "/account"
    },
    {
      "type": "element_visible",
      "selector": { "strategy": "role", "value": { "role": "heading", "name": "My Account" } },
      "element_id": "d9e43f60"
    }
  ]
}
```

---

## 8. End-to-End Execution Walkthrough

**Command:**

```bash
python main.py prd-run \
  --prd toolbox.md \
  --url https://practicesoftwaretesting.com \
  --credentials-file toolbox_creds.json
```

**Step 1 — Credentials loaded**

```json
{
  "url": "https://practicesoftwaretesting.com/#/auth/login",
  "username": "customer@practicesoftwaretesting.com",
  "password": "welcome01"
}
```

**Step 2 — Crawler launches**

- Chromium opens headless
- Navigates to `https://practicesoftwaretesting.com/#/auth/login`
- Auto-detects login form fields (email, password, submit)
- Fills credentials, submits, waits for stable DOM
- Saves `storage_state` to `sessions` table for `practicesoftwaretesting.com`
- Injects A11y JS, captures all interactive elements
- Stores in `locators` table

**Step 3 — Semantic extraction**

- Playwright `a11y.snapshot()` called on each page
- Structured context stored in `states` table

**Step 4 — Plan generation**

- `generator.py` reads `toolbox.md`
- Pulls locators from DB (filtered by crawled URLs)
- Formats prompt: PRD + locators + semantic context
- One AI call → returns JSON array of test plans
- Plans saved to `plans/` directory

**Step 5 — Execution**

For each plan:
- Context restored from saved session (no re-login needed)
- Each step resolved → action dispatched
- URL changes recorded in `transitions` table
- New pages crawled on-the-fly for updated locators
- Assertions evaluated
- Results written to `reports/`

**Step 6 — Summary**

```
╔══════════════════════════════════════════╗
║           QAPAL TEST RESULTS             ║
╠══════════════════════════════════════════╣
║  Total:   12    Passed: 10    Failed: 2  ║
╚══════════════════════════════════════════╝

 PASS  TC001  User can log in with valid credentials      (3.2s)
 PASS  TC002  Product catalog displays with filters       (4.1s)
 PASS  TC003  Add item to cart and verify total           (5.8s)
 FAIL  TC004  Checkout with credit card                   (8.2s)
       → Step 7: Could not resolve selector for "CVV field"
       → Screenshot: reports/screenshots/TC004_step7_fail.png
```

---

## 9. CLI Commands Reference

### `prd-run` (most common)

```bash
python main.py prd-run \
  --prd <FILE.md> [FILE2.md ...] \
  --url <URL> [URL2 ...] \
  [--force]               # Re-crawl even if DB is fresh
  [--spider]              # Follow links (BFS)
  [--depth N]             # Spider depth (default: 2)
  [--max-cases N]         # Limit number of test cases generated
  [--output DIR]          # Report output directory
  [--headless]            # Run browser headless (default: true)
  [--credentials-file F]  # Login credentials JSON
```

### `crawl`

```bash
python main.py crawl \
  --urls <URL> [URL2 ...] \
  [--force] [--spider] [--depth N] \
  [--headless] [--credentials-file F]
```

### `run`

```bash
python main.py run \
  (--plans <PLAN.json> [...]  |  --tests <TC.json> [...]) \
  [--output DIR] [--headless] [--credentials-file F]
```

### `plan`

```bash
python main.py plan \
  --tests <TC.json> [...] \
  [--output DIR]
```

### `semantic`

```bash
python main.py semantic \
  --urls <URL> [...] \
  [--headless]
```

### `graph-crawl`

```bash
python main.py graph-crawl \
  --urls <URL> [...] \
  [--depth N] [--max-pages N] \
  [--headless] [--credentials-file F]
```

### `graph`

```bash
python main.py graph \
  [--from-url URL]     # Show paths from this URL
  [--to-url URL]       # Show paths to this URL
  [--path FROM TO]     # Find shortest path between pages
  [--stats]            # Show graph statistics
```

### `status`

```bash
python main.py status
```

---

## 10. Environment Variables

### Required

| Variable | Values | Description |
|---|---|---|
| `QAPAL_AI_PROVIDER` | `anthropic` \| `openai` \| `grok` | AI provider selection |

### API Keys (at least one required)

| Variable | Provider |
|---|---|
| `ANTHROPIC_API_KEY` | Anthropic Claude |
| `OPENAI_API_KEY` | OpenAI or any OpenAI-compatible endpoint |
| `XAI_API_KEY` / `GROK_API_KEY` | xAI Grok |

### Browser & Database (optional)

| Variable | Default | Description |
|---|---|---|
| `QAPAL_DB_PATH` | `locators.json` | TinyDB file path |
| `QAPAL_HEADLESS` | `true` | Headless browser mode |
| `QAPAL_SCREENSHOTS` | `reports/screenshots` | Screenshot output directory |
| `QAPAL_ACTION_TIMEOUT` | `10000` | Action timeout in milliseconds |
| `CRAWLER_STALE_MINUTES` | `60` | Re-crawl threshold in minutes |
| `QAPAL_CRAWL_CONCURRENCY` | `3` | Parallel crawl workers |
| `LOCATOR_MISS_THRESHOLD` | `3` | Miss count before marking locator invalid |

### AI Configuration (optional)

| Variable | Default | Description |
|---|---|---|
| `QAPAL_AI_MODEL` | provider default | Override model name |
| `QAPAL_AI_BASE_URL` | provider default | Custom OpenAI-compatible base URL |

**Example — Groq with Llama 4:**

```bash
export QAPAL_AI_PROVIDER=openai
export QAPAL_AI_BASE_URL=https://api.groq.com/openai/v1
export QAPAL_AI_MODEL=meta-llama/llama-4-scout-17b-16e-instruct
export OPENAI_API_KEY=gsk_...
```

**Example — Local Ollama:**

```bash
export QAPAL_AI_PROVIDER=openai
export QAPAL_AI_BASE_URL=http://localhost:11434/v1
export QAPAL_AI_MODEL=llama3.2
export OPENAI_API_KEY=ollama
```

---

## 11. Key Design Decisions

### Single AI call per plan

**Why:** Cost, speed, reproducibility. Loop-based AI agents make 10–100 AI calls per test run. QAPAL makes 1 (planning) + 0 (execution) + at most 1 (recovery). Roughly **87.5% cheaper** than agent loops.

**Trade-off:** The plan cannot adapt to runtime surprises unless the replanner is triggered.

### Frozen plan JSON

**Why:** Separation of concerns. Planners and executors are fully decoupled. Plans can be version-controlled, diffed, reviewed by humans, and replayed offline.

**Trade-off:** Plans go stale if the app's DOM structure changes significantly between plan generation and execution.

### Locator chains with fallback

**Why:** Any single selector strategy is fragile. Testids disappear, ARIA names change, CSS selectors break on refactors. Chains provide layered resilience with graceful degradation.

### Soft decay (miss_count)

**Why:** A single failed test run should not invalidate a locator — it could be a timing issue or transient DOM state. Decay over multiple misses is more robust than immediate invalidation.

### Name normalization before hashing

**Why:** E-commerce apps have prices, dates, and counts embedded in element names. Normalising before hashing prevents duplicate DB entries for `"Add $9.99 to cart"` and `"Add $14.99 to cart"` (same element, different product context).

### One replan max

**Why:** Prevent infinite recovery loops. If a single replan fails, the test is genuinely broken and needs human attention — more AI calls won't fix a structural app change.

### Session persistence

**Why:** Login takes 2–5 seconds per test. With 50 test cases, that's 2–4 minutes just for authentication. Saving and restoring `storage_state` cuts this overhead to near zero.

### State graph built from execution

**Why:** Navigation paths are learned empirically from real test runs. The AI does not have to guess or invent URLs — it receives real, observed paths guaranteed to work.

### A11y-first locator strategy

**Why:** ARIA roles and names are the most stable identifiers in modern web apps. They are explicitly set by developers for accessibility and rarely change for cosmetic reasons. CSS selectors and XPaths are far more brittle.

---

## 12. Credential File Format

```json
{
  "url": "https://example.com/#/auth/login",
  "username": "user@example.com",
  "password": "secret123",

  "username_selector": "[data-testid='email']",
  "password_selector": "[data-testid='password']",
  "submit_selector":   "[data-testid='login-btn']",

  "wait_for": "[data-testid='user-menu']"
}
```

| Field | Required | Description |
|---|---|---|
| `url` | Yes | Full URL of the login page (include hash for SPAs: `/#/auth/login`) |
| `username` | Yes | Login username or email |
| `password` | Yes | Login password |
| `username_selector` | No | Explicit selector for username field (auto-detected if omitted) |
| `password_selector` | No | Explicit selector for password field (auto-detected if omitted) |
| `submit_selector` | No | Explicit selector for submit button (auto-detected if omitted) |
| `wait_for` | No | Selector to wait for after login to confirm success |

**Auto-detection fallback order:**

- Username: `input[type=email]` → `input[type=text][name*=user]` → `input[type=text][name*=email]` → `input[type=text]`
- Password: `input[type=password]`
- Submit: `button[type=submit]` → `button:text-matches('sign in', 'i')` → `button:text-matches('log in', 'i')` → `button`

**SPA note:** For Angular, React, and Vue apps using hash routing, always include the hash fragment in `url`. The crawler needs to navigate directly to the login route, not just the app root.
