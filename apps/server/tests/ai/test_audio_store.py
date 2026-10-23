"""The local rehearsal-audio store (audio/store.py): keys, paths, writes, deletes.

Every test gets its own AUDIO_DIR under tmp_path (conftest's `audio_dir`).
"""

from __future__ import annotations

import os
import stat
from pathlib import Path
from uuid import UUID, uuid4

import pytest

from redream_ai.audio import store
from redream_ai.config import Settings

_USER = UUID("0f8b6c1e-2a3d-4e5f-8a9b-0c1d2e3f4a5b")
_DIGEST_A = "a" * 24
_DIGEST_B = "b" * 24


def _key(lang: str = "en", digest: str = _DIGEST_A, dream_id: int = 7) -> str:
    return store.key_for(_USER, dream_id, lang, digest)


# --- settings -------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [(None, "data/audio"), ("", "data/audio"), ("  ", "data/audio"), ("var/mp3", "var/mp3")],
)
def test_audio_dir_is_resolved_against_the_working_directory(
    monkeypatch, tmp_path: Path, raw, expected
):
    monkeypatch.chdir(tmp_path)
    if raw is None:
        monkeypatch.delenv("AUDIO_DIR", raising=False)
    else:
        monkeypatch.setenv("AUDIO_DIR", raw)

    audio_dir = Settings(_env_file=None).audio_dir

    assert audio_dir.is_absolute()
    assert audio_dir == (tmp_path / expected).resolve()
    assert not audio_dir.exists()  # created on first write


def test_the_storage_settings_are_gone():
    for name in ("supabase_storage_bucket", "supabase_public_url", "rehearsal_url_ttl_seconds"):
        assert name not in Settings.model_fields


# --- keys -----------------------------------------------------------------------


def test_key_format():
    assert _key() == f"users/{_USER}/7/en-{_DIGEST_A}.mp3"
    assert store.is_key(_key())
    # The digest is cut to its first 24 hex chars.
    assert store.key_for(_USER, 7, "de", "c" * 64) == f"users/{_USER}/7/de-{'c' * 24}.mp3"


@pytest.mark.parametrize(
    "key",
    [
        f"users/{_USER}/7/../7/en-{_DIGEST_A}.mp3",
        f"users/{_USER}/../{_USER}/7/en-{_DIGEST_A}.mp3",
        f"/users/{_USER}/7/en-{_DIGEST_A}.mp3",
        f"/etc/users/{_USER}/7/en-{_DIGEST_A}.mp3",
        f"users\\{_USER}\\7\\en-{_DIGEST_A}.mp3",
        f"users/{_USER}/7\\en-{_DIGEST_A}.mp3",
        f"users/{str(_USER).upper()}/7/en-{_DIGEST_A}.mp3",
        f"users/{_USER}/7/en-{_DIGEST_A.upper()}.mp3",
        f"users/{_USER}/7/EN-{_DIGEST_A}.mp3",
        f"users/{_USER}/7/fr-{_DIGEST_A}.mp3",
        f"users/{_USER}/0/en-{_DIGEST_A}.mp3",
        f"users/{_USER}/07/en-{_DIGEST_A}.mp3",
        f"users/{_USER}/-7/en-{_DIGEST_A}.mp3",
        f"users/{_USER}/x/en-{_DIGEST_A}.mp3",
        f"users/{_USER}/7/v2-en-{_DIGEST_A}.mp3",
        f"users/{_USER}/7/en-{_DIGEST_A}.mp3/",
        f"users/{_USER}/7/en-{_DIGEST_A}.mp3\n",
        f"users/{_USER}/7/en-{'a' * 23}.mp3",
        f"users/{str(_USER).replace('-', '')}/7/en-{_DIGEST_A}.mp3",
        "",
    ],
)
def test_invalid_keys_are_refused_before_any_filesystem_use(key):
    assert not store.is_key(key)
    with pytest.raises(store.UnsafePathError):
        store.path_for(key)
    with pytest.raises(store.UnsafePathError):
        store.exists(key)
    with pytest.raises(store.UnsafePathError):
        store.write(key, b"x")


