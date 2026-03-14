# QAPAL Roadmap
## Bugs → Features: Priority P0 → P5

Current state: 5/5 passing on practicesoftwaretesting.com, 2/5 on books.toscrape.com.

---

## Priority Summary

| # | Priority | Area | What |
|---|----------|------|------|
| 1 | 🔴 P0 | generator.py | Empty `url_contains` value on sites with no nav graph |
| 2 | 🔴 P0 | generator.py | AI hallucinates `testid` selectors on plain-HTML sites |
| 3 | 🟡 P1 | generator.py + ai_client.py | Lightweight post-plan validator using small model |
| 4 | 🟡 P1 | locator_db.py + state_graph.py | URL pattern normalization (ULID/UUID → `:id`) |
| 5 | 🟡 P1 | state_graph.py | Semantic hash from a11y snapshot (not raw DOM hash) |
| 6 | 🟡 P1 | state_graph.py + crawler.py | `states` table + `enrich_and_add()` + `has_state()` |
| 7 | 🟢 P2 | state_graph.py + crawler.py | Page type classification (navigation / modal / partial) |
| 8 | 🟢 P3 | main.py + crawler.py | BFS auto-crawl from seed URL on first `prd-run` |
| 9 | 🟢 P3 | crawler.py | No-revisit enforcement in BFS using `has_state()` |
| 10 | 🔵 P4 | main.py + generator.py | Plan naming with PRD slug prefix (no overwriting) |
| 11 | 🔵 P4 | main.py + generator.py | `--num-tests N` integer flag |
| 12 | 🔵 P4 | crawler.py + state_graph.py | Screenshot saved per page node during crawl |
| 13 | ⬜ P5 | main.py + executor.py | Structured JSON run report |
| 14 | ⬜ P5 | executor.py | Concurrent test execution (`--parallel N`) |
| 15 | ⬜ P5 | executor.py | Visual regression baseline + diff |

---

## 🔴 P0 — Critical Bug Fixes

### P0.1 — Empty `url_contains` value
**File:** `generator.py` — `_fix_element_assertions()`

When a plan navigates to a URL with no locator DB entries (e.g. books.toscrape.com pagination), `page_path` is empty. The post-processor emits `{"type": "url_contains", "value": ""}` which passes for any URL, making the assertion useless.

**Fix:** Guard before emitting fallback — if `page_path` is empty, keep the original assertion unchanged instead of replacing it with an empty-value `url_contains`.

---

### P0.2 — Hallucinated `testid` selectors on plain-HTML sites
**File:** `generator.py` — new `_fix_selector_strategies()` post-processor

On sites with no `data-testid` attributes (e.g. books.toscrape.com), the AI generates `testid_prefix` and `testid` selectors that match zero elements at runtime. The AI has no way to know at generation time whether the site uses testid.

**Fix:** Add `_fix_selector_strategies(plans, locator_map)` as the last post-processor:
1. Check if any locator in `locator_map` uses `testid` or `testid_prefix` strategy
2. If none → the site doesn't use testid. For every step/assertion that has a `testid` selector, search `locator_map` for the closest `role` or `text` match and replace
3. If some → leave all plans unchanged (site uses testid)

Post-processor order:
```
_inject_login_if_missing
→ _inject_cart_prerequisite
→ _fix_url_assertions
→ _fix_element_assertions
→ _fix_selector_strategies   ← new
```

---

## 🟡 P1 — Robustness Improvements

### P1.0 — Lightweight post-plan validator (small model)
**Files:** `generator.py`, `ai_client.py`, `executor.py`

The post-processor chain catches most AI hallucinations via DB lookup, but misses cases where the element exists in the DB under a slightly different name. A small second-pass model can catch these cheaply.

**How it works:**
- After the full post-processor chain, call a small/fast model with a compact prompt: plan JSON + top 50 locators for the starting URL
- Model verifies each selector exists in the locator list; if not, replaces with closest match
- Failure is non-fatal — if the validator call fails, the original plan is used

**Small model per provider:**
- Anthropic → `claude-haiku-4-5-20251001`
- OpenAI → `gpt-4o-mini`
- Groq/OpenAI-compat → `meta-llama/llama-4-scout-17b-16e-instruct`

**Add to `ai_client.py`:** optional `model_override` parameter on `complete()` so the validator can request a different (smaller) model without changing the main client's configured model.

**Also apply to on-fail recovery:** `executor.py`'s AI rediscovery path (`QAPAL_AI_REDISCOVERY`) should use the small model via `model_override` — faster and cheaper than the full generation model.

**Token cost:** ~800 tokens per plan vs 8,192 for generation (~10% overhead).

---

### P1.1 — URL pattern normalization
**Files:** `locator_db.py`, `state_graph.py`

Nav graph stores raw URLs like `/product/01KKNWM4EP4HYEP1X6CF8622XB`. When product ULIDs rotate, all edges become stale. The generator picks wrong landing URLs for product assertions.

**Fix:** Add `_url_to_pattern(url)` to `locator_db.py`:
- Replace ULID (26-char base32), UUID, and numeric (4+ digit) path segments with `:id`
- e.g. `/product/01KKNWM4EP4HYEP1X6CF8622XB` → `/product/:id`

Apply in `state_graph.record_transition()`: store a `url_pattern` field on each edge alongside the raw URL. Use pattern-based grouping in `format_for_prompt()` to show the highest-count edge per pattern rather than every unique ULID URL.

---

### P1.2 — Semantic hash from a11y snapshot
**File:** `state_graph.py`

