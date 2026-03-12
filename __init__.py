"""
QAPal - Deterministic AI Testing System
=======================================

A test automation framework where AI plans once, and code executes deterministically.

The system separates AI planning from deterministic execution:
  - Crawler: Maps the app, extracts UI elements, stores in DB
  - Planner: Queries DB, calls AI once, outputs frozen plan
  - Executor: Runs plan deterministically, no AI in the loop

Key principles:
  - AI never judges test outcomes
  - AI only suggests locators during planning
  - All assertions are DOM-based and deterministic
  - Plans are cacheable and versionable

Usage:
    from qapal import LocatorDB, Crawler, Planner, Executor
    
    # Initialize
    db = LocatorDB("locators.json")
    
    # Phase 1: Crawl
    async with Crawler(db) as crawler:
        await crawler.bulk_crawl(["https://app.com/"])
    
    # Phase 2: Plan
    planner = Planner(db, ai_client)
    plan = planner.create_plan(test_case)
    
    # Phase 3: Execute
    async with Executor(db) as executor:
        result = await executor.run(plan)
"""

from .locator_db import LocatorDB
from .crawler import Crawler, crawl_page, wait_for_stable
from .planner import Planner, PlanningError
from .executor import Executor
from .actions import ACTIONS, validate_action, get_action
from .assertions import ASSERTIONS, validate_assertion, get_assertion
from .ai_client import AIClient

__version__ = "1.0.0"
__all__ = [
    # Core classes
    "LocatorDB",
    "Crawler",
    "Planner",
    "Executor",
    "AIClient",
    
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
