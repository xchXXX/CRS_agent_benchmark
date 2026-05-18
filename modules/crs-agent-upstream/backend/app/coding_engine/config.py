"""Configuration helpers for the coding engine."""

from __future__ import annotations

from pathlib import Path

from dotenv import load_dotenv


BACKEND_DIR = Path(__file__).resolve().parents[2]
PROJECT_ROOT = BACKEND_DIR.parent
CODING_RUNS_DIR = BACKEND_DIR / ".data" / "coding_runs"

DEFAULT_MAX_ITERATIONS = 3
DEFAULT_HARNESS_TIMEOUT_SECONDS = 120
DEFAULT_HARNESS_COMMAND = (
    "cd backend && .venv/bin/python -c \"print('coding engine harness placeholder')\""
)


def load_runtime_env() -> None:
    """Load project env files without overriding explicitly exported values."""

    load_dotenv(BACKEND_DIR / ".env", override=False)
    load_dotenv(BACKEND_DIR / ".env.runtime", override=False)

