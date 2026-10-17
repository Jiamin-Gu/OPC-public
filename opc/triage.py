"""调度助手：CEO 在任何频道说的话（全公司、某位成员的私聊、项目经理频道），先判断是不是在要求改动已有的工作。

不管 CEO 跟谁说，都整理成同样的变更单（让某个任务返工 / 暂停 / 继续），所以结果一样：
例如对下游的同事说"上游那份文档不对，你先停"，和对上游的同事说"你这里要改"，都是让上游任务返工，
下游任务按依赖关系自动暂停（受影响的范围由 changes.py 计算，不让模型猜）。

模型只做理解：哪个任务、做什么、CEO 的意见是什么。拿不准就当作普通对话。
变更单不会直接执行：运行时把它做成确认卡片，CEO 确认后才生效。
"""
import json
import re

from .changes import FINISHED_STATUSES, downstream
from .company import Company
from .roles import ROLES

STATUS = {"pending": "待开始", "running": "进行中", "review": "待审查", "reviewing": "审查中", "rework": "返工中",
          "escalated": "等 CEO 裁决", "done": "已完成", "failed": "失败", "skipped": "已跳过", "cancelled": "已取消"}
KINDS = ("rework", "pause", "resume", "retry")

PROMPT = """你是「{name}」公司的调度助手（项目经理），负责理解 CEO 说的话是不是在要求改动团队已有的工作。

当前指令：{directive}
任务看板（只能用这里的编号）：
{board}

CEO 是在{where}说的这句话。{topic}

判断规则：
1. 只有 CEO 明确要求修改、重做、暂停或继续已有任务时，才是改动（change = true）。提问、讨论、表扬、闲聊、交代以后的新工作，都不是改动。拿不准时当作不是改动。
2. CEO 指出某个任务的产出有问题、需要改进时，不管他是对谁说的，都是让那个任务返工（rework）。feedback 写清楚 CEO 的具体要求（保留他的原意和关键细节，不要自己发挥）。
   例如对工程师说"产品经理的需求文档里候补规则不对，你先等等"，是让产品经理的需求文档返工，不是让工程师返工。
3. 依赖它的任务会自动暂停、等它改完再继续，不要为下游任务单独写 pause。
4. CEO 明确要求先停下某个任务，用 pause；要求继续某个暂停的任务，用 resume；要求失败的任务重新做，用 retry。
5. 还没开始（待开始）的任务不能返工。{reply_rule}

只输出 JSON：
{{"change": true 或 false, "orders": [{{"task": "T1", "kind": "rework / pause / resume / retry", "feedback": "CEO 的具体要求"}}], "reply": "{reply_hint}"}}"""

REPLY_RULE = ("\n6. 你在项目经理频道里，要用 reply 回答 CEO：进度、你的理解或建议，一两句话，只根据看板上的信息，不要编造。"
              "如果是改动，说明你打算怎么安排（CEO 还要确认）。")


def _name(role_id: str) -> str:
    return ROLES[role_id].name if role_id in ROLES else role_id


def board(tasks: list[dict]) -> tuple[str, dict[str, dict]]:
    """把任务看板写成文字，任务用短编号（T1、T2……），返回 (文字, 编号 → 任务)。"""
    keys, lines = {}, []
    for i, t in enumerate(tasks, 1):
        key = f"T{i}"
        keys[key] = t
        deps = [k for k, x in keys.items() if x["id"] in t.get("depends_on", [])]
        line = f"- {key} {_name(t['assignee'])}《{t['title']}》：{STATUS.get(t['status'], t['status'])}"
        if t.get("paused"):
            line += "（已暂停）"
        if deps:
            line += f"，依赖 {'、'.join(deps)}"
        if t.get("reviewer"):
            line += f"，审查人 {_name(t['reviewer'])}"
        if t.get("files"):
            line += f"，产出 {'、'.join(t['files'])}"
        if t.get("result") and t["status"] in ("done", "review", "escalated"):
            line += f"。汇报摘要：{t['result'][:150]}"
        lines.append(line)
    return "\n".join(lines) or "（还没有任务）", keys