@pytest.mark.parametrize("value", ["0", "-1", "+1", "01", " 1", "1 ", "1.0", "x", "", 0, -3, True])
def test_dream_ids_are_parsed_strictly(value):
    with pytest.raises(store.UnsafePathError):
        store.parse_dream_id(value)


def test_dream_id_parse_accepts_positive_ints():
    assert store.parse_dream_id("7") == 7
    assert store.parse_dream_id(42) == 42


def test_key_for_refuses_bad_parts():
    with pytest.raises(store.UnsafePathError):
        store.key_for(_USER, 7, "fr", _DIGEST_A)
    with pytest.raises(store.UnsafePathError):
        store.key_for(_USER, 0, "en", _DIGEST_A)
    with pytest.raises(store.UnsafePathError):
        store.key_for(str(_USER), 7, "en", _DIGEST_A)  # type: ignore[arg-type]


def test_belongs_to_checks_user_and_dream():
    key = _key()
    assert store.belongs_to(key, _USER, 7)
    assert not store.belongs_to(key, uuid4(), 7)
    assert not store.belongs_to(key, _USER, 8)
    assert not store.belongs_to(_key(dream_id=71), _USER, 7)
    assert not store.belongs_to(None, _USER, 7)
    assert not store.belongs_to(f"users/{_USER}/7/../8/en-{_DIGEST_A}.mp3", _USER, 7)


# --- paths --------------------------------------------------------------------


def test_path_is_under_audio_dir(audio_dir: Path):
    assert store.path_for(_key()) == audio_dir / _key()


