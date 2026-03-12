# State Graph Engine — Product Requirements Document

## Overview

The State Graph Engine teaches QAPAL how pages in an application connect to each other.
Rather than treating every URL as an isolated island, QAPAL now observes which UI actions
cause page transitions during test execution and records those edges in a persistent directed
graph. Over time the graph becomes an accurate map of the application's navigation flows,
which the AI planner uses to compute real multi-step paths instead of guessing them.

## Problem Statement

Current QAPAL test plans that involve multiple pages are fragile because:

1. The AI planner has no knowledge of how pages connect — it guesses navigation sequences
   and frequently produces incorrect intermediate steps.
2. When a PRD asks for something like "create a new user," the planner does not know that
   reaching `/users/create` requires first navigating through `/dashboard` then `/users`.
3. Each test run discards the navigation knowledge it produces, so the system never improves
   across runs.

## Goals

- Automatically record page transitions as a by-product of normal test execution.
- Build a persistent, queryable graph of URL → URL edges annotated with the triggering action.
- Inject the graph as navigation context into AI planning prompts to improve multi-page plan accuracy.
- Expose the graph through a CLI command so engineers can inspect and debug application flows.

## Non-Goals

- This feature does not replace the locator crawler; it complements it.
- It does not perform any AI inference on the graph itself — graph data is formatted as plain
  text and injected into existing AI prompts.
