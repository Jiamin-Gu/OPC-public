"""公司的调度图（LangGraph）：事件驱动，每来一个事件运行一次。

    START → intake ─┬─(新指令 / CEO 要求重新规划)→ plan ─┬─(等 CEO 审批)→ END
                    │                                    └─(自动批准)→ schedule
                    ├─(任务做完 / 变更单 / 审批 / 裁决 / 恢复)→ schedule ─┬─(派发了任务 / 还有任务在做 / 等裁决 / 有暂停)→ END
                    │                                                     └─(全部结束)→ report → END
                    └─(CEO 停止了公司 / 取消指令)→ END

事件（运行时放进图的输入 event）：
- directive：CEO 对全公司下令
- plan_decision：CEO 审批计划（批准 / 修改意见重新规划 / 取消）
- job_done：一个工作运行结束（执行、审查），带着它的结果
- change：变更单（让某个任务返工、暂停、继续，规则见 changes.py）
- decision：CEO 裁决审查谈不拢的任务（接受 / 再改一轮 / 放弃）
- stopped：CEO 停止了公司，所有工作运行都已停下
- recover：程序重启后，把没跑完的任务退回去重新派发

schedule 一满足条件就派发：把要启动的工作运行写进 dispatch，由执行引擎（engine.py）在线程里并行运行，
结束时再送回一个 job_done。所以一个任务做完，依赖它的任务马上开始，不用等同时在做的其他任务；
变更单也马上生效。审批和裁决不再用 interrupt 让整张图停下：计划等待审批时写在 approval 里，
需要裁决的任务状态是 escalated，CEO 的决定作为事件送进来，其他任务照常进行。

消息（各频道的对话）不放在 state 里，存在 LangGraph 的 Store（见 messages.py）。
"""
import threading
from dataclasses import dataclass, field

from langgraph.config import get_stream_writer
from langgraph.graph import END, START, StateGraph
from langgraph.runtime import Runtime
from langgraph.store.memory import InMemoryStore
from langgraph.types import Command

from . import planner
from .agents import Cancelled
from .changes import (FINISHED_STATUSES, apply_change, cancel_unfinished, dispatch, merge_job_result, ready_batch,
                      recover)
from .company import Company
from .config import MAX_REVIEW_ROUNDS
from .inbox import format_notes
from . import security, tracing
from .knowledge import service as kb_service
from .messages import MessageLog
from .roles import ROLES
from .state import CompanyState, Task, add_usage, new_id, new_message, sum_usage

ALL = "all"            # 全公司频道


@dataclass
class Ctx:
    """运行时上下文：每次运行时传入，不进入 state、不被持久化。"""
    company: Company
    cancel: threading.Event = field(default_factory=threading.Event)
    auto_approve: bool = False
    max_review_rounds: int = MAX_REVIEW_ROUNDS


def _directive_tasks(state: CompanyState) -> dict[str, Task]:
    did = (state.get("directive") or {}).get("id")
    return {t["id"]: t for t in state.get("tasks", {}).values() if t["directive_id"] == did}


def _role_name(role_id: str) -> str:
    return ROLES[role_id].name if role_id in ROLES else role_id


def _post(runtime: Runtime[Ctx], channel: str, *messages: dict) -> None:
    """写进消息记录，并实时推给界面。"""
    MessageLog(runtime.store, runtime.context.company.id).append(channel, *messages)
    emit = get_stream_writer()
    for m in messages:
        emit({"type": "message", "channel": channel, "message": m})


def _cancellable(ctx: Ctx, on_delta):
    def wrapped(kind: str, text: str) -> None:
        if ctx.cancel.is_set():
            raise Cancelled()
        on_delta(kind, text)
    return wrapped


def _delta_emitter(emit, msg_id: str, channel: str, sender: str, kinds=("text", "reasoning")):
    def on_delta(kind: str, text: str) -> None:
        if kind in kinds:
            emit({"type": "delta", "msg_id": msg_id, "channel": channel, "sender": sender, "kind": kind, "text": text})
    return on_delta


# ---------------------------------------------------------------- 处理事件

