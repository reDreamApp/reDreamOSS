"""GET /api/v1/instance and the settings behind it.

Pins the exact response shape and its values across the settings (voice,
registration mode, daily cap, operator fields), that it needs no token, no
database and no consent (`public_client` fails the test if the route reaches
any of them), the Cache-Control header, and the startup checks on
INSTANCE_NAME, OPERATOR_NAME, OPERATOR_CONTACT and PRIVACY_URL.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from redream_api.api.v1.instance.routes import API_VERSION
from redream_api.api.v1.instance.schemas import InstanceOut
from redream_api.core import consent
from redream_api.core.config import Settings, settings

_INSTANCE_VARS = {
    "INSTANCE_NAME": 80,
    "OPERATOR_NAME": 120,
    "OPERATOR_CONTACT": 200,
    "PRIVACY_URL": 500,
}


async def _instance(client) -> dict:
    r = await client.get("/api/v1/instance")
    assert r.status_code == 200, r.text
    return r.json()


# --- the route ---------------------------------------------------------------------


async def test_shape_and_defaults(public_client):
    assert await _instance(public_client) == {
        "name": None,
        "api_version": 1,
        "consent_version": consent.CONSENT_VERSION,
        "registration": "invite",
        "daily_ai_requests_per_user": 100,
        "features": {"chat": True, "transcription": False, "rehearsal_audio": False},
        "operator": {"name": None, "contact": None, "privacy_url": None},
    }
    assert API_VERSION == 1


async def test_no_cache(public_client):
    r = await public_client.get("/api/v1/instance")
    assert r.headers["cache-control"] == "no-cache"


async def test_no_token_needed_with_real_auth(auth_client):
    """auth_client keeps get_current_user real: a gated route would answer 401."""
    r = await auth_client.get("/api/v1/instance")
    assert r.status_code == 200
    assert "authorization" not in r.request.headers


async def test_voice_on(public_client, voice_on):
    features = (await _instance(public_client))["features"]
    assert features == {"chat": True, "transcription": True, "rehearsal_audio": True}


async def test_each_half_of_voice(public_client, fake_ai):
    fake_ai.stt_enabled = True
    features = (await _instance(public_client))["features"]
    assert (features["transcription"], features["rehearsal_audio"]) == (True, False)
    fake_ai.stt_enabled, fake_ai.tts_enabled = False, True
    features = (await _instance(public_client))["features"]
    assert (features["transcription"], features["rehearsal_audio"]) == (False, True)


async def test_chat_off_without_llm(public_client, fake_ai):
    fake_ai.llm_configured = False
    assert (await _instance(public_client))["features"]["chat"] is False


@pytest.mark.parametrize("mode", ["open", "invite", "closed"])
async def test_registration_mode(public_client, monkeypatch, mode):
    monkeypatch.setattr(settings, "registration_mode", mode)
    assert (await _instance(public_client))["registration"] == mode


@pytest.mark.parametrize("cap", [0, 100, 7])
async def test_daily_cap(public_client, monkeypatch, cap):
    monkeypatch.setattr(settings, "daily_ai_requests_per_user", cap)
    assert (await _instance(public_client))["daily_ai_requests_per_user"] == cap


async def test_operator_fields_set(public_client, monkeypatch):
    monkeypatch.setattr(settings, "instance_name", "Dream Lab")
    monkeypatch.setattr(settings, "operator_name", "Example Club e.V.")
    monkeypatch.setattr(settings, "operator_contact", "privacy@example.org")
    monkeypatch.setattr(settings, "privacy_url", "https://example.org/privacy")
    body = await _instance(public_client)
    assert body["name"] == "Dream Lab"
    assert body["operator"] == {
        "name": "Example Club e.V.",
        "contact": "privacy@example.org",
        "privacy_url": "https://example.org/privacy",
    }


async def test_consent_version_follows_the_constant(public_client, monkeypatch):
    monkeypatch.setattr(consent, "CONSENT_VERSION", "2099-01-01")
    assert (await _instance(public_client))["consent_version"] == "2099-01-01"


def test_response_model_forbids_extra_fields():
    body = {
        "name": None,
        "api_version": 1,
        "consent_version": "v",
        "registration": "open",
        "daily_ai_requests_per_user": 0,
        "features": {"chat": True, "transcription": False, "rehearsal_audio": False},
        "operator": {"name": None, "contact": None, "privacy_url": None},
    }
    InstanceOut.model_validate(body)
    with pytest.raises(ValidationError):
        InstanceOut.model_validate({**body, "extra": 1})
    with pytest.raises(ValidationError):
        InstanceOut.model_validate({**body, "features": {**body["features"], "x": True}})
    with pytest.raises(ValidationError):
        InstanceOut.model_validate({**body, "operator": {**body["operator"], "x": None}})


# --- settings ----------------------------------------------------------------------


def test_unset_is_none(monkeypatch):
    for var in _INSTANCE_VARS:
        monkeypatch.delenv(var, raising=False)
    s = Settings(_env_file=None)
    assert (s.instance_name, s.operator_name, s.operator_contact, s.privacy_url) == (None,) * 4


def test_a_local_env_file_does_not_leak_into_tests(monkeypatch, tmp_path):
    """Settings read .env from the working directory (apps/server, where the
    README has operators fill one in). conftest blanks the four variables, and
    the environment beats the file: neither a named instance nor an invalid
    PRIVACY_URL there may change what the tests see."""
    (tmp_path / ".env").write_text(
        "INSTANCE_NAME=Dream Lab\nOPERATOR_NAME=Jo\nOPERATOR_CONTACT=me@example.org\n"
        "PRIVACY_URL=http://example.org/privacy\n",
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    s = Settings()
    assert (s.instance_name, s.operator_name, s.operator_contact, s.privacy_url) == (None,) * 4


@pytest.mark.parametrize("blank", ["", "   ", "\n\t "])
def test_blank_is_none(monkeypatch, blank):
    for var in _INSTANCE_VARS:
        monkeypatch.setenv(var, blank)
    s = Settings(_env_file=None)
    assert (s.instance_name, s.operator_name, s.operator_contact, s.privacy_url) == (None,) * 4


def test_values_are_stripped(monkeypatch):
    monkeypatch.setenv("INSTANCE_NAME", "  Dream Lab  ")
    monkeypatch.setenv("OPERATOR_NAME", " Jo ")
    monkeypatch.setenv("OPERATOR_CONTACT", " https://example.org/contact ")
    monkeypatch.setenv("PRIVACY_URL", " https://example.org/privacy\n")
    s = Settings(_env_file=None)
    assert s.instance_name == "Dream Lab"
    assert s.operator_name == "Jo"
    assert s.operator_contact == "https://example.org/contact"
    assert s.privacy_url == "https://example.org/privacy"


@pytest.mark.parametrize(("var", "max_len"), _INSTANCE_VARS.items())
def test_too_long_stops_startup(monkeypatch, var, max_len):
    prefix = "https://example.org/" if var == "PRIVACY_URL" else ""
    monkeypatch.setenv(var, prefix + "x" * (max_len + 1 - len(prefix)))
    with pytest.raises(ValidationError, match=f"{var} is {max_len + 1} characters"):
        Settings(_env_file=None)


@pytest.mark.parametrize(("var", "max_len"), _INSTANCE_VARS.items())
def test_at_the_limit_is_accepted(monkeypatch, var, max_len):
    prefix = "https://example.org/" if var == "PRIVACY_URL" else ""
    monkeypatch.setenv(var, prefix + "x" * (max_len - len(prefix)))
    assert len(getattr(Settings(_env_file=None), var.lower())) == max_len


@pytest.mark.parametrize(
    "url",
    [
        "http://example.org/privacy",  # http on a public host
        "http://localhost.example.org/privacy",
        "https://",  # no host
        "https:///privacy",
        "javascript:alert(1)",
        "/privacy",  # relative
        "privacy.html",
        "//example.org/privacy",  # scheme-relative
        "ftp://example.org/privacy",
        "https://example .org/privacy",
        "https://example.org:port/privacy",
        "http://example.org\\@localhost/privacy",  # a browser reads "\" as "/": example.org
        "https://example.org\\privacy",
        "http://user@localhost/privacy",  # userinfo: the host parsers see may differ
        "https://user:pw@example.org/privacy",
        "https://example.org/\x1bprivacy",  # control, format and NBSP characters
        "https://example.org/\u200bprivacy",
        "https://example.org/\u00a0privacy",
    ],
)
def test_bad_privacy_url_stops_startup(monkeypatch, url):
    monkeypatch.setenv("PRIVACY_URL", url)
    with pytest.raises(ValidationError, match="PRIVACY_URL must be an absolute https:// URL"):
        Settings(_env_file=None)


@pytest.mark.parametrize(
    "url",
    [
        "https://example.org/privacy",
        "https://example.org",
        "http://localhost:8080/privacy",
        "http://127.0.0.1/privacy",
    ],
)
def test_good_privacy_url_is_accepted(monkeypatch, url):
    monkeypatch.setenv("PRIVACY_URL", url)
    assert Settings(_env_file=None).privacy_url == url


def test_errors_echo_no_values(monkeypatch):
    """The startup error names the variable, never a value (INVITE_CODE and
    the keys must not end up in a log)."""
    monkeypatch.setenv("INVITE_CODE", "invite-secret-4711")
    monkeypatch.setenv("PRIVACY_URL", "http://example.org/path-secret")
    monkeypatch.setenv("OPERATOR_NAME", "name-secret" * 20)
    with pytest.raises(ValidationError) as exc:
        Settings(_env_file=None)
    message = str(exc.value)
    assert "PRIVACY_URL" in message
    assert "OPERATOR_NAME" in message
    assert "invite-secret-4711" not in message
    assert "path-secret" not in message
    assert "name-secret" not in message


def test_database_url_error_names_only_the_scheme(monkeypatch):
    """A sync DATABASE_URL is refused without echoing its password."""
    monkeypatch.setenv("DATABASE_URL", "postgresql://redream:db-secret-4711@db:5432/redream")
    with pytest.raises(ValidationError) as exc:
        Settings(_env_file=None)
    message = str(exc.value)
    assert "DATABASE_URL must use an async driver" in message
    assert "got: postgresql://…" in message
    assert "db-secret-4711" not in message
    assert "redream:" not in message