- It does not automatically discover new pages by following links (that remains the crawler's job).

## Application URL

State graph data is stored in the same database file as locators (default: `locators.json`).
There is no separate URL to test; the feature is exercised through the existing CLI commands
and the new `graph` subcommand.

## Features

### 1. Automatic Transition Recording

During every test run (`run` or `prd-run`), whenever a UI action causes the browser URL
to change, QAPAL records a transition edge containing:

- Source URL (normalized, query/fragment stripped)
- Destination URL
- Triggering action type (`click`, `fill`, `navigate`, etc.)
- Human-readable trigger label (element name, link text, or target URL)
- The raw Playwright selector used (for debugging)
- A traversal count that increments on every re-observation
- Timestamps (first seen, last seen)
- Session IDs (which test IDs caused this transition, capped at 10)

Transitions are deduplicated: the same logical navigation (same source, destination,
action type, and label) always maps to a single record whose count increments.

### 2. Persistent Graph Storage

Transitions are stored in a `transitions` table inside the existing TinyDB database
(`locators.json`). No separate file or database connection is required. All writes are
serialised through the existing LocatorDB threading lock.

The graph survives process restarts and accumulates across multiple test sessions.
More test runs produce a more complete and reliable graph.

### 3. BFS Path Finding

The engine exposes shortest-path queries between any two URLs using breadth-first search.
Higher-traversal-count edges are explored first, so the returned path prefers routes that
have been reliably observed over theoretical but untested routes.

Input: source URL, destination URL, optional max depth (default 8 hops)
Output: ordered list of transition edges forming the path, or None if unreachable

Multi-source BFS is also available: given a list of entry-point URLs, the engine returns
a map of every reachable page and the shortest path to reach it.

### 4. AI Prompt Injection

Every time the planner or generator calls the AI, the navigation graph is formatted as a
compact text block and injected into the prompt between the Semantic Context section and
the Available Locators section.

The injected block contains two parts:

**Edge list** — all recorded transitions relevant to the URLs under test, sorted by
traversal count descending, capped at 40 edges:

```
Known page transitions (from observed test runs):
  /login --[click "Sign In"]--> /dashboard  (5x observed)
  /dashboard --[click "Users"]--> /users  (3x observed)
  /users --[click "New User"]--> /users/create  (1x observed)
```

**Reachable paths** — shortest navigation path from each entry-point URL to every
reachable destination, formatted as breadcrumbs + action sequence:

```
Reachable navigation paths from entry points:
  /login → /dashboard → /users → /users/create
    (click "Sign In" then click "Users" then click "New User")
```

When the graph is empty or contains no data for the relevant URLs, the block renders as
a placeholder string and the AI falls back to its existing behaviour.

### 5. Graph CLI Command

A new `graph` subcommand lets engineers inspect the recorded transition data.

```
python main.py graph
```

Displays all recorded edges sorted by traversal count:

```
State Graph  [db: locators.json]
   graph edges  : 7
   unique pages : 4

 Edges (7 shown):
   https://app.com/login
     --[click "Sign In"]--> https://app.com/dashboard  (5x)
   https://app.com/dashboard
     --[click "Users"]--> https://app.com/users  (3x)
```

Subcommand options:

| Flag | Description |
|---|---|
| `--stats` | Show edge/page counts and most-traversed edges only |
| `--from-url URL` | Filter to edges originating from a specific URL |
| `--to-url URL` | Filter to edges leading to a specific URL |
| `--path FROM TO` | Compute and display the shortest navigation path between two URLs |

`--path` example output:

```
Shortest path (2 hops):
  1. https://app.com/login
     click "Sign In"
  2. https://app.com/dashboard
     click "Users"
  3. https://app.com/users
```

### 6. Status Integration

The existing `status` command now shows graph statistics alongside the existing DB metrics:

```
QAPal Status
   database        : locators.json
   total elements  : 97  (valid: 97)
   pages crawled   : 11
   semantic states : 2
   graph edges     : 7  (pages: 4)
   AI client       : openai / gpt-4o
```

## Acceptance Criteria

### AC1 — Transition Recording

- After running any test plan that includes a click or navigate action that causes a URL
  change, at least one transition record must exist in the database.
- Re-running the same plan increments the traversal count on existing edges rather than
  creating duplicate records.
- Transitions where from_url equals to_url are silently discarded.
- The test case ID (`session_id`) is stored on the transition record.

### AC2 — Navigate Action Recording

- When a `navigate` action causes a URL change (source URL ≠ destination URL), the
  transition is recorded with `trigger_action = "navigate"` and the target URL as the label.

### AC3 — Element-Triggered Navigation Recording

- When a `click`, `fill`, `check`, or other element-targeting action causes a URL change,
  the transition is recorded with the action type and the element name (from the selector)
  as the label.

### AC4 — Path Finding

- `get_path(from_url, to_url)` returns None when no recorded path connects the two URLs.
- `get_path(from_url, to_url)` returns an empty list when from_url equals to_url.
- `get_path` returns a list of transition dicts that form a valid chain
  (each edge's `to_url` equals the next edge's `from_url`).
- When multiple paths exist, the path with higher-count edges is preferred.

### AC5 — Prompt Injection

- When a `StateGraph` is provided to `Planner` or `TestGenerator`, the AI prompt contains
  a "Navigation Graph" section with at least the edge list.
- When no `StateGraph` is provided (default), the section renders as a placeholder string
  and no error is raised.
- When the graph has data for the relevant URLs, the "Reachable navigation paths" subsection
  is included in the prompt.

### AC6 — CLI: graph command

- `python main.py graph` exits 0 and prints edge data when transitions exist.
- `python main.py graph` exits 0 and prints a "no transitions recorded yet" message when
  the graph is empty.
- `python main.py graph --stats` prints edge count, unique page count, and most-traversed
  edges.
- `python main.py graph --path FROM TO` prints the shortest path when one exists, or a
  "no path found" message when unreachable.
- `python main.py graph --from-url URL` filters output to edges from that URL only.

### AC7 — Status Integration

- `python main.py status` displays `graph edges` and `pages` counts.

### AC8 — Backwards Compatibility

- All existing tests pass without modification.
- The `Executor`, `Planner`, and `TestGenerator` constructors accept `state_graph=None`
  (the default); when None, all behaviour is identical to the pre-feature baseline.
- Existing `locators.json` databases from before this feature are read without error;
  the `transitions` table is created lazily on first write.

## Test Cases

### TC001 — Record Transition on Click Navigation

1. Navigate to the TodoMVC app.
2. Add a todo item.
3. Click the "Active" filter link (causes URL change to `/#/active`).
4. Assert that a transition record exists with:
   - `from_url` = normalized TodoMVC base URL
   - `to_url` = normalized `/#/active` URL
   - `trigger.action` = "click"
   - `trigger.label` = "Active"
   - `traversal_count` ≥ 1

### TC002 — Deduplicate Repeated Transitions

1. Run TC001 twice.
2. Assert that only one transition record exists for the Active link click.
3. Assert that `traversal_count` = 2.

### TC003 — Record Navigate Action Transition

1. Execute a plan with a `navigate` step that goes from page A to page B (different URLs).
2. Assert a transition record exists with `trigger_action = "navigate"`.

### TC004 — No Self-Transition

1. Execute a `navigate` step where the destination equals the current URL.
2. Assert that no transition record is created for that step.

### TC005 — BFS Shortest Path — Direct Edge

1. Seed the graph with a single edge: `/login` → `/dashboard` via click "Sign In".
2. Call `get_path("/login", "/dashboard")`.
3. Assert the result is a list of length 1 containing the correct edge.

### TC006 — BFS Shortest Path — Two Hops

1. Seed the graph with edges:
   - `/login` → `/dashboard` via click "Sign In"
   - `/dashboard` → `/users` via click "Users"
2. Call `get_path("/login", "/users")`.
3. Assert the result is a list of length 2 with edges in the correct order.

### TC007 — BFS Shortest Path — Unreachable

1. Seed the graph with a single edge: `/login` → `/dashboard`.
2. Call `get_path("/login", "/settings")`.
3. Assert the result is None.

### TC008 — BFS Same-URL Path

1. Call `get_path("/login", "/login")`.
2. Assert the result is an empty list.

### TC009 — Prompt Contains Navigation Graph Section

1. Create a `Planner` with a `StateGraph` that has at least one transition.
2. Capture the AI prompt (via a fake AI client).
3. Assert the prompt contains "Navigation Graph".
4. Assert the prompt contains "Known page transitions".

### TC010 — Prompt Placeholder When Graph Empty

1. Create a `Planner` with a `StateGraph` that has no transitions.
2. Capture the AI prompt.
3. Assert the prompt contains "no navigation graph data yet".

### TC011 — Prompt Placeholder When No StateGraph

1. Create a `Planner` with `state_graph=None`.
2. Capture the AI prompt.
3. Assert the prompt contains "no navigation graph".
4. Assert no exception is raised.

### TC012 — graph CLI Empty DB

1. Point the CLI at an empty database.
2. Run `python main.py graph`.
3. Assert exit code 0.
4. Assert output contains "no transitions recorded yet".

### TC013 — graph CLI With Data

1. Seed the database with two transition records.
2. Run `python main.py graph`.
3. Assert exit code 0.
4. Assert output contains both edge descriptions.

### TC014 — graph CLI --path

1. Seed: `/login` → `/dashboard` → `/users` (two edges).
2. Run `python main.py graph --path /login /users`.
3. Assert output contains "2 hops".
4. Assert output lists both intermediate steps.

### TC015 — graph CLI --stats

1. Seed any transitions.
2. Run `python main.py graph --stats`.
3. Assert output contains `graph edges` count.
4. Assert output does not print the full edge list.

### TC016 — status Shows Graph Counts

1. Run `python main.py status`.
2. Assert output line `graph edges` is present.
3. Assert the count is a non-negative integer.

### TC017 — Full Pipeline Integration

1. Run `prd-run` against TodoMVC.
2. After completion, run `python main.py graph`.
3. Assert at least one transition was recorded (navigate or click that changed URL).

### TC018 — Backwards Compatibility: No StateGraph

1. Run the existing unit test suite (`python tests/test.py`).
2. Assert all tests pass.
3. Instantiate `Executor(db)`, `Planner(db, ai)`, `TestGenerator(db, ai)` without
   `state_graph` argument.
4. Assert no TypeError is raised.

## Application URL

```
N/A — this is a backend feature. Use the existing TodoMVC demo as the test target:
https://demo.playwright.dev/todomvc/#/
```

## Notes

- The graph is append-only by design. There is no automatic pruning. If transitions become
  stale (e.g., the application routing changes), engineers should clear the transitions table
  manually or via a future `graph --clear` flag.
- Traversal count is the primary quality signal. Edges observed only once should be treated
  as possibly flaky; edges observed 5+ times are highly reliable.
- The `session_ids` list on each edge (capped at 10) enables tracing which test IDs exercise
  a particular navigation path — useful for coverage analysis.