def intake_node(state: CompanyState, runtime: Runtime[Ctx]) -> Command:
    ev = state.get("event") or {}
    kind = ev.get("type")
    tasks = _directive_tasks(state)
    base = {"dispatch": []}

    if kind == "directive":
        if any(t.get("job") for t in tasks.values()):  # 上一条指令还有任务在执行：换掉指令会让它们没人管
            _post(runtime, ALL, new_message("system", "error", "上一条指令还有任务在执行，这条指令没有开始；"
                                            "可以等它们做完，或者先停止", directive_id=ev["directive"]["id"]))
            return Command(goto=END, update=base)
        # 上一条指令里没做完的任务（暂停的、等上游的、要返工还没派发的）：换了指令就没人管了，标记为已取消
        leftover = cancel_unfinished(list(tasks.values()))
        if leftover:
            names = "、".join(f"《{t['title']}》" for t in leftover.values())
            _post(runtime, ALL, new_message("system", "event", f"开始新指令：上一条指令里没做完的任务已取消：{names}"))
        return Command(goto="plan", update={**base, "directive": ev["directive"], "feedback": "", "approval": None,
                                            "tasks": leftover})

    if kind == "plan_decision":
        if not state.get("approval"):
            return Command(goto=END, update=base)
        pending = {i: t for i, t in tasks.items() if t["status"] == "pending"}
        action = ev.get("action")
        if action == "approve":
            _post(runtime, ALL, new_message("system", "event", "CEO 批准了计划，开始执行"))
            return Command(goto="schedule", update={**base, "approval": None})
        cancelled = {i: {**t, "status": "cancelled"} for i, t in pending.items()}
        if action == "revise":
            feedback = str(ev.get("feedback", "")).strip() or "请重新规划"
            _post(runtime, ALL, new_message("ceo", "text", f"对计划的修改意见：{feedback}"))
            return Command(goto="plan", update={**base, "tasks": cancelled, "feedback": feedback, "approval": None})
        _post(runtime, ALL, new_message("system", "event", "CEO 取消了这条指令"))
        return Command(goto=END, update={**base, "tasks": cancelled, "approval": None})

    if kind == "plan_edit":  # CEO 审批时直接编辑计划（不调用规划助手）：发一张新的计划卡片，审批移到它上面
        approval = state.get("approval")
        if not approval or approval.get("type") != "plan_review":
            return Command(goto=END, update=base)
        current = [tasks[i] for i in approval["task_ids"] if i in tasks]
        try:
            specs, notes = planner.edit_plan(ev.get("tasks"), current, runtime.context.company.role_ids)
        except planner.PlanError as e:  # 网页提交前已经校验过；这里是兜底
            _post(runtime, ALL, new_message("system", "error", f"计划没有改成：{e}"))
            return Command(goto=END, update=base)
        d = state["directive"]
        plan_tag = new_id()[:4]
        ids = {s["key"]: s["id"] or f"{d['id']}-{plan_tag}-{s['key']}" for s in specs}
        edited = {}
        for s in specs:
            fields = dict(title=s["title"], detail=s["detail"], assignee=s["assignee"], reviewer=s["reviewer"],
                          depends_on=[ids[x] for x in s["depends_on"]], starts_after=[ids[x] for x in s["starts_after"]],
                          deliverables=s["deliverables"], acceptance=s["acceptance"])
            edited[ids[s["key"]]] = ({**tasks[s["id"]], **fields} if s["id"] else
                                     Task(id=ids[s["key"]], directive_id=d["id"], status="pending", result="", files=[],
                                          round=0, reviews=[], ceo_note="", quick=False, **fields))
        edited.update({t["id"]: {**t, "status": "cancelled"} for t in current if t["id"] not in edited})
        msg_id = new_id()
        text = "CEO 修改了计划：\n" + "\n".join(f"- {n}" for n in notes) if notes else "CEO 保存了计划（没有改动）"
        _post(runtime, ALL, new_message("planner", "plan", text, id=msg_id, tasks=list(ids.values()),
                                        directive_id=d["id"], edited=True))
        if ev.get("approve"):
            _post(runtime, ALL, new_message("system", "event", "CEO 批准了计划，开始执行"))
            return Command(goto="schedule", update={**base, "tasks": edited, "approval": None})
        return Command(goto=END, update={**base, "tasks": edited,
                                         "approval": {**approval, "task_ids": list(ids.values()), "plan_msg": msg_id}})

    if kind == "job_done":
        cur = tasks.get(ev.get("task_id"))
        merged = merge_job_result(cur, ev["job"], ev.get("task"), bool(ev.get("paused")), ev.get("files") or [],
                                  ev.get("kind", "work")) if cur and not ev.get("cancelled") else None
        used = ev.get("usage") or {}
        update = {**base, "usage": used}
        did = (cur or {}).get("directive_id") or (state.get("directive") or {}).get("id")
        if did and used:  # 按指令、按任务也记一份：界面上看每条指令、每个任务花了多少
            update["directive_usage"] = {did: used}
        if merged:
            update["tasks"] = {merged["id"]: {**merged, "usage": add_usage({"t": cur.get("usage") or {}},
                                                                              {"t": sum_usage(used)})["t"]}}
        return Command(goto="schedule", update=update)

    if kind == "change":
        changed, text = apply_change(tasks, ev["order"], snapshot=lambda t: runtime.context.company.snapshot(
            f"{t['id']}-{new_id()[:6]}", t.get("files", [])))
        if text:
            _post(runtime, ALL, new_message("system", "event", text, task_id=ev["order"]["task_id"]))
        return Command(goto="schedule", update={**base, "tasks": changed})

    if kind == "decision":
        task = tasks.get(ev.get("task_id"))
        if not task or task["status"] != "escalated":
            return Command(goto=END, update=base)
        changed, text = decide(task, ev.get("action"), str(ev.get("feedback", "")).strip(),
                               runtime.context.max_review_rounds)
        _post(runtime, ALL, new_message("ceo", "text", text, task_id=task["id"]))
        return Command(goto="schedule", update={**base, "tasks": {task["id"]: changed}})

    if kind == "stopped":
        _post(runtime, ev.get("channel") or ALL, new_message("system", "event", "CEO 停止了执行"))
        return Command(goto=END, update={**base, "tasks": cancel_unfinished(list(tasks.values())), "approval": None})

    if kind == "recover":
        return Command(goto="schedule", update={**base, "tasks": recover(list(tasks.values()))})

    if kind == "usage":  # 私聊的用量
        return Command(goto=END, update={**base, "usage": ev.get("usage") or {}})

    return Command(goto=END, update=base)


