"""Exception types used across the ``todo`` CLI.

Every error carries an ``exit_code`` that :func:`todo_cli.cli.main` maps to the
process exit status:

* ``1`` -- runtime / business error (missing id, unreadable / invalid /
  unwritable data file);
* ``2`` -- usage error (empty text, invalid id, broken command line).

``0`` always means success and is never represented by an exception.

The file-related errors (:class:`ReadFileError`, :class:`InvalidFileError`,
:class:`WriteFileError`) carry the offending ``path`` and the raw ``reason`` so
that the presentation layer (``cli.py``) can render the exact wording required
by the PRD.
"""


class TodoError(Exception):
    """Base class for all controlled errors."""

    exit_code = 1

    def __init__(self, message=""):
        # type: (str) -> None
        super().__init__(message)
        self.message = message


class UsageError(TodoError):
    """Bad usage: empty text or a non-positive / non-numeric id."""

    exit_code = 2


class NotFoundError(TodoError):
    """The requested todo id does not exist in the document."""

    exit_code = 1

    def __init__(self, todo_id):
        # type: (int) -> None
        super().__init__("todo #%d not found." % todo_id)
        self.todo_id = todo_id


class ReadFileError(TodoError):
    """The data file exists but could not be read (permissions, IO, ...)."""

    exit_code = 1

    def __init__(self, path, reason):
        # type: (object, str) -> None
        super().__init__(reason)
        self.path = path
        self.reason = reason


class InvalidFileError(TodoError):
    """The data file is not valid JSON or violates the v1 document contract."""

    exit_code = 1

    def __init__(self, path, reason):
        # type: (object, str) -> None
        super().__init__(reason)
        self.path = path
        self.reason = reason


class WriteFileError(TodoError):
    """The document could not be written (missing directory, permission, IO)."""

    exit_code = 1

    def __init__(self, path, reason):
        # type: (object, str) -> None
        super().__init__(reason)
        self.path = path
        self.reason = reason
