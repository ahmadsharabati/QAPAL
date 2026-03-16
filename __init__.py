"""
QAPal - AI-Powered UI Testing & UX Intelligence
================================================

A test automation framework with two modes:

1. **Deterministic Testing** — AI plans once, code executes deterministically.
   - Crawler: Maps the app, extracts UI elements, stores in DB
   - Planner: Queries DB, calls AI once, outputs frozen plan
   - Executor: Runs plan deterministically, no AI in the loop

2. **Vision-Enabled Exploratory Testing** — Autonomous app exploration & UX audit.
   - Explorer: Vision-guided DFS navigation discovers pages and UX issues
   - UX Evaluator: Rule-based (DOM) + vision-based (VLM) heuristic evaluation
   - UX Report: Rich HTML reports with severity-ranked findings

Usage:
    from qapal import LocatorDB, Crawler, Planner, Executor
    from qapal import Explorer, VisionClient, UXEvaluator

    # Deterministic testing
    db = LocatorDB("locators.json")
    async with Crawler(db) as crawler:
        await crawler.bulk_crawl(["https://app.com/"])
    planner = Planner(db, ai_client)
    plan = planner.create_plan(test_case)
    async with Executor(db) as executor:
        result = await executor.run(plan)

    # Exploratory UX testing
    vision = VisionClient.from_env()
    async with Explorer(db, vision_client=vision) as explorer:
        trace = await explorer.explore("https://app.com")
"""

from .locator_db import LocatorDB
from .crawler import Crawler, crawl_page, wait_for_stable
from .planner import Planner, PlanningError
from .executor import Executor
from .actions import ACTIONS, validate_action, get_action
from .assertions import ASSERTIONS, validate_assertion, get_assertion
from .ai_client import AIClient
from .vision_client import VisionClient
from .explorer import Explorer
from .ux_evaluator import UXEvaluator, UXAuditResult

__version__ = "2.0.0"
__all__ = [
    # Core classes
    "LocatorDB",
    "Crawler",
    "Planner",
    "Executor",
    "AIClient",

    # Vision & exploration
    "VisionClient",
    "Explorer",
    "UXEvaluator",
    "UXAuditResult",

    # Errors
    "PlanningError",

    # Functions
    "crawl_page",
    "wait_for_stable",
    "validate_action",
    "validate_assertion",
    "get_action",
    "get_assertion",

    # Registries
    "ACTIONS",
    "ASSERTIONS",
]