def decide(task: Task, action: str, feedback: str, max_rounds: int) -> tuple[Task, str]:
    """CEO 裁决审查谈不拢的任务：接受现状 / 附意见再改一轮 / 放弃。"""
    name = f"《{task['title']}》"
    last = (task.get("reviews") or [{}])[-1]
    if last.get("verdict") == "checks":  # 交付检查几次都没通过
        if action == "accept":  # 用现在的版本：有审查人照常审查
            return ({**task, "status": "review" if task.get("reviewer") else "done", "run_from": None},
                    f"裁决{name}：接受现在的版本。{feedback}")
        if action == "rework":  # 带着没通过的检查项再改一轮
            failed = "；".join(i["text"] for i in last.get("issues") or [])
            note = (feedback + "\n" if feedback else "") + f"交付检查没通过的项：{failed}"
            return ({**task, "status": "rework", "ceo_note": note, "note_checked": False, "run_from": None},
                    f"裁决{name}：再改一轮。{feedback}")
    if last.get("verdict") == "incomplete":  # 步数用完还没做完
        if action == "accept":  # 用现在的版本：有审查人照常审查
            return ({**task, "status": "review" if task.get("reviewer") else "done", "worker": None, "interrupted": False,
                     "limited": False, "extensions": 0, "run_from": None},
                    f"裁决{name}：接受现在的版本。{feedback}")
        if action == "rework":  # 接着做：带着原对话，再给一份步数
            return ({**task, "status": task.get("run_from") or "pending", "extensions": 0, "continue_note": feedback},
                    f"裁决{name}：接着做完。{feedback}")
    if action == "accept":  # 当时没解决的审查意见：CEO 已接受，之后不再当作待解决的问题
        return ({**task, "status": "done", "ceo_note": feedback, "reviews": waive(task.get("reviews") or [])},
                f"裁决{name}：接受现在的版本。{feedback}")
    if action == "rework":  # 再给一轮：下次审查还不通过会再次上报
        return ({**task, "status": "rework", "ceo_note": feedback or "请按审查意见修改", "note_checked": False,
                 "closing": False,
                 "round": max(task.get("round", 0), task.get("round_base", 0) + max_rounds) - 1},
                f"裁决{name}：再改一轮。{feedback}")
    return ({**task, "status": "failed", "result": task["result"] + "\n\n（CEO 放弃了这个任务）", "ceo_note": feedback},
            f"裁决{name}：放弃这个任务，依赖它的任务会跳过。{feedback}")


def waive(reviews: list[dict]) -> list[dict]:
    """最后一次要求修改的审查标记为"CEO 已接受"。"""
    out = list(reviews)
    for i in range(len(out) - 1, -1, -1):
        if out[i].get("verdict") == "changes":
            out[i] = {**out[i], "waived": True}
            break
    return out


