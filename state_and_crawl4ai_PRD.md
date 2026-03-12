# QAPAL Feature PRD

## Feature: Unknown State Recovery + Crawl4AI Semantic Context

### Goal

Enhance QAPAL to automatically recover from unknown states during test execution and provide semantic AI context using Crawl4AI.

Benefits:

1. The AI planner can make better decisions about next steps for unknown states.
2. Execution remains deterministic.
3. Minimal token usage since Crawl4AI output is structured.

---

## Problem

Currently:

* Executor may encounter an unknown state (e.g., `/dashboard` after login).
* Without prior locators or semantic context, the planner cannot make meaningful decisions.
* Tests fail if locators are not found.

We need:

* Dynamic AI-assisted locator extraction.
* Semantic context for the planner (page roles, buttons, tables, links).

---

## Solution Overview

1. Executor detects unknown state.
2. Executor triggers state snapshot (DOM + accessibility tree).
3. Crawl4AI extracts semantic content:

   * Page description
   * Roles of elements
   * Tables, forms, navigation links
4. AI planner receives:

   * PRD
   * Execution history
   * New state locators
   * Crawl4AI semantic context
5. Planner generates new steps.
6. Executor continues execution.

---

## System Architecture

```
Crawler (initial)
   ↓
State DB + Locator DB
   ↓
Crawl4AI extraction (initial semantic context)
   ↓
Planner
   ↓
Executor
   ↓
Unknown state detected?
   ├─ Yes → State Snapshot
   │       ↓
   │   Update State DB
   │       ↓
   │   Replanner (AI) with updated Crawl4AI context
   │       ↓
   │   Resume execution
   └─ No → continue
```

---

## Crawl4AI Step

### Purpose

* Convert raw DOM snapshot into AI-friendly structured data.
* Summarize page elements in semantic terms: buttons, forms, tables, links.
* Provide context for AI planning, not for execution.
* Triggered at the beginning of the test to provide initial semantic context for planning.

### Example Crawl4AI Output

```json
{
  "page": "Dashboard",
  "description": "Main dashboard showing user management and statistics",
  "buttons": ["Create User", "Logout"],
  "links": ["/settings", "/reports"],
  "tables": ["Users Table", "Activity Logs"],
  "forms": ["Search Users"]
}
```

This is attached to the state metadata and included in the initial planner input.

---

## Planner Context Injection

When planning or replanning:

```json
{
  "PRD": "...",
  "ExecutionHistory": [
    "goto /login",
    "fill email",
    "fill password",
    "click login"
  ],
  "CurrentState": {
    "url": "/dashboard",
    "locators": [...],
    "semantic_context": Crawl4AI_output
  }
}
```

Planner now knows what each element represents, reducing hallucination.

---

## Token Usage Strategy

1. Crawl4AI output is structured and concise (~300–500 tokens per page).
2. Planner call includes:

   * PRD (~300 tokens)
   * Execution history (~150–200 tokens)
   * State locators + Crawl4AI semantic summary (~500 tokens)
3. Total tokens per recovery call: ~1000–1200 tokens.
4. Once new state locators are saved, future tests require zero AI calls for this state.

---

## Unknown State Execution Flow

1. Executor runs step → fails to find locator.
2. Detects unknown state (new URL or <30% locator match).
3. Captures DOM snapshot.
4. Updates Crawl4AI semantic context.
5. Saves state + locators + semantic context in DB.
6. Triggers replanning with AI → new steps generated.
7. Retry failed step.
8. Continue test normally.

---

## Edge Cases & Handling

### Infinite loops / Replan limits

* Max replans per test: 1
* Max unknown states per test: configurable (default: 3)

### Redirect loops

* If the same URL is visited repeatedly >3 times → test fails.

### Modals

* URL unchanged → treat as same state
* Snapshot only includes overlay elements

### Dynamic content

* Tables / new rows → locator = null, use text-based assertion
* Crawl4AI provides semantic labels to help AI choose which rows to check

### Partial DOM changes

* Same page but new section appears → merge new elements into existing state

---

## Data Model Changes

Add `semantic_context` to StateDB:

```json
{
  "state_id": "dashboard_default",
  "url": "/dashboard",
  "dom_hash": "abc123",
  "elements": [...],
  "semantic_context": Crawl4AI_output
}
```

---

## Success Metrics

* > 85% of unknown state failures recover automatically.
* Multi-page flows continue without manual locator addition.
* Semantic context reduces AI hallucinations and improves plan accuracy.
* Token usage is minimized: only one AI call per new state.

---

## Benefits

1. Self-learning test execution
2. Multi-page robustness
3. Minimal token usage
4. Better AI understanding of new states

---

## Future Improvements

* Build state graph for smarter planning.
* Visual diffing of DOM changes for dynamic content.
* Auto-prioritize elements in semantic context for faster planning.
