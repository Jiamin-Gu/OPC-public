"""LangGraph 的状态定义。

状态分层，而不是一个大字典：
- directive：当前正在执行的 CEO 指令
- tasks：任务看板（所有指令的任务，按 id 合并；并行的角色各自写自己的任务，不会冲突）
- usage：每个角色累计的 token 用量（累加）
- directive_usage：每条指令的用量（指令 id -> 角色 -> 用量），每个任务上也记着它自己的用量（task.usage）
消息不在 state 里：存在 LangGraph 的 Store（见 messages.py），否则每个检查点都要再存一遍全部对话。
公司配置等不变的东西不放在 state 里，而是作为运行时上下文传入（见 graph.Ctx）。
"""
import uuid
from datetime import datetime
from typing import Annotated, TypedDict


class Task(TypedDict):
    id: str
    directive_id: str
    title: str
    detail: str
    assignee: str           # 角色 id
    depends_on: list[str]   # 依赖的任务 id
    status: str             # pending / running / review / reviewing / rework / escalated / done / failed / skipped / cancelled
                            # （状态机见 changes.py）
    result: str             # 完成后角色的汇报
    files: list[str]        # 写过的文件
    reviewer: str           # 审查人角色 id，空表示不需要审查
    round: int              # 已经审查了几轮
    reviews: list[dict]     # 审查记录：[{round, verdict, issues, summary}]
    ceo_note: str           # CEO 裁决时给的意见（再改一轮时使用）
    # 以下字段在 v0.7 加入，旧数据里可能没有，读取时都用 .get
    starts_after: list[str]  # 开始依赖：这些任务开始后才能开始（不用等它们完成）
    started: bool           # 已经派发执行过（开始依赖据此判断）
    paused: bool            # CEO 暂停了这个任务
    interrupted: bool       # 执行到一半被暂停：恢复时在已有的产出上继续
    resync: dict            # 上游任务返工过：{上游任务 id: 返工前的快照标签}，恢复时带上改动对比
    round_base: int         # 重新开始一轮审查时，之前已经审查过几轮（审查轮数从这里往上数）
    worker: str | None      # 角色执行循环的检查点（线程 id）：暂停后恢复时带着原对话接着做
    job: str | None         # 正在执行这个任务的工作运行；有 job 的任务不会再派发
    run_from: str | None    # 执行中的任务派发前是 pending 还是 rework（暂停时退回去）
    limited: bool           # 上一次执行步数用完、停下了（恢复时接着做剩下的部分）
    extensions: int         # 这一轮执行已经因为步数用完自动接着做了几次
    continue_note: str      # CEO 裁决"接着做"时给的意见
    # 以下字段在 v0.12 加入：机械检查
    deliverables: list[dict]  # 交付要求：[{path, template, sections, must_mention, min_chars}]，提交时逐条机械检查
    acceptance: list[str]   # 验收标准：审查人逐条判断
    responses: list[dict]   # 返工时负责人对每条审查意见的回应 [{no, action: fixed/disagree, detail}]
    base_tag: str           # 上次审查（或 CEO 要求返工）时产出文件的快照：复审只看这之后的改动，返工核对文件是否真的改过
    reviewed: bool          # 已经完整审查过一次（之后的审查是复审）
    note_checked: bool      # CEO 的意见（ceo_note）已经在审查里核对过：之后的返工不再要求回应它
    headline: str           # 负责人给 CEO 的一两句话结论（全公司频道的任务卡片显示它）
    decisions: list[str]    # 需要 CEO 决定或确认的事项（汇总在最终汇报里）
    # v0.15 加入
    quick: bool             # 小任务：不用审批、不审查，答案可以直接写在汇报里，最终汇报就是它的汇报
    closing: bool           # 下一轮复审是收尾复审：只核对剩下的问题，新发现记为建议（复审收敛之后）
    usage: dict             # 这个任务累计的用量（执行、审查、返工、问同事都算）：{prompt, completion, cached, calls}


class Message(TypedDict, total=False):
    id: str
    sender: str             # "ceo" / 角色 id / "planner" / "reporter" / "system"
    kind: str               # text / plan / report / event / error / review（审查结论）/ interject（CEO 实时插话）
    text: str
    reasoning: str
    tools: list[dict]       # 这条消息生成过程中调用的工具
    files: list[str]
    task_id: str
    directive_id: str
    tasks: list[str]        # plan 消息：计划包含的任务 id
    ts: str


def merge_tasks(left: dict | None, right: dict | None) -> dict:
    return {**(left or {}), **(right or {})}


def add_usage(left: dict | None, right: dict | None) -> dict:
    out = {k: dict(v) for k, v in (left or {}).items()}
    for who, u in (right or {}).items():
        cur = out.setdefault(who, {"prompt": 0, "completion": 0, "cached": 0, "calls": 0})
        for key in ("prompt", "completion", "cached", "calls"):
            cur[key] = cur.get(key, 0) + u.get(key, 0)
    return out


def sum_usage(by_role: dict | None) -> dict:
    """{角色: 用量} 加成一份用量。"""
    out = {"prompt": 0, "completion": 0, "cached": 0, "calls": 0}
    for u in (by_role or {}).values():
        for key in out:
            out[key] += u.get(key, 0)
    return out


def add_directive_usage(left: dict | None, right: dict | None) -> dict:
    out = dict(left or {})
    for did, by_role in (right or {}).items():
        out[did] = add_usage(out.get(did), by_role)
    return out


class CompanyState(TypedDict, total=False):
    directive: dict         # {"id", "text", "target"}；target 为 "all" 或角色 id
    feedback: str           # CEO 对计划的修改意见（重新规划时使用）
    plan_summary: str
    approval: dict | None   # 等 CEO 审批的计划：{type: plan_review, directive_id, summary, task_ids}
    event: dict             # 这次运行处理的事件（调度图的输入，见 graph.py）
    dispatch: list          # 这次运行要启动的工作运行：[{job, kind, task_id}]
    tasks: Annotated[dict[str, Task], merge_tasks]
    usage: Annotated[dict[str, dict], add_usage]
    directive_usage: Annotated[dict[str, dict], add_directive_usage]


def new_id() -> str:
    return uuid.uuid4().hex[:12]


def new_message(sender: str, kind: str, text: str, **extra) -> Message:
    msg: Message = {"id": extra.pop("id", None) or new_id(), "sender": sender, "kind": kind, "text": text,
                    "ts": datetime.now().isoformat(timespec="seconds")}
    msg.update({k: v for k, v in extra.items() if v not in (None, "", [])})
    return msg