# ---------------------------------------------------------------- 规划

def _trace_root(company, directive: dict):
    """这条指令的链路（根步骤）。记录失败时返回 None，不影响调度。"""
    try:
        return tracing.root(company, f"directive:{directive['id']}", f"指令：{directive['text'][:40]}",
                            **{"opc.directive": tracing.preview(directive["text"], 500)})
    except Exception:
        return None


def plan_node(state: CompanyState, runtime: Runtime[Ctx]) -> Command:
    ctx, emit = runtime.context, get_stream_writer()
    d = state["directive"]
    msg_id = new_id()
    emit({"type": "status", "role": "planner", "status": "working"})
    previous = [t for t in state.get("tasks", {}).values() if t["directive_id"] != d["id"]]
    cancel_check = _cancellable(ctx, _delta_emitter(emit, msg_id, ALL, "planner", kinds=("reasoning",)))
    memory, recalled = "", []
    root = _trace_root(ctx.company, d)
    with tracing.use(root), tracing.span("规划", "chain", **{"opc.revision": bool(state.get("feedback"))}) as plan_span:
        if ctx.company.kb:  # 长期记忆：用指令原文检索以前的结论、任务记录和资料（这条指令自己的不算）
            with tracing.span("知识库召回", "retrieval") as rs:
                records = {"tasks": dict(state.get("tasks") or {}),
                           "channels": {"all": MessageLog(runtime.store, ctx.company.id).read(ALL)}}
                memory, recalled = kb_service.recall(
                    ctx.company, d["text"], records,
                    exclude=lambda s: s == f"directive:{d['id']}" or s.startswith(f"task:{d['id']}-"))
                rs.set(**{"opc.hits": len(recalled), "opc.sources": recalled or None})
                if memory and security.enabled():  # 召回的资料和记录也是外部内容：加边界、检测注入
                    memory, _ = security.redact(memory)
                    hits = security.scan(memory)
                    memory = security.wrap(memory, "知识库召回")
                    if hits:
                        rs.set(**{"opc.injection": [h["kind"] for h in hits]})
                        _post(runtime, ALL, new_message("system", "security", "\n".join(
                            ["⚠️ 规划时从知识库召回的内容里有像是在给 AI 下指令的文字，按规则没有照做："]
                            + [f"- {h['why']}：「{h['excerpt']}」" for h in hits]
                            + ["可以在「知识库」里找到这份资料，确认是否要删除。"]), findings=hits, source="知识库召回"))
        try:
            summary, specs, usage = planner.make_plan(ctx.company, d["text"], state.get("feedback", ""), previous,
                                                      cancel_check, memory=memory)
        except planner.PlanError as e:
            _post(runtime, ALL, new_message("system", "error", str(e), id=msg_id))
            plan_span.finish(error=str(e))
            if root:
                root.finish(error=str(e))
            return Command(goto=END, update={"plan_summary": "", "feedback": ""})
        finally:
            emit({"type": "status", "role": "planner", "status": "idle"})
        plan_span.set(**{"opc.tasks": len(specs)})
    # 每一版计划有独立的标识：CEO 提修改意见重新规划时，新任务不会覆盖旧计划里已取消的任务
    plan_tag = new_id()[:4]
    ids = {s["key"]: f"{d['id']}-{plan_tag}-{s['key']}" for s in specs}
    tasks = {ids[s["key"]]: Task(id=ids[s["key"]], directive_id=d["id"], title=s["title"], detail=s["detail"],
                                 assignee=s["assignee"], depends_on=[ids[x] for x in s["depends_on"]],
                                 starts_after=[ids[x] for x in s.get("starts_after", [])],
                                 status="pending", result="", files=[], reviewer=s.get("reviewer", ""),
                                 round=0, reviews=[], ceo_note="", deliverables=s.get("deliverables") or [],
                                 acceptance=s.get("acceptance") or [], quick=bool(s.get("quick"))) for s in specs}
    quick = all(t["quick"] for t in tasks.values())
    _post(runtime, ALL, new_message("planner", "plan", summary, id=msg_id, tasks=list(tasks), directive_id=d["id"],
                                    quick=quick, recalled=recalled))
    update = {"tasks": tasks, "plan_summary": summary, "feedback": "", "usage": {"planner": usage},
              "directive_usage": {d["id"]: {"planner": usage}}}
    if ctx.auto_approve or quick:  # 小任务不等审批：直接开始，CEO 随时可以停止
        if quick and not ctx.auto_approve:
            _post(runtime, ALL, new_message("system", "event", "小任务：直接开始，不需要审批，也不安排审查"))
        return Command(goto="schedule", update=update)
    approval = {"type": "plan_review", "directive_id": d["id"], "summary": summary, "task_ids": list(tasks),
                "plan_msg": msg_id}
    return Command(goto=END, update={**update, "approval": approval})