def triage(company: Company, text: str, tasks: list[dict], directive: dict, where: str,
           topic: dict | None = None, manager: bool = False, recent: list[dict] | None = None,
           on_delta=None) -> dict:
    """返回 {change, orders: [{kind, task_id, feedback}], reply, usage}。出错时当作普通对话（change=False）。"""
    text_board, keys = board(tasks)
    topic_text = f"CEO 选中的是《{topic['title']}》这项工作在讨论。" if topic else ""
    prompt = PROMPT.format(name=company.meta["name"], directive=directive.get("text", "（无）"), board=text_board,
                           where=where, topic=topic_text, reply_rule=REPLY_RULE if manager else "",
                           reply_hint="给 CEO 的回复" if manager else "可以留空")
    history = "\n".join(f"{'CEO' if m['sender'] == 'ceo' else _name(m['sender'])}：{m['text'][:300]}"
                        for m in (recent or []) if m.get("kind") in ("text", "interject"))
    user = (f"这个频道最近的对话：\n{history}\n\n" if history else "") + f"CEO 刚说：{text}"
    llm = company.llm_for(None)
    messages = [{"role": "system", "content": prompt}, {"role": "user", "content": user}]
    usage = {"prompt": 0, "completion": 0, "cached": 0, "calls": 0}
    for _ in range(2):
        try:
            reply = llm.chat(messages, on_delta=on_delta, json_mode=True)
        except Exception:
            break
        if reply.usage:
            usage = {"prompt": usage["prompt"] + reply.usage.prompt, "completion": usage["completion"] + reply.usage.completion,
                     "cached": usage["cached"] + getattr(reply.usage, "cached", 0), "calls": usage["calls"] + 1}
        try:
            data = json.loads(re.search(r"\{.*\}", reply.content or "", re.S).group(0))
            return {**parse(data, keys), "usage": usage}
        except Exception as e:
            messages += [{"role": "assistant", "content": reply.content or ""},
                         {"role": "user", "content": f"输出格式不对（{e}），请只输出要求的 JSON。"}]
    return {"change": False, "orders": [], "reply": "", "usage": usage}


def parse(data: dict, keys: dict[str, dict]) -> dict:
    """校验模型给出的变更单：只保留看板上存在、动作合法的；返工不能用在还没开始的任务上。"""
    orders, seen = [], set()
    for o in data.get("orders") or [] if data.get("change") else []:
        if not isinstance(o, dict):
            continue
        task = keys.get(str(o.get("task", "")).strip().upper())
        kind = str(o.get("kind", "")).strip()
        if not task or kind not in KINDS or (task["id"], kind) in seen:
            continue
        if kind == "rework" and task["status"] == "pending" and not task.get("started"):
            continue
        if kind in ("pause", "resume") and task["status"] in FINISHED_STATUSES:
            continue
        if kind == "retry" and task["status"] != "failed":
            continue
        seen.add((task["id"], kind))
        orders.append({"kind": kind, "task_id": task["id"], "feedback": str(o.get("feedback") or "").strip()})
    return {"change": bool(orders), "orders": orders, "reply": str(data.get("reply") or "").strip()}


def describe(orders: list[dict], tasks: list[dict]) -> tuple[str, list[str]]:
    """给确认卡片用：每条变更单怎么说，以及会因此暂停的下游任务（按依赖关系算出来）。"""
    by_id = {t["id"]: t for t in tasks}
    lines, affected = [], []
    for o in orders:
        t = by_id[o["task_id"]]
        name = f"{_name(t['assignee'])}《{t['title']}》"
        if o["kind"] == "rework":
            lines.append(f"{name}返工：{o['feedback'] or '（没有具体意见）'}")
            for d in downstream(tasks, t["id"]):
                dt = by_id[d]
                if dt["status"] not in FINISHED_STATUSES - {"done"} and d not in affected:
                    affected.append(d)
        elif o["kind"] == "pause":
            lines.append(f"暂停{name}")
        elif o["kind"] == "retry":
            lines.append(f"重新做{name}（因为它失败而跳过的任务也一起恢复）")
        else:
            lines.append(f"继续{name}")
    titles = [f"{_name(by_id[d]['assignee'])}《{by_id[d]['title']}》" for d in affected]
    return "\n".join(lines), titles
