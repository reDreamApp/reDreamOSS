"""Rehearsal MP3s on the local disk, under AUDIO_DIR (ADR 0010).

Key: ``users/{user_id}/{dream_id}/{language}-{hash}.mp3``, relative to
AUDIO_DIR. The per-user folder is what account deletion (DELETE /users/me)
removes; the per-dream folder what dream deletion and an edited dream remove.
A dream keeps one file per language: writing a new one deletes the older
files of that language.

Every key is checked against KEY_RE before any filesystem use, and its path
must stay inside AUDIO_DIR with no symlink between AUDIO_DIR and the file.
Writes are atomic (temp file, fsync, rename), files 0600, folders 0700.

Filesystem only, all of it blocking: call it from async code through
asyncio.to_thread.
"""

from __future__ import annotations

import contextlib
import os
import re
import shutil
import stat
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import TypeGuard
from uuid import UUID

from ..config import settings

KEY_RE = re.compile(
    r"^users/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
    r"/[1-9][0-9]{0,18}/(en|de)-[0-9a-f]{24}\.mp3$"
)
_DREAM_ID_RE = re.compile(r"^[1-9][0-9]{0,18}$")
_DIGEST_LEN = 24

_DIR_MODE = 0o700
_FILE_MODE = 0o600


class UnsafePathError(ValueError):
    """A key or folder that fails validation, leaves AUDIO_DIR or passes a symlink."""


def is_key(key: object) -> TypeGuard[str]:
    return isinstance(key, str) and KEY_RE.fullmatch(key) is not None


def parse_dream_id(value: object) -> int:
    """A dream id as an int: a positive int, or its plain decimal string
    (no sign, spaces or leading zeros). Raises UnsafePathError."""
    if isinstance(value, int) and not isinstance(value, bool):
        text = str(value)
    elif isinstance(value, str):
        text = value
    else:
        raise UnsafePathError("invalid dream id")
    if not _DREAM_ID_RE.fullmatch(text):
        raise UnsafePathError("invalid dream id")
    return int(text)


def key_for(user_id: UUID, dream_id: int, language: str, digest: str) -> str:
    """The key of one rendering; `digest` names it (its first 24 hex chars)."""
    key = f"{_dream_folder(user_id, dream_id)}/{language}-{digest[:_DIGEST_LEN]}.mp3"
    if not is_key(key):
        raise UnsafePathError("invalid audio key")
    return key


def belongs_to(key: object, user_id: UUID, dream_id: int) -> TypeGuard[str]:
    """True when `key` is a valid key in this user's folder for this dream."""
    return is_key(key) and key.startswith(_dream_folder(user_id, dream_id) + "/")


def path_for(key: str) -> Path:
    """The file of `key` under AUDIO_DIR; it need not exist. Raises UnsafePathError."""
    if not is_key(key):
        raise UnsafePathError("invalid audio key")
    return _safe_path(key)


def exists(key: str) -> bool:
    """True when `key`'s file exists as a regular file."""
    return path_for(key).is_file()


def write(key: str, data: bytes) -> None:
    """Store `data` as `key`'s file, atomically, then delete the dream's older
    files in the same language (the other language's stay)."""
    path = path_for(key)
    _make_dirs(path.parent)
    _safe_path(key)  # again: no symlink slipped in while the folders were made
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as f:
            os.fchmod(f.fileno(), _FILE_MODE)
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(tmp)
        raise
    _prune(path)


def delete_dream(user_id: UUID, dream_id: int) -> None:
    """Delete one dream's folder. A missing folder is success; any other
    OSError is raised."""
    _delete_tree(_dream_folder(user_id, dream_id))


def delete_user(user_id: UUID) -> None:
    """Delete one user's folder, every dream's audio in it. A missing folder
    is success; any other OSError is raised."""
    _delete_tree(_user_folder(user_id))


def _user_folder(user_id: UUID) -> str:
    if not isinstance(user_id, UUID):
        raise UnsafePathError("invalid user id")
    return f"users/{user_id}"  # str(UUID): canonical, lower-case


def _dream_folder(user_id: UUID, dream_id: int) -> str:
    return f"{_user_folder(user_id)}/{parse_dream_id(dream_id)}"


def _safe_path(relative: str) -> Path:
    """AUDIO_DIR/relative, refused when it leaves AUDIO_DIR or any existing
    part below AUDIO_DIR is a symlink."""
    root = settings.audio_dir
    path = root / relative
    part = root
    for name in Path(relative).parts:
        part = part / name
        try:
            mode = part.lstat().st_mode
        except (FileNotFoundError, NotADirectoryError):
            break  # nothing exists from here on
        if stat.S_ISLNK(mode):
            raise UnsafePathError("symlink in the audio path")
    if not path.resolve().is_relative_to(root):
        raise UnsafePathError("audio path outside AUDIO_DIR")
    return path


def _make_dirs(folder: Path) -> None:
    """Create `folder` and its missing parents, each 0700 (mkdir -p would
    give the parents the default mode)."""
    missing: list[Path] = []
    while not folder.exists():
        missing.append(folder)
        folder = folder.parent
    for path in reversed(missing):
        with contextlib.suppress(FileExistsError):
            path.mkdir(mode=_DIR_MODE)


def _prune(path: Path) -> None:
    """Delete the other files of `path`'s language in its folder."""
    language = path.name.split("-", 1)[0]
    pattern = re.compile(rf"^{language}-[0-9a-f]{{{_DIGEST_LEN}}}\.mp3$")
    with os.scandir(path.parent) as entries:
        stale = [e.path for e in entries if pattern.fullmatch(e.name) and e.name != path.name]
    for stale_path in stale:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(stale_path)


def _delete_tree(relative: str) -> None:
    shutil.rmtree(_safe_path(relative), onexc=_ignore_missing)


def _ignore_missing(func: Callable[..., object], path: str, exc: BaseException) -> None:
    """rmtree's error hook: something already gone is deleted, anything else raises."""
    if not isinstance(exc, FileNotFoundError):
        raise exc