# ---------------------------------------------------------------- 派发

def schedule_node(state: CompanyState, runtime: Runtime[Ctx]) -> Command:
    """一满足条件就派发：这次要启动的工作运行写进 dispatch。全部结束时去汇报。"""
    if state.get("approval") or not state.get("directive"):
        return Command(goto=END, update={"dispatch": []})
    tasks = _directive_tasks(state)
    work, review = ready_batch(list(tasks.values()))
    updates, jobs = {}, []
    for t in work:
        job = new_id()
        updates[t["id"]] = dispatch(t, "work", job, thread=f"{runtime.context.company.id}/{t['id']}/{job}")
        jobs.append({"job": job, "kind": "work", "task_id": t["id"]})
    for t in review:
        job = new_id()
        updates[t["id"]] = dispatch(t, "review", job)
        jobs.append({"job": job, "kind": "review", "task_id": t["id"]})
    after = list({**tasks, **updates}.values())
    waiting = (jobs or any(t.get("job") for t in after) or any(t["status"] == "escalated" for t in after)
               or any(t.get("paused") and t["status"] not in FINISHED_STATUSES for t in after))
    return Command(goto=END if waiting else "report", update={"tasks": updates, "dispatch": jobs})


# ---------------------------------------------------------------- 汇报

def report_node(state: CompanyState, runtime: Runtime[Ctx]) -> dict:
    ctx, emit = runtime.context, get_stream_writer()
    tasks = list(_directive_tasks(state).values())
    # 依赖的任务失败了，这些任务永远无法开始
    skipped = {t["id"]: {**t, "status": "skipped", "result": "依赖的任务没有完成，已跳过"}
               for t in tasks if t["status"] == "pending"}
    final = [skipped.get(t["id"], t) for t in tasks if t["status"] != "cancelled"]
    root = _trace_root(ctx.company, state["directive"])
    if len(final) == 1 and final[0].get("quick") and final[0]["status"] == "done":
        # 小任务做完了：负责人的汇报就是结果，不再让汇报助手转述一遍（省一次调用，也不丢细节）
        _post(runtime, ALL, new_message("reporter", "report", final[0].get("result") or "（没有汇报）",
                                        directive_id=state["directive"]["id"], quick=True))
        if root:
            root.set(**{"opc.tasks": len(final)}).finish()
        return {"tasks": skipped}
    notes = [m for m in MessageLog(runtime.store, ctx.company.id).read(ALL)
             if m.get("kind") == "interject" and m.get("directive_id") == state["directive"]["id"]]
    msg_id = new_id()
    emit({"type": "status", "role": "reporter", "status": "working"})
    try:
        with tracing.use(root), tracing.span("汇报", "chain"):
            text, usage = planner.make_report(ctx.company, state["directive"]["text"], final,
                                              _cancellable(ctx, _delta_emitter(emit, msg_id, ALL, "reporter")),
                                              notes=format_notes(notes))
    finally:
        emit({"type": "status", "role": "reporter", "status": "idle"})
    if root:
        statuses = [t["status"] for t in final]
        root.set(**{"opc.tasks": len(final), "opc.done": statuses.count("done"),
                    "opc.failed": statuses.count("failed") + statuses.count("skipped")}).finish()
    _post(runtime, ALL, new_message("reporter", "report", text, id=msg_id, directive_id=state["directive"]["id"]))
    return {"tasks": skipped, "usage": {"planner": usage}, "directive_usage": {state["directive"]["id"]: {"planner": usage}}}


# ---------------------------------------------------------------- 组装

def build_graph(checkpointer=None, store=None):
    g = StateGraph(CompanyState, context_schema=Ctx)
    g.add_node("intake", intake_node, destinations=("plan", "schedule", END))
    g.add_node("plan", plan_node, destinations=("schedule", END))
    g.add_node("schedule", schedule_node, destinations=("report", END))
    g.add_node("report", report_node)
    g.add_edge(START, "intake")
    g.add_edge("report", END)
    return g.compile(checkpointer=checkpointer, store=store if store is not None else InMemoryStore())
