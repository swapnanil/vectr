"""Reports the configured LLM model name for API response metadata."""
from __future__ import annotations

import os


def get_model() -> str:
    return os.getenv("LLM_MODEL", "claude-sonnet-4-6")
