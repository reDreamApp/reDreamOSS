"""reDreamOSS on this computer, for one person: `make start`.

    uv run --project apps/server python scripts/start.py [--port 8000] [--build-web] [--no-browser]

1. `.env` at the repo root: copied from .env.example on the first run (mode
   600); fill in the LLM_* lines (and STT_*/TTS_* for voice) and start again.
2. The web app in apps/server/data/web: the release's build for this
   checkout's newest tag, checked against its published SHA-256; with
   --build-web, or when there is no release to fetch, built here with Flutter
   (fvm or flutter on PATH).
3. A SQLite database in apps/server/data/redream.sqlite3 (created or upgraded).
4. The server on http://localhost:PORT in personal mode: one user, no sign-in,
   reachable from this computer only, and only with the key in
   apps/server/data/personal.key (made on the first run): the browser opens
   with it in the address (#key=…), and it is printed for other browsers.
   Ctrl+C stops it.

The settings in .env are read by the server; this script never reads them.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import os
import secrets
import shutil
import stat
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
import webbrowser
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SERVER = ROOT / "apps" / "server"
APP = ROOT / "apps" / "app"
DATA = SERVER / "data"
WEB = DATA / "web"
KEY_FILE = DATA / "personal.key"
ENV_FILE = ROOT / ".env"
RELEASES = "https://github.com/reDreamApp/reDreamOSS/releases/download"
# The flags the release workflow builds with: no CDN, so CanvasKit and the
# fonts come from this server (apps/app/web/flutter_bootstrap.js: no service
# worker either).
WEB_BUILD_FLAGS = ["build", "web", "--release", "--no-web-resources-cdn"]


def main() -> int:
    # Each line at once, also into a pipe or a file: the link before the server's log.
    if isinstance(sys.stdout, io.TextIOWrapper):
        sys.stdout.reconfigure(line_buffering=True)
    parser = argparse.ArgumentParser(description="Run reDreamOSS on this computer.")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--build-web", action="store_true", help="build the web app here")
    parser.add_argument("--no-browser", action="store_true", help="do not open a browser")
    args = parser.parse_args()

    if not ENV_FILE.exists():
        shutil.copyfile(ROOT / ".env.example", ENV_FILE)
        ENV_FILE.chmod(stat.S_IRUSR | stat.S_IWUSR)
        print(
            "Created .env. Fill in LLM_BASE_URL, LLM_API_KEY, LLM_MODEL and LLM_SMALL_MODEL "
            "(and STT_*/TTS_* for voice; .env.example has examples), then run this again."
        )
        return 1

    DATA.mkdir(parents=True, exist_ok=True)
    try:
        ensure_web_app(build=args.build_web)
    except WebAppError as exc:
        print(f"start: {exc}", file=sys.stderr)
        return 1

    env = dict(os.environ)
    env.update(
        REGISTRATION_MODE="personal",
        PERSONAL_KEY=personal_key(),
        DATABASE_URL=f"sqlite+aiosqlite:///{(DATA / 'redream.sqlite3').as_posix()}",
        WEB_DIR=str(WEB),
        AUDIO_DIR=str(DATA / "audio"),
    )
    uv = ["uv", "run", "--project", str(SERVER), "--env-file", str(ENV_FILE)]
    migrated = subprocess.run([*uv, "python", "-m", "redream_api.db.migrate"], cwd=SERVER, env=env)
    if migrated.returncode != 0:
        return migrated.returncode

    url = f"http://localhost:{args.port}"
    link = f"{url}/#key={env['PERSONAL_KEY']}"
    print(f"\nreDreamOSS runs at {url} (Ctrl+C stops it)")
    print(f"In another browser on this computer, open: {link}\n")
    server = subprocess.Popen(
        [
            *uv,
            "uvicorn",
            "redream_api.main:app",
            "--host",
            "127.0.0.1",
            "--port",
            str(args.port),
            "--workers",
            "1",
            "--no-proxy-headers",
            "--no-access-log",
            "--timeout-graceful-shutdown",
            "5",
        ],
        cwd=SERVER,
        env=env,
    )
    try:
        if not args.no_browser:
            open_when_ready(url, link, server)
        return server.wait()
    except KeyboardInterrupt:
        server.terminate()
        return server.wait()


class WebAppError(Exception):
    pass


def personal_key() -> str:
    """The key the browser needs to sign in; made once, readable only by you."""
    if KEY_FILE.exists():
        key = KEY_FILE.read_text().strip()
        if len(key) >= 32:
            return key
    key = secrets.token_urlsafe(32)
    KEY_FILE.write_text(key + "\n")
    KEY_FILE.chmod(stat.S_IRUSR | stat.S_IWUSR)
    return key


def ensure_web_app(*, build: bool) -> None:
    tag = newest_tag()
    local = f"local {head_commit()}"
    have = (WEB / ".version").read_text().strip() if (WEB / ".version").exists() else None
    if not build and have in (tag, local) and (WEB / "index.html").exists():
        return
    if not build and tag is not None:
        try:
            install(download(tag), tag)
            return
        except WebAppError as exc:
            print(f"start: {exc}; building the web app here instead", file=sys.stderr)
    flutter = find_flutter()
    if flutter is None:
        if (WEB / "index.html").exists():
            print("start: no newer web app available; using the one in data/web", file=sys.stderr)
            return
        raise WebAppError(
            "no web app: no release to download for this checkout and no Flutter to build "
            "one (install Flutter, or check out a release tag)"
        )
    print("Building the web app (a few minutes the first time)…")
    try:
        subprocess.run([*flutter, "pub", "get"], cwd=APP, check=True)
        subprocess.run([*flutter, *WEB_BUILD_FLAGS], cwd=APP, check=True)
    except subprocess.CalledProcessError as exc:
        raise WebAppError(f"the web app did not build (exit {exc.returncode})") from exc
    replace_web(APP / "build" / "web", local)


def newest_tag() -> str | None:
    try:
        out = subprocess.run(
            ["git", "describe", "--tags", "--abbrev=0", "--match", "v[0-9]*"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return None
    return out.stdout.strip() or None


def head_commit() -> str:
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"], cwd=ROOT, capture_output=True, text=True
        )
    except OSError:
        return "unknown"
    return out.stdout.strip() or "unknown"


def download(tag: str) -> Path:
    name = f"reDreamOSS-web-{tag}.zip"
    folder = Path(tempfile.mkdtemp(prefix="redream-web-"))
    target = folder / name
    try:
        print(f"Downloading the web app {tag}…")
        with urllib.request.urlopen(f"{RELEASES}/{tag}/{name}", timeout=60) as res:
            target.write_bytes(res.read())
        with urllib.request.urlopen(f"{RELEASES}/{tag}/{name}.sha256", timeout=30) as res:
            published = res.read().decode().split()
        if not published:
            raise WebAppError(f"the web app {tag} has an empty checksum file")
        if hashlib.sha256(target.read_bytes()).hexdigest() != published[0].lower():
            raise WebAppError(f"the downloaded web app {tag} does not match its SHA-256")
    except (urllib.error.URLError, OSError, WebAppError) as exc:
        shutil.rmtree(folder, ignore_errors=True)
        if isinstance(exc, WebAppError):
            raise
        raise WebAppError(f"could not download the web app {tag} ({exc})") from exc
    return target


def install(archive: Path, tag: str) -> None:
    unpacked = archive.parent / "web"
    with zipfile.ZipFile(archive) as zf:
        zf.extractall(unpacked)  # extractall drops absolute paths and ".." parts
    if not (unpacked / "index.html").exists():
        raise WebAppError(f"the web app {tag} has no index.html")
    replace_web(unpacked, tag)
    shutil.rmtree(archive.parent, ignore_errors=True)


def replace_web(source: Path, version: str) -> None:
    staging = DATA / "web.new"
    shutil.rmtree(staging, ignore_errors=True)
    shutil.copytree(source, staging)
    (staging / ".version").write_text(version + "\n")
    shutil.rmtree(WEB, ignore_errors=True)
    staging.rename(WEB)


def find_flutter() -> list[str] | None:
    # Full paths: on Windows both are .bat files, which subprocess finds only so.
    if fvm := shutil.which("fvm"):
        return [fvm, "flutter"]
    if flutter := shutil.which("flutter"):
        return [flutter]
    return None


def open_when_ready(url: str, link: str, server: subprocess.Popen[bytes]) -> None:
    import time

    for _ in range(60):
        if server.poll() is not None:
            return
        try:
            with urllib.request.urlopen(f"{url}/api/v1/health", timeout=1):
                webbrowser.open(link)
                return
        except (urllib.error.URLError, OSError):
            time.sleep(0.5)


if __name__ == "__main__":
    sys.exit(main())
