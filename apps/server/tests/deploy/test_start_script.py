"""scripts/start.py (`make start`): first run, the web app download and its
checks, and the build flags shared with the release and CI workflows."""

from __future__ import annotations

import hashlib
import importlib.util
import stat
import zipfile
from pathlib import Path
from types import ModuleType

import pytest

from .conftest import REPO_ROOT


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "start_script", REPO_ROOT / "scripts" / "start.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def start(tmp_path: Path, monkeypatch) -> ModuleType:
    """The script with its paths moved into tmp_path."""
    module = _load()
    root = tmp_path / "repo"
    data = root / "apps" / "server" / "data"
    (root / "apps" / "server").mkdir(parents=True)
    (root / ".env.example").write_text("LLM_API_KEY=\n")
    monkeypatch.setattr(module, "ROOT", root)
    monkeypatch.setattr(module, "DATA", data)
    monkeypatch.setattr(module, "WEB", data / "web")
    monkeypatch.setattr(module, "KEY_FILE", data / "personal.key")
    monkeypatch.setattr(module, "ENV_FILE", root / ".env")
    monkeypatch.setattr(module, "find_flutter", lambda: None)
    return module


def _release(tmp_path: Path, tag: str, *, sha256: str | None = None) -> str:
    """A release folder like GitHub's, as a file:// URL."""
    folder = tmp_path / "releases" / tag
    folder.mkdir(parents=True)
    archive = folder / f"reDreamOSS-web-{tag}.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("index.html", "<!doctype html><title>reDreamOSS</title>")
        zf.writestr("main.dart.js", "// app")
    digest = sha256 or hashlib.sha256(archive.read_bytes()).hexdigest()
    (folder / f"{archive.name}.sha256").write_text(f"{digest}  {archive.name}\n")
    return (tmp_path / "releases").as_uri()


def test_the_build_flags_match_the_workflows() -> None:
    command = "flutter " + " ".join(_load().WEB_BUILD_FLAGS)
    for workflow in ("release-apk.yml", "ci.yml"):
        text = (REPO_ROOT / ".github" / "workflows" / workflow).read_text()
        assert command in text, workflow


def test_the_first_run_only_creates_a_private_env(start, monkeypatch) -> None:
    monkeypatch.setattr("sys.argv", ["start.py", "--no-browser"])
    monkeypatch.setattr(start.subprocess, "run", _forbidden)
    monkeypatch.setattr(start.subprocess, "Popen", _forbidden)

    assert start.main() == 1
    env = start.ENV_FILE
    assert env.read_text() == "LLM_API_KEY=\n"
    assert stat.S_IMODE(env.stat().st_mode) == 0o600


def test_the_release_web_app_is_fetched_checked_and_kept(start, tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(start, "RELEASES", _release(tmp_path, "v0.1.0"))
    monkeypatch.setattr(start, "newest_tag", lambda: "v0.1.0")
    start.DATA.mkdir(parents=True)

    start.ensure_web_app(build=False)

    assert (start.WEB / "index.html").exists()
    assert (start.WEB / ".version").read_text().strip() == "v0.1.0"
    monkeypatch.setattr(start, "download", _forbidden)
    start.ensure_web_app(build=False)  # the same tag: nothing to fetch


def test_a_web_app_that_does_not_match_its_checksum_is_refused(
    start, tmp_path, monkeypatch
) -> None:
    monkeypatch.setattr(start, "RELEASES", _release(tmp_path, "v0.1.0", sha256="0" * 64))

    with pytest.raises(start.WebAppError, match="does not match its SHA-256"):
        start.download("v0.1.0")


def test_no_release_and_no_flutter_is_a_clear_error(start, tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(start, "RELEASES", (tmp_path / "nothing").as_uri())
    monkeypatch.setattr(start, "newest_tag", lambda: "v0.1.0")
    start.DATA.mkdir(parents=True)

    with pytest.raises(start.WebAppError, match="no Flutter"):
        start.ensure_web_app(build=False)


def test_the_personal_key_is_made_once_and_private(start) -> None:
    start.DATA.mkdir(parents=True)

    key = start.personal_key()

    assert len(key) >= 32
    assert stat.S_IMODE(start.KEY_FILE.stat().st_mode) == 0o600
    assert start.personal_key() == key  # the next start: the same key, the same links


def _forbidden(*args, **kwargs):
    raise AssertionError("not expected here")