def test_a_symlinked_folder_is_refused(audio_dir: Path, tmp_path: Path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (audio_dir / "users" / str(_USER)).mkdir(parents=True)
    (audio_dir / "users" / str(_USER) / "7").symlink_to(outside, target_is_directory=True)

    with pytest.raises(store.UnsafePathError):
        store.path_for(_key())
    with pytest.raises(store.UnsafePathError):
        store.write(_key(), b"x")
    with pytest.raises(store.UnsafePathError):
        store.delete_dream(_USER, 7)
    assert list(outside.iterdir()) == []


def test_a_symlinked_user_folder_is_refused(audio_dir: Path, tmp_path: Path):
    outside = tmp_path / "outside"
    (outside / "7").mkdir(parents=True)
    (audio_dir / "users").mkdir(parents=True)
    (audio_dir / "users" / str(_USER)).symlink_to(outside, target_is_directory=True)

    with pytest.raises(store.UnsafePathError):
        store.exists(_key())
    with pytest.raises(store.UnsafePathError):
        store.delete_user(_USER)
    assert (outside / "7").is_dir()


def test_a_symlinked_file_is_refused(audio_dir: Path, tmp_path: Path):
    secret = tmp_path / "secret.mp3"
    secret.write_bytes(b"not yours")
    folder = audio_dir / "users" / str(_USER) / "7"
    folder.mkdir(parents=True)
    (folder / f"en-{_DIGEST_A}.mp3").symlink_to(secret)

    with pytest.raises(store.UnsafePathError):
        store.exists(_key())


def test_a_folder_linked_to_another_users_folder_is_refused(audio_dir: Path):
    """The link stays inside AUDIO_DIR, so only the symlink check can catch it:
    user A's key must not reach user B's file."""
    other = uuid4()
    store.write(store.key_for(other, 7, "en", _DIGEST_A), b"B's audio")
    (audio_dir / "users" / str(_USER)).mkdir(mode=0o700)
    (audio_dir / "users" / str(_USER) / "7").symlink_to(
        audio_dir / "users" / str(other) / "7", target_is_directory=True
    )

    with pytest.raises(store.UnsafePathError):
        store.exists(_key())
    with pytest.raises(store.UnsafePathError):
        store.path_for(_key())
    with pytest.raises(store.UnsafePathError):
        store.write(_key("en", _DIGEST_B), b"A's audio")
    with pytest.raises(store.UnsafePathError):
        store.delete_dream(_USER, 7)
    assert (audio_dir / store.key_for(other, 7, "en", _DIGEST_A)).read_bytes() == b"B's audio"


def test_a_user_folder_linked_to_another_users_folder_is_refused(audio_dir: Path):
    other = uuid4()
    store.write(store.key_for(other, 7, "en", _DIGEST_A), b"B's audio")
    (audio_dir / "users" / str(_USER)).symlink_to(
        audio_dir / "users" / str(other), target_is_directory=True
    )

    with pytest.raises(store.UnsafePathError):
        store.exists(_key())
    with pytest.raises(store.UnsafePathError):
        store.delete_user(_USER)
    assert store.exists(store.key_for(other, 7, "en", _DIGEST_A))


def test_a_file_where_a_folder_belongs_counts_as_missing(audio_dir: Path):
    (audio_dir / "users").mkdir(parents=True)
    (audio_dir / "users" / str(_USER)).write_bytes(b"not a folder")

    assert not store.exists(_key())


# --- writes ---------------------------------------------------------------------


def test_write_is_atomic_and_private(audio_dir: Path):
    store.write(_key(), b"mp3 bytes")

    path = audio_dir / _key()
    assert path.read_bytes() == b"mp3 bytes"
    assert store.exists(_key())
    assert os.listdir(path.parent) == [path.name]  # no temp file left
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    for folder in (audio_dir, audio_dir / "users", path.parent.parent, path.parent):
        assert stat.S_IMODE(folder.stat().st_mode) == 0o700


def test_a_failed_write_leaves_no_temp_file(audio_dir: Path, monkeypatch):
    def broken(src, dst):
        raise OSError("disk full")

    monkeypatch.setattr(store.os, "replace", broken)
    with pytest.raises(OSError, match="disk full"):
        store.write(_key(), b"x")

    assert os.listdir(audio_dir / "users" / str(_USER) / "7") == []
    assert not store.exists(_key())


def test_write_prunes_older_files_of_the_same_language_only(audio_dir: Path):
    store.write(_key("en", _DIGEST_A), b"en old")
    store.write(_key("de", _DIGEST_A), b"de")
    store.write(_key(dream_id=8), b"other dream")

    store.write(_key("en", _DIGEST_B), b"en new")

    folder = audio_dir / "users" / str(_USER) / "7"
    assert sorted(os.listdir(folder)) == [f"de-{_DIGEST_A}.mp3", f"en-{_DIGEST_B}.mp3"]
    assert store.exists(_key(dream_id=8))


def test_rewriting_the_same_key_keeps_it(audio_dir: Path):
    store.write(_key(), b"one")
    store.write(_key(), b"two")
    assert (audio_dir / _key()).read_bytes() == b"two"


# --- deletes --------------------------------------------------------------------


def test_delete_dream_removes_only_that_dream(audio_dir: Path):
    store.write(_key(dream_id=7), b"x")
    store.write(_key(dream_id=8), b"y")

    store.delete_dream(_USER, 7)

    assert not (audio_dir / "users" / str(_USER) / "7").exists()
    assert store.exists(_key(dream_id=8))


def test_delete_user_removes_every_dream(audio_dir: Path):
    other = uuid4()
    store.write(_key(dream_id=7), b"x")
    store.write(_key("de", dream_id=8), b"y")
    store.write(store.key_for(other, 9, "en", _DIGEST_A), b"z")

    store.delete_user(_USER)

    assert not (audio_dir / "users" / str(_USER)).exists()
    assert store.exists(store.key_for(other, 9, "en", _DIGEST_A))


def test_deleting_a_missing_folder_is_fine(audio_dir: Path):
    store.delete_dream(_USER, 7)  # AUDIO_DIR itself does not exist yet
    store.delete_user(_USER)
    store.write(_key(dream_id=8), b"x")
    store.delete_dream(_USER, 7)
    assert store.exists(_key(dream_id=8))


@pytest.mark.skipif(os.geteuid() == 0, reason="root ignores folder permissions")
def test_other_delete_errors_are_raised(audio_dir: Path):
    store.write(_key(), b"x")
    folder = audio_dir / "users" / str(_USER) / "7"
    folder.chmod(0o500)  # its file cannot be unlinked
    try:
        with pytest.raises(PermissionError):
            store.delete_dream(_USER, 7)
        with pytest.raises(PermissionError):
            store.delete_user(_USER)
    finally:
        folder.chmod(0o700)
    assert store.exists(_key())
