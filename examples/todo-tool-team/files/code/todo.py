#!/usr/bin/env python3
"""todo CLI：一个零依赖的命令行待办事项工具（第一版）。

用法：
    python code/todo.py add "买牛奶"
    python code/todo.py list [--all]
    python code/todo.py done <id>
    python code/todo.py delete <id>
    python code/todo.py --file /path/to/todos.json list

数据文件路径优先级（高到低）：
    1) 命令行 --file/-f <路径>
    2) 环境变量 TODO_FILE
    3) 当前目录下的 todos.json

对应文档：docs/PRD.md、docs/TECH_DESIGN.md
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, NoReturn, Optional, Sequence

# ---------------------------------------------------------------------------
# ④ 基础设施：常量
# ---------------------------------------------------------------------------

DATA_VERSION = 1
DEFAULT_FILENAME = "todos.json"
ENV_VAR = "TODO_FILE"
MAX_TEXT_LENGTH = 500
PROG = "todo"

EXIT_OK = 0
EXIT_NOT_FOUND = 1
EXIT_USAGE = 2
EXIT_DATA = 3


class TodoError(Exception):
    """业务异常：携带面向用户的错误信息与退出码。"""

    def __init__(self, message: str, exit_code: int) -> None:
        super().__init__(message)
        self.message = message
        self.exit_code = exit_code


def now_string() -> str:
    """当前本地时间，格式 YYYY-MM-DDTHH:MM:SS（秒级精度）。"""
    return datetime.now().replace(microsecond=0).isoformat()


def corrupt_error(path: Path) -> TodoError:
    """数据文件损坏的统一错误。"""
    return TodoError(f"错误：数据文件已损坏，路径：{path}", EXIT_DATA)


# ---------------------------------------------------------------------------
# ① CLI 层：参数解析
# ---------------------------------------------------------------------------


class TodoArgumentParser(argparse.ArgumentParser):
    """出错时把帮助信息打印到 stderr，并以中文提示 + 退出码 2 结束。"""

    _PREFIX_TRANSLATIONS = (
        ("the following arguments are required: ", "缺少参数 "),
        ("unrecognized arguments: ", "未知参数 "),
        ("expected at least one argument", "缺少参数值"),
        ("expected one argument", "参数值缺失"),
    )

    def error(self, message: str) -> NoReturn:  # type: ignore[override]
        self.print_help(sys.stderr)
        print(f"错误：{self._translate(message)}", file=sys.stderr)
        raise SystemExit(EXIT_USAGE)

    @classmethod
    def _translate(cls, message: str) -> str:
        if "invalid choice:" in message:
            bad = message.split("invalid choice:", 1)[1].split("(", 1)[0].strip()
            return f"无效的子命令：{bad}"
        for prefix, chinese in cls._PREFIX_TRANSLATIONS:
            if prefix in message:
                return chinese + message.split(prefix, 1)[1]
        return f"参数解析失败：{message}"


def add_file_option(parser: argparse.ArgumentParser, default: Any) -> None:
    """给解析器加上 --file/-f 选项，用于指定数据文件路径。"""
    parser.add_argument(
        "-f",
        "--file",
        dest="file",
        metavar="<路径>",
        default=default,
        help="数据文件路径（默认 ./%s，也可用环境变量 %s 指定）"
        % (DEFAULT_FILENAME, ENV_VAR),
    )


def build_parser() -> TodoArgumentParser:
    """构建顶层解析器与四个子命令。

    --file 既可在子命令之前给出，也可在子命令之后给出（子解析器用 SUPPRESS
    作为默认值，避免覆盖顶层已解析到的取值）。
    """
    parser = TodoArgumentParser(
        prog=PROG,
        description="命令行待办事项工具：添加、列出、完成、删除待办，数据保存在本地 JSON 文件。",
        epilog=(
            "示例：\n"
            f'  {PROG} add "买牛奶"\n'
            f"  {PROG} list\n"
            f"  {PROG} list --all\n"
            f"  {PROG} done 1\n"
            f"  {PROG} delete 1\n"
            f"  {PROG} --file /tmp/my-todos.json add \"买牛奶\"\n"
            "\n"
            f"数据文件：--file/-f > 环境变量 {ENV_VAR} > 当前目录 ./{DEFAULT_FILENAME}\n"
            "退出码：0 成功，1 找不到 ID，2 参数错误，3 数据文件错误"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    add_file_option(parser, None)
    subparsers = parser.add_subparsers(dest="command")

    parser_add = subparsers.add_parser(
        "add", help="添加一条待办", description="添加一条待办事项。"
    )
    parser_add.add_argument(
        "text",
        metavar="<内容>",
        nargs="+",
        help="待办内容，不能为空，最长 %d 个字符；含空格时建议用引号包裹" % MAX_TEXT_LENGTH,
    )
    add_file_option(parser_add, argparse.SUPPRESS)

    parser_list = subparsers.add_parser(
        "list", help="列出待办（默认仅未完成）", description="列出待办事项，默认只显示未完成的。"
    )
    parser_list.add_argument(
        "-a",
        "--all",
        action="store_true",
        dest="all",
        help="显示全部待办，包括已完成的",
    )
    add_file_option(parser_list, argparse.SUPPRESS)

    parser_done = subparsers.add_parser(
        "done", help="把待办标记为已完成", description="把指定 ID 的待办标记为已完成。"
    )
    parser_done.add_argument("id", metavar="<id>", help="待办 ID，正整数")
    add_file_option(parser_done, argparse.SUPPRESS)

    parser_delete = subparsers.add_parser(
        "delete", help="删除一条待办", description="删除指定 ID 的待办（不做二次确认）。"
    )
    parser_delete.add_argument("id", metavar="<id>", help="待办 ID，正整数")
    add_file_option(parser_delete, argparse.SUPPRESS)

    return parser


def usage_error(parser: argparse.ArgumentParser, message: str) -> NoReturn:
    """用法错误：帮助信息 + 错误提示（都走 stderr），退出码 2。"""
    parser.print_help(sys.stderr)
    raise TodoError(message, EXIT_USAGE)


def parse_todo_id(raw: str, parser: argparse.ArgumentParser) -> int:
    """校验 <id> 为正整数；非法时按用法错误处理。"""
    text = raw.strip()
    if not (text.isascii() and text.isdigit()) or int(text) <= 0:
        usage_error(parser, f"错误：<id> 必须是正整数，收到：{raw}")
    return int(text)


# ---------------------------------------------------------------------------
# ② 命令层：纯业务逻辑（不接触文件、不打印）
# ---------------------------------------------------------------------------


def validate_text(text: str) -> None:
    """校验待办内容非空且不超过长度上限。"""
    if not text:
        raise TodoError("错误：待办内容不能为空", EXIT_USAGE)
    if len(text) > MAX_TEXT_LENGTH:
        raise TodoError(
            f"错误：待办内容过长（最多 {MAX_TEXT_LENGTH} 个字符，当前 {len(text)} 个）",
            EXIT_USAGE,
        )


def find_todo(state: Dict[str, Any], todo_id: int) -> Optional[Dict[str, Any]]:
    """按 ID 查找待办，找不到返回 None。"""
    for todo in state["todos"]:
        if todo["id"] == todo_id:
            return todo
    return None


def cmd_add(state: Dict[str, Any], text: str) -> str:
    """追加一条待办。前置条件：text 已通过 validate_text。"""
    todo_id = state["next_id"]
    state["todos"].append(
        {
            "id": todo_id,
            "text": text,
            "done": False,
            "created_at": now_string(),
            "done_at": None,
        }
    )
    state["next_id"] = todo_id + 1
    return f"已添加 #{todo_id}: {text}"


def cmd_list(state: Dict[str, Any], show_all: bool = False) -> str:
    """生成清单文本；无匹配条目时返回「暂无待办事项」。"""
    todos = state["todos"]
    if not show_all:
        todos = [todo for todo in todos if not todo["done"]]
    if not todos:
        return "暂无待办事项"
    lines: List[str] = []
    for todo in todos:
        mark = "x" if todo["done"] else " "
        lines.append(f"{todo['id']}. [{mark}] {todo['text']}")
    return "\n".join(lines)


def cmd_done(state: Dict[str, Any], todo_id: int) -> str:
    """把待办标记为完成；重复完成不报错。"""
    todo = find_todo(state, todo_id)
    if todo is None:
        raise TodoError(f"错误：找不到 ID 为 {todo_id} 的待办", EXIT_NOT_FOUND)
    if todo["done"]:
        return f"#{todo_id} 已是完成状态"
    todo["done"] = True
    todo["done_at"] = now_string()
    return f"已完成 #{todo_id}: {todo['text']}"


def cmd_delete(state: Dict[str, Any], todo_id: int) -> str:
    """删除待办；ID 不复用。"""
    todo = find_todo(state, todo_id)
    if todo is None:
        raise TodoError(f"错误：找不到 ID 为 {todo_id} 的待办", EXIT_NOT_FOUND)
    state["todos"] = [item for item in state["todos"] if item["id"] != todo_id]
    return f"已删除 #{todo_id}: {todo['text']}"


# ---------------------------------------------------------------------------
# ③ 存储层：路径解析、读取与校验、原子写入
# ---------------------------------------------------------------------------


def resolve_data_path(
    cli_file: Optional[str] = None, environ: Optional[Dict[str, str]] = None
) -> Path:
    """解析数据文件路径：--file 优先，其次 TODO_FILE，最后当前目录 ./todos.json。

    在每次命令执行时调用（不在导入时固化），以便测试或脚本在运行时改变来源。
    """
    env = os.environ if environ is None else environ
    if cli_file is not None:
        raw = cli_file.strip()
    else:
        raw = (env.get(ENV_VAR) or "").strip()
    if raw:
        return Path(raw).expanduser()
    return Path(DEFAULT_FILENAME)


def new_state() -> Dict[str, Any]:
    """空数据。"""
    return {"version": DATA_VERSION, "next_id": 1, "todos": []}


def load_state(path: Path) -> Dict[str, Any]:
    """读取并归一化数据；文件不存在或为空时返回空数据；损坏时抛退出码 3。"""
    try:
        if not path.exists():
            return new_state()
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return new_state()
    except IsADirectoryError:
        raise TodoError(f"错误：数据文件路径不是普通文件，路径：{path}", EXIT_DATA)
    except PermissionError:
        raise TodoError(f"错误：没有权限读取数据文件，路径：{path}", EXIT_DATA)
    except UnicodeDecodeError:
        raise TodoError(f"错误：数据文件不是合法的 UTF-8 文本，路径：{path}", EXIT_DATA)
    except OSError as exc:
        raise TodoError(
            f"错误：无法读取数据文件（{exc.strerror or exc}），路径：{path}", EXIT_DATA
        )

    if not raw.strip():
        return new_state()
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        raise corrupt_error(path)
    return normalize_state(data, path)


def normalize_state(data: Any, path: Path) -> Dict[str, Any]:
    """校验数据结构并归一化：补默认值、查重 ID、修正 next_id、按 ID 排序。"""
    if not isinstance(data, dict):
        raise corrupt_error(path)

    version = data.get("version", DATA_VERSION)
    if not isinstance(version, int) or isinstance(version, bool):
        raise corrupt_error(path)
    if version != DATA_VERSION:
        raise TodoError(
            f"错误：不支持的数据文件版本 {version}，路径：{path}", EXIT_DATA
        )

    raw_todos = data.get("todos", [])
    if not isinstance(raw_todos, list):
        raise corrupt_error(path)

    todos: List[Dict[str, Any]] = []
    seen_ids = set()
    for item in raw_todos:
        if not isinstance(item, dict):
            raise corrupt_error(path)

        todo_id = item.get("id")
        if not isinstance(todo_id, int) or isinstance(todo_id, bool) or todo_id <= 0:
            raise corrupt_error(path)
        if todo_id in seen_ids:
            raise TodoError(
                f"错误：数据文件中存在重复 ID {todo_id}，路径：{path}", EXIT_DATA
            )
        seen_ids.add(todo_id)

        text = item.get("text")
        done = item.get("done")
        if not isinstance(text, str) or not isinstance(done, bool):
            raise corrupt_error(path)

        created_at = item.get("created_at")
        if not isinstance(created_at, str):
            created_at = ""
        done_at = item.get("done_at")
        if not (done_at is None or isinstance(done_at, str)):
            done_at = None

        todos.append(
            {
                "id": todo_id,
                "text": text,
                "done": done,
                "created_at": created_at,
                "done_at": done_at,
            }
        )

    todos.sort(key=lambda todo: todo["id"])

    max_id = max(seen_ids) if seen_ids else 0
    next_id = data.get("next_id", max_id + 1)
    if not isinstance(next_id, int) or isinstance(next_id, bool) or next_id <= max_id:
        next_id = max_id + 1

    return {"version": DATA_VERSION, "next_id": next_id, "todos": todos}


def save_state(path: Path, state: Dict[str, Any]) -> None:
    """原子写入：同目录临时文件 + fsync + os.replace，失败不破坏原文件。"""
    directory = path.parent if str(path.parent) else Path(".")
    try:
        directory.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise TodoError(
            f"错误：无法创建数据目录（{exc.strerror or exc}），路径：{directory}",
            EXIT_DATA,
        )

    tmp_name: Optional[str] = None
    try:
        fd, tmp_name = tempfile.mkstemp(
            prefix=".todo-", suffix=".tmp", dir=str(directory)
        )
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(state, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_name, str(path))
        tmp_name = None
    except OSError as exc:
        raise TodoError(
            f"错误：无法写入数据文件（{exc.strerror or exc}），路径：{path}", EXIT_DATA
        )
    finally:
        if tmp_name is not None:
            try:
                os.unlink(tmp_name)
            except OSError:
                pass


# ---------------------------------------------------------------------------
# ④ 编排与入口
# ---------------------------------------------------------------------------


def main(argv: Optional[Sequence[str]] = None) -> int:
    """解析参数并执行命令，返回进程退出码。"""
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command is None:
        usage_error(parser, "错误：缺少子命令（可用：add、list、done、delete）")

    try:
        cli_file: Optional[str] = getattr(args, "file", None)
        if cli_file is not None and not cli_file.strip():
            usage_error(parser, "错误：--file 参数不能为空")

        text: Optional[str] = None
        if args.command == "add":
            text = " ".join(args.text).strip()
            validate_text(text)

        todo_id: Optional[int] = None
        if args.command in ("done", "delete"):
            todo_id = parse_todo_id(args.id, parser)

        data_path = resolve_data_path(cli_file=cli_file)
        state = load_state(data_path)

        if args.command == "add":
            message = cmd_add(state, text)  # type: ignore[arg-type]
            save_state(data_path, state)
        elif args.command == "list":
            message = cmd_list(state, show_all=bool(args.all))
        elif args.command == "done":
            message = cmd_done(state, todo_id)  # type: ignore[arg-type]
            save_state(data_path, state)
        elif args.command == "delete":
            message = cmd_delete(state, todo_id)  # type: ignore[arg-type]
            save_state(data_path, state)
        else:
            usage_error(parser, f"错误：无效的子命令：{args.command}")
    except TodoError as exc:
        print(exc.message, file=sys.stderr)
        return exc.exit_code

    print(message)
    return EXIT_OK


def run() -> None:
    """进程入口：处理中断与管道关闭，按退出码结束进程。"""
    try:
        code = main()
    except KeyboardInterrupt:
        print("错误：操作已取消", file=sys.stderr)
        code = 130
    except BrokenPipeError:
        code = EXIT_OK

    try:
        sys.stdout.flush()
    except (BrokenPipeError, ValueError):
        pass

    sys.exit(code)


if __name__ == "__main__":
    run()
