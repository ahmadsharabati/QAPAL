# QAPal - Deterministic AI Testing System

A test automation framework where **AI plans once, and code executes deterministically**.

## The Problem

Current AI testing tools let the model decide if a test passed. That's not testing — that's opinion. Results are non-reproducible, token-expensive, and untrustworthy in CI/CD.

## The Solution

Separate what the AI does from what the code does:

| AI (Planner) | Code (Executor) |
|--------------|-----------------|
| Reads test case | Executes actions |
| Maps to selectors | Verifies assertions |
| **One call** | **Zero AI calls** |
| ~500 tokens | Deterministic |

**The AI never sees the outcome.**

## Installation

```bash
pip install playwright tinydb anthropic
playwright install chromium
```

## Quick Start

```python
import asyncio
from locator_db import LocatorDB
from crawler import Crawler
from planner import Planner, AnthropicClient
from executor import Executor

async def main():
    # Initialize
    db = LocatorDB("locators.json")
    ai_client = AnthropicClient(api_key="sk-...")
    
    # Phase 1: Crawl (run once per app version)
    async with Crawler(db) as crawler:
        await crawler.bulk_crawl([
            "https://app.com/login",
            "https://app.com/dashboard",
        ])
    
    # Phase 2: Plan (cacheable)
    planner = Planner(db, ai_client)
    
    test_case = {
        "id": "TC001",
        "name": "User can log in",
        "steps": [
            {"action": "navigate", "url": "https://app.com/login"},
            {"action": "fill", "target": {"role": "textbox", "name": "Email"}, "value": "user@test.com"},
            {"action": "click", "target": {"role": "button", "name": "Sign In"}},
        ],
        "assertions": [
            {"type": "url_contains", "value": "/dashboard"},
        ],
    }
    
    plan = planner.create_plan(test_case)
    
    # Phase 3: Execute (deterministic)
    async with Executor(db) as executor:
        result = await executor.run(plan)
    
    print(f"Status: {result['status']}")  # pass or fail

asyncio.run(main())
```

## CLI Usage

```bash
# Crawl the app
python main.py crawl --config config.json

# Run tests
python main.py test --config config.json --tests tests/*.json

# Check status
python main.py status --db locators.json
```

## Architecture

```
┌──────────────┐     ┌──────────────┐     ┌──────────────┐
│   Crawler    │────→│   LocatorDB  │────→│   Planner    │
│              │     │              │     │              │
│ • Crawl URLs │     │ • locators   │     │ • Query DB   │
│ • Extract    │     │ • pages      │     │ • Call AI    │
│   elements   │     │ • sessions   │     │ • Output plan│
└──────────────┘     └──────────────┘     └──────┬───────┘
                                                  │
                                                  ▼
                                           ┌──────────────┐
                                           │     Plan     │
                                           │   (frozen)   │
                                           └──────┬───────┘
                                                  │
                                                  ▼
                     ┌──────────────┐     ┌──────────────┐
                     │   Executor   │────→│   Results    │
                     │              │     │              │
                     │ • No AI      │     │ • pass/fail  │
                     │ • DOM only   │     │ • screenshots│
                     └──────────────┘     └──────────────┘
```

## Test Case Format

```json
{
  "id": "TC001",
  "name": "User can log in",
  "url": "https://app.com/login",
  "steps": [
    {"action": "navigate", "url": "https://app.com/login"},
    {"action": "fill", "target": {"role": "textbox", "name": "Email"}, "value": "user@test.com"},
    {"action": "click", "target": {"role": "button", "name": "Sign In"}}
  ],
  "assertions": [
    {"type": "url_contains", "value": "/dashboard"},
    {"type": "element_visible", "target": {"role": "button", "name": "Log Out"}}
  ]
}
```

## Supported Actions

| Category | Actions |
|----------|---------|
| Navigation | `navigate`, `refresh`, `go_back`, `go_forward` |
| Interaction | `click`, `dblclick`, `hover`, `scroll` |
| Input | `fill`, `type`, `clear`, `press`, `select` |
| State | `check`, `uncheck`, `focus`, `blur` |
| Utility | `wait`, `screenshot`, `evaluate` |

## Supported Assertions

| Category | Assertions |
|----------|------------|
| URL | `url_equals`, `url_contains`, `url_matches` |
| Page | `title_equals`, `title_contains` |
| Existence | `element_exists`, `element_not_exists`, `element_count` |
| Visibility | `element_visible`, `element_hidden` |
| State | `element_enabled`, `element_disabled`, `element_checked`, `element_unchecked`, `element_focused` |
| Content | `element_text_equals`, `element_text_contains`, `element_value_equals` |
| Attribute | `element_attribute`, `element_has_class` |
| Custom | `javascript` |

## Selector Strategies

```json
// ARIA role + name (preferred)
{"strategy": "role", "value": {"role": "button", "name": "Submit"}}

// data-testid attribute
{"strategy": "testid", "value": "submit-btn"}

// CSS selector (fragile)
{"strategy": "css", "value": "form > button.primary"}

// Label text
{"strategy": "label", "value": "Email Address"}

// Placeholder
{"strategy": "placeholder", "value": "Enter your email"}
```

## Token Savings

| Approach | Tokens/Test |
|----------|-------------|
| Traditional AI loop | ~4,000 |
| QAPal (one call) | ~500 |
| **Savings** | **87.5%** |

## Project Structure

```
qapal/
├── __init__.py          # Package exports
├── locator_db.py        # TinyDB wrapper for locators
├── crawler.py           # Page crawler
├── planner.py           # AI planner (one call)
├── executor.py          # Deterministic executor
├── actions.py           # Action definitions
├── assertions.py        # Assertion definitions
├── main.py              # CLI orchestrator
├── config/
│   └── config.json      # Configuration
└── tests/
    ├── TC001_login.json
    ├── TC002_add_to_cart.json
    └── ...
```

## Environment Variables

```bash
export QAPAL_BASE_URL="https://app.com"
export QAPAL_AI_API_KEY="sk-..."
export QAPAL_AI_PROVIDER="anthropic"
export QAPAL_DB_PATH="locators.json"
export QAPAL_HEADLESS="true"
```

## License

MIT
