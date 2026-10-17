"""Business layer: an in-memory view over a loaded document.

``TodoStore`` is pure logic -- it never touches the filesystem and never prints.
It keeps every todo as a plain ``dict`` so that unknown fields survive a
read/modify/write cycle (PRD 6.2 engineering constraint).
"""

from .errors import NotFoundError
from .storage import now_iso


class TodoStore(object):
    """Mutable collection of todos plus the ``next_id`` counter."""

    def __init__(self, document):
        # type: (dict) -> None
        self._document = document
        if not isinstance(self._document.get("todos"), list):
            self._document["todos"] = []
        self._todos = self._document["todos"]

    @property
    def todos(self):
        # type: () -> list
        return self._todos

    @property
    def next_id(self):
        # type: () -> int
        return self._document.get("next_id", 1)

    def find(self, todo_id):
        # type: (int) -> object
        """Return the todo with ``todo_id`` or ``None``."""
        for todo in self._todos:
            if todo.get("id") == todo_id:
                return todo
        return None

    def add(self, text):
        # type: (str) -> dict
        """Append a new todo, allocating ``new_id = next_id`` (R-7)."""
        todo = {
            "id": self._document.get("next_id", 1),
            "text": text,
            "done": False,
            "created_at": now_iso(),
        }
        self._document["next_id"] = todo["id"] + 1
        self._todos.append(todo)
        return todo

    def mark_done(self, todo_id):
        # type: (int) -> tuple
        """Mark a todo done.

        Returns ``(todo, changed)``; ``changed`` is ``False`` for the idempotent
        "already completed" branch, in which case nothing must be written.
        Raises :class:`NotFoundError` if the id does not exist.
        """
        todo = self.find(todo_id)
        if todo is None:
            raise NotFoundError(todo_id)
        if todo.get("done"):
            return todo, False
        todo["done"] = True
        return todo, True

    def delete(self, todo_id):
        # type: (int) -> dict
        """Remove a todo and return it; ``next_id`` is intentionally untouched."""
        todo = self.find(todo_id)
        if todo is None:
            raise NotFoundError(todo_id)
        self._todos.remove(todo)
        return todo

    def pending(self):
        # type: () -> list
        """Not-done todos, sorted by id ascending."""
        return sorted(
            [todo for todo in self._todos if not todo.get("done")],
            key=lambda todo: todo.get("id", 0),
        )

    def sorted_all(self):
        # type: () -> list
        """All todos, sorted by id ascending."""
        return sorted(self._todos, key=lambda todo: todo.get("id", 0))

    def to_document(self):
        # type: () -> dict
        """Return the document to persist (unknown fields preserved)."""
        self._document["todos"] = self._todos
        return self._document
