# QAPAL Roadmap
## Pending Features: Priority P0 → P5

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
| `generator.py` | `_fix_url_assertions` button-click fix: button role with no nav graph match resolves to current URL | d88e8a0 |
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

---

## Priority Summary

| # | Priority | Area | What |
|---|----------|------|------|
| 1 | 🔴 P0 | `generator.py` | Wrong ARIA role in selectors not corrected (`role=button` when DB has `role=link`) |
| 2 | 🔴 P0 | `crawler.py` + `locator_db.py` | Unnamed buttons missing from DB (no accessible name, no testid → never stored) |
| 3 | 🟡 P1 | `locator_db.py` + `state_graph.py` + `crawler.py` | DOM Template Fingerprinting — skip re-crawling structurally identical pages |
| 4 | 🟡 P1 | `executor.py` | Small model for AI rediscovery path (`QAPAL_AI_REDISCOVERY`) |
| 5 | 🟢 P2 | `crawler.py` | Wire `classify_page_change()` into graph-crawl to tag edges with `page_change_type` |
| 6 | 🟢 P3 | `crawler.py` | No-revisit enforcement — use `has_state()` in BFS instead of in-memory set |
| 7 | 🔵 P4 | `crawler.py` + `state_graph.py` | Screenshot per page node during crawl → `reports/states/<state_id>.png` |
| 8 | ⬜ P5 | `main.py` + `executor.py` | Structured JSON run report |
| 9 | ⬜ P5 | `executor.py` | Concurrent test execution (`--parallel N`) |
| 10 | ⬜ P5 | `executor.py` | Visual regression baseline + diff |

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
    """Copy all locator records from source_url to target_url. Returns count."""
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

## 🟡 P1 — Small Model in Executor Rediscovery

**File:** `executor.py`

The AI rediscovery path (`QAPAL_AI_REDISCOVERY`) currently uses the same large model as generation. `model_override` is already implemented in `ai_client.py` — the executor just needs to pass `model_override=self._ai.small_model` to the existing `ai_client.complete()` call in the rediscovery code path.

**Token cost reduction:** ~90% cheaper per recovery call.

---

## 🟢 P2 — Wire `classify_page_change()` into graph-crawl

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

### P5.1 — Structured JSON Run Report
After each `run` / `prd-run`, write `reports/run_<timestamp>.json`:
- Per-test: pass/fail, duration, assertion results, screenshot path on failure
- Summary: total/pass/fail counts, run duration

### P5.2 — Concurrent Test Execution
Add `--parallel N` flag. Use `asyncio.gather()` to run N plans simultaneously, each in its own browser context.

### P5.3 — Visual Regression Baseline
After a passing run, save a screenshot per assertion step as a baseline. On re-run, compare with pixel diff threshold. Visual regressions flagged in report but do not block a functional pass.

---

## Files Affected (Pending Work)

| File | Changes |
|------|---------|
| `generator.py` | P0.3: role mismatch correction in `_fix_selector_strategies` |
| `crawler.py` | P0.4: capture unnamed buttons with semantic id; P1: template check in `crawl_page`; P3: `has_state` in BFS; P4: screenshot |
| `locator_db.py` | P0.4: `id` strategy in locator chain; P1: `_compute_template_hash`, `_strip_nth`, `inherit_locators` |
| `state_graph.py` | P1: `page_templates` table + 3 methods; P4: screenshot in `enrich_and_add` |
| `executor.py` | P1: `model_override` in AI rediscovery; P5.2: `--parallel`; P5.3: visual baseline |
| `main.py` | P2: `classify_page_change` in graph-crawl; P5.1: run report write |
