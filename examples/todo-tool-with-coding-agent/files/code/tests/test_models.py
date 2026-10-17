"""Unit tests for :mod:`todo_cli.models` (id allocation, idempotency, ordering)."""

import re

import pytest

from todo_cli.errors import NotFoundError
from todo_cli.models import TodoStore

CREATED_AT_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")


def todo(todo_id, text="t", done=False):
    return {"id": todo_id, "text": text, "done": done, "created_at": "2025-05-01T02:10:33Z"}


def make_store(todos=None, next_id=None):
    if todos is None:
        todos = []
    if next_id is None:
        next_id = max([item["id"] for item in todos], default=0) + 1
    return TodoStore({"version": 1, "next_id": next_id, "todos": todos})


def test_add_assigns_incrementing_ids_and_bumps_next_id():
    store = make_store()
    first = store.add("a")
    second = store.add("b")
    assert (first["id"], second["id"]) == (1, 2)
    assert store.to_document()["next_id"] == 3


def test_add_produces_contract_fields():
    store = make_store()
    item = store.add("买牛奶")
    assert item["text"] == "买牛奶"
    assert item["done"] is False
    assert CREATED_AT_RE.match(item["created_at"])


def test_add_does_not_reuse_deleted_ids():
    store = make_store([todo(1), todo(2), todo(3)], next_id=4)
    store.delete(2)
    new = store.add("new")
    assert new["id"] == 4
    assert store.to_document()["next_id"] == 5


def test_mark_done_is_idempotent():
    store = make_store([todo(1)])
    item, changed = store.mark_done(1)
    assert changed is True
    assert item["done"] is True
    item_again, changed_again = store.mark_done(1)
    assert changed_again is False
    assert item_again["done"] is True


def test_mark_done_missing_raises_not_found():
    store = make_store([todo(1)])
    with pytest.raises(NotFoundError):
        store.mark_done(99)


def test_delete_removes_todo_but_keeps_next_id():
    store = make_store([todo(1)], next_id=7)
    removed = store.delete(1)
    assert removed["id"] == 1
    assert store.to_document()["todos"] == []
    assert store.to_document()["next_id"] == 7


def test_delete_missing_raises_not_found():
    store = make_store([todo(1)])
    with pytest.raises(NotFoundError):
        store.delete(99)


def test_pending_and_sorted_all_are_sorted_by_id():
    store = make_store([todo(3, done=True), todo(1), todo(2, done=True)])
    assert [item["id"] for item in store.pending()] == [1]
    assert [item["id"] for item in store.sorted_all()] == [1, 2, 3]


def test_find_returns_none_for_missing_id():
    store = make_store([todo(1)])
    assert store.find(1)["id"] == 1
    assert store.find(2) is None


def test_to_document_preserves_unknown_fields():
    document = {
        "version": 1,
        "next_id": 2,
        "todos": [dict(todo(1), tag="x")],
        "extra": {"k": "v"},
    }
    store = TodoStore(document)
    store.add("b")
    out = store.to_document()
    assert out["version"] == 1
    assert out["extra"] == {"k": "v"}
    assert out["todos"][0]["tag"] == "x"
