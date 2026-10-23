"""scripts/init-env.sh in a temporary copy of the repo layout: creates .env
(mode 600) from .env.example, fills only the empty POSTGRES_PASSWORD= and
INVITE_CODE= lines, never prints the database password, changes nothing on a
second run and refuses to run without .env.example."""

from __future__ import annotations

import os
import re
import shutil
import signal
import stat
import subprocess
from pathlib import Path

import pytest

from .conftest import REPO_ROOT

PASSWORD = re.compile(r"^POSTGRES_PASSWORD=([0-9a-f]{32})$", re.MULTILINE)
INVITE = re.compile(r"^INVITE_CODE=([abcdefghjkmnpqrstuvwxyz23456789]{12})$", re.MULTILINE)


@pytest.fixture
def layout(tmp_path: Path) -> Path:
    """A repo root with only what the script needs."""
    root = tmp_path / "repo"
    (root / "scripts").mkdir(parents=True)
    shutil.copy(REPO_ROOT / "scripts" / "init-env.sh", root / "scripts" / "init-env.sh")
    shutil.copy(REPO_ROOT / ".env.example", root / ".env.example")
    return root


def _ignore_sigpipe() -> None:
    signal.signal(signal.SIGPIPE, signal.SIG_IGN)


def _run(root: Path) -> subprocess.CompletedProcess[str]:
    # From another directory: the script finds the repo root by its own path.
    elsewhere = root.parent / "elsewhere"
    elsewhere.mkdir(exist_ok=True)
    # A TMPDIR of its own: the script's temporary copy of .env must not stay.
    tmpdir = root.parent / "tmpdir"
    tmpdir.mkdir(exist_ok=True)
    proc = subprocess.Popen(
        ["sh", str(root / "scripts" / "init-env.sh")],
        cwd=elsewhere,
        env={**os.environ, "TMPDIR": str(tmpdir)},
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        # As GitHub's runner starts it: SIGPIPE ignored. A pipeline that
        # relies on SIGPIPE to stop reading /dev/urandom then runs forever
        # (GNU) or yields nothing (BSD).
        preexec_fn=_ignore_sigpipe,
        # Its own process group, so a timeout ends the whole pipeline, not
        # only sh (a left-over tr/fold would keep a CPU core busy).
        start_new_session=True,
    )
    try:
        stdout, stderr = proc.communicate(timeout=30)
    except subprocess.TimeoutExpired:
        os.killpg(proc.pid, signal.SIGKILL)
        proc.communicate()
        pytest.fail("init-env.sh did not finish within 30 s")
    assert list(tmpdir.iterdir()) == []
    return subprocess.CompletedProcess(proc.args, proc.returncode, stdout, stderr)


def _set(env_file: Path, name: str, line: str) -> None:
    text = env_file.read_text()
    assert f"\n{name}=\n" in text
    env_file.write_text(text.replace(f"\n{name}=\n", f"\n{line}\n"))


def _without(text: str, *names: str) -> list[str]:
    return [
        line for line in text.splitlines() if not line.startswith(tuple(f"{n}=" for n in names))
    ]


def test_creates_env_with_mode_600_and_both_secrets(layout: Path) -> None:
    result = _run(layout)
    assert result.returncode == 0, result.stderr
    env_file = layout / ".env"
    assert stat.S_IMODE(env_file.stat().st_mode) == 0o600
    env = env_file.read_text()
    password = PASSWORD.search(env)
    invite = INVITE.search(env)
    assert password and invite, "POSTGRES_PASSWORD or INVITE_CODE missing or malformed"
    # Everything else is the template, line for line.
    template = (layout / ".env.example").read_text()
    names = ("POSTGRES_PASSWORD", "INVITE_CODE")
    assert _without(env, *names) == _without(template, *names)
    # The invite code is shown for sharing, the database password never.
    assert invite.group(1) in result.stdout
    assert password.group(1) not in result.stdout + result.stderr
    assert "docker compose up -d --build" in result.stdout
    assert "python -m redream_api.cli create-user" in result.stdout


def test_second_run_changes_nothing(layout: Path) -> None:
    assert _run(layout).returncode == 0
    first = (layout / ".env").read_text()
    result = _run(layout)
    assert result.returncode == 0, result.stderr
    assert (layout / ".env").read_text() == first
    assert "generated" not in result.stdout


