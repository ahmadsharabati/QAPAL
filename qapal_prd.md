# QAPAL - Product Requirements Document (PRD)

## 1. Product Overview
QAPAL is a professional-grade UI automation framework that leverages AI to achieve **Deterministic Execution** while maintaining a **Zero-Boilerplate** experience. Unlike traditional "black-box" AI agents, QAPAL separates the reasoning phase (Dynamic Planning) from the automation phase (Deterministic Execution), ensuring tests are fast, reliable, and 100% auditable.

## 2. Core Value Proposition
- **Zero-Boilerplate**: Generate robust E2E test suites directly from Markdown PRDs or user requirements.
- **Extreme Token Efficiency**: Uses compact locator databases and semantic context instead of expensive, noisy raw DOM snapshots.
- **Persistent Intelligence**: A self-learning "State Graph" accumulates application navigation knowledge across all test runs.
- **CI/CD Native**: Built for stability with deterministic Playwright steps—no real-time AI "loops" during execution unless recovery is needed.

## 3. Key Features

### 3.1 Advanced AI Reasoning & Safety
- **Two-Pass Validation**: Every test plan undergoes a primary generation phase followed by a high-speed "small model" validation pass (e.g., Claude Haiku or GPT-4o-mini) to verify and fix selector mismatches.
- **Self-Correcting Strategies**: Automatically detects sites without `data-testid` and repairs hallucinated selectors by mapping them to the most relevant ARIA roles and labels in the locator database.
- **Dynamic Navigation Repair**: Intelligent detection of fragile, dynamic URLs (e.g., ULIDs/UUIDs). Automatically converts direct navigations into reliable category-traversal sequences.

### 3.2 Autonomous Discovery (Layer 2)
- **Zero-Config First Run**: Automatically detects sparse navigation graphs and triggers interactive spidering to map the application before the first test is generated.
- **State Graph Engine**: Records Source -> Action -> Sink transitions to build a persistent "topology" of the site.
- **Semantic Extraction**: Uses Crawl4AI to provide the AI with a structural understanding of the UI, focusing on intent over raw HTML.

### 3.3 Robustness & Self-Healing (Layer 3)
- **Real-Time Replanner**: On-the-fly patching for tests that encounter broken locators or unexpected UI states.
- **Style-Agnostic Assertions**: Validation logic that matches raw data, ignoring visual decorators like formatting or UI badges.
- **Hierarchical Selector Chain**: Multi-strategy fallback (TestID -> Role -> Label) with automatic retry logic.

### 3.4 Enterprise CLI & Scale
- **Multi-PRD Environment**: Automatic slug-based plan prefixing allows QAPAL to test multiple complex features or disparate applications from a single environment without test-ID overclashes.
- **Precision Generation**: Granular control via `--num-tests N` to generate the exact coverage needed.
- **Comprehensive Reporting**: Structured JSON reports with timing, step-by-step outcomes, and failure diagnostics.

## 4. Technical Architecture

- **Language**: Python 3.10+
- **Automation Driver**: Playwright (Optimized for headless performance)
- **Storage Layer**: TinyDB (`locators.json`) for zero-latency storage of locators, states, and graph transitions.
- **AI Intelligence**: Optimized for multi-model pipelines (Sonnet for planning, Haiku for validation).
- **Security**: Local persistent storage; secure credential injection for authenticated flows.

## 5. Roadmap
- **Visual Baseline Engine**: Screenshot-based visual regression with automatic delta-reporting.
- **Parallel Contexts**: Multi-browser concurrent test execution (`--parallel N`).
- **CI/CD Integrations**: Native plugins for major CI platforms and issue trackers.
