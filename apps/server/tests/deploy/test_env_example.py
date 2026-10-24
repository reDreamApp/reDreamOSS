"""The one operator-facing .env.example (repo root) documents every setting
the two Settings models define, the compose stack's own values, and nothing
that no longer exists; the required values come first."""

from __future__ import annotations

import re

from redream_ai.config import Settings as AiSettings
from redream_api.core.config import Settings as ApiSettings

from .conftest import REPO_ROOT, SERVER_DIR

ENV_EXAMPLE = REPO_ROOT / ".env.example"
# Read by compose.yaml, not by the server.
COMPOSE_ONLY = {"POSTGRES_PASSWORD", "DOMAIN"}
COMPOSE_OPTIONAL = {"SERVER_PORT", "NETWORK_SUBNET"}  # documented as commented examples
# Read by Compose itself from .env (a commented example too).
COMPOSE_CLI = {"COMPOSE_PROFILES"}
# Where local commands are documented; run from apps/server, they must load
# the repo root's .env (Settings read .env from the working directory).
LOCAL_COMMAND_DOCS = [
    ENV_EXAMPLE,
    REPO_ROOT / "README.md",
    REPO_ROOT / "RELEASING.md",
    REPO_ROOT / "docs" / "SELF_HOSTING.md",
    REPO_ROOT / "docs" / "LOCAL_TESTING.md",
    SERVER_DIR / "README.md",
    *sorted((SERVER_DIR / "src").rglob("*.py")),
    *sorted((SERVER_DIR / "evals").rglob("*.py")),
]


def _settings() -> set[str]:
    names = set()
    for model in (AiSettings, ApiSettings):
        for name, field in model.model_fields.items():
            assert field.alias is None and field.validation_alias is None, name
            names.add(name.upper())
    return names


def _assigned(text: str) -> list[str]:
    return re.findall(r"^([A-Z][A-Z0-9_]*)=", text, re.MULTILINE)


def _commented(text: str) -> set[str]:
    return set(re.findall(r"^#\s*([A-Z][A-Z0-9_]*)=", text, re.MULTILINE))


def test_documents_every_setting_and_nothing_else() -> None:
    assigned = _assigned(ENV_EXAMPLE.read_text())
    assert len(assigned) == len(set(assigned)), "a setting is listed twice"
    assert set(assigned) == _settings() | COMPOSE_ONLY


def test_commented_examples_name_existing_settings() -> None:
    known = _settings() | COMPOSE_ONLY | COMPOSE_OPTIONAL | COMPOSE_CLI
    assert _commented(ENV_EXAMPLE.read_text()) <= known
    assert COMPOSE_OPTIONAL | COMPOSE_CLI <= _commented(ENV_EXAMPLE.read_text())


def test_required_then_cost_first() -> None:
    assert _assigned(ENV_EXAMPLE.read_text())[:9] == [
        "LLM_BASE_URL",
        "LLM_API_KEY",
        "LLM_MODEL",
        "LLM_SMALL_MODEL",
        "POSTGRES_PASSWORD",
        "INVITE_CODE",
        "DOMAIN",
        "REGISTRATION_MODE",
        "DAILY_AI_REQUESTS_PER_USER",
    ]


def test_secrets_are_empty_for_init_env() -> None:
    text = ENV_EXAMPLE.read_text()
    for name in ("POSTGRES_PASSWORD", "INVITE_CODE", "LLM_API_KEY"):
        assert re.search(rf"^{name}=$", text, re.MULTILINE), name


def test_every_compose_variable_is_documented() -> None:
    compose = (REPO_ROOT / "compose.yaml").read_text()
    used = set(re.findall(r"(?<!\$)\$\{([A-Z][A-Z0-9_]*)", compose))
    text = ENV_EXAMPLE.read_text()
    assert used == COMPOSE_ONLY | COMPOSE_OPTIONAL
    assert used <= set(_assigned(text)) | _commented(text)


def test_values_with_a_dollar_are_single_quoted() -> None:
    # Compose and uv run --env-file expand $name in an unquoted or
    # double-quoted value; the header tells operators to single-quote it.
    text = ENV_EXAMPLE.read_text()
    assert "single quotes" in text.split("# ---- Required")[0]
    for line in text.splitlines():
        value = line.partition("=")[2].lstrip()
        if re.match(r"^#?\s*[A-Z][A-Z0-9_]*=", line) and "$" in value:
            assert value.startswith("'") and value.endswith("'"), line


def test_local_commands_read_the_root_env() -> None:
    found = 0
    for path in LOCAL_COMMAND_DOCS:
        for options, script in re.findall(r"uv run([^`\n]*?)python ([\w./-]+)", path.read_text()):
            if script == "scripts/start.py":
                continue  # make start: the script passes .env to the server itself
            found += 1
            assert "--env-file ../../.env" in options, f"{path}: uv run{options}python"
    assert found >= 4  # .env.example, apps/server/README.md, cli.py, migrate.py


def test_the_old_server_env_example_is_gone() -> None:
    assert not (SERVER_DIR / ".env.example").exists()
    assert not (REPO_ROOT / "infra").exists()