Current struct hash is a raw DOM hash — unique per page load because of CSRF tokens, timestamps, cart counts. Two visits to the same page produce different hashes → state graph grows unboundedly, BFS revisits identical states.

**Fix:** Add `_compute_semantic_hash(a11y_snapshot)`:
- Hash sorted `(role, normalized_name)` pairs from the a11y node list
- Normalize names: strip prices (`$1.99`), timestamps (`12:30`), badge counts (`(3)`)
- Result: same semantic page = same hash regardless of dynamic content

---

### P1.3 — `states` table + `enrich_and_add()` + `has_state()`
**Files:** `state_graph.py`, `crawler.py`

StateGraph only tracks edges (transitions). There is no per-page node store, so BFS can't check "have I seen this page before?" and screenshots can't be attached to states.

**Fix:** Add a `states` TinyDB table to `StateGraph`:
- `enrich_and_add(url, a11y_snapshot, screenshot_path, semantic_context)` — upsert a state record, return `state_id`
- `has_state(state_id)` — boolean lookup for BFS no-revisit check
- Update `record_transition()` to accept and store `from_state_id` / `to_state_id` on edges

Wire `crawler.py` to call `enrich_and_add()` after each page crawl.

---

## 🟢 P2 — Smarter Assertion Generation

### P2.1 — Page type classification
**Files:** `state_graph.py`, `crawler.py`

After any interaction, neither the crawler nor the generator knows if a navigation happened, a modal appeared, or just a partial DOM update occurred. This distinction is critical for correct assertion selection.

**Fix:** Add `classify_page_change(before_snap, after_snap, before_url, after_url)`:
- `navigation` — URL changed
- `modal` — `dialog` / `alertdialog` ARIA role appeared, URL unchanged
- `partial` — semantic hash changed, no modal, URL unchanged
- `none` — nothing changed

Tag nav graph edges with `page_change_type`. Feed this into the generator's Rule 12:
- `navigation` → assert `url_contains` destination
- `modal` → assert `element_visible` dialog content
- `partial` → assert `element_visible` updated content or `url_contains` same path

---

## 🟢 P3 — BFS Auto-Crawl

### P3.1 — BFS from seed URL on first `prd-run`
**Files:** `main.py`, `crawler.py`

First `prd-run` on a new site has 0 locators and 0 nav graph edges. The AI hallucinates selectors because the locator prompt is empty. Users currently must run `crawl` manually before `prd-run`.

**Fix:** In the `prd-run` flow, before calling the generator:
- If `--spider` flag is set, OR `state_graph.stats()["unique_pages"] < 3` → run BFS auto-crawl from seed URL
- Add `bfs_crawl(seed_url, max_pages=30, max_depth=3)` to `Crawler`
- BFS respects same-origin constraint (skips external links)
- CLI: `python main.py prd-run --prd foo.md --url https://... --spider`

---

### P3.2 — No-revisit enforcement
**File:** `crawler.py`

BFS should not re-crawl pages it has already visited in previous runs. Currently it uses an in-memory `visited` set that resets each run.

**Fix:** Replace in-memory set with `state_graph.has_state(state_id)` check. Already-known states are skipped unless `--force` is passed.

---

## 🔵 P4 — Quality of Life

### P4.1 — Plan naming with PRD slug prefix
**Files:** `main.py`, `generator.py`

`prd-run bookshop_prd.md` generates `plans/TC001_*.json`, overwriting toolshop plans that also start at TC001. Different PRDs stomp each other.

**Fix:** Derive slug from PRD filename (`bookshop_prd.md` → `bookshop`). Plan IDs become `bookshop-TC001_browse.json`. Existing plans without a slug prefix are unaffected.

---

### P4.2 — `--num-tests N` integer flag
**Files:** `main.py`, `generator.py`

Replace the boolean `--max-cases` flag with `--num-tests N` (integer). Feeds directly into the generator prompt: "generate exactly N test cases."

```bash
python main.py prd-run --prd foo.md --url https://... --num-tests 3
```

---

### P4.3 — Screenshot per page node
**Files:** `crawler.py`, `state_graph.py`

During crawl, save a screenshot of each page and attach the path to the state record in the `states` table. Makes the state graph inspectable — you can see what each node looks like.

Saved to: `reports/states/<state_id>.png`

---

## ⬜ P5 — Nice to Have

### P5.1 — Structured JSON run report
After each `run` / `prd-run`, write `reports/run_<timestamp>.json`:
- Per-test: pass/fail, duration, assertion results, screenshot path on failure
- Summary: total/pass/fail counts, run duration

### P5.2 — Concurrent test execution
Add `--parallel N` flag. Use `asyncio.gather()` to run N plans simultaneously, each in its own browser context. Sequential is default; parallel is opt-in.

### P5.3 — Visual regression baseline
After a passing run, save a screenshot per assertion step as a baseline. On re-run, compare with pixel diff threshold. Visual regressions are flagged in the report but do not block a functional pass.

---

## Files Affected

| File | Changes |
|------|---------|
| `generator.py` | P0.1, P0.2, P1.0, P4.1, P4.2 |
| `ai_client.py` | P1.0 (`model_override` param) |
| `executor.py` | P1.0 (small model for rediscovery) |
| `locator_db.py` | P1.1 (`_url_to_pattern`) |
| `state_graph.py` | P1.1, P1.2, P1.3, P2.1 |
| `crawler.py` | P1.3, P3.1, P3.2, P4.3 |
| `main.py` | P3.1, P4.1, P4.2, P5.1 |
