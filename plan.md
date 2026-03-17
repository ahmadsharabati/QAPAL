# Feature Test Generation — Implementation Plan

## Goal

Add a unified `generate` command that produces test plans from **three input modes**:

| Mode | Trigger | Input | Output |
|------|---------|-------|--------|
| **PRD** | `--prd file.md` | Markdown PRD | Test plans (existing flow, enhanced) |
| **Plain text** | `--text "..."` or `--text-file` | Natural language sentences/bullets | Test plans |
| **Auto-discover** | Neither `--prd` nor `--text` | Just URL(s) | Crawl → infer features → test plans |

All three modes produce the same JSON plan format and reuse the existing post-processing pipeline.

---

## New Files

### 1. `feature_generator.py` — Core module (~300 lines)

**Class: `FeatureTestGenerator`**

```python
class FeatureTestGenerator:
    def __init__(
        self,
        db: LocatorDB,
        ai_client: AIClient,
        state_graph=None,
        num_tests: Optional[int] = None,
        max_locators: int = 80,
        negative_tests: bool = False,
        compiled_model_path: Optional[str] = None,
    )
```

**Three public methods — one per mode:**

#### `generate_from_prd(prd_content, urls, credentials=None) -> list[dict]`
- Delegates to existing `TestGenerator.generate_plans_from_prd()`
- This is the pass-through mode — no new logic needed, just a unified entry point

#### `generate_from_text(text, urls, credentials=None) -> list[dict]`
- Takes free-form text (single sentence, bullet list, or paragraph)
- **Step 1:** Build the same locator/semantic/nav-graph context as generator.py
- **Step 2:** Single AI call with a new `_TEXT_TO_PLANS_SYSTEM` prompt that:
  - Accepts natural language test descriptions
  - Maps them to the same JSON plan format
  - Uses available locators (not invented selectors)
- **Step 3:** Runs through `TestGenerator._parse_plans()` for validation/repair
- Returns validated plans

#### `generate_from_discovery(urls, credentials=None) -> list[dict]`
- No PRD, no text — the AI figures out what to test
- **Step 1:** Crawl URLs (caller handles this, locators already in DB)
- **Step 2:** Load all locators + semantic contexts + nav graph
- **Step 3:** Single AI call with `_DISCOVERY_SYSTEM` prompt that:
  - Receives the full site map (pages, elements, navigation paths)
  - Identifies testable user flows (login, search, add-to-cart, form submission, navigation, etc.)
  - Generates test plans covering the discovered features
  - Prioritizes: auth flows > CRUD operations > navigation > edge cases
- **Step 4:** Runs through `_parse_plans()` for validation
- Returns validated plans

### AI Prompts (in `feature_generator.py`)

**`_TEXT_TO_PLANS_SYSTEM`** — System prompt for plain-text mode:
- Same rules as `_GENERATOR_SYSTEM` (selector strategies, JSON format, no invented URLs)
- Additional instruction: "The user describes tests in plain language. Convert each description into a deterministic test plan."
- Handles: single sentence, numbered list, bullet points, paragraph with multiple scenarios

**`_DISCOVERY_SYSTEM`** — System prompt for auto-discover mode:
- Same selector/action/assertion rules
- Additional instruction: "You are given a complete map of a web application (pages, interactive elements, navigation paths). Identify the most important user flows and generate test plans for them."
- Prioritization guidance: auth > forms > CRUD > navigation > content verification
- Must use ONLY elements present in the locator DB

---

## Changes to Existing Files

### 2. `main.py` — Add `generate` CLI command

New subcommand parser:

```
python main.py generate --url <urls> [--prd <files>] [--text "..."] [--text-file <path>]
                        [--num-tests N] [--negative-tests] [--compile]
                        [--credentials-file FILE] [--spider] [--depth N]
                        [--output DIR] [--max-locators N] [--force]
```

**Mode selection logic:**
- If `--prd` provided → PRD mode
- If `--text` or `--text-file` provided → plain text mode
- If neither → auto-discover mode

New async handler: `cmd_generate(args)`

**Flow:**
1. Crawl URLs (same as prd-run step 1)
2. Semantic extraction (same as prd-run step 2)
3. Optional compile (same as prd-run step 2.5)
4. Route to appropriate `FeatureTestGenerator` method based on mode
5. Save plans to `--output` dir (default: `plans/`)
6. Print summary (number of plans, test IDs)

### 3. `generator.py` — Extract reusable helpers

Make these methods accessible to `feature_generator.py`:
- `_parse_plans()` — already on `TestGenerator` class, will call via composition
- `_format_locators()` — already importable from `planner.py`
- `_format_semantic_contexts()` — already importable from `planner.py`

No structural changes needed — `FeatureTestGenerator` will compose a `TestGenerator` instance internally to reuse its `_parse_plans()` pipeline.

---

## Implementation Steps

### Step 1: Create `feature_generator.py`
- Define `FeatureTestGenerator` class
- Implement `generate_from_prd()` (delegates to `TestGenerator`)
- Write `_TEXT_TO_PLANS_SYSTEM` prompt
- Implement `generate_from_text()` with AI call + parse pipeline
- Write `_DISCOVERY_SYSTEM` prompt
- Implement `generate_from_discovery()` with AI call + parse pipeline

### Step 2: Add `generate` command to `main.py`
- Add argparse subcommand with all flags
- Implement `cmd_generate(args)` handler
- Wire mode selection logic
- Add plan saving + summary output

### Step 3: Add unit tests
- Add tests to `tests/test_new_modules.py` (or new file `tests/test_feature_generator.py`)
- Test mode selection logic
- Test plain-text parsing with mocked AI responses
- Test discovery prompt construction
- Test that PRD mode correctly delegates

### Step 4: Run tests + commit

---

## Design Decisions

1. **Composition over inheritance** — `FeatureTestGenerator` wraps `TestGenerator` rather than extending it. This keeps both classes focused.

2. **Single AI call per mode** — consistent with QAPAL's token-efficiency philosophy. Discovery mode makes one call to identify features AND generate plans simultaneously.

3. **Same plan format** — all three modes output identical JSON plans. The executor doesn't need any changes.

4. **Reuse post-processing** — `_parse_plans()` handles URL normalization, selector repair, prerequisite injection. No need to duplicate this.

5. **`generate` is plan-only** — it does NOT execute. Users run `python main.py run --plan ...` separately. This keeps the separation of concerns. (Users who want crawl+plan+run still use `prd-run`.)

---

## What This Does NOT Change

- `executor.py` — no changes (plans are the same format)
- `assertions.py` / `actions.py` — no changes
- `crawler.py` / `locator_db.py` — no changes
- `explorer.py` — not involved (exploration is a separate concept)
- Existing `prd-run` command — unchanged, still works as before
