# Changelog

All notable changes to QAPAL are documented here.
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).
Versioning follows [Semantic Versioning](https://semver.org/).

---

## [2.0.1] — 2026-03-17

### Summary
Point release adding `--ai-fallback` flag and comprehensive real-world validation across TodoMVC, SauceDemo, BooksToScrape, GitHub, and Wikipedia.

### Added
- **`--ai-fallback` flag** on `fix` and `heal` commands — enables one-shot LLM inference for selectors that have degraded into unreadable CSS paths with no accessible name. Opt-in, never overrides selectors the deterministic engine can already resolve.
- **`examples/` directory** — three drop-in `.github/workflows/` templates:
  - `typescript-playwright.yml` — selector health check for TS/Playwright repos
  - `python-playwright.yml` — selector health check for Python/pytest repos
  - `self-heal-on-failure.yml` — full auto-heal pipeline (tests fail → heal → retry → PR)
- **GitHub Action published to Marketplace** (`ahmadsharabati/QAPAL@v2.0.1`)

### Validated
Real-world validation against live public websites:
- **TodoMVC**: CSS `.new-todo` → `get_by_role("textbox", name="What needs to be done?")` `[A — 0.88]`
- **SauceDemo**: Auth-gated app — `input[type='password']` → `get_by_test_id("password")` `[A — 0.95]`; correctly refused to patch post-login buttons probed from unauthenticated URL
- **BooksToScrape**: Multi-page deep hierarchy — `.nav-list > li > ul > li:nth-child(3) > a` → `get_by_role("link", name="Historical Fiction")` `[A — 0.88]`; left downstream content selectors untouched (no false-positive remediation)
- **GitHub + Wikipedia**: Confirmed `[F — 0.00]` refusal on zero-semantic CSS paths; `--ai-fallback` correctly recovered both via LLM accessibility snapshot inference

### Fixed
- DOM extraction logic in `cli.py` and `crawler.py`
- PR diff tooling in `patcher.py`

---

## [2.0.0] — 2026-03-17

### Summary
Major architectural pivot: QAPAL evolves from an AI test-generation platform into a **Locator Intelligence Engine** for Playwright. Core philosophy unchanged — AI plans once, code executes deterministically.

### Added
- **`probe.py`** — standalone 4-step locator resolution engine extracted from `executor.py`
  - DB chain → primary selector → fallback → AI rediscovery
  - `ProbeResult`, `ElementInfo` dataclasses
- **`ranker.py`** — weighted selector scoring model
  - Factors: strategy (35%), uniqueness (30%), visibility (15%), interaction (10%), history (10%)
  - Strategy scores: `testid=1.0`, `id=0.9`, `role=0.8`, `aria-label=0.75`, `label=0.7`, `placeholder=0.65`, `text=0.5`, `css=0.3`, `xpath=0.2`
  - Letter grades A/B/C/D/F
- **`parser.py`** — regex-based selector parser for Python and TypeScript Playwright APIs
  - Handles snake_case (`get_by_test_id`) and camelCase (`getByTestId`)
  - `ParsedSelector`, `SelectorCandidate` dataclasses
- **`patcher.py`** — unified diff generator + bottom-to-top applicator (no line offset drift)
  - `preview_patches()` for dry-run inspection
  - `format_patch_summary()` for human-readable output
- **`scaffold.py`** — test file skeleton generator with pre-validated selectors; no AI required
  - Python and TypeScript output
- **`cli.py`** — new CLI commands: `analyze`, `fix`, `generate`, `probe`, `heal`
- **`action.yml`** — GitHub Action with inputs: `url`, `test-files`, `mode`, `min-confidence`, `create-pr`, `language`, `credentials-file`; outputs: `total-selectors`, `strong-selectors`, `weak-selectors`, `broken-selectors`, `patches-applied`, `pr-url`
- **`pyproject.toml`** — installable via `pip install qapal`, `qapal` CLI entry point, Python 3.10+
- **`tests/test_locator_engine.py`** — 95 unit tests for `ranker`, `parser`, `patcher`, `scaffold`; zero network dependencies
- **`.github/workflows/ci.yml`** — CI: unit tests (Python 3.10/3.11/3.12), syntax check, package build, PyPI publish on merge

### Changed
- `executor.py` — locator resolution chain refactored into `probe.py`; backward-compatible re-export maintained
- `locator_db.py` — added `build_chain` public alias for `_build_chain`
- `__init__.py` — updated exports for all new engine modules
- `generator.py` — `temperature=0` for deterministic plan generation; `min_count=2` nav graph filter; `_fix_url_assertions()` post-processor
- `state_graph.py` — `min_count` parameter; filters `navigate` trigger noise; prefix URL matching; caps reachable paths at 12
- `ai_client.py` — `temperature=0` default on all `complete()` / `acomplete()` signatures

### Fixed
- `UnboundLocalError` for `count` variable in executor locator resolution
- Testid OR-locator now covers `data-testid`, `data-test`, `data-cy`, `data-qa`
- `scroll_into_view_if_needed()` added before all interaction actions
- Fallback selector only returned when `count >= 1`
- Removed `set_test_id_attribute("data-test")` (superseded by OR-locator)

---

## [1.x] — Legacy AI Test Generation Platform

Earlier versions of QAPAL operated as an AI-in-the-loop test generation tool:
- `generator.py` + `planner.py` produced frozen JSON execution plans from PRD markdown files
- `executor.py` ran plans deterministically (zero AI in the happy path)
- `crawler.py` + `locator_db.py` persisted element discovery across sessions
- `replanner.py` provided self-healing recovery (capped at 1 replan, 3 unknown states)
- `state_graph.py` maintained a persistent navigation topology graph

The core determinism principle (`AI plans once, code executes`) is preserved and extended in v2.
