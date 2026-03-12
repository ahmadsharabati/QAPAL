# Graph-Crawl Engine — Product Requirements Document

## Overview

The Graph-Crawl Engine is a site-exploration command that navigates an application
the way a real user would — by following links — and simultaneously builds two
artefacts: a **locator database** (elements on each page) and a **State Graph**
(how pages connect to each other). It replaces ad-hoc spider logic and eliminates
the need for regex-based URL deduplication by relying on the graph's own edge
semantics to represent the site's structure.

## Problem Statement

Before the Graph-Crawl Engine existed, QAPAL had two options for learning about
a target application:

1. **Manual URL list** — engineers enumerate every URL to crawl; impractical for
   large sites and error-prone (pages get missed).
2. **Spider with regex deduplication** — follows links but uses regular expressions
   to avoid crawling duplicate page types (e.g. every product page). Fragile:
   regex patterns break when URL schemes change and incorrectly merge or split
   page types.

Neither option populated the State Graph, so the AI planner had no navigation
context on the first run and had to guess multi-step paths.

## Goals

- Navigate the application naturally (BFS over clickable links) from one or more
  entry points.
- Record every page-to-page navigation as a State Graph edge, capturing the link
  text as the trigger label.
- Crawl each newly discovered page for locators (populating the locator DB).
- Respect a configurable depth and page cap to keep runtime predictable.
- Support authenticated applications via the existing credentials file mechanism.
- Expose the command as `python main.py graph-crawl`.

## Non-Goals

- Does not replace normal test execution (`run`, `prd-run`). It is a one-time or
  periodic seeding step, not a continuous operation.
- Does not click buttons, fill forms, or exercise dynamic interactions. Only
  `<a href>` links are followed.
