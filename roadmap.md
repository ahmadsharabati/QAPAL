# QAPAL Roadmap
## Pending Features: Priority P1 → P5

Current state: **5/5 on practicesoftwaretesting.com, 5/5 on books.toscrape.com, 5/5 on automationexercise.com.**

---

## ✅ Completed

| Area | Fix / Feature | Commit |
|------|---------------|--------|
| `generator.py` | `_fix_url_assertions`: nav-graph-based URL simulation + `nav_graph_resolved` flag (button vs link click disambiguation) | b97f425 |
| `generator.py` | `_fix_selector_strategies`: replace `testid` selectors on sites with no testid attributes | b97f425 |
| `generator.py` | `_fix_element_assertions`: validate role+name assertions against DB, replace hallucinations | b97f425 |
| `generator.py` | `_fix_url_assertions`: guard against empty `url_contains` value (P0.1) | b97f425 |
| `generator.py` | Prefix threshold raised 6→8 chars in `_fix_element_assertions` to prevent false positives (#4) | d88e8a0 |
| `generator.py` | 
`_fix_url_assertions` button-click fix: button role with no nav graph match resolves to current URL | d88e8a0 |
| `executor.py` | `element_count`: unknown operator now returns assertion fail instead of silent equality fallback (#9) | d88e8a0 |
| `executor.py` | `element_has_class`: reads `value` key correctly (was always `None` via `a.get("class")`) (#11) | d88e8a0 |
| `executor.py` | `wait` action: all timeout defaults use `ACTION_TIMEOUT` env var instead of hardcoded 30s (#8) | d88e8a0 |
| `executor.py` | `count = 0` initialized before primary selector block (prevented UnboundLocalError) | earlier |
| `executor.py` | Testid OR-locator covering `data-testid`, `data-test`, `data-cy`, `data-qa` | earlier |
| `executor.py` | `scroll_into_view_if_needed()` before interaction actions | earlier |
| `locator_db.py` | `upsert`: `.get()` fallbacks on `locators`/`history` keys — no crash on corrupt DB entries (#20) | d88e8a0 |
| `locator_db.py` | `_build_chain`: skips `role=none` for role+name entries, adds `text` strategy instead (#13) | d88e8a0 |
| `ai_client.py` | `model_override` param on `complete()` / `acomplete()` for small-model calls | earlier |
| `state_graph.py` | `format_for_prompt()`: filters navigate-action noise, uses prefix URL matching, caps at 12 paths | earlier |
| `main.py` | PRD slug prefix for plan filenames (`bookshop-TC001_*`, `toolbox-TC001_*`) — no overwriting | earlier |
| `generator.py` | `_fix_role_mismatches()`: corrects wrong ARIA role in role-strategy selectors against DB (P0.3) | 13bfc00 |
| `crawler.py` | A11Y_JS captures unnamed buttons with semantic `id` (e.g. `#submit_search`) (P0.4) | 13bfc00 |
| `locator_db.py` | `_build_chain()`: `id` strategy placed before `role+name`; `elemId` field wired through (P0.4) | 13bfc00 |

---

## Priority Summary

| # | Priority | Area | What |
|---|----------|------|------|
| 1 | ✅ ~~P0~~ | `generator.py` | ~~Wrong ARIA role~~ — fixed in 13bfc00 |
| 2 | ✅ ~~P0~~ | `crawler.py` + `locator_db.py` | ~~Unnamed buttons missing from DB~~ — fixed in 13bfc00 |
| 3 | 🔴 **P1** | `executor.py` | **Passive error interception** — console errors, network failures, JS exceptions on every run |
| 4 | 🔴 **P1** | `executor.py` + `main.py` | **Visual regression baseline + diff** — screenshot per page/navigation, pixel-diff on re-runs |
| 5 | 🟡 P1 | `locator_db.py` + `state_graph.py` + `crawler.py` | DOM Template Fingerprinting — skip re-crawling structurally identical pages |
| 6 | ✅ ~~P1~~ | `executor.py` | ~~Small model for AI rediscovery~~ — `model_override=small_model` wired in `_ai_rediscover()` |
| 7 | 🟢 **P2** | `generator.py` + `main.py` | **Negative test generation** — failure-path + boundary/edge-case tests from existing PRD |
| 8 | 🟢 P2 | `action_miner.py` + `site_compiler.py` + `generator.py` + `main.py` | Compiled Site Model & UI Action Mining Engine — 90% token reduction, reusable actions |
| 9 | 🟢 P3 | `crawler.py` + `main.py` | Wire `classify_page_change()` into graph-crawl to tag edges with `page_change_type` |
| 10 | 🟢 P3 | `crawler.py` | No-revisit enforcement — use `has_state()` in BFS instead of in-memory set |
| 11 | 🔵 P4 | `crawler.py` + `state_graph.py` | Screenshot per page node during crawl → `reports/states/<state_id>.png` |
| 12 | ⬜ P5 | `executor.py` | Concurrent test execution (`--parallel N`) |

---

## 🔴 P0 — Generator Bug Fixes (found on automationexercise.com)

### P0.3 — Wrong ARIA role not corrected by post-processor

**File:** `generator.py` — `_fix_selector_strategies()`

**Problem:** The AI generated `{"strategy": "role", "value": {"role": "button", "name": "View Product"}}` but the locator DB had `role=link, name="View Product"`. The executor failed because `get_by_role("button", name="View Product")` matches nothing.

`_fix_selector_strategies` currently only replaces `testid`/`testid_prefix` selectors — it does not validate whether the AI-chosen role matches what the DB actually recorded. Any mismatch (`button` vs `link`, `combobox` vs `listbox`, etc.) passes through uncorrected.

**Fix:** In `_fix_selector_strategies`, after the testid replacement pass, add a second pass that validates `role` strategy selectors:

```python
# For each step with strategy=role, look up the element by name in the DB.
# If the DB has a matching entry with a different role, correct the plan's role.
for step in plan.get("steps", []):
    sel = step.get("selector", {})
    if sel.get("strategy") == "role" and isinstance(sel.get("value"), dict):
        plan_role = sel["value"].get("role", "")
        plan_name = sel["value"].get("name", "")
        db_match = _find_by_name_in_db(plan_name, url, locator_map)
        if db_match and db_match["role"] != plan_role:
            sel["value"]["role"] = db_match["role"]
            sel["_role_corrected"] = True
```

`_find_by_name_in_db(name, url, locator_map)` — searches the locator map for an entry whose accessible name fuzzy-matches `plan_name` and returns `{role, name}`. Uses the same URL-context scoping as `_find_best_role_selector`.

**Impact:** Eliminates the most common AI hallucination on sites that don't use testid. On automationexercise.com this caused TC004 to fail on the first `prd-run`.

---

### P0.4 — Unnamed buttons missing from locator DB

**Files:** `crawler.py` (JS extraction), `locator_db.py`

**Problem:** The crawler's A11Y_JS extraction filters to `actionable` elements: `actionable = testid exists OR (role + name + visible + non-zero size)`. A button with no accessible name and no `data-testid`/`data-qa` is `actionable=false` and is dropped before being written to the DB.

On automationexercise.com, `<button id="submit_search">` has no text, no aria-label, no testid — it never enters the DB. The AI then guesses a name ("Search") that matches nothing at runtime.

**Fix:** In A11Y_JS, extend the `actionable` condition to also capture elements that have a non-empty `id` attribute that looks like a semantic identifier (not auto-generated):

```javascript
// Existing:
actionable = !!testid || (!!role && !!name && isVisible && hasSize);

// Add: also capture elements with a meaningful id even without a name
var hasSemanticId = elem.id && !elem.id.match(/^[a-z]+-\d+$/);  // skip auto-IDs like "btn-42"
actionable = actionable || (hasSemanticId && isVisible && hasSize);
```

When an element is stored this way, build its locator chain with `strategy: "id"` as the primary (highest confidence) entry:
```python
{"strategy": "id", "value": elem_id, "unique": True}
```

The DB entry name falls back to the `id` value itself (e.g., `name="submit_search"`) so the AI can reference it semantically in the prompt.

**Impact:** Buttons like `#submit_search`, `#btn_proceed`, `#place_order` become discoverable. The AI can generate `{"strategy": "id", "value": "submit_search"}` directly.

---

## 🟡 P1 — DOM Template Fingerprinting

### Problem

QAPAL currently crawls every URL it visits. On a site with 100 product pages, it crawls all 100 — even though every product page shares the **same structural layout** (same buttons, same form, same DOM positions). This wastes time, inflates `locators.json`, and gives the AI redundant context.

The semantic hash (`compute_semantic_hash`) already exists, but it hashes `(role, name)` pairs — it's content-aware. A **template hash** must be **structure-only**: same interactive elements in the same DOM positions, regardless of what they're named.

**Example — books.toscrape.com:**
- `/catalogue/a-light-in-the-attic_1000/index.html`
- `/catalogue/tipping-the-velvet_999/index.html`
- ...998 more product pages

All share one template: `[breadcrumb links, h1, price table, availability row]`. We should crawl exactly **one** and inherit locators for the rest.

---

### Design

#### Template Hash

Structural fingerprint — **role + container (tag only, no id/attrs) + dom_path (without `:nth(N)` indices)**. Ignores all content (names, labels, prices).

```python
# locator_db.py — new helper
_NTH_RE = re.compile(r":nth\(\d+\)")

def _strip_nth(dom_path: str) -> str:
    """article>div:nth(2)>form>button  →  article>div>form>button"""
    return _NTH_RE.sub("", dom_path)

def _compute_template_hash(elements: list[dict]) -> str:
    structural_keys = sorted(
        (
            elem.get("identity", {}).get("role", ""),
            elem.get("identity", {}).get("container", "").split("[")[0].split("#")[0],
            _strip_nth(elem.get("identity", {}).get("dom_path", ""))
        )
        for elem in elements
        if elem.get("locators", {}).get("actionable", False)
    )
    return hashlib.sha256(json.dumps(structural_keys).encode()).hexdigest()[:12]
```

#### New TinyDB Table: `page_templates`

```python
# state_graph.py — new table alongside existing 'page_states'
{
    "template_id":   "abc123def456",
    "url_pattern":   "https://books.toscrape.com/catalogue/:id/index.html",
    "sample_url":    "https://books.toscrape.com/catalogue/a-light-in-the-attic_1000/index.html",
    "element_count": 15,
    "first_seen":    "2026-03-14T...",
    "match_count":   0,
}
```

#### `_url_to_pattern()` — URL Generalization

Converts a concrete URL to a pattern by replacing dynamic path segments with `:id`:

```python
import re

_DYNAMIC_SEGMENT = re.compile(
    r"(?<=/)"                     # after a slash
    r"(?:"
        r"[a-f0-9]{8,}"          # hex IDs (UUIDs, mongo IDs)
        r"|[a-zA-Z0-9_-]*\d{2,}" # slugs with numeric suffix (e.g. tipping-the-velvet_999)
        r"|\d+"                   # pure numeric segments
    r")"
    r"(?=/|$)"                    # before next slash or end
)

def _url_to_pattern(url: str) -> str:
    """
    https://books.toscrape.com/catalogue/a-light-in-the-attic_1000/index.html
    → https://books.toscrape.com/catalogue/:id/index.html
    """
    return _DYNAMIC_SEGMENT.sub(":id", url)
```

Heuristic: any path segment that is purely numeric, contains a hex string (8+ chars), or ends with a numeric suffix (2+ digits) is treated as dynamic. This covers the common patterns (product IDs, UUIDs, slug-with-number) without over-generalizing.

#### Crawl Skip Logic

**In `crawler.py` `crawl_page()`**, after element extraction, before storing to DB:

```python
template_hash = _compute_template_hash(raw_elements)
if state_graph:
    existing = state_graph.get_template(template_hash)
    if existing:
        state_graph.record_template_match(template_hash, url)
        db.inherit_locators(source_url=existing["sample_url"], target_url=url)
        return {"elements": len(raw_elements), "new": 0, "template_match": True,
                "template_id": template_hash, "inherited_from": existing["sample_url"]}

result = _store_elements(raw_elements, url, db)
if state_graph:
    state_graph.register_template(template_hash, url, raw_elements,
                                  url_pattern=_url_to_pattern(url))
```

#### New Methods on `StateGraph`

```python
def get_template(self, template_id: str) -> dict | None: ...
def register_template(self, template_id: str, url: str,
                      elements: list[dict], url_pattern: str = "") -> None: ...
def record_template_match(self, template_id: str, url: str) -> None: ...
```

#### `inherit_locators()` in `LocatorDB`

```python
def inherit_locators(self, source_url: str, target_url: str) -> int:
    """Copy all locator records from source_url to target_url. Returns count.
    Each inherited record gets a 'template_id' field for cascade tracking."""
```

**Staleness handling:** When the sample URL is re-crawled (e.g., stale-check triggers after 60 minutes), all inherited records sharing the same `template_id` must be refreshed. The `inherit_locators` method tags each copied record with `template_id` so that `re_crawl()` can cascade-update all inherited copies:

```python
# In crawl_page(), when re-crawling a known sample_url:
if is_recrawl and state_graph:
    template = state_graph.get_template_by_sample_url(url)
    if template:
        inherited_urls = state_graph.get_inherited_urls(template["template_id"])
        for target_url in inherited_urls:
            db.inherit_locators(source_url=url, target_url=target_url)
```

### Files Affected

| File | Change |
|------|--------|
| `locator_db.py` | `_compute_template_hash()`, `_strip_nth()`, `inherit_locators()` |
| `state_graph.py` | `page_templates` table, `get_template()`, `register_template()`, `record_template_match()` |
| `crawler.py` | Template check + skip in `crawl_page()` |

### Expected Impact

- **books.toscrape.com**: 1000 product pages → crawl 1, inherit 999. Crawl time: minutes → seconds.
- **DB size**: Eliminates duplicate locator records for structurally identical pages.

---

## ✅ Shipped — Small Model in Executor Rediscovery

**File:** `executor.py` — `_ai_rediscover()`

The `acomplete()` call now passes `model_override=ai_client.small_model` (Haiku for Anthropic). ~90% cheaper per recovery call.

---

## 🔴 P1 — Passive Error Interception

**File:** `executor.py`

### Problem

Tests can pass all assertions while the site has real bugs: an API returns 500 but the UI masks it, a JS exception fires but the page looks fine, a third-party resource fails and breaks a feature silently. No assertion catches these — they require passive monitoring.

### Implementation

After page creation (line ~1232 in `executor.py`), attach Playwright event listeners before the step loop:

```python
_console_errors: list[dict] = []
_network_failures: list[dict] = []
_js_exceptions: list[str] = []

page.on("console",       lambda msg: _console_errors.append({"text": msg.text, "url": msg.location.get("url","")}) if msg.type == "error" else None)
page.on("requestfailed", lambda req: _network_failures.append({"url": req.url, "failure": req.failure}) if _is_signal_failure(req.url, base_url) else None)
page.on("pageerror",     lambda err: _js_exceptions.append(str(err)))
```

**Noise filter** — skip CDN assets, analytics, fonts, static files. User-extensible via `QAPAL_NOISE_DOMAINS`:
```python
_DEFAULT_NOISE_DOMAINS = ("google-analytics.com", "doubleclick.net", "fonts.googleapis.com", "sentry.io")
_NOISE_DOMAINS = _DEFAULT_NOISE_DOMAINS + tuple(
    d.strip() for d in os.getenv("QAPAL_NOISE_DOMAINS", "").split(",") if d.strip()
)
_NOISE_EXTS    = (".png", ".jpg", ".gif", ".woff", ".woff2", ".svg", ".ico", ".css")

def _is_signal_failure(url: str, base_url: str) -> bool:
    if any(d in url for d in _NOISE_DOMAINS): return False
    if any(url.endswith(e) for e in _NOISE_EXTS): return False
    return True
```

**Result enrichment** — add before returning result dict:
```python
result["passive_errors"] = {
    "console_errors":    _console_errors,
    "network_failures":  _network_failures,
    "js_exceptions":     _js_exceptions,
}
result["has_passive_errors"] = bool(_console_errors or _network_failures or _js_exceptions)
```

`has_passive_errors=True` does **not** flip `passed` — it's a warning. The `main.py` summary prints a separate "Passive errors detected" section listing affected tests.

**Files:** `executor.py` only. No new dependencies.

---

## 🔴 P1 — Visual Regression Baseline + Diff

**Files:** `executor.py`, `main.py`, `requirements.txt`

### Problem

Functional assertions pass even when the page looks broken: layout shifts, missing images, wrong colors, overlapping elements. Visual regression catches these automatically once a baseline exists.

### Implementation

**New env vars:**
```bash
QAPAL_VISUAL_REGRESSION=false   # opt-in (default off)
QAPAL_VISUAL_THRESHOLD=0.02     # 2% pixel diff tolerance
```

**New dependency:** `Pillow>=10.0.0`

**Baseline directory structure:**
```
reports/
  baseline/<test_id>/step_3.png, step_7.png, ...   ← one per page/navigation
  visual_diff/<test_id>_<ts>/step_3_diff.png        ← red pixels = regressions
```

**Per-page screenshots** — taken after every `navigate` action and after any `click` that triggers a URL change. This catches broken layouts on every page the test visits, not just at assertion points:

```python
# In the step loop, after executing a step:
if VISUAL_REGRESSION and (step["action"] == "navigate" or url_changed):
    vr = await _compare_visual(page, tc_id, step_index)
    if vr:
        visual_regressions.append(vr)
```

```python
async def _compare_visual(page, test_id, step_index) -> dict | None:
    from PIL import Image, ImageChops
    baseline = SCREENSHOT_DIR / "baseline" / test_id / f"step_{step_index}.png"
    if not baseline.exists():
        baseline.parent.mkdir(parents=True, exist_ok=True)
        await page.screenshot(path=str(baseline), full_page=False)
        return None  # first run — save baseline, no diff
    current = SCREENSHOT_DIR / "visual_diff" / f"{test_id}_{_ts()}" / f"step_{step_index}.png"
    current.parent.mkdir(parents=True, exist_ok=True)
    await page.screenshot(path=str(current), full_page=False)
    img_a = Image.open(baseline).convert("RGB")
    img_b = Image.open(current).convert("RGB").resize(img_a.size)
    diff  = ImageChops.difference(img_a, img_b)
    diff_pct = sum(1 for r,g,b in diff.getdata() if r+g+b > 30) / (img_a.width * img_a.height)
    if diff_pct > VISUAL_THRESHOLD:
        return {"step_index": step_index, "diff_pct": round(diff_pct, 4),
                "baseline": str(baseline), "current": str(current)}
    return None
```

Add `visual_regressions` list to result; `main.py` prints "VISUAL REGRESSIONS" warning section. Regressions are warnings — they do **not** flip `passed`.

**CLI:** `--update-baseline` flag in `prd-run` / `run` deletes existing baseline for affected tests.

---

## 🟢 P2 — Negative Test Generation + Form Boundary Testing

**Files:** `generator.py`, `main.py`

### Problem

QAPAL only tests happy paths. Real bugs hide on error paths: wrong credentials, empty required fields, too-long inputs, special characters. These are never exercised.

### Solution

One extra AI call per `prd-run` (when `--negative-tests` flag is set) generates failure-path and boundary-value test cases from the existing positive plans. Selectors are reused — no extra crawling needed.

### Implementation

**New CLI flag:** `--negative-tests` (off by default)

**New method `TestGenerator.generate_negative_plans()`:**

```python
_NEGATIVE_SYSTEM = """
You are a security-aware QA engineer generating NEGATIVE test cases.
Rules:
1. For each positive test with a login/form step, generate ONE failure-path test:
   wrong credentials, missing required field, or invalid email format.
2. For each positive test with form fill steps, generate ONE boundary test using:
   - Empty string for a required field
   - A 1001-character string for a text input
   - The value: <script>alert(1)</script> for any text field
   - The value: ' OR 1=1-- for any text field
3. Assertions must check element_visible for an error message — NOT url_contains navigation.
4. Reuse selectors from the positive plan exactly. Do not invent new selectors.
5. Test ID: {positive_id}_neg (failure path), {positive_id}_boundary (edge cases).
6. Output: JSON array of test plans (same schema as positive plans).
"""
```

**Integration after positive plan generation:**
```python
if self._negative_tests:
    raw_neg = self._ai.complete(neg_prompt, system_prompt=_NEGATIVE_SYSTEM, ...)
    neg_plans = self._parse_plans(raw_neg)   # run same post-processor chain
    parsed_plans.extend(neg_plans)
```

**Plan naming:** `bookshop-TC001_login_neg.json`, `bookshop-TC001_login_boundary.json`

**Files:** `generator.py` (`generate_negative_plans()` + `_negative_tests` flag in `__init__`), `main.py` (`--negative-tests` arg)

---

## 🟢 P2 — Compiled Site Model & UI Action Mining Engine

### Problem

The AI generator currently receives a raw dump of up to 2600+ locators per `prd-run`. This:
- Costs 6,000–10,000 tokens per plan generation call
- Forces the AI to reason about DOM structure instead of user workflows
- Produces fragile selectors because the AI picks from noise, not meaning
- Provides no reuse — login steps are re-generated from scratch for every site

### Solution

Two new modules transform the raw locator DB + state graph into a compact **compiled application model** that the AI reasons about at the workflow level, not the DOM level.

```
Crawler → locator_db + state_graph
                    ↓
            action_miner.py        ← discovers reusable UI actions
                    ↓
            site_compiler.py       ← compiles states + actions into model
                    ↓
           compiled_model.json     ← compact site representation (~400 tokens)
                    ↓
            generator.py           ← uses compiled model instead of raw locators
```

**Token reduction:** ~2600 locators × ~3 tokens = ~7,800 tokens → ~400 tokens for compiled model. **~95% reduction.**

---

### Dependency

**Practical dependency on P1 DOM Template Fingerprinting.** The `action_miner._group_by_url_pattern()` method needs URL patterns to cluster locators by page type. P1's template fingerprinting produces these patterns via `_url_to_pattern()`. Without P1, the miner would need a fallback (e.g., grouping by URL path prefix), which produces noisier and less accurate state groupings. **Recommended: implement P1 first.**

---

### Module 1: `action_miner.py`

Discovers reusable semantic actions by clustering locators on each page into workflow units.

#### Detection Heuristics

| Pattern | Detected Action |
|---------|----------------|
| `textbox[email]` + `textbox[password]` + `button[login/sign in]` | `login(email, password)` |
| `textbox[search/keyword]` + `button[search/submit]` | `search(query)` |
| `textbox[first-name]` + `textbox[last-name]` + `textbox[email]` + `textbox[password]` + submit | `create_account(user)` |
| `button[add to cart/add to basket]` on a product URL | `add_to_cart()` |
| `button[checkout/place order/proceed]` | `proceed_to_checkout()` |
| Nav links in state graph transitions | `navigate_to_<page>()` |

#### Action Schema

```python
# action_miner.py — output per discovered action
{
    "name": "login",
    "description": "Log in with email and password",
    "entry_url_pattern": "/auth/login",
    "post_url_pattern": "/account",          # from state graph transition (if known)
    "parameters": ["email", "password"],
    "steps": [
        {"type": "fill",  "selector": {"strategy": "testid", "value": "email"},          "param": "email"},
        {"type": "fill",  "selector": {"strategy": "testid", "value": "password"},       "param": "password"},
        {"type": "click", "selector": {"strategy": "testid", "value": "login-submit"}}
    ]
}
```

#### Implementation

```python
# action_miner.py

class ActionMiner:
    def __init__(self, db: LocatorDB, state_graph: StateGraph):
        self._db = db
        self._sg = state_graph

    def mine(self, base_url: str) -> list[dict]:
        """Return list of discovered action dicts for base_url."""
        actions = []
        for url_pattern, locs in self._group_by_url_pattern(base_url):
            actions += self._detect_form_actions(url_pattern, locs)
            actions += self._detect_nav_actions(url_pattern, locs)
        return self._deduplicate(actions)

    def _detect_form_actions(self, url_pattern, locs) -> list[dict]: ...
    def _detect_nav_actions(self, url_pattern, locs) -> list[dict]: ...
    def _group_by_url_pattern(self, base_url) -> dict: ...
    def _deduplicate(self, actions) -> list[dict]: ...
```

---

### Module 2: `site_compiler.py`

Compiles the mined actions + state graph into a compact `compiled_model.json`.

#### Output Schema

```json
{
  "version": "1.0",
  "compiled_at": "2026-03-15T...",
  "base_url": "https://practicesoftwaretesting.com",
  "token_estimate": 380,
  "states": {
    "home":           {"url_pattern": "/",              "available_actions": ["navigate_to_products", "navigate_to_login"]},
    "login":          {"url_pattern": "/auth/login",    "available_actions": ["login"]},
    "products":       {"url_pattern": "/category/:id",  "available_actions": ["filter_by_category", "navigate_to_product"]},
    "product_detail": {"url_pattern": "/product/:id",   "available_actions": ["add_to_cart"]},
    "cart":           {"url_pattern": "/checkout",      "available_actions": ["proceed_to_checkout"]},
    "account":        {"url_pattern": "/account",       "available_actions": ["navigate_to_logout"]}
  },
  "actions": {
    "login": {
      "description": "Log in with email and password",
      "entry_state": "login",
      "post_state": "account",
      "parameters": ["email", "password"],
      "steps": [
        {"type": "fill",  "selector": {"strategy": "testid", "value": "email"},         "param": "email"},
        {"type": "fill",  "selector": {"strategy": "testid", "value": "password"},      "param": "password"},
        {"type": "click", "selector": {"strategy": "testid", "value": "login-submit"}}
      ]
    },
    "search": {
      "description": "Search for a product by keyword",
      "entry_state": "products",
      "post_state": "products",
      "parameters": ["query"],
      "steps": [
        {"type": "fill",  "selector": {"strategy": "role", "value": {"role": "textbox", "name": "Search Product"}}, "param": "query"},
        {"type": "click", "selector": {"strategy": "id",   "value": "submit_search"}}
      ]
    },
    "add_to_cart": {
      "description": "Add the current product to the cart",
      "entry_state": "product_detail",
      "post_state": "product_detail",
      "parameters": [],
      "steps": [
        {"type": "click", "selector": {"strategy": "testid", "value": "add-to-cart"}}
      ]
    }
  }
}
```

#### Implementation

```python
# site_compiler.py

class SiteCompiler:
    def __init__(self, db: LocatorDB, state_graph: StateGraph):
        self._miner = ActionMiner(db, state_graph)
        self._sg = state_graph

    def compile(self, base_url: str, output_path: str = "compiled_model.json") -> dict:
        actions = self._miner.mine(base_url)
        states  = self._build_state_map(base_url, actions)
        model   = {
            "version":        "1.0",
            "compiled_at":    datetime.utcnow().isoformat(),
            "base_url":       base_url,
            "token_estimate": self._estimate_tokens(states, actions),
            "states":         states,
            "actions":        {a["name"]: a for a in actions},
        }
        with open(output_path, "w") as f:
            json.dump(model, f, indent=2)
        return model

    def _build_state_map(self, base_url, actions) -> dict: ...
    def _estimate_tokens(self, states, actions) -> int: ...
```

---

### Module 3: Generator Integration (`generator.py`)

When `compiled_model.json` exists and is fresh (< `CRAWLER_STALE_MINUTES` old), the generator uses it instead of the raw locator dump.

**Prompt change (old):**
```
AVAILABLE LOCATORS (2,600 entries):
[{"url": "...", "role": "button", "name": "Add to cart", ...}, ...]
```

**Prompt change (new):**
```
APPLICATION MODEL (compiled):
States: home → login → account → ...
Actions:
  login(email, password)  — on /auth/login  → /account
  search(query)           — on /products    → /products
  add_to_cart()           — on /product/:id → stays
  ...
```

The AI generates plans by composing actions instead of constructing selectors from scratch. Selectors are already embedded in the action definitions.

```python
# generator.py — new method
def _load_compiled_model(self, base_url: str) -> dict | None:
    path = os.getenv("QAPAL_COMPILED_MODEL", "compiled_model.json")
    if not os.path.exists(path):
        return None
    model = json.load(open(path))
    if model.get("base_url", "").rstrip("/") != base_url.rstrip("/"):
        return None  # model is for a different site
    return model

# In generate(): prefer compiled model over raw locators
compiled = self._load_compiled_model(base_url)
if compiled:
    locator_context = _format_compiled_model(compiled)   # ~400 tokens
else:
    locator_context = self._db.format_for_prompt(...)    # ~7,800 tokens
```

---

### New CLI Command

```bash
# Compile site model (crawl must have run first)
python main.py compile --url https://practicesoftwaretesting.com

# Output
#   compiled_model.json           ← committed alongside plans/
#   console: "Compiled 6 states, 8 actions — 380 tokens (est.)"

# prd-run auto-uses compiled model if present
python main.py prd-run --prd toolbox.md --url https://practicesoftwaretesting.com
#   → "Using compiled model: 6 states, 8 actions (380 tokens)"
```

---

### Files

| File | Type | Change |
|------|------|--------|
| `action_miner.py` | **new** | Discovers reusable UI actions from locator DB + state graph |
| `site_compiler.py` | **new** | Compiles states + actions into `compiled_model.json` |
| `generator.py` | modified | Use compiled model in prompt when available; fallback to locator dump |
| `main.py` | modified | Add `compile` CLI command; auto-detect compiled model in `prd-run` |
| `compiled_model.json` | **new artifact** | Committed alongside `plans/`; version-controlled per site |

### Success Metrics

| Metric | Current | Target |
|--------|---------|--------|
| Tokens per plan generation | ~7,800 | ~400 |
| Plan generation time | ~8s | ~2s |
| Selector hallucination rate | ~15% on plain-HTML sites | <2% |
| Action reuse across test cases | 0% | 100% for login/search/cart |

---

## 🟢 P3 — Wire `classify_page_change()` into graph-crawl

**File:** `crawler.py`, `main.py`

`classify_page_change()` is implemented in `state_graph.py` but nothing calls it. In `cmd_graph_crawl()` in `main.py`, after each link click, call `classify_page_change(before_snap, after_snap, before_url, after_url)` and store the result as `page_change_type` on the recorded edge.

The generator's Rule 12 can then use `page_change_type` to select the right assertion type:
- `navigation` → `url_contains`
- `modal` → `element_visible` on dialog
- `partial` → `element_visible` on updated content

---

## 🟢 P3 — No-Revisit via `has_state()` in BFS

**File:** `crawler.py`

`spider_crawl()` uses an in-memory `visited` set that resets every run. Replace with `state_graph.has_state(state_id)` so already-known semantic states are skipped across runs. Add `--force` flag to bypass.

---

## 🔵 P4 — Screenshot Per Page Node

**Files:** `crawler.py`, `state_graph.py`

During crawl, after `crawl_page()`, save `page.screenshot(path=f"reports/states/{state_id}.png")` and pass `screenshot_path` to `enrich_and_add()`. Makes the state graph visually inspectable.

---

## ⬜ P5 — Nice to Have

### P5.1 — Concurrent Test Execution
Add `--parallel N` flag. Use `asyncio.gather()` to run N plans simultaneously, each in its own browser context. Note: TinyDB is not thread-safe — parallel runs that trigger AI rediscovery will need either a lock or a per-context DB.

> **Removed:** P5.1 (Structured JSON Run Report) — already implemented in `main.py`.
> **Removed:** P5.3 (Visual Regression Baseline) — promoted to P1 #4 with full design.

---

## Files Affected (Pending Work)

| File | Changes |
|------|---------|
| `executor.py` | P1: passive error interception + visual regression per-page screenshots; P5: `--parallel` |
| `main.py` | P1: visual regression summary output; P2: `compile` CLI command + auto-detect in `prd-run`; P3: `classify_page_change` in graph-crawl |
| `generator.py` | P2: compiled model prompt integration; P2: negative test generation |
| `crawler.py` | P1: template check in `crawl_page`; P3: `has_state` in BFS; P4: screenshot |
| `locator_db.py` | P1: `_compute_template_hash`, `_strip_nth`, `inherit_locators` |
| `state_graph.py` | P1: `page_templates` table + 3 methods; P4: screenshot in `enrich_and_add` |
| `action_miner.py` | **new** — P2: UI Action Mining Engine |
| `site_compiler.py` | **new** — P2: Site Compiler |
| `compiled_model.json` | **new artifact** — P2: output of compile command |
