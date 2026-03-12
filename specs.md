# QAPAL PRD-to-Test Automation Specifications

## Features Included

### 1. Zero-Config PRD to Execution (`prd-run`)
- **Ingestion**: Reads a human-written Markdown PRD detailing feature acceptance criteria.
- **Context Injection**: Pre-crawls the specified URL(s) and feeds the exact live accessibility-tree (A11y) to the AI along with the PRD.
- **Single-Call AI Planning**: Generates deterministic, strictly-mapped execution plans referencing exact elements in *one single AI call* to save tokens and time. 
- **Immediate Execution**: Immediately routes the generated JSON plans into the `Executor` to perform the UI manipulations without human intervention.
- **Reporting**: Dumps a final pass/fail `.json` report in the `reports/` directory.

### 2. Intelligent Dynamic Element Handling
- **Rule-Based Targeting**: The generator prompt is explicitly instructed that any newly created element (e.g., submitting a form and seeing a new row appear) will *not* be in the pre-crawled locator list.
- **Fallback Invention**: For these mid-test elements, the AI is allowed to invent a `strategy: "text"` or `strategy: "css"` assertion selector on the fly (setting `element_id: null`).
- **Result**: The test can seamlessly assert that actions successfully modified the DOM without needing costly re-crawls midway through test execution.

---

## How Does QAPAL Handle DOM Changes?

If the website changes its layout, CSS classes, or even exact text, QAPAL employs a multi-tiered recovery system to prevent tests from being brittle:

### Tier 1: Semantic Locator Priority
QAPAL avoids raw CSS matching when possible. It relies heavily on `testids` and `role+name` (e.g., `role: button, name: Submit`). If the button moves to a different generic `div` on the page, the test still succeeds perfectly because the role and name are unchanged.

### Tier 2: The "Fallback" Selector
During the generation phase, `generator.py` maps every element step to a primary selector and an immediate fallback (e.g., Primary: `role=button`, Fallback: `testid=btn-submit`). If the primary fails, the Executor instantly tries the fallback before raising an exception.

### Tier 3: AI Rediscovery (`QAPAL_AI_REDISCOVERY=true`)
If both the primary and fallback fail (which usually means the structure changed significantly or text was altered), the test does **not** fail immediately.
1. The Executor extracts a fresh, live snapshot of the accessibility tree of the current broken page.
2. It sends this snapshot to the AI along with the details of what it *was* trying to click on.
3. The AI finds the new corresponding element.
4. The Executor performs the action on the newly discovered element, and permanently maps this new path into the `LocatorDB`. Subsequent executions will use the new, correct path natively without needing the AI.
