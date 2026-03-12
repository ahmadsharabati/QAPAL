# QAPAL - Product Requirements Document (PRD)

## 1. Product Overview
QAPAL is a deterministic, AI-powered UI automation framework designed for maximum token efficiency and CI/CD reliability. It bridges the gap between human-readable requirements (PRDs) and robust, self-healing test execution by separating **Dynamic Planning** from **Deterministic Execution**.

## 2. Core Value Proposition
- **Zero-Boilerplate**: Generate full E2E suites from Markdown PRDs.
- **Token Efficiency**: Compact locator/graph context instead of raw DOM snapshots.
- **Self-Learning**: A persistent "State Graph" that learns application navigation over time.
- **Deterministic by Default**: AI plans once; Execution is 100% Playwright code (no real-time agent loops).

## 3. Product Features

### 3.1 Intelligent Generation
- **PRD-to-Plan**: Direct conversion of human requirements into executable JSON plans.
- **Multi-Test Batching**: Generates the maximum number of edge cases and "happy paths" from a single requirement.
- **Self-Documenting Registries**: Actions and Assertions are defined in code (Python) and automatically injected into AI prompts, ensuring the model never "hallucinates" unsupported interactions.

### 3.2 Application Understanding (Layer 2)
- **State Graph Engine**: Automatically discovery the site's "topology." Records transitions (e.g., Login -> Dashboard) and creates a persistent map of the application. This allows the AI to generate complex multi-page navigation plans based on observed reality rather than speculation.
- **Semantic Extraction**: Uses Crawl4AI and Accessibility Trees to provide high-level "meaning" to page states (forms, tables, primary actions), enabling the AI to reason about page structure without raw DOM noise.

### 3.3 Robustness & Self-Healing (Layer 3)
- **Unknown State Recovery**: Automatically detects when a test lands on an unexpected page or if a locator disappears. Triggers a "Replanner" to patch the test mid-execution.
- **Absolute Style-Blindness**: Assertions target verbatim text, ignoring visual decorations (e.g., strikethroughs, bold tags).
- **Fallback Chains**: Automatic retry of element lookup using alternative strategies (TestID -> Role -> Text).

## 4. Technical Architecture
- **Language**: Python
- **Engine**: Playwright (Headless/Headed)
- **AI Models**: Primarily Claude-3.5-Sonnet (Anthropic) or GPT-4o (OpenAI).
- **Storage**: Local TinyDB (`locators.json`) for zero-latency, persistent state (Locators, States, Transitions).

## 5. Roadmap
- **Visual Intelligence**: Integration of screenshot-based visual regression into the semantic assertion layer.
- **Plugin Ecosystem**: Native integrations for GitHub Actions, GitLab CI, and common bug trackers (Jira/Linear).
- **Auto-Spidering**: Proactive graph-crawling to map the entire application before the first test is even written.
