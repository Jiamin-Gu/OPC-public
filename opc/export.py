"""导出一家公司的成果：共享文件区的全部产出 + 对话记录（Markdown）。

    python -m opc.export 公司id [--home 数据目录] [--out 输出目录]

对话记录从 LangGraph 的 SQLite 检查点读取：CEO 的指令、执行计划、每个角色的汇报、最终的执行汇报，
以及 CEO 和各角色的单独沟通。
"""
import argparse
import shutil
import sqlite3
import sys
from pathlib import Path

from langgraph.checkpoint.sqlite import SqliteSaver

from . import use_utf8_output
from .company import CompanyStore
from .graph import ALL, build_graph
from .messages import MessageLog, migrate_legacy, open_sqlite_store
from .roles import ROLES

SPECIAL = {"ceo": ("👤", "CEO"), "planner": ("🗂️", "规划助手"), "reporter": ("📊", "汇报助手"), "system": ("⚙️", "系统")}
STATUS = {"pending": "待开始", "done": "已完成", "failed": "失败", "skipped": "已跳过", "cancelled": "已取消",
          "review": "待审查", "rework": "返工中", "escalated": "等 CEO 裁决"}
VERDICT = {"pass": "通过", "changes": "要求修改", "unclear": "没有给出结论", "error": "审查失败"}


def who(sender: str) -> str:
    if sender in SPECIAL:
        icon, name = SPECIAL[sender]
    elif sender in ROLES:
        icon, name = ROLES[sender].icon, ROLES[sender].name
    else:
        icon, name = "❓", sender
    return f"{icon} {name}"


def tool_summary(tools: list[dict]) -> str:
    """把工具调用压缩成一行，例如：读取 3 个文件，运行 12 条命令（11 条成功）。"""
    reads = sum(1 for t in tools if t.get("name") in ("read_file",))
    runs = [t for t in tools if t.get("name") == "run_command"]
    delegated = sum(1 for t in tools if t.get("name") == "code_agent")
    parts = []
    if reads:
        parts.append(f"读取文件 {reads} 次")
    if delegated:
        parts.append(f"交给编码 agent {delegated} 次")
    if runs:
        parts.append(f"运行命令 {len(runs)} 条（成功 {sum(1 for t in runs if t.get('exit') == 0)} 条）")
    return "，".join(parts)


def render_message(m: dict, tasks: dict) -> list[str]:
    head = f"### {who(m['sender'])}" + (f" · {m['ts'][11:16]}" if m.get("ts") else "")
    if m.get("task_id") in tasks:
        head += f" · 任务：{tasks[m['task_id']]['title']}"
    if m["kind"] == "event":
        return [f"*{m['text']}*", ""]
    if m["kind"] == "interject":
        head += "（执行中实时插话）"
    if m.get("rework"):
        head += f"（按第 {m['rework']} 轮审查意见返工）"
    lines = [head, ""]
    if m["kind"] == "review":
        lines += [f"**审查（第 {m.get('round', '?')} 轮）：{VERDICT.get(m.get('verdict'), m.get('verdict'))}**"
                  + (f"。{m['summary']}" if m.get("summary") else ""), ""]
        lines += [f"{i}. {issue['text']}" for i, issue in enumerate(m.get("issues") or [], 1)]
        if m.get("issues"):
            lines.append("")
    if m["kind"] == "plan":
        lines += ["**执行计划**：" + m["text"], ""]
        for tid in m.get("tasks", []):
            t = tasks.get(tid)
            if not t:
                continue
            deps = "、".join(tasks[d]["title"] for d in t["depends_on"] if d in tasks)
            lines.append(f"- {who(t['assignee'])}：**{t['title']}**（{STATUS.get(t['status'], t['status'])}）"
                         + (f"，依赖：{deps}" if deps else ""))
            lines.append(f"  - {t['detail']}")
        lines.append("")
        return lines
    if m["kind"] == "report":
        lines += ["**执行汇报**", ""]
    lines += [m["text"].strip(), ""]
    extra = []
    if m.get("files"):
        extra.append("产出文件：" + "、".join(f"`{f}`" for f in m["files"]))
    if m.get("tools") and tool_summary(m["tools"]):
        extra.append(tool_summary(m["tools"]))
    if extra:
        lines += ["> " + "；".join(extra), ""]
    return lines


def export(company_id: str, home: Path | None, out: Path) -> Path:
    store = CompanyStore(home)
    company = store.load(company_id)
    if not company:
        sys.exit(f"找不到公司：{company_id}")
    checkpointer = SqliteSaver(sqlite3.connect(store.checkpoint_path, check_same_thread=False))
    messages = open_sqlite_store(store.checkpoint_path)
    migrate_legacy(checkpointer, messages, company_id)
    graph = build_graph(checkpointer, messages)
    state = graph.get_state({"configurable": {"thread_id": company_id}}).values or {}
    tasks, usage = state.get("tasks", {}), state.get("usage", {})
    channels = MessageLog(messages, company_id).all()

    out.mkdir(parents=True, exist_ok=True)
    if (out / "files").exists():
        shutil.rmtree(out / "files")
    shutil.copytree(company.files_root, out / "files",
                    ignore=shutil.ignore_patterns("__pycache__", ".pytest_cache", "*.pyc"))

    meta = company.meta
    lines = [f"# {meta['name']}：对话记录", "", f"公司目标：{meta.get('mission') or '（未设定）'}", "",
             "团队：" + "、".join(who(r) for r in company.role_ids), ""]
    if usage:
        lines += ["## token 用量", "", "| 角色 | 调用次数 | 输入 | 其中命中缓存 | 输出 |", "|---|---|---|---|---|"]
        for k, u in usage.items():
            lines.append(f"| {who(k)} | {u.get('calls', 0)} | {u.get('prompt', 0):,} | {u.get('cached', 0):,} | "
                         f"{u.get('completion', 0):,} |")
        lines.append("")
    lines += ["## 全公司频道", ""]
    for m in channels.get(ALL, []):
        lines += render_message(m, tasks)
    for role in company.role_ids:
        if channels.get(role):
            lines += [f"## 和{who(role)}的单独沟通", ""]
            for m in channels[role]:
                lines += render_message(m, tasks)
    (out / "transcript.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return out


def main() -> None:
    use_utf8_output()
    parser = argparse.ArgumentParser(description="导出一家公司的成果（文件 + 对话记录）")
    parser.add_argument("company_id")
    parser.add_argument("--home", help="数据目录，默认 ~/.opc")
    parser.add_argument("--out", help="输出目录，默认 ./export-公司id")
    args = parser.parse_args()
    out = export(args.company_id, Path(args.home) if args.home else None,
                 Path(args.out or f"export-{args.company_id}"))
    print(f"已导出到 {out}：files/（公司文件区）和 transcript.md（对话记录）")


if __name__ == "__main__":
    main()
