# QAPAL Project Structure & Architecture

## 1. Directory Structure

```text
QAPAL/
├── main.py                 # Unified CLI entry point (crawl, plan, run, prd-run, graph)
├── crawler.py              # Page crawler (extracts locators & spidering)
├── generator.py            # AI Test Generator (PRD -> JSON Plans)
├── executor.py             # Deterministic Executor (JSON Plans -> Playwright actions)
├── planner.py              # Test Planner (Lower-level plan creation logic)
├── replanner.py            # Recovery logic for unknown states (Self-healing)
├── semantic_extractor.py   # Page understanding (Crawl4AI / A11y tree)
├── state_graph.py          # Navigation mapper (builds URL transition graph)
├── actions.py              # Action Registry (Contract for UI interactions)
├── assertions.py           # Assertion Registry (Contract for state verification)
├── locator_db.py           # TinyDB wrapper (Stores locators, states, and graph)
├── ai_client.py            # AI Provider wrapper (OpenAI/Anthropic)
├── locators.json           # Central database (persistent state)
├── plans/                  # Generated test execution plans (JSON)
├── reports/                # Execution reports & screenshots
└── docs/                   # PRDs and specifications
```

## 2. Working Diagram (Data Flow)

```mermaid
graph TD
    PRD[Markdown PRD] --> Generator
    
    subgraph "Planning Phase"
        Generator[Test Generator]
        DB[(Locator DB)] --> Generator
        SG[State Graph] --> Generator
        SE[Semantic Extractor] --> Generator
        Registry[Action & Assertion Registries] -.-> Generator
    end
    
    Generator --> Plan[JSON Execution Plan]
    
    subgraph "Execution Phase"
        Plan --> Executor
        Executor[Deterministic Executor]
        Registry -.-> Executor
        Executor --> Playwright[Playwright / Browser]
        Playwright --> |State Change| SG
    end
    
    subgraph "Recovery Phase (Self-Healing)"
        Executor --> |Element Not Found / Unknown State| Replanner
        Replanner[AI Replanner] --> |New Steps| Executor
        Playwright --> |New Page Snapshot| SE
        SE --> |Semantic Context| Replanner
    end
    
    Executor --> Report[JSON Results / Screenshots]
```

## 3. Component Breakdown

### 3.1 Planning Layer
*   **TestGenerator**: The brain of the system. Reads the PRD, pulls active locators from the DB, fetches navigation paths from the `StateGraph`, and uses the `SemanticExtractor` to understand page structures. Produces deterministic JSON test plans.
*   **Planner**: Handles the mapping of high-level test cases to specific locators stored in the DB.
*   **Two-Pass Validator**: A fast secondary validation layer (often using a smaller model like Haiku or GPT-4o-mini) that evaluates generated steps against the target site's raw DOM or known locators to prune un-actionable steps.

### 3.2 Execution Layer
*   **Executor**: A strict Playwright-based runner. It follows the plan exactly without real-time AI "loops" (unless replanning).
*   **Agentic Constraints**: Automatically enforces element interaction rules, semantic strategies (e.g., using `role` or `text` chains instead of brittle `css`), and strict assertion operators (`element_count`, `element_has_class`).
*   **Dynamic Navigation Repair**: Uses the `StateGraph` to intercept navigation to dynamic URLs (UUIDs/ULIDs) and patches the sequence to use category/list navigation instead to avoid 404s.

### 3.3 Application Understanding Layer
*   **State Graph**: Learns the "map" of the application by observing transitions. It provides the AI with real-world routes (e.g., "to get to /settings, first click /profile").
*   **Semantic Extractor**: Uses Crawl4AI or Accessibility Trees to "describe" a page to the AI in terms of roles (buttons, forms, links) rather than raw HTML.
*   **Auto-Spidering (Crawler)**: Triggers automatically on unrecognized sites to aggressively map components, buttons, and locators before generation begins (`Zero-Config Discovery`).

### 3.4 Self-Healing Layer
*   **Replanner**: Triggered only when a test breaks. It analyzes the current page's semantic context and the remaining steps, then requests a "patch" from the AI to recover.
*   **Strategy Recovery**: Maps invalid `data-testid` guesses (hallucinations on plain-HTML sites) to valid `role` and `text` locators via fuzz-matching in `generator.py`.

### 3.5 Contract Layer
*   **actions.py**: Defines every supported UI interaction (click, fill, select). Serves as a unified contract between the AI and the Executor.
*   **assertions.py**: Defines robust verification standards (`url_contains`, `element_visible`). Allows soft validation options to prevent test flakiness.
