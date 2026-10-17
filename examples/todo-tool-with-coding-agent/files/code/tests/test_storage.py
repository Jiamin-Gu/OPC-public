"""Unit tests for :mod:`todo_cli.storage` (path resolution, load, atomic save)."""

import json
import os
import re

import pytest

from todo_cli import storage
from todo_cli.errors import InvalidFileError, WriteFileError

CREATED_AT_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")


def _fake_home(monkeypatch, home):
    monkeypatch.setenv("HOME", str(home))
    if os.name == "nt":
        monkeypatch.setenv("USERPROFILE", str(home))


# --- resolve_path ----------------------------------------------------------- #
def test_resolve_path_prefers_file_option(tmp_path, monkeypatch):
    monkeypatch.setenv("TODO_FILE", str(tmp_path / "env.json"))
    assert storage.resolve_path(str(tmp_path / "cli.json")) == tmp_path / "cli.json"


def test_resolve_path_uses_env_var(tmp_path, monkeypatch):
    monkeypatch.setenv("TODO_FILE", str(tmp_path / "env.json"))
    assert storage.resolve_path(None) == tmp_path / "env.json"


def test_resolve_path_default_is_home_dot_todo_json(tmp_path, monkeypatch):
    monkeypatch.delenv("TODO_FILE", raising=False)
    _fake_home(monkeypatch, tmp_path)
    assert storage.resolve_path(None) == tmp_path / ".todo.json"


def test_resolve_path_expands_tilde(tmp_path, monkeypatch):
    _fake_home(monkeypatch, tmp_path)
    assert storage.resolve_path("~/t.json") == tmp_path / "t.json"


def test_resolve_path_empty_env_falls_back_to_default(tmp_path, monkeypatch):
    monkeypatch.setenv("TODO_FILE", "")
    _fake_home(monkeypatch, tmp_path)
    assert storage.resolve_path(None) == tmp_path / ".todo.json"


# --- load ------------------------------------------------------------------- #
def test_load_missing_file_returns_empty_document_and_creates_nothing(tmp_path):
    path = tmp_path / "missing.json"
    assert storage.load(path) == {"version": 1, "next_id": 1, "todos": []}
    assert not path.exists()


def test_load_empty_file_is_treated_as_empty_document(tmp_path):
    path = tmp_path / "empty.json"
    path.write_text("", encoding="utf-8")
    assert storage.load(path) == storage.empty_document()


def test_load_invalid_json_raises(tmp_path):
    path = tmp_path / "bad.json"
    path.write_text("{ not json", encoding="utf-8")
    with pytest.raises(InvalidFileError):
        storage.load(path)


def test_load_unsupported_version_raises(tmp_path):
    path = tmp_path / "v2.json"
    path.write_text(json.dumps({"version": 2, "next_id": 1, "todos": []}), encoding="utf-8")
    with pytest.raises(InvalidFileError):
        storage.load(path)


@pytest.mark.parametrize(
    "payload",
    [
        {"version": 1, "next_id": 0, "todos": []},
        {"version": 1, "next_id": True, "todos": []},
        {"version": 1, "next_id": 1, "todos": "oops"},
        {"version": 1, "next_id": 1, "todos": [{"id": "1", "text": "a", "done": False, "created_at": "x"}]},
        {"version": 1, "next_id": 1, "todos": [{"id": 1, "text": "", "done": False, "created_at": "x"}]},
        {"version": 1, "next_id": 1, "todos": [{"id": 1, "text": "a", "done": "no", "created_at": "x"}]},
        {"version": 1, "next_id": 1, "todos": [{"id": 1, "text": "a", "done": False}]},
        {
            "version": 1,
            "next_id": 2,
            "todos": [
                {"id": 1, "text": "a", "done": False, "created_at": "x"},
                {"id": 1, "text": "b", "done": False, "created_at": "x"},
            ],
        },
        [1, 2, 3],
    ],
)
def test_load_rejects_malformed_documents(tmp_path, payload):
    path = tmp_path / "bad.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(InvalidFileError):
        storage.load(path)


# --- save ------------------------------------------------------------------- #
def test_save_roundtrip_is_utf8_with_trailing_newline(tmp_path):
    path = tmp_path / "todos.json"
    document = storage.empty_document()
    document["todos"].append(
        {"id": 1, "text": "买牛奶", "done": False, "created_at": storage.now_iso()}
    )
    document["next_id"] = 2
    storage.save(path, document)

    raw = path.read_bytes()
    assert raw.endswith(b"\n")
    text = raw.decode("utf-8")
    assert "买牛奶" in text
    assert json.loads(text) == document


def test_save_leaves_no_temporary_files(tmp_path):
    path = tmp_path / "todos.json"
    storage.save(path, storage.empty_document())
    leftovers = [entry.name for entry in tmp_path.iterdir() if entry.name != "todos.json"]
    assert leftovers == []


def test_save_missing_directory_raises_and_creates_nothing(tmp_path):
    path = tmp_path / "nope" / "todos.json"
    with pytest.raises(WriteFileError):
        storage.save(path, storage.empty_document())
    assert not (tmp_path / "nope").exists()


def test_save_failure_keeps_original_content_and_cleans_temp_file(tmp_path, monkeypatch):
    path = tmp_path / "todos.json"
    storage.save(path, storage.empty_document())
    before = path.read_bytes()

    def boom(*args, **kwargs):
        raise OSError("disk on fire")

    monkeypatch.setattr(storage.os, "replace", boom)
    with pytest.raises(WriteFileError):
        storage.save(path, {"version": 1, "next_id": 9, "todos": []})

    assert path.read_bytes() == before
    leftovers = [entry.name for entry in tmp_path.iterdir() if entry.name != "todos.json"]
    assert leftovers == []


# --- helpers ---------------------------------------------------------------- #
@pytest.mark.parametrize(
    "parts,expected",
    [
        (["  多余 空格  "], "多余 空格"),
        (["a\nb"], "a b"),
        (["a", "b", "c"], "a b c"),
        (["  ", ""], ""),
        (["\t x \t"], "x"),
        (["🌱", "浇水"], "🌱 浇水"),
    ],
)
def test_sanitize_text(parts, expected):
    assert storage.sanitize_text(parts) == expected


def test_now_iso_matches_contract():
    assert CREATED_AT_RE.match(storage.now_iso())