def test_runs_differ(layout: Path, tmp_path: Path) -> None:
    other = tmp_path / "other"
    shutil.copytree(layout, other)
    assert _run(layout).returncode == 0
    assert _run(other).returncode == 0
    a, b = (layout / ".env").read_text(), (other / ".env").read_text()
    assert PASSWORD.findall(a) != PASSWORD.findall(b)
    assert INVITE.findall(a) != INVITE.findall(b)


def test_existing_values_are_kept(layout: Path) -> None:
    template = (layout / ".env.example").read_text()
    assert "\nPOSTGRES_PASSWORD=\n" in template
    env_file = layout / ".env"
    env_file.write_text(template.replace("\nPOSTGRES_PASSWORD=\n", "\nPOSTGRES_PASSWORD=my-own\n"))
    env_file.chmod(0o640)
    result = _run(layout)
    assert result.returncode == 0, result.stderr
    env = env_file.read_text()
    assert "\nPOSTGRES_PASSWORD=my-own\n" in env
    assert INVITE.search(env)
    # An existing file is filled in place: its mode is the operator's, but a
    # file others can read is pointed out.
    assert stat.S_IMODE(env_file.stat().st_mode) == 0o640
    assert "chmod 600 .env" in result.stderr
    assert "my-own" not in result.stdout + result.stderr


def test_a_private_env_gets_no_mode_warning(layout: Path) -> None:
    result = _run(layout)
    assert result.returncode == 0, result.stderr
    assert "warning" not in result.stderr


@pytest.mark.parametrize("password", ["pa$word@1/x", "p%40ss:word", "pässwort"])
def test_an_unsafe_own_password_is_kept_with_a_warning(layout: Path, password: str) -> None:
    env_file = layout / ".env"
    shutil.copy(layout / ".env.example", env_file)
    env_file.chmod(0o600)
    _set(env_file, "POSTGRES_PASSWORD", f"POSTGRES_PASSWORD={password}")
    result = _run(layout)
    assert result.returncode == 0, result.stderr
    assert f"\nPOSTGRES_PASSWORD={password}\n" in env_file.read_text()
    assert "POSTGRES_PASSWORD has characters other than" in result.stderr
    # Neither the password nor a part of it is printed.
    assert password not in result.stdout + result.stderr
    assert "word" not in result.stdout + result.stderr.replace("PASSWORD", "")


@pytest.mark.parametrize("line", ['INVITE_CODE=""', "INVITE_CODE=''", "INVITE_CODE=  "])
def test_a_blank_looking_secret_is_not_filled_but_reported(layout: Path, line: str) -> None:
    env_file = layout / ".env"
    shutil.copy(layout / ".env.example", env_file)
    env_file.chmod(0o600)
    _set(env_file, "INVITE_CODE", line)
    result = _run(layout)
    assert result.returncode == 0, result.stderr
    assert f"\n{line}\n" in env_file.read_text()
    assert "INVITE_CODE in .env looks empty" in result.stderr
    assert "Invite code to share" not in result.stdout
    assert PASSWORD.search(env_file.read_text())


def test_crlf_line_endings_are_reported(layout: Path) -> None:
    env_file = layout / ".env"
    env_file.write_bytes((layout / ".env.example").read_bytes().replace(b"\n", b"\r\n"))
    env_file.chmod(0o600)
    result = _run(layout)
    assert result.returncode == 0, result.stderr
    assert "Windows line endings" in result.stderr
    # Not filled ("NAME=\r" is not an empty line), and said so.
    assert "POSTGRES_PASSWORD in .env looks empty" in result.stderr
    assert "generated" not in result.stdout


def test_missing_env_example_fails(layout: Path) -> None:
    (layout / ".env.example").unlink()
    result = _run(layout)
    assert result.returncode != 0
    assert ".env.example" in result.stderr
    assert not (layout / ".env").exists()


def test_missing_env_example_fails_even_with_an_env(layout: Path) -> None:
    # The explicit check, not a failing cp: .env is there already.
    assert _run(layout).returncode == 0
    before = (layout / ".env").read_text()
    (layout / ".env.example").unlink()
    result = _run(layout)
    assert result.returncode == 1
    assert "init-env: .env.example not found" in result.stderr
    assert result.stdout == ""
    assert (layout / ".env").read_text() == before