- Does not deduplicate URLs by pattern — the graph's edge semantics handle
  structural deduplication naturally (two product pages create two separate edges
  from their category page, but both edges share the same trigger label pattern
  and the graph's BFS will prefer higher-count edges).
- Does not generate or execute test plans.

## Features

### 1. BFS Navigation with Transition Recording

Starting from one or more seed URLs, the engine performs a breadth-first traversal:

1. Navigate to the current URL.
2. Extract all `<a href>` links visible on the page.
3. For each same-domain link, record a State Graph transition:
   - `from_url` = current page (normalized)
   - `to_url` = link destination (normalized)
   - `trigger_action` = `"click"`
   - `trigger_label` = link text (first 60 characters)
   - `session_id` = `"graph-crawl"`
4. Add unvisited destinations to the BFS queue for the next depth level.
5. Crawl the current page for locators (element extraction).

### 2. Page Cap and Depth Limit

Two independent limits prevent runaway crawls:

| Parameter | CLI flag | Default | Description |
|---|---|---|---|
| Max depth | `--depth N` | 2 | Stop following links beyond N hops from the seed |
| Max pages | `--max-pages N` | 40 | Hard cap on total pages crawled |

When either limit is reached, the engine stops discovering new pages but
completes crawling of already-queued pages.

### 3. Authentication Support

The engine accepts `--credentials-file` (same format as `crawl` and `prd-run`)
and reuses the existing `_build_context` / `_run_login` infrastructure. Session
state is saved to the database after first login and reused for all subsequent
page visits, so login happens at most once per domain per run.

### 4. Locator DB Population

Every page visited is passed through the full locator extraction pipeline
(`crawl_page`), storing interactive elements in the same `locators.json`
database used by the planner and executor. This means a single `graph-crawl`
invocation is sufficient to seed both the navigation graph and the element DB
before running `prd-run`.

### 5. Progress Output

For each page visited, the engine prints:

```
  [graph-crawl] (12/40) https://app.com/category/hand-tools — 47 elements | 38 new
```

On completion:

```
 Graph-crawl complete
   pages crawled : 18
   graph edges   : 94  (pages: 18)
   duration      : 43210ms
```

## CLI Reference

```
python main.py graph-crawl \
  --urls https://app.com \
         https://app.com/auth/login \
  --credentials-file creds.json \
  --depth 2 \
  --max-pages 40 \
  --headless
```

| Flag | Default | Description |
|---|---|---|
| `--urls` / `-u` | required | One or more entry-point URLs |
| `--depth N` | 2 | Max BFS depth from seed URLs |
| `--max-pages N` | 40 | Max total pages to crawl |
| `--headless` / `-H` | false | Run browser without a visible window |
| `--credentials-file FILE` | none | JSON credentials for authenticated sites |

## Recommended Workflow

```
# Step 1 — Build graph + locator DB (run once per environment, re-run when app changes)
python main.py graph-crawl \
  --urls https://app.com https://app.com/auth/login \
  --credentials-file creds.json \
  --depth 2 --headless

# Step 2 — Inspect what was discovered
python main.py graph --stats
python main.py graph --path https://app.com/auth/login https://app.com/checkout

# Step 3 — Generate and run tests (graph context injected automatically into AI prompts)
python main.py prd-run \
  --prd my_prd.md \
  --url https://app.com https://app.com/auth/login \
  --credentials-file creds.json \
  --max-cases --headless
```

## Acceptance Criteria

### AC1 — Basic Navigation Recording
- After running `graph-crawl` from a seed URL with `--depth 1`, the State Graph
  contains at least one transition whose `from_url` equals the normalized seed URL.
- The transition's `trigger_action` is `"click"` and `trigger_label` is the
  visible link text from the page.

### AC2 — Locator Population
- After running `graph-crawl`, `python main.py status` shows `total_elements > 0`
  for each crawled URL.
- The locator DB contains entries for pages discovered via link-following, not just
  the seed URLs.

### AC3 — Depth Limit
- With `--depth 1`, only pages directly linked from the seed are crawled.
  Pages two hops away are recorded as graph edges but not crawled for locators.
- With `--depth 0`, only the seed URLs themselves are crawled; no links are followed.

### AC4 — Page Cap
- With `--max-pages 5`, no more than 5 pages are crawled regardless of depth or
  the number of links discovered.

### AC5 — Authentication
- When `--credentials-file` is supplied and the seed URL redirects to a login page,
  the engine logs in and proceeds with crawling the authenticated pages.
- Session is saved to DB; re-running `graph-crawl` on the same domain reuses the
  saved session and does not log in again.

### AC6 — Same-Domain Only
- Links pointing to external domains are recorded as graph edges but NOT added to
  the BFS queue and NOT crawled for locators.

### AC7 — No Duplicate Page Visits
- Each normalized URL is visited at most once per `graph-crawl` invocation, even
  if multiple pages link to the same destination.

### AC8 — State Graph Edge Deduplication
- Running `graph-crawl` twice on the same site increments the `traversal_count`
  on existing edges rather than creating duplicate records.

### AC9 — CLI Exit Codes
- `python main.py graph-crawl` exits 0 on success.
- Exits non-zero if no URLs are provided or if the browser fails to start.

## Test Cases

### TC001 — Single Depth Crawl
1. Run `graph-crawl --urls https://practicesoftwaretesting.com --depth 1 --headless`.
2. Assert exit code 0.
3. Assert `python main.py graph --stats` shows `graph edges > 0`.
4. Assert `python main.py status` shows `total_elements > 0`.

### TC002 — Depth 0 Only Crawls Seed
1. Run `graph-crawl --urls https://practicesoftwaretesting.com --depth 0 --headless`.
2. Assert only one page is crawled (the seed URL).
3. Assert `graph edges = 0` (no links followed, no transitions recorded).

### TC003 — Page Cap Respected
1. Run `graph-crawl --urls https://practicesoftwaretesting.com --depth 2 --max-pages 3 --headless`.
2. Assert no more than 3 pages appear in `python main.py status` under `pages crawled`.

### TC004 — Authenticated Crawl
1. Run `graph-crawl` with `--credentials-file` pointing to valid credentials.
2. Assert pages behind authentication (e.g. `/account/profile`) appear in the graph.
3. Assert the DB session is saved (re-run does not trigger login again).

### TC005 — External Links Not Crawled
1. Run `graph-crawl` on a page that has links to `https://github.com`.
2. Assert `github.com` pages are NOT present in `python main.py status`.
3. Assert a transition edge pointing to the github URL IS recorded in the graph
   (the link exists; we just don't follow it).

### TC006 — Idempotent Re-run
1. Run `graph-crawl` twice with the same arguments.
2. Assert the number of pages in the DB does not double.
3. Assert `traversal_count` on edges incremented to 2.

### TC007 — prd-run Uses Graph Context
1. Run `graph-crawl` to seed the graph.
2. Run `prd-run --prd my_prd.md --url <seed>`.
3. Capture AI prompt (via fake AI client in tests).
4. Assert prompt contains "Navigation Graph" section with at least one edge.

### TC008 — Progress Output Format
1. Run `graph-crawl` with `--max-pages 5`.
2. Assert stdout contains lines matching `[graph-crawl] (N/5)`.
3. Assert summary line contains `pages crawled` and `graph edges`.

## Notes

- `graph-crawl` is additive: it never deletes existing locators or graph edges.
  Running it again after a deployment enriches the graph with new pages and
  increments traversal counts on existing edges.
- For applications where important pages are only reachable through form
  submissions or button clicks (e.g. checkout after adding to cart), `graph-crawl`
  alone is insufficient. Those transitions will be recorded by the executor during
  normal test runs and added to the graph automatically.
- The engine follows only `<a href>` links, not JavaScript navigation or button
  clicks. SPA applications that route entirely through JS events may require
  supplemental manual URL seeding.
