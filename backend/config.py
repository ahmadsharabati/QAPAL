"""
Application settings — loaded from environment variables / .env file.

All config is centralized here. No magic strings in routers or services.
"""

from pydantic_settings import BaseSettings
from typing import List


class Settings(BaseSettings):
    """
    Backend configuration.  Reads from env vars or .env file.
    Override any field with an environment variable of the same name.
    """

    # --- App ---
    APP_NAME: str = "QAPAL"
    APP_VERSION: str = "1.0.0"
    DEBUG: bool = False

    # --- Database ---
    DATABASE_URL: str = "sqlite:///qapal.db"

    # --- Auth ---
    SECRET_KEY: str = "dev"  # "dev" enables stub auth for local testing
    JWT_ALGORITHM: str = "HS256"

    # --- CORS ---
    CORS_ORIGINS: List[str] = [
        "http://localhost:3000",
        "http://localhost:5173",
        "chrome-extension://*",
    ]

    # --- Quota ---
    FREE_TIER_LIMIT: int = 5
    STARTER_TIER_LIMIT: int = 50
    PRO_TIER_LIMIT: int = -1  # unlimited

    # --- Test Auth (Phase 3) ---
    QAPAL_TEST_USER: Optional[str] = None
    QAPAL_TEST_PASS: Optional[str] = None

    # --- Scan (Deep Scan engine) ---
    SCAN_TIMEOUT_SECONDS: int = 300
    SCAN_MAX_DEPTH: int = 2
    SCAN_MAX_PAGES_DEFAULT: int = 3
    SCAN_NUM_TESTS: int = 3
    SCAN_EXEC_CONCURRENCY: int = 2
    SCAN_TRACE_DIR: str = "/tmp/qapal_traces"

    # --- Browser Pool ---
    # Max concurrent Playwright browser contexts kept warm across scans.
    # Each context uses ~200-400MB RAM. Size to (available RAM - 1GB) / 400MB.
    # t3.medium (4GB): 4  |  t3.large (8GB): 8  |  t3.xlarge (16GB): 16
    BROWSER_POOL_SIZE: int = 4
    BROWSER_HEADLESS: bool = True

    model_config = {
        "env_file": ".env",
        "env_file_encoding": "utf-8",
        "case_sensitive": True,
        "extra": "ignore",  # ignore QAPAL_* engine vars in .env
    }


# Singleton — import this everywhere
settings = Settings()
