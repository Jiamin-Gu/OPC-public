"""End-to-end tests for the ``todo`` CLI.

Mapped one-to-one to PRD section 8 acceptance criteria
(AC-ADD / AC-LIST / AC-DONE / AC-DEL / AC-G / AC-ERR) plus data-integrity and
subprocess smoke tests.

Isolation rules:

* every case works on a file inside ``tmp_path`` (``--file``);
* an autouse fixture snapshots the *real* ``~/.todo.json`` and fails the test if
  anything changed it -- no test may read or write the user's real file;
* cases about the default path / ``~`` expansion monkeypatch ``HOME`` (and
  ``USERPROFILE`` on Windows) onto ``tmp_path``.
"""

import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

from todo_cli import __version__, cli

CODE_DIR = Path(__file__).resolve().parents[1]
CREATED_AT_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")


# --------------------------------------------------------------------------- #
# helpers / isolation
# --------------------------------------------------------------------------- #
def data_file(tmp_path):
    return tmp_path / "todos.json"


def run(tmp_path, *argv):
    """Run the CLI against the isolated temp data file."""
    return cli.main(["--file", str(data_file(tmp_path))] + list(argv))


def read_doc(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def make_todo(todo_id, text, done=False, created_at="2025-05-01T02:10:33Z"):
    return {"id": todo_id, "text": text, "done": done, "created_at": created_at}


def seed(path, todos, next_id=None):
    if next_id is None:
        next_id = max([item["id"] for item in todos], default=0) + 1
    Path(path).write_text(
        json.dumps({"version": 1, "next_id": next_id, "todos": todos}, ensure_ascii=False, indent=2)
        + "\n",
        encoding="utf-8",
    )


def use_fake_home(monkeypatch, fake_home):
    monkeypatch.delenv("TODO_FILE", raising=False)
    monkeypatch.setenv("HOME", str(fake_home))
    if os.name == "nt":
        monkeypatch.setenv("USERPROFILE", str(fake_home))


def _snapshot(path):
    try:
        stat = path.stat()
    except OSError:
        return None
    return (stat.st_mtime_ns, path.read_bytes())


@pytest.fixture(autouse=True)
def guard_real_home():
    """Fail if a test creates or modifies the real ``~/.todo.json``."""
    real_default = Path.home() / ".todo.json"
    before = _snapshot(real_default)
    assert os.environ.get("TODO_FILE") != str(real_default), (
        "TODO_FILE must not point at the real ~/.todo.json"
    )
    yield
    assert _snapshot(real_default) == before, "a test touched the real ~/.todo.json"


# --------------------------------------------------------------------------- #
# AC-ADD-1 .. AC-ADD-9
# --------------------------------------------------------------------------- #
def test_ac_add_1_creates_file_with_full_contract(tmp_path, capsys):
    path = data_file(tmp_path)
    assert run(tmp_path, "add", "买牛奶") == 0
    out = capsys.readouterr()
    assert out.out == "Added #1: 买牛奶\n"
    assert out.err == ""

    doc = read_doc(path)
    assert doc["version"] == 1
    assert doc["next_id"] == 2
    assert len(doc["todos"]) == 1
    item = doc["todos"][0]
    assert item["id"] == 1
    assert item["text"] == "买牛奶"
    assert item["done"] is False
    assert CREATED_AT_RE.match(item["created_at"])


def test_ac_add_2_second_add_gets_id_2_and_keeps_first(tmp_path, capsys):
    path = data_file(tmp_path)
    assert run(tmp_path, "add", "买牛奶") == 0
    capsys.readouterr()
    first = read_doc(path)["todos"][0]

    assert run(tmp_path, "add", "写周报") == 0
    assert capsys.readouterr().out == "Added #2: 写周报\n"

    doc = read_doc(path)
    assert doc["next_id"] == 3
    assert doc["todos"][0] == first
    assert doc["todos"][1]["text"] == "写周报"


def test_ac_add_3_multiple_arguments_joined_with_single_space(tmp_path, capsys):
    assert run(tmp_path, "add", "给", "orders", "表加索引") == 0
    out = capsys.readouterr()
    assert out.out == "Added #1: 给 orders 表加索引\n"
    assert read_doc(data_file(tmp_path))["todos"][0]["text"] == "给 orders 表加索引"


def test_ac_add_4_surrounding_and_double_spaces_collapsed(tmp_path, capsys):
    assert run(tmp_path, "add", "  多余 空格  ") == 0
    capsys.readouterr()
    assert read_doc(data_file(tmp_path))["todos"][0]["text"] == "多余 空格"


def test_ac_add_5_newlines_folded_into_spaces(tmp_path, capsys):
    assert run(tmp_path, "add", "a\nb") == 0
    capsys.readouterr()
    assert read_doc(data_file(tmp_path))["todos"][0]["text"] == "a b"


def test_ac_add_6_empty_text_is_usage_error_and_writes_nothing(tmp_path, capsys):
    path = data_file(tmp_path)
    assert run(tmp_path, "add", "") == 2
    out = capsys.readouterr()
    assert out.out == ""
    assert "Error: todo text cannot be empty." in out.err
    assert not path.exists()

    seed(path, [make_todo(1, "existing")])
    before = path.read_bytes()
    assert run(tmp_path, "add", "   ") == 2
    out2 = capsys.readouterr()
    assert out2.out == ""
    assert "Error: todo text cannot be empty." in out2.err
    assert path.read_bytes() == before


def test_ac_add_7_missing_text_is_usage_error(tmp_path, capsys):
    assert run(tmp_path, "add") == 2
    out = capsys.readouterr()
    assert out.out == ""
    assert "usage" in out.err.lower()
    assert not data_file(tmp_path).exists()


def test_ac_add_8_id_continues_from_next_id(tmp_path, capsys):
    seed(
        data_file(tmp_path),
        [make_todo(1, "a"), make_todo(2, "b", done=True), make_todo(3, "c")],
        next_id=4,
    )
    assert run(tmp_path, "add", "新任务") == 0
    out = capsys.readouterr()
    assert out.out == "Added #4: 新任务\n"
    assert read_doc(data_file(tmp_path))["next_id"] == 5


def test_ac_add_9_emoji_roundtrip(tmp_path, capsys):
    path = data_file(tmp_path)
    assert run(tmp_path, "add", "🌱 浇水") == 0
    assert capsys.readouterr().out == "Added #1: 🌱 浇水\n"
    assert read_doc(path)["todos"][0]["text"] == "🌱 浇水"
    # stored verbatim (ensure_ascii=False), not as \uXXXX escapes
    assert "🌱 浇水" in path.read_text(encoding="utf-8")
    assert run(tmp_path, "list") == 0
    assert capsys.readouterr().out == "1. [ ] 🌱 浇水\n"


# --------------------------------------------------------------------------- #
# AC-LIST-1 .. AC-LIST-9
# --------------------------------------------------------------------------- #
def test_ac_list_1_missing_file_prints_no_pending_and_creates_nothing(tmp_path, capsys):
    path = data_file(tmp_path)
    assert run(tmp_path, "list") == 0
    out = capsys.readouterr()
    assert out.out == "No pending todos.\n"
    assert out.err == ""
    assert not path.exists()


def test_ac_list_2_empty_document_with_all_prints_no_todos(tmp_path, capsys):
    seed(data_file(tmp_path), [])
    assert run(tmp_path, "list", "--all") == 0
    assert capsys.readouterr().out == "No todos yet.\n"


def _mixed_view(tmp_path):
    seed(
        data_file(tmp_path),
        [
            make_todo(1, "买牛奶"),
            make_todo(2, "写周报", done=True),
            make_todo(3, "给 orders 表加索引"),
        ],
    )


def test_ac_list_3_pending_only_hides_completed(tmp_path, capsys):
    _mixed_view(tmp_path)
    assert run(tmp_path, "list") == 0
    out = capsys.readouterr()
    assert out.out == "1. [ ] 买牛奶\n3. [ ] 给 orders 表加索引\n"
    assert "写周报" not in out.out


def test_ac_list_4_all_shows_status_markers(tmp_path, capsys):
    _mixed_view(tmp_path)
    assert run(tmp_path, "list", "--all") == 0
    out = capsys.readouterr()
    assert out.out == "1. [ ] 买牛奶\n2. [x] 写周报\n3. [ ] 给 orders 表加索引\n"


def test_ac_list_5_all_done_prints_no_pending(tmp_path, capsys):
    seed(data_file(tmp_path), [make_todo(1, "a", done=True), make_todo(2, "b", done=True)])
    assert run(tmp_path, "list") == 0
    assert capsys.readouterr().out == "No pending todos.\n"


def test_ac_list_6_list_does_not_modify_file(tmp_path, capsys):
    path = data_file(tmp_path)
    seed(path, [make_todo(1, "买牛奶"), make_todo(2, "写周报", done=True)])
    before_bytes = path.read_bytes()
    before_mtime = path.stat().st_mtime_ns

    assert run(tmp_path, "list") == 0
    assert run(tmp_path, "list", "--all") == 0
    capsys.readouterr()

    assert path.read_bytes() == before_bytes
    assert path.stat().st_mtime_ns == before_mtime


def test_ac_list_7_short_and_long_flag_are_equivalent(tmp_path, capsys):
    _mixed_view(tmp_path)
    assert run(tmp_path, "list", "--all") == 0
    long_output = capsys.readouterr().out
    assert run(tmp_path, "list", "-a") == 0
    short_output = capsys.readouterr().out
    assert long_output == short_output


def test_ac_list_8_format_and_trailing_newline(tmp_path, capsys):
    seed(data_file(tmp_path), [make_todo(1, "🌱 浇水"), make_todo(2, "写周报", done=True)])
    assert run(tmp_path, "list", "--all") == 0
    out = capsys.readouterr().out
    assert out == "1. [ ] 🌱 浇水\n2. [x] 写周报\n"
    assert out.endswith("\n")


def test_ac_list_9_help_documents_all_flag(tmp_path, capsys):
    assert run(tmp_path, "list", "-h") == 0
    out = capsys.readouterr()
    assert out.err == ""
    assert "--all" in out.out
    assert "list" in out.out.lower()
    assert "Example" in out.out


# --------------------------------------------------------------------------- #
# AC-DONE-1 .. AC-DONE-8
# --------------------------------------------------------------------------- #
def test_ac_done_1_marks_completed_and_keeps_next_id(tmp_path, capsys):
    path = data_file(tmp_path)
    seed(path, [make_todo(1, "买牛奶"), make_todo(2, "写周报")], next_id=3)
    assert run(tmp_path, "done", "1") == 0
    out = capsys.readouterr()
    assert out.out == "Completed #1: 买牛奶\n"
    assert out.err == ""

    doc = read_doc(path)
    assert doc["todos"][0]["done"] is True
    assert doc["todos"][1] == make_todo(2, "写周报")
    assert doc["next_id"] == 3


def test_ac_done_2_already_completed_is_idempotent_and_does_not_write(tmp_path, capsys):
    path = data_file(tmp_path)
    seed(path, [make_todo(1, "买牛奶", done=True)])
    before_bytes = path.read_bytes()
    before_mtime = path.stat().st_mtime_ns

    assert run(tmp_path, "done", "1") == 0
    out = capsys.readouterr()
    assert out.out == "Todo #1 is already completed.\n"
    assert out.err == ""

    assert path.read_bytes() == before_bytes
    assert path.stat().st_mtime_ns == before_mtime


def test_ac_done_3_missing_id_fails_without_writing(tmp_path, capsys):
    path = data_file(tmp_path)
    seed(path, [make_todo(1, "a")])
    before = path.read_bytes()

    assert run(tmp_path, "done", "99") == 1
    out = capsys.readouterr()
    assert out.out == ""
    assert out.err == "Error: todo #99 not found.\n"
    assert path.read_bytes() == before


def test_ac_done_4_non_numeric_id_is_usage_error(tmp_path, capsys):
    path = data_file(tmp_path)
    seed(path, [make_todo(1, "a")])
    before = path.read_bytes()

    assert run(tmp_path, "done", "abc") == 2
    out = capsys.readouterr()
    assert out.out == ""
    assert "Error: 'abc' is not a valid todo id." in out.err
    assert path.read_bytes() == before


@pytest.mark.parametrize("raw", ["0", "-1"])
def test_ac_done_5_non_positive_id_is_usage_error(tmp_path, capsys, raw):
    path = data_file(tmp_path)
    seed(path, [make_todo(1, "a")])
    before = path.read_bytes()

    assert run(tmp_path, "done", raw) == 2
    out = capsys.readouterr()
    assert out.out == ""
    assert "not a valid todo id" in out.err
    assert path.read_bytes() == before


def test_ac_done_6_empty_document_reports_not_found(tmp_path, capsys):
    path = data_file(tmp_path)

    path.write_text("", encoding="utf-8")
    assert run(tmp_path, "done", "1") == 1
    out = capsys.readouterr()
    assert out.out == ""
    assert out.err == "Error: todo #1 not found.\n"

    seed(path, [])
    assert run(tmp_path, "done", "1") == 1
    out2 = capsys.readouterr()
    assert out2.err == "Error: todo #1 not found.\n"


def test_ac_done_7_deleted_id_reports_not_found(tmp_path, capsys):
    seed(data_file(tmp_path), [make_todo(1, "a"), make_todo(3, "c")], next_id=4)
    assert run(tmp_path, "done", "2") == 1
    out = capsys.readouterr()
    assert out.out == ""
    assert out.err == "Error: todo #2 not found.\n"


def test_ac_done_8_default_view_updates_after_done(tmp_path, capsys):
    seed(data_file(tmp_path), [make_todo(1, "买牛奶")])
    assert run(tmp_path, "done", "1") == 0
    capsys.readouterr()

    assert run(tmp_path, "list") == 0
    assert capsys.readouterr().out == "No pending todos.\n"
    assert run(tmp_path, "list", "--all") == 0
    assert capsys.readouterr().out == "1. [x] 买牛奶\n"


# --------------------------------------------------------------------------- #
# AC-DEL-1 .. AC-DEL-7
# --------------------------------------------------------------------------- #
def test_ac_del_1_deletes_and_keeps_other_entries_and_next_id(tmp_path, capsys):
    path = data_file(tmp_path)
    seed(path, [make_todo(1, "买牛奶"), make_todo(2, "写周报"), make_todo(3, "c")], next_id=4)

    assert run(tmp_path, "delete", "2") == 0
    out = capsys.readouterr()
    assert out.out == "Deleted #2: 写周报\n"
    assert out.err == ""

    doc = read_doc(path)
    assert [item["id"] for item in doc["todos"]] == [1, 3]
    assert doc["todos"][0]["text"] == "买牛奶"
    assert doc["next_id"] == 4


def test_ac_del_2_second_delete_reports_not_found(tmp_path, capsys):
    path = data_file(tmp_path)
    seed(path, [make_todo(1, "a"), make_todo(2, "b")], next_id=3)
    assert run(tmp_path, "delete", "2") == 0
    capsys.readouterr()
    after_first = path.read_bytes()

    assert run(tmp_path, "delete", "2") == 1
    out = capsys.readouterr()
    assert out.out == ""
    assert out.err == "Error: todo #2 not found.\n"
    assert path.read_bytes() == after_first


def test_ac_del_3_ids_are_never_reused(tmp_path, capsys):
    path = data_file(tmp_path)
    seed(path, [make_todo(1, "a"), make_todo(2, "b"), make_todo(3, "c")], next_id=4)

    assert run(tmp_path, "delete", "2") == 0
    capsys.readouterr()
    assert run(tmp_path, "add", "新任务") == 0
    assert capsys.readouterr().out == "Added #4: 新任务\n"

    doc = read_doc(path)
    assert [item["id"] for item in doc["todos"]] == [1, 3, 4]
    assert doc["next_id"] == 5


def test_ac_del_4_deleting_last_entry_keeps_next_id(tmp_path, capsys):
    path = data_file(tmp_path)
    seed(path, [make_todo(1, "a")], next_id=5)

    assert run(tmp_path, "delete", "1") == 0
    capsys.readouterr()

    doc = read_doc(path)
    assert doc["todos"] == []
    assert doc["next_id"] == 5


@pytest.mark.parametrize("raw", ["abc", "0", "-1"])
def test_ac_del_5_invalid_id_is_usage_error(tmp_path, capsys, raw):
    path = data_file(tmp_path)
    seed(path, [make_todo(1, "a")])
    before = path.read_bytes()

    assert run(tmp_path, "delete", raw) == 2
    out = capsys.readouterr()
    assert out.out == ""
    assert "not a valid todo id" in out.err
    assert path.read_bytes() == before


def test_ac_del_6_missing_file_reports_not_found_and_creates_nothing(tmp_path, capsys):
    path = data_file(tmp_path)
    assert run(tmp_path, "delete", "1") == 1
    out = capsys.readouterr()
    assert out.out == ""
    assert out.err == "Error: todo #1 not found.\n"
    assert not path.exists()


def test_ac_del_7_no_interactive_prompt(tmp_path, capsys):
    import time

    path = data_file(tmp_path)
    seed(path, [make_todo(1, "a")])

    start = time.monotonic()
    assert run(tmp_path, "delete", "1") == 0
    elapsed = time.monotonic() - start
    capsys.readouterr()

    assert elapsed < 5
    assert read_doc(path)["todos"] == []


# --------------------------------------------------------------------------- #
# AC-G-1 .. AC-G-9  (global conventions)
# --------------------------------------------------------------------------- #
def test_ac_g_1_file_option_defines_the_target(tmp_path, capsys, monkeypatch):
    monkeypatch.delenv("TODO_FILE", raising=False)
    target = tmp_path / "custom" / "todos.json"
    target.parent.mkdir()

    assert cli.main(["--file", str(target), "add", "测试"]) == 0
    out = capsys.readouterr()
    assert out.out == "Added #1: 测试\n"
    assert read_doc(target)["todos"][0]["text"] == "测试"


def test_ac_g_2_todo_file_env_var_defines_the_target(tmp_path, capsys, monkeypatch):
    target = tmp_path / "env.json"
    monkeypatch.setenv("TODO_FILE", str(target))

    assert cli.main(["add", "a"]) == 0
    capsys.readouterr()
    assert read_doc(target)["todos"][0]["text"] == "a"


def test_ac_g_3_file_option_overrides_env_var(tmp_path, capsys, monkeypatch):
    env_target = tmp_path / "env.json"
    cli_target = tmp_path / "cli.json"
    monkeypatch.setenv("TODO_FILE", str(env_target))

    assert cli.main(["--file", str(cli_target), "add", "a"]) == 0
    capsys.readouterr()
    assert cli_target.exists()
    assert not env_target.exists()


def test_ac_g_4_default_path_is_home_todo_json(tmp_path, capsys, monkeypatch):
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    use_fake_home(monkeypatch, fake_home)

    assert cli.main(["add", "a"]) == 0
    capsys.readouterr()
    assert (fake_home / ".todo.json").exists()


def test_ac_g_5_no_subcommand_is_usage_error(capsys):
    assert cli.main([]) == 2
    out = capsys.readouterr()
    assert out.out == ""
    assert "usage" in out.err.lower()


def test_ac_g_6_unknown_command_is_usage_error(capsys):
    assert cli.main(["frobnicate"]) == 2
    out = capsys.readouterr()
    assert out.out == ""
    assert "invalid choice" in out.err


def test_ac_g_7_help_lists_all_subcommands(capsys):
    assert cli.main(["--help"]) == 0
    out = capsys.readouterr()
    assert out.err == ""
    for name in ("add", "list", "done", "delete"):
        assert name in out.out
    assert "--file" in out.out


def test_ac_g_8_tilde_in_file_option_is_expanded(tmp_path, capsys, monkeypatch):
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    use_fake_home(monkeypatch, fake_home)

    assert cli.main(["--file", "~/t.json", "add", "a"]) == 0
    capsys.readouterr()
    assert (fake_home / "t.json").exists()
    assert read_doc(fake_home / "t.json")["todos"][0]["text"] == "a"


def test_ac_g_9_version_output(capsys):
    assert cli.main(["--version"]) == 0
    assert capsys.readouterr().out == "todo 0.1.0\n"
    assert __version__ == "0.1.0"


@pytest.mark.parametrize("argv", [[], ["--help"], ["--version"], ["frobnicate"], ["add"]])
def test_main_never_raises_system_exit(capsys, argv):
    assert cli.main(argv) in (0, 1, 2)
    capsys.readouterr()


# --------------------------------------------------------------------------- #
# AC-ERR-1 .. AC-ERR-6
# --------------------------------------------------------------------------- #
def test_ac_err_1_invalid_json_is_reported_and_file_untouched(tmp_path, capsys):
    path = data_file(tmp_path)
    path.write_text("{ 这不是 json", encoding="utf-8")
    before = path.read_bytes()

    assert run(tmp_path, "list") == 1
    out = capsys.readouterr()
    assert out.out == ""
    assert "Error: invalid todo file" in out.err
    assert str(path) in out.err
    assert path.read_bytes() == before


def test_ac_err_2_unsupported_version_is_reported_and_file_untouched(tmp_path, capsys):
    path = data_file(tmp_path)
    path.write_text('{"version": 2, "next_id": 1, "todos": []}', encoding="utf-8")
    before = path.read_bytes()

    assert run(tmp_path, "add", "a") == 1
    out = capsys.readouterr()
    assert out.out == ""
    assert "Error: invalid todo file" in out.err
    assert str(path) in out.err
    assert path.read_bytes() == before


def test_ac_err_3_bad_todos_type_is_reported_and_file_untouched(tmp_path, capsys):
    path = data_file(tmp_path)
    path.write_text('{"todos": "oops"}', encoding="utf-8")
    before = path.read_bytes()

    assert run(tmp_path, "list") == 1
    out = capsys.readouterr()
    assert out.out == ""
    assert "Error: invalid todo file" in out.err
    assert path.read_bytes() == before


@pytest.mark.skipif(
    getattr(os, "geteuid", lambda: 1)() == 0,
    reason=(
        "AC-ERR-4 cannot be reproduced as root: root bypasses file permissions, "
        "so os.access() still reports the file writable."
    ),
)
def test_ac_err_4_read_only_file_reports_write_error(tmp_path, capsys):
    path = data_file(tmp_path)
    seed(path, [make_todo(1, "a")])
    before = path.read_bytes()
    os.chmod(path, 0o444)
    try:
        assert run(tmp_path, "add", "b") == 1
        out = capsys.readouterr()
        assert out.out == ""
        assert "Error: cannot write todo file" in out.err
        assert str(path) in out.err
        assert path.read_bytes() == before
    finally:
        os.chmod(path, 0o644)


def test_ac_err_5_missing_directory_reports_write_error(tmp_path, capsys):
    target = tmp_path / "nonexistent_dir" / "t.json"

    assert cli.main(["--file", str(target), "add", "a"]) == 1
    out = capsys.readouterr()
    assert out.out == ""
    assert "Error: cannot write todo file" in out.err
    assert str(target) in out.err
    assert not (tmp_path / "nonexistent_dir").exists()


def test_ac_err_6_unknown_fields_are_preserved(tmp_path, capsys):
    path = data_file(tmp_path)
    document = {
        "version": 1,
        "next_id": 3,
        "todos": [
            {
                "id": 1,
                "text": "a",
                "done": False,
                "created_at": "2025-05-01T02:10:33Z",
                "tag": "x",
            },
            {"id": 2, "text": "b", "done": True, "created_at": "2025-05-01T02:11:05Z"},
        ],
        "extra_top_level": {"k": "v"},
    }
    path.write_text(json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    assert run(tmp_path, "add", "c") == 0
    capsys.readouterr()

    written = read_doc(path)
    assert written["version"] == 1
    assert written["next_id"] == 4
    assert written["extra_top_level"] == {"k": "v"}
    assert written["todos"][0]["tag"] == "x"
    assert [item["id"] for item in written["todos"]] == [1, 2, 3]


# --------------------------------------------------------------------------- #
# data integrity + real default-file isolation
# --------------------------------------------------------------------------- #
def test_data_integrity_add_done_delete_add(tmp_path, capsys):
    path = data_file(tmp_path)

    assert run(tmp_path, "add", "a") == 0
    assert run(tmp_path, "add", "b") == 0
    assert run(tmp_path, "done", "1") == 0
    assert run(tmp_path, "delete", "1") == 0
    assert run(tmp_path, "add", "c") == 0
    capsys.readouterr()

    doc = read_doc(path)
    assert doc["version"] == 1
    assert doc["next_id"] == 4
    ids = [item["id"] for item in doc["todos"]]
    assert ids == [2, 3]
    assert ids == sorted(ids)
    assert all(CREATED_AT_RE.match(item["created_at"]) for item in doc["todos"])
    assert doc["todos"][0]["done"] is False


def test_real_default_file_is_never_created(tmp_path, capsys):
    real_default = Path.home() / ".todo.json"
    existed_before = real_default.exists()

    assert run(tmp_path, "add", "guarded") == 0
    capsys.readouterr()

    assert real_default.exists() == existed_before


# --------------------------------------------------------------------------- #
# subprocess end-to-end smoke test
# --------------------------------------------------------------------------- #
def test_subprocess_end_to_end(tmp_path):
    todo_file = tmp_path / "todos.json"

    ok = subprocess.run(
        [sys.executable, "-m", "todo_cli", "--file", str(todo_file), "add", "x"],
        cwd=str(CODE_DIR),
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    assert ok.returncode == 0
    assert ok.stdout == "Added #1: x\n"
    assert ok.stderr == ""
    assert read_doc(todo_file)["todos"][0]["text"] == "x"

    missing = subprocess.run(
        [sys.executable, "-m", "todo_cli", "--file", str(todo_file), "done", "99"],
        cwd=str(CODE_DIR),
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    assert missing.returncode == 1
    assert missing.stdout == ""
    assert missing.stderr == "Error: todo #99 not found.\n"
