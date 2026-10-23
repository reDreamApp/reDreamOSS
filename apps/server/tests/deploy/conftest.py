"""The deployment files at the repo root (compose.yaml, .env.example,
scripts/init-env.sh, deploy/) and the server image's Dockerfile, checked
without Docker. CI's stack job runs the real thing."""

from __future__ import annotations

import os

# Set env BEFORE importing redream_api so the global Settings constructs
# cleanly when tests/deploy runs alone (the tests only read the Settings
# classes' fields). setdefault: tests/api's conftest may have pinned these.
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")
os.environ.setdefault("INVITE_CODE", "test-invite-code")
os.environ.setdefault("LLM_BASE_URL", "http://llm.invalid/v1")
os.environ.setdefault("LLM_API_KEY", "dummy")
os.environ.setdefault("LLM_MODEL", "m-large")
os.environ.setdefault("LLM_SMALL_MODEL", "m-small")

from pathlib import Path

import pytest
import yaml  # PyYAML, installed with uvicorn[standard]

REPO_ROOT = Path(__file__).resolve().parents[4]
SERVER_DIR = REPO_ROOT / "apps" / "server"


@pytest.fixture(scope="session")
def compose() -> dict:
    """compose.yaml as YAML sees it (aliases resolved to the anchored object)."""
    return yaml.safe_load((REPO_ROOT / "compose.yaml").read_text())
