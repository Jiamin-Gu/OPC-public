"""Storage layer for the ``todo`` CLI.

Responsibilities (single direction of dependency: ``cli`` -> ``models`` ->
``storage``):

* resolve the data file path (``--file`` > ``TODO_FILE`` > ``~/.todo.json``);
* load and validate the JSON document (R-1 / R-2 / R-3);
* write the document back atomically (R-4) without ever creating missing
  directories (R-5).

This module never prints anything and never calls ``sys.exit``; it raises the
errors defined in :mod:`todo_cli.errors`.
"""

import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from .errors import InvalidFileError, ReadFileError, WriteFileError

DEFAULT_FILENAME = ".todo.json"
SUPPORTED_VERSION = 1


def resolve_path(file_option=None):
    # type: (object) -> Path
    """Return the data file path.

    Priority: ``--file PATH`` > ``TODO_FILE`` env var > ``~/.todo.json``.
    Every value is expanded with ``~`` (``expanduser``) and made absolute so
    error messages always contain an absolute path.
    """
    if file_option:
        raw = str(file_option)
    else:
        raw = os.environ.get("TODO_FILE") or ""
    if not raw:
        raw = "~/" + DEFAULT_FILENAME
    return Path(os.path.abspath(os.path.expanduser(raw)))


def empty_document():
    # type: () -> dict
    """Return the document used when the file does not exist (R-1)."""
    return {"version": SUPPORTED_VERSION, "next_id": 1, "todos": []}


def now_iso():
    # type: () -> str
    """Current UTC time as ``YYYY-MM-DDTHH:MM:SSZ`` (PRD section 5)."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def sanitize_text(parts):
    # type: (list) -> str
    """Join ``parts`` with a single space and collapse all whitespace.

    Equivalent to ``strip()`` followed by folding any run of whitespace
    (including newlines and tabs) into one space. Returns ``""`` if the result
    is empty; the caller decides that this is a usage error.
    """
    return " ".join(" ".join(parts).split())


def _os_reason(exc):
    # type: (OSError) -> str
    """Human readable reason for an OSError used in error messages."""
    if isinstance(exc, FileNotFoundError):
        return "directory does not exist"
    return getattr(exc, "strerror", None) or str(exc)


def _validate(path, data):
    # type: (Path, object) -> dict
    """Validate a decoded document, raising ``InvalidFileError`` on failure."""
    if not isinstance(data, dict):
        raise InvalidFileError(path, "top-level JSON value is not an object")

    version = data.get("version")
    if version != SUPPORTED_VERSION:
        raise InvalidFileError(
            path,
            "unsupported version %r (expected %d)" % (version, SUPPORTED_VERSION),
        )

    next_id = data.get("next_id")
    if isinstance(next_id, bool) or not isinstance(next_id, int) or next_id < 1:
        raise InvalidFileError(path, "field 'next_id' must be an integer >= 1")

    todos = data.get("todos")
    if not isinstance(todos, list):
        raise InvalidFileError(path, "field 'todos' must be a list")

    seen_ids = set()
    for item in todos:
        if not isinstance(item, dict):
            raise InvalidFileError(path, "each todo must be an object")
        todo_id = item.get("id")
        if isinstance(todo_id, bool) or not isinstance(todo_id, int) or todo_id < 1:
            raise InvalidFileError(path, "field 'id' must be an integer >= 1")
        if todo_id in seen_ids:
            raise InvalidFileError(path, "duplicate todo id %d" % todo_id)
        seen_ids.add(todo_id)
        text = item.get("text")
        if not isinstance(text, str) or not text:
            raise InvalidFileError(path, "field 'text' must be a non-empty string")
        if not isinstance(item.get("done"), bool):
            raise InvalidFileError(path, "field 'done' must be a boolean")
        if not isinstance(item.get("created_at"), str):
            raise InvalidFileError(path, "field 'created_at' must be a string")
    return data


def load(path):
    # type: (Path) -> dict
    """Load and validate the document at ``path``.

    * missing file -> ``empty_document()`` without creating anything (R-1);
    * unreadable file -> :class:`ReadFileError` (R-2);
    * invalid JSON / structure / version -> :class:`InvalidFileError` (R-3).

    A zero-byte (or whitespace-only) file is treated as an empty document, so
    ``todo done 1`` reports ``todo #1 not found.`` instead of a parse error.
    Unknown fields are preserved: the decoded dict is returned as-is.
    """
    try:
        raw = Path(path).read_text(encoding="utf-8")
    except FileNotFoundError:
        return empty_document()
    except OSError as exc:
        raise ReadFileError(path, _os_reason(exc)) from exc

    if not raw.strip():
        return empty_document()

    try:
        data = json.loads(raw)
    except ValueError as exc:
        raise InvalidFileError(path, str(exc)) from exc

    return _validate(path, data)


def save(path, document):
    # type: (Path, dict) -> None
    """Atomically write ``document`` to ``path`` (R-4 / R-5).

    Writes a temporary file in the same directory, flushes + fsyncs it and then
    ``os.replace()``s it over the target. On any failure the temporary file is
    removed and the previous file (if any) is left untouched. Missing
    directories are never created.
    """
    path = Path(path)

    if path.exists() and not os.access(str(path), os.W_OK):
        raise WriteFileError(path, "permission denied")

    tmp_fd = None
    tmp_name = None
    try:
        tmp_fd, tmp_name = tempfile.mkstemp(
            dir=str(path.parent),
            prefix=path.name + ".",
            suffix=".tmp",
        )
        with os.fdopen(tmp_fd, "w", encoding="utf-8", newline="\n") as handle:
            # fdopen() took ownership of the descriptor.
            tmp_fd = None
            json.dump(document, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_name, str(path))
        tmp_name = None
    except OSError as exc:
        raise WriteFileError(path, _os_reason(exc)) from exc
    finally:
        if tmp_fd is not None:
            try:
                os.close(tmp_fd)
            except OSError:
                pass
        if tmp_name is not None and os.path.exists(tmp_name):
            try:
                os.unlink(tmp_name)
            except OSError:
                pass
