"""Presentation layer: argparse definition, command dispatch, wording, exit codes.

All user-visible wording lives here as module constants so it can be compared
one-to-one with PRD section 7.2. :func:`main` returns an exit code and *never*
raises ``SystemExit`` -- argparse's own exits (``-h``, usage errors, unknown
subcommands) are caught and mapped to a return value.
"""

import argparse
import sys

from . import __version__
from .errors import (
    InvalidFileError,
    NotFoundError,
    ReadFileError,
    TodoError,
    UsageError,
    WriteFileError,
)
from .models import TodoStore
from .storage import load, resolve_path, sanitize_text, save

# --- fixed wording (PRD 7.2) -------------------------------------------------
MSG_ADDED = "Added #{id}: {text}"
MSG_COMPLETED = "Completed #{id}: {text}"
MSG_ALREADY_DONE = "Todo #{id} is already completed."
MSG_DELETED = "Deleted #{id}: {text}"
MSG_NO_PENDING = "No pending todos."
MSG_NO_TODOS = "No todos yet."
MSG_NOT_FOUND = "Error: todo #{id} not found."
MSG_EMPTY_TEXT = "Error: todo text cannot be empty."
MSG_BAD_ID = "Error: '{raw}' is not a valid todo id."
MSG_READ_FAIL = "Error: cannot read todo file {path}: {reason}"
MSG_INVALID_FILE = "Error: invalid todo file {path}: {reason}"
MSG_WRITE_FAIL = "Error: cannot write todo file {path}: {reason}"

_FILE_ERROR_MESSAGES = {
    ReadFileError: MSG_READ_FAIL,
    InvalidFileError: MSG_INVALID_FILE,
    WriteFileError: MSG_WRITE_FAIL,
}


def _parse_id(raw):
    # type: (str) -> int
    """Validate a positive integer id, raising :class:`UsageError` otherwise."""
    if raw.isdigit() and int(raw) >= 1:
        return int(raw)
    raise UsageError(MSG_BAD_ID.format(raw=raw))


def build_parser():
    # type: () -> argparse.ArgumentParser
    """Build the ``todo`` argument parser (four subcommands)."""
    parser = argparse.ArgumentParser(
        prog="todo",
        description="A tiny command-line todo list backed by a single JSON file.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--file",
        metavar="PATH",
        default=None,
        help="Path to the JSON data file (overrides TODO_FILE and ~/.todo.json).",
    )
    parser.add_argument(
        "--version",
        action="version",
        version="todo %s" % __version__,
        help="Show the program version and exit.",
    )
    subparsers = parser.add_subparsers(dest="command", title="commands")

    add_parser = subparsers.add_parser(
        "add",
        help="Add a new todo.",
        description=(
            "Add a new todo. Multiple TEXT arguments are joined with a single "
            "space, so no quoting is needed."
        ),
        epilog="Example:\n  todo add buy milk\n  Added #1: buy milk",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    add_parser.add_argument(
        "text",
        metavar="TEXT",
        nargs="+",
        help="Todo text (one or more words).",
    )

    list_parser = subparsers.add_parser(
        "list",
        help="List pending todos (use --all for every todo).",
        description=(
            "List todos. Only pending todos are shown by default; pass --all "
            "to include completed ones."
        ),
        epilog=(
            "Examples:\n"
            "  todo list\n"
            "  1. [ ] buy milk\n"
            "  todo list --all\n"
            "  1. [ ] buy milk\n"
            "  2. [x] write report"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    list_parser.add_argument(
        "--all",
        "-a",
        action="store_true",
        help="Show all todos, including completed ones.",
    )

    done_parser = subparsers.add_parser(
        "done",
        help="Mark a todo as completed (idempotent).",
        description="Mark the todo with the given ID as completed. Re-running it is a no-op.",
        epilog="Example:\n  todo done 1\n  Completed #1: buy milk",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    done_parser.add_argument(
        "id_raw",
        metavar="ID",
        help="Positive integer id of the todo (as shown by 'todo list').",
    )

    delete_parser = subparsers.add_parser(
        "delete",
        help="Delete a todo.",
        description="Delete the todo with the given ID. There is no confirmation prompt.",
        epilog="Example:\n  todo delete 2\n  Deleted #2: write report",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    delete_parser.add_argument(
        "id_raw",
        metavar="ID",
        help="Positive integer id of the todo (as shown by 'todo list').",
    )

    return parser


def _cmd_add(args, path):
    # type: (argparse.Namespace, object) -> int
    store = TodoStore(load(path))
    text = sanitize_text(args.text)
    if not text:
        raise UsageError(MSG_EMPTY_TEXT)
    todo = store.add(text)
    save(path, store.to_document())
    print(MSG_ADDED.format(id=todo["id"], text=todo["text"]))
    return 0


def _cmd_list(args, path):
    # type: (argparse.Namespace, object) -> int
    store = TodoStore(load(path))
    rows = store.sorted_all() if args.all else store.pending()
    if not rows:
        print(MSG_NO_TODOS if args.all else MSG_NO_PENDING)
        return 0
    for todo in rows:
        marker = "x" if todo.get("done") else " "
        print("{id}. [{marker}] {text}".format(id=todo["id"], marker=marker, text=todo["text"]))
    return 0


def _cmd_done(args, path):
    # type: (argparse.Namespace, object) -> int
    todo_id = _parse_id(args.id_raw)
    store = TodoStore(load(path))
    todo, changed = store.mark_done(todo_id)
    if changed:
        save(path, store.to_document())
        print(MSG_COMPLETED.format(id=todo["id"], text=todo["text"]))
    else:
        print(MSG_ALREADY_DONE.format(id=todo["id"]))
    return 0


def _cmd_delete(args, path):
    # type: (argparse.Namespace, object) -> int
    todo_id = _parse_id(args.id_raw)
    store = TodoStore(load(path))
    todo = store.delete(todo_id)
    save(path, store.to_document())
    print(MSG_DELETED.format(id=todo["id"], text=todo["text"]))
    return 0


_COMMANDS = {
    "add": _cmd_add,
    "list": _cmd_list,
    "done": _cmd_done,
    "delete": _cmd_delete,
}


def _run(argv):
    # type: (object) -> int
    parser = build_parser()
    args = parser.parse_args(argv)
    if getattr(args, "command", None) is None:
        parser.error("a command is required")
    path = resolve_path(args.file)
    return _COMMANDS[args.command](args, path)


def _system_exit_code(exc):
    # type: (SystemExit) -> int
    code = exc.code
    if code is None:
        return 0
    if isinstance(code, int):
        return code
    return 2


def main(argv=None):
    # type: (object) -> int
    """Run the CLI and return the process exit code (0 / 1 / 2).

    Never raises ``SystemExit``: argparse usage errors and ``-h`` / ``--help``
    / ``--version`` are converted into a return value so tests can call
    ``cli.main([...])`` directly.
    """
    try:
        return _run(argv)
    except SystemExit as exc:
        return _system_exit_code(exc)
    except UsageError as exc:
        print(exc.message, file=sys.stderr)
        return exc.exit_code
    except NotFoundError as exc:
        print(MSG_NOT_FOUND.format(id=exc.todo_id), file=sys.stderr)
        return exc.exit_code
    except TodoError as exc:
        template = _FILE_ERROR_MESSAGES.get(type(exc))
        if template is None:
            print(exc.message, file=sys.stderr)
        else:
            print(template.format(path=exc.path, reason=exc.reason), file=sys.stderr)
        return exc.exit_code
    except BrokenPipeError:
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
