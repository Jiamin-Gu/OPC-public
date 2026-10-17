"""OPC 提示词三个版本的对比：同一家公司、同一条指令，分别用 concise（简洁版）/ minimal（极简版）/ complete（完整版）跑一次。

    python examples/prompt-levels-ab/opc_prompt_ab.py concise   # 结果写到 examples/prompt-levels-ab/concise/（会覆盖）
"""
import json
import shutil
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from langgraph.checkpoint.memory import InMemorySaver  # noqa: E402
from langgraph.types import Command  # noqa: E402

from opc.company import CompanyStore  # noqa: E402
from opc.graph import ALL, Ctx, build_graph  # noqa: E402
from opc.state import new_id, new_message  # noqa: E402

LEVEL = sys.argv[1]
OUT = Path(__file__).parent / LEVEL
DIRECTIVE = ("我们要做一款帮大学生管理生活费的记账微信小程序。请先产出 MVP 阶段的需求文档、交互设计、技术方案和测试计划，"
             "这一阶段只写文档、不写代码。")

if OUT.exists():
    shutil.rmtree(OUT)
OUT.mkdir(parents=True)
store = CompanyStore(OUT / "home")
company = store.create("小账本", "帮大学生管好生活费", [{"id": r} for r in ("pm", "designer", "engineer", "qa")],
                       allow_run=False, web=False, prompt_level=LEVEL)
graph = build_graph(InMemorySaver())
config = {"configurable": {"thread_id": company.id}}
ctx = Ctx(company=company, auto_approve=True)
directive = {"id": new_id()[:6], "text": DIRECTIVE, "target": ALL}
payload = {"directive": directive, "channels": {ALL: [new_message("ceo", "text", DIRECTIVE, directive_id=directive["id"])]}}

start = time.time()
events = []
while True:
    for mode, chunk in graph.stream(payload, config, context=ctx, stream_mode=["custom", "updates"]):
        if mode == "custom" and chunk.get("type") in ("tool", "status", "task"):
            t = chunk.get("task") or {}
            line = {k: chunk.get(k) for k in ("type", "sender", "role", "name", "path", "ok", "status")}
            if t:
                line.update(task=t.get("title"), task_status=t.get("status"))
            events.append(line)
            if chunk.get("type") == "task":
                print(f"[{time.time() - start:5.0f}s] 任务《{t.get('title')}》→ {t.get('status')}", flush=True)
    state = graph.get_state(config)
    if not state.next:  # 结束
        break
    # 审查谈不拢上报 CEO：对比测试里一律采纳当前产出继续，记录下来
    print(f"[{time.time() - start:5.0f}s] 中断：{state.next}，按'采纳'继续", flush=True)
    events.append({"type": "escalation", "next": list(state.next)})
    payload = Command(resume={"action": "accept", "feedback": ""})

values = graph.get_state(config).values
tasks = values.get("tasks", {})
usage = values.get("usage", {})
summary = {
    "level": LEVEL, "seconds": round(time.time() - start),
    "usage": usage,
    "total": {k: sum(u.get(k, 0) for u in usage.values()) for k in ("prompt", "completion", "cached", "calls")},
    "tasks": [{"title": t["title"], "assignee": t["assignee"], "reviewer": t.get("reviewer"), "status": t["status"],
               "rounds": t.get("round", 0), "files": t.get("files", []),
               "reviews": [{"round": r["round"], "verdict": r["verdict"], "issues": [i["text"] for i in r.get("issues", [])],
                            "summary": r.get("summary", "")} for r in t.get("reviews", [])]}
              for t in tasks.values()],
    "escalations": sum(1 for e in events if e["type"] == "escalation"),
}
shutil.copytree(company.files_root, OUT / "files")
(OUT / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8")
(OUT / "events.json").write_text(json.dumps(events, ensure_ascii=False, indent=1), encoding="utf-8")
print(json.dumps({k: summary[k] for k in ("level", "seconds", "total", "escalations")}, ensure_ascii=False))
for t in summary["tasks"]:
    print(f"- {t['title']}（{t['assignee']}，审查 {t['reviewer']}）{t['status']}，{t['rounds']} 轮，文件 {t['files']}")
