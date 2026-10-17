"""工作运行：一个任务的一次执行、一次审查，或者一次私聊。由执行引擎（engine.py）在线程里并行运行。

每个工作运行只看自己的任务快照，用执行循环（worker.py）完成，结束时交回一个 job_done 事件给调度图（graph.py）：
- 执行（work）：带着任务说明、上游同事的成果、CEO 的插话完成任务；返工时带着审查意见；被暂停时停在 interrupt，
  下次带着原对话接着做（恢复时只追加暂停之后发生的事：返工意见、上游改动对比、新的插话）
- 审查（review）：审查人用 submit_review 给出结论；多轮仍不通过就上报 CEO
- 私聊（chat）：CEO 单独和某个角色对话，可以和团队的工作同时进行
- 执行时可以和同事协作：发现上游的产出有问题就上报（做成调整建议，CEO 确认后上游返工），需要确认细节就问同事

消息写进消息记录（messages.py）并实时推给界面；任务状态的变化只通过 job_done 交给调度图，工作运行不直接改 state。
"""
import threading
from dataclasses import dataclass, field
from typing import Callable, TypedDict

from . import checks, condense, prompts, task_formats
from .agents import Cancelled, run_role
from .company import Company
from .inbox import Inbox, format_notes
from .messages import MessageLog
from .roles import ROLES
from .state import Task, add_usage, new_id, new_message
from .worker import _DEFAULT_SAVER, has_checkpoint

ALL = "all"            # 全公司频道
MEMORY_MESSAGES = 6    # 角色执行公司任务时，参考自己频道里最近几条消息
# 步数用完自动接着做几次、交付 / 审查结论最多提交几次、最多问同事几次：按公司的投入档位（effort.py），在 JobCtx 里
EXTRA_ROUNDS = 1       # 复审在收敛时（上一轮的问题全部解决、新问题很少），轮数用完后最多再给几轮
CONVERGING_ISSUES = 2  # 新问题不超过这么多条才算在收敛
# 收敛之后的下一轮是"收尾复审"：只核对剩下的这几个问题改好没有，新发现一律记为建议。试跑里每次修改都会带出 1～2 个
# 新问题，审查永远挑得完，任务就一直谈不拢；收尾复审保证收敛之后最多再一轮就结束
DIRECT_HISTORY = 20    # 单独对话时带上的历史消息条数


class WorkPayload(TypedDict):
    """任务说明需要的信息：自己的任务 + 上游已完成的任务 + 同时进行的任务（开始依赖）+ CEO 的指令。"""
    task: Task
    upstream: list[Task]
    peers: list[Task]
    directive: dict


@dataclass
class JobCtx:
    company: Company
    log: MessageLog
    emit: Callable[[dict], None]           # 实时事件（流式文字、工具调用、角色状态……）
    cancel: threading.Event                # CEO 停止了整个公司
    job: str = ""
    kind: str = "work"                     # work / review / chat
    task: Task | None = None               # 派发时的任务快照
    tasks: dict = field(default_factory=dict)   # 这条指令下所有任务的快照（找上游、同时进行的任务）
    directive: dict = field(default_factory=dict)
    stop: threading.Event | None = None    # 这个任务被暂停
    inbox: Inbox = field(default_factory=Inbox)
    saver: object = None                   # 执行循环的检查点存储
    max_role_steps: int = 12
    max_review_rounds: int = 2
    extensions: int = 1                    # 执行任务步数用完时自动接着做几次，再用完就交给 CEO 裁决
    rejects: int = 3                       # 交付 / 审查结论最多提交几次：前几次核对不通过退回给本人改，最后一次照收
    asks: int = 3                          # 每次执行最多问同事几次
    role: str = ""                         # 私聊：和谁聊
    topic: Task | None = None              # 私聊：围绕哪项工作讨论（None 是自由聊天）

    def post(self, channel: str, *messages: dict) -> None:
        self.log.append(channel, *messages)
        for m in messages:
            self.emit({"type": "message", "channel": channel, "message": m})

    def payload(self) -> WorkPayload:
        t = self.task
        return WorkPayload(task=_view(t), upstream=[self.tasks[d] for d in t["depends_on"] if d in self.tasks],
                           peers=[self.tasks[d] for d in t.get("starts_after", []) if d in self.tasks],
                           directive=self.directive)


def _view(task: Task) -> Task:
    """任务说明里按派发前的状态写（执行中的任务记着原来是 pending 还是 rework）。"""
    return {**task, "status": task.get("run_from") or task["status"]} if task.get("status") == "running" else task


def _role_name(role_id: str) -> str:
    return ROLES[role_id].name if role_id in ROLES else role_id


REVIEW_TOOL = {"type": "function", "function": {
    "name": "submit_review",
    "description": "提交审查结论。检查完后调用一次；系统会先核对，不合格会退回让你改。",
    "parameters": {"type": "object", "properties": {
        "verdict": {"type": "string", "enum": ["pass", "changes"],
                    "description": "pass = 没有必须修改的问题；changes = 有必须修改的问题"},
        "summary": {"type": "string", "description": "总体评价：检查了哪些内容、推演了哪些场景、结论和依据"},
        "issues": {"type": "array", "description": "必须修改的问题（会退回给负责人）",
                   "items": {"type": "object", "properties": {
                       "where": {"type": "string", "description": "文件和章节"},
                       "quote": {"type": "string", "description": "逐字复制问题所在的原文（几个词到一两句；缺了什么就复制应该写它的那一节的标题）。"
                                                                   "系统会核对原文确实存在"},
                       "problem": {"type": "string", "description": "什么问题、按它做会怎么出错（一个具体情形）"},
                       "expected": {"type": "string", "description": "期望怎样"}},
                       "required": ["where", "quote", "problem"]}},
        "previous": {"type": "array", "description": "复审时必填：上一轮的每个问题是否已经解决",
                     "items": {"type": "object", "properties": {
                         "no": {"type": "integer"},
                         "status": {"type": "string", "enum": ["resolved", "unresolved"]},
                         "note": {"type": "string"}}, "required": ["no", "status"]}},
        "suggestions": {"type": "array", "items": {"type": "string"}, "description": "建议修改、小改进（不退回）"},
    }, "required": ["verdict", "summary"]}}}

SUBMIT_RULE = ("# 怎么提交\n用 submit_review 提交结论，系统会先核对，不合格会退回让你改：\n"
               "- issues 只写必须修改的问题（会退回给负责人）。每条写 where（文件和章节）、quote（逐字复制问题所在的原文，"
               "几个词到一两句；缺了什么就复制应该写它的那一节的标题）、problem（什么问题、按它做会怎么出错）、expected（期望怎样）。"
               "quote 在文件里找不到的会被退回\n"
               "- 建议修改、小改进写进 suggestions，不退回\n"
               "- 有必须修改的问题 verdict 就是 changes，没有就是 pass")


FINISH_TOOL = {"type": "function", "function": {
    "name": "finish_task",
    "description": "做完任务后调用，提交交付。系统会按任务说明里的交付要求自动检查，不通过会告诉你哪里要改，改好再提交；"
                   "不提交不算完成。",
    "parameters": {"type": "object", "properties": {
        "headline": {"type": "string", "description": "给 CEO 看的一两句话结论（不超过 100 字）：最重要的成果或结论是什么"},
        "summary": {"type": "string", "description": "给 CEO 和审查人的汇报：做了什么、产出了哪些文件、关键决定，以及你做的假设和「待确认」的事项"},
        "decisions": {"type": "array", "items": {"type": "string"},
                      "description": "需要 CEO 决定或确认的事项（每条一句话，例如「年推广预算上限」），没有就不写"},
        "responses": {"type": "array", "description": "返工时必填：逐条回应（编号和返工说明里一致，CEO 的意见是第 0 条）",
                      "items": {"type": "object", "properties": {
                          "no": {"type": "integer"},
                          "action": {"type": "string", "enum": ["fixed", "disagree"]},
                          "detail": {"type": "string", "description": "已修改：改了哪里、怎么改的；不同意：理由"}},
                          "required": ["no", "action", "detail"]}},
        "upstream": {"type": "string", "description": "上游有改动时必填：你的产出受了哪些影响、怎么处理的"}},
        "required": ["headline", "summary"]}}}

FINISH_RULE = ("# 怎么交付\n做完后调用 finish_task 提交，不提交不算完成：headline 写给 CEO 的一两句话结论；summary 写给 CEO 和审查人的"
               "汇报（做了什么、产出了哪些文件、关键决定、你做的假设和「待确认」的事项）；需要 CEO 决定或确认的事写进 decisions。"
               "提交时系统按上面的交付要求自动检查，不通过会告诉你哪里不合格，改好后再提交。")


QUICK_RULE = ("这是一个小任务：CEO 想尽快拿到结果。直接动手，做到任务要求的程度就停，篇幅够用就好，"
              "不要扩展成完整的方案文档，也不要为了凑字数铺陈背景。")
QUICK_NO_FILE = ("这个任务不需要写文件：把完整的结果（结论、要点、依据和来源）直接写在 finish_task 的 summary 里，可以用 Markdown，"
                 "CEO 会直接在频道里看到它；只有任务明确要求时才写文件。")
QUICK_RESULT_CHARS = 12000  # 小任务的汇报就是给 CEO 的结果，保留得比常规任务（3000 字）多


def pending_note(task: Task) -> str:
    """还没在审查里核对过的 CEO 意见（核对过的只作为背景，不再要求逐条回应）。"""
    return "" if task.get("note_checked") else str(task.get("ceo_note") or "")


def open_issues(task: Task) -> list[dict]:
    """还没解决的审查意见：这一轮返工以来最后一次审查要求修改的问题（CEO 裁决接受过的不算）。"""
    base = task.get("round_base", 0)
    reviews = [r for r in task.get("reviews") or [] if r["round"] > base and r.get("verdict") in ("pass", "changes")]
    last = reviews[-1] if reviews else None
    if not last or last["verdict"] != "changes" or last.get("waived"):
        return []
    return list(last.get("issues") or [])


def task_brief(payload: WorkPayload, memory: list[dict] | None = None, company: Company | None = None) -> str:
    """给角色的任务说明：CEO 指令 + 自己的任务 + 交付要求 + 上游同事的成果 + 和 CEO 的近期单独沟通。"""
    task = payload["task"]
    parts = [f"CEO 给全公司的指令：{payload['directive']['text']}", "",
             f"分配给你的任务：{task['title']}", task["detail"]]
    if task.get("reviewer"):
        parts.append(f"（完成后由{_role_name(task['reviewer'])}审查，审查通过才算完成）")
    if task.get("quick"):
        parts += ["", QUICK_RULE if task.get("deliverables") else QUICK_RULE + QUICK_NO_FILE]
    if payload["upstream"]:
        parts += ["", "你依赖的同事任务已经完成，请先阅读他们的产出："]
        for t in payload["upstream"]:
            shorts = [f for f in t["files"] if condense.is_concise(f)]
            parts.append(f"- {_role_name(t['assignee'])}《{t['title']}》，产出文件：{'、'.join(t['files']) or '无'}。"
                         + (f"（{'、'.join(shorts)} 是简洁版，便于快速了解，细节以完整版为准）" if shorts else "")
                         + f"汇报：{t['result'][:600]}")
    if payload.get("peers"):
        parts += ["", "下面这些同事的任务和你同时在进行（它们开始后你才开始），产出可能还没写完，需要时可以读一读已有的文件："]
        parts += [f"- {_role_name(t['assignee'])}《{t['title']}》" for t in payload["peers"]]
    memory = [m for m in memory or [] if m.get("kind") == "text"]
    if memory:
        parts += ["", "你和 CEO 最近的单独沟通（供参考）："]
        parts += [f"{'CEO' if m['sender'] == 'ceo' else '你'}：{m['text'][:300]}" for m in memory]
    parts.append(task_formats.requirements_brief(task))
    return "\n".join(p for p in parts if p is not None)


def rework_brief(task: Task) -> str:
    issues = open_issues(task)
    last = next((r for r in reversed(task.get("reviews") or []) if r.get("verdict") == "changes"), {}) if issues else {}
    who = f"{_role_name(task.get('reviewer', ''))}审查后要求修改" if issues else "CEO 看过之后要求修改"
    parts = ["", f"# 这是返工：你之前交付了这个任务，{who}",
             f"你上次的产出文件：{'、'.join(task['files']) or '无'}", f"你上次的汇报：{task['result'][:800]}", ""]
    if issues:
        parts.append(f"第 {last.get('round')} 轮审查意见：{last.get('summary', '')}")
        parts += [f"{i}. {issue['text']}" for i, issue in enumerate(issues, 1)]
    if pending_note(task):
        parts += ["", f"第 0 条 · CEO 的意见（必须遵守）：{pending_note(task)}"]
    parts += ["", "请先重新阅读你的产出文件，再逐条处理：用 edit_file 只修改有问题的部分，审查没有提出问题的内容保持不变，"
              "不要用 write_file 重写整份文件；同一个文件要改的所有地方放进一次 edit_file 调用的 edits 列表，"
              "不要一处调用一次。不同意的，说明具体理由。",
              "改的时候注意这处修改会不会和文件里其他地方（以及同事的文档）冲突，需要的话一并修改。",
              "做完后调用 finish_task 提交，在 responses 里逐条回应（no 和上面的编号一致"
              + ("，CEO 的意见是第 0 条" if pending_note(task) else "") + "）：已修改（fixed）写明改了哪里、怎么改的；"
              "不同意（disagree）写明理由。说已修改的，系统会核对文件确实改过；审查人会据此复查。"]
    return "\n".join(parts)


def resume_brief(task: Task, company: Company | None, all_tasks: dict, interrupted: bool = True) -> str:
    """暂停后重新开始、上游返工后同步时，补充在任务说明后面的内容。"""
    parts = []
    if interrupted and task.get("interrupted"):
        parts += ["", "# 继续之前的工作",
                  "你之前开始过这个任务，做到一半被暂停了。先用 list_files / read_file 看看你已经写好的文件，"
                  "在此基础上继续完成，不要从头重写。"]
    for up_id, tag in (task.get("resync") or {}).items():
        up = all_tasks.get(up_id)
        if not up or not company:
            continue
        diff = company.diff_since(tag, up.get("files", []))
        parts += ["", f"# 上游有改动：{_role_name(up['assignee'])}的《{up['title']}》在你开始之后返工过",
                  f"返工原因（CEO 的意见）：{up.get('ceo_note') or '无'}"]
        parts += [f"改动对比（- 是删掉的，+ 是新加的）：\n```diff\n{diff}\n```"] if diff else ["（文件内容没有变化）"]
        parts.append(f"你之前的产出：{'、'.join(task.get('files') or []) or '还没有文件'}。请对照上面的改动，"
                     "检查你的产出哪些地方受影响，用 edit_file 只改受影响的部分（所有修改放进一次调用的 edits 列表）；"
                     "完全不受影响就说明原因，不用改文件。"
                     "提交时在 finish_task 的 upstream 里写明受了哪些影响、怎么处理的。")
    return "\n".join(parts)


def resume_note(task: Task, company: Company | None, all_tasks: dict, notes: list[dict]) -> str:
    """暂停的任务带着原对话恢复时，追加的一条消息：只说暂停之后发生了什么。"""
    if task.get("limited"):  # 上一段的步数用完了
        parts = ["# 上一段工作的步数用完了，请接着完成剩下的部分",
                 "已经做好的不要重做：先回顾还差什么，做完后照常写最后的汇报（返工时逐条写明每个问题的处理结果）。"]
        if task.get("continue_note"):
            parts.append(f"CEO 的意见（必须遵守）：{task['continue_note']}")
    else:
        parts = ["# 你的任务暂停后恢复了，请在之前的基础上继续完成"]
        if task["status"] == "rework" and pending_note(task):
            parts.append(rework_brief(task))
    parts.append(resume_brief(task, company, all_tasks, interrupted=False))
    if notes:
        parts += ["", format_notes(notes)]
    return "\n".join(p for p in parts if p)


def review_brief(payload: WorkPayload, round_no: int, company=None, previous: list[dict] | None = None,
                 diff: str = "", must_read: list[str] | None = None) -> str:
    """审查说明。previous 不是 None 表示复审：只看上一轮的问题（previous）和这次的改动（diff）。"""
    task = payload["task"]
    owner = _role_name(task["assignee"])
    parts = [f"CEO 给全公司的指令：{payload['directive']['text']}", "",
             f"请你审查{owner}的任务《{task['title']}》（第 {round_no} 轮审查）。",
             f"任务要求：{task['detail']}",
             f"产出文件：{'、'.join(f for f in task['files'] if not condense.is_concise(f)) or '无'}",
             f"{owner}的汇报：{task['result'][:3000]}"]
    shorts = [f for f in task["files"] if condense.is_concise(f)]
    if shorts:
        parts.insert(-1, f"（另有系统根据完整版自动生成的简洁版：{'、'.join(shorts)}，不用审查，以完整版为准）")
    if task.get("acceptance"):
        parts += ["", "验收标准（逐条判断是否满足；只按标准判断，不要额外加码）："]
        parts += [f"{i}. {a}" for i, a in enumerate(task["acceptance"], 1)]
    if task.get("deliverables"):
        parts.append("产出文件是否齐全、规定的章节和内容、字数、格式已经由系统检查通过，不用再查。")
    if pending_note(task):
        parts.append(f"CEO 的意见：{pending_note(task)}（检查是否照做了）")
    if previous is not None:
        parts += ["", "# 这是复审：第一轮已经全文审查过，这次只检查两件事",
                  "1. 上一轮的每个问题是否已经解决，用 previous 逐条给出 resolved / unresolved。"
                  f"{owner}不同意的，理由合理就算 resolved，不合理就是 unresolved 并在 note 里说明原因："]
        parts += [f"【上轮问题 {n}】{i['text']}" for n, i in enumerate(previous, 1)] or ["（上一轮没有要求修改的问题）"]
        parts += ["2. 这次的改动有没有带出新的问题。只有出在改动部分的新问题才写进 issues（quote 引用改动后的原文）；"
                  "改动之外的新发现写进 suggestions，不作为退回的理由。",
                  "改动对比（- 是删掉的，+ 是新加的）：", f"```diff\n{diff}\n```" if diff else "（文件内容没有变化）"]
    parts += ["", prompts.review_rules(company.prompt_level(task["reviewer"])) if company
              else prompts.review_rules(), "", SUBMIT_RULE]
    if must_read:
        parts.append(f"- 提交前必须用 read_file 读过要审查的文件：{'、'.join(must_read)}")
    if previous:
        parts.append("- previous 要逐条覆盖上一轮的问题（no 对应【上轮问题 N】）")
    return "\n".join(parts)



# ---------------------------------------------------------------- 执行

def consult_answers(j: JobCtx) -> list[dict]:
    """这条指令下同事问答的记录：[{asker, answerer, question, answer}]。"""
    msgs = j.log.read(ALL)
    questions = {m["id"]: m for m in msgs if m.get("consult") and m.get("task_id") in j.tasks}
    out = []
    for m in msgs:
        q = questions.get(m.get("reply_to") or "")
        if m.get("consult") and q:
            out.append({"asker": q["sender"], "answerer": m["sender"], "question": q["text"], "answer": m["text"]})
    return out


def decisions_brief(j: JobCtx, task: Task, revising: bool) -> str:
    """同事问答里定下的口径：
    - 上游同事回答的：写进下游的任务说明，上游文档里可能还没写，以这里为准
    - 自己回答过的：返工、同步时要求一并写进自己的文档（不然这些规则只存在于对话里，别人看文档时找不到）"""
    answers = consult_answers(j)
    if not answers:
        return ""
    upstream = {j.tasks[d]["assignee"] for d in task.get("depends_on", []) + task.get("starts_after", []) if d in j.tasks}
    fmt = lambda a: f"- {_role_name(a['asker'])}{a['question'][:300]}\n  {_role_name(a['answerer'])}的回答：{a['answer'][:800]}"  # noqa: E731
    parts = []
    given = [a for a in answers if a["answerer"] in upstream and a["answerer"] != task["assignee"]]
    if given:
        parts += ["", "# 同事问答里已经定下的口径（上游文档里可能还没写，以这里为准）"] + [fmt(a) for a in given]
    mine = [a for a in answers if a["answerer"] == task["assignee"]]
    if mine and revising:
        parts += ["", "# 你在同事问答里给出的口径",
                  "下面是同事问你时你给出的回答。文档里还没写的，这次一并写进去（同事是按这些回答做的）："] + [fmt(a) for a in mine]
    return "\n".join(parts)


def write_guard(j: JobCtx, task: Task, rework: bool = False):
    """执行任务时写文件的范围：自己任务的文件、交付要求里的文件、新文件。别的任务的产出、别的同事写的文件不能改——
    发现上游的问题要上报，不能自己改（否则上游不知道自己的文档被改了，审查也被绕过）。
    返工时不能用 write_file 整篇重写自己已有的文件：试跑里整篇重写会把审查没提到的地方也改掉，带出新的矛盾，复审又要从头挑。"""
    company = j.company

    def rel(path: str) -> str | None:
        try:
            return company.resolve(path).relative_to(company.files_root.resolve()).as_posix()
        except Exception:
            return None

    own = {rel(p) for p in list(task.get("files") or []) + [d["path"] for d in task.get("deliverables") or []]}
    owners = {}
    for t in j.tasks.values():
        if t["id"] != task["id"]:
            for p in list(t.get("files") or []) + [d["path"] for d in t.get("deliverables") or []]:
                owners.setdefault(rel(p), t)

    def check(path: str, tool: str = "edit_file") -> str | None:
        r = rel(path or "")
        if r and r in own and rework and tool == "write_file" and _exists(company, r):
            return (f"返工时不能用 write_file 重写 {r}：用 edit_file 只改审查指出的地方（同一个文件要改的地方放进一次调用的 "
                    "edits 列表），没提到的内容保持不变")
        if not r or r in own:
            return None  # 路径不合法时交给写文件的工具报错
        hint = "发现它有问题：上游的用 report_upstream_issue 上报，或者用 ask_colleague 问负责人；需要类似的内容就写到你自己的文件里"
        if r in owners:
            t = owners[r]
            return f"{r} 是{_role_name(t['assignee'])}《{t['title']}》的产出，不能改。{hint}"
        author = company.file_meta().get(r, {}).get("author")
        if author and author != task["assignee"] and author in ROLES:
            return f"{r} 是{_role_name(author)}写的文件，不在你这次任务的范围里，不能改。{hint}"
        return None
    return check


def run_work(j: JobCtx) -> dict:
    task, payload = j.task, j.payload()
    view = payload["task"]
    role_id = task["assignee"]
    rework = view["status"] == "rework"
    msg_id = new_id()
    j.emit({"type": "status", "role": role_id, "status": "working", "task_id": task["id"]})
    notes = j.log.notes(j.directive.get("id"), role_id)
    listen = j.inbox.listener(role_id, {m["id"] for m in notes})
    if task.get("interrupted") and has_checkpoint(j.saver, task.get("worker")):  # 暂停过：带着原对话接着做
        conversation, note = [], resume_note(view, j.company, j.tasks, notes) + decisions_brief(j, task, bool(view.get("resync")))
    else:
        memory = j.log.read(role_id, last=MEMORY_MESSAGES)  # 和 CEO 的近期单独沟通
        brief = (task_brief(payload, memory, j.company) + (rework_brief(view) if rework else "")
                 + resume_brief(view, j.company, j.tasks) + decisions_brief(j, task, rework or bool(view.get("resync"))))
        if notes:
            brief += "\n\n" + format_notes(notes)
        brief += "\n\n" + COLLAB_RULE + "\n\n" + FINISH_RULE
        conversation, note = [{"role": "user", "content": brief}], None
    collab = Collab(j, role_id, task)
    deliver = Deliver(j, task, view)
    tools = ([REPORT_TOOL] if collab.upstream() else []) + [ask_tool(j.company, role_id, j.asks), FINISH_TOOL]
    done = {"type": "job_done", "job": j.job, "kind": "work", "task_id": task["id"]}
    try:
        out = run_role(j.company, role_id, conversation, j.emit, msg_id, ALL, j.cancel, j.max_role_steps,
                       listen=listen, stop=j.stop, saver=j.saver, thread_id=task.get("worker"), resume_note=note,
                       extra_tools=tools, handlers={"report_upstream_issue": collab.report, "ask_colleague": collab.ask,
                                                    "finish_task": deliver},
                       after_tools=collab.flush, pause_on_limit=True, finished=deliver.finished, require_finish=True,
                       observe=deliver.observe, can_write=write_guard(j, task, rework))
    except Cancelled:
        return {**done, "cancelled": True}
    except Exception as e:  # 某个角色调用失败（比如密钥错误）不影响其他并行的角色
        text = f"任务失败：{type(e).__name__}: {e}"
        j.post(ALL, new_message(role_id, "error", text, id=msg_id, task_id=task["id"]))
        return {**done, "task": {**task, "status": "failed", "result": text, "worker": None}}
    finally:
        j.emit({"type": "status", "role": role_id, "status": "idle"})
    usage = {role_id: out["usage"], **collab.usage}  # 被问的同事的用量也记上
    if out.get("limited"):  # 步数用完：第一次自动接着做，再用完交给 CEO
        return {**done, "task": _limited(j, task, out, msg_id), "files": out["files"], "usage": usage}
    if out.get("paused"):  # 执行循环停在 interrupt：写好的文件和做到一半的进度都保留，调度图记下检查点
        j.post(ALL, new_message("system", "event", f"{_role_name(role_id)}的《{task['title']}》已暂停，"
                                "做到一半的进度和写好的文件都保留", task_id=task["id"]))
        return {**done, "paused": True, "files": out["files"], "usage": usage}
    if deliver.result is None:  # 提醒过了也没有提交：把最终回复当作汇报，照样做机械检查
        deliver.result = {"summary": out["text"], "failed": deliver.problems({"summary": out["text"]})}
    report = deliver.report()
    headline, decisions = deliver.headline(), deliver.decisions()
    j.post(ALL, new_message(role_id, "text", report, id=msg_id, reasoning=out["reasoning"], tools=out["tools"],
                            files=out["files"], task_id=task["id"], rework=task.get("round") if rework else None,
                            headline=headline, decisions=decisions))
    if rework or view.get("resync") or view.get("interrupted"):  # 返工、同步、继续时是在原有文件上改
        files = sorted(set(task.get("files") or []) | set(out["files"]))
    else:
        files = out["files"]
    result = {**task, "result": report[:QUICK_RESULT_CHARS if task.get("quick") else 3000], "files": files, "resync": {}, "interrupted": False, "worker": None,
              "limited": False, "extensions": 0, "continue_note": "", "responses": deliver.result.get("responses") or [],
              "headline": headline, "decisions": decisions}
    failed = deliver.result.get("failed")
    if failed:  # 交付检查几次都没通过：交给 CEO（接受现在的版本 / 附意见再改 / 放弃）
        record = {"round": task.get("round", 0), "verdict": "checks", "issues": [{"text": p} for p in failed],
                  "summary": f"交付检查提交了 {deliver.rejects + 1} 次仍没有通过"}
        result.update(status="escalated", reviews=list(task.get("reviews") or []) + [record])
    else:
        result.update(status="review" if task.get("reviewer") else "done", run_from=None)
        try:
            made, extra = make_concise(j, task)
        except Cancelled:
            return {**done, "cancelled": True}
        usage = add_usage(usage, extra)
        # 简洁版没压下来时文件已经删掉：任务的文件列表里也去掉
        kept = [f for f in result["files"] if not condense.is_concise(f) or j.company.resolve(f).is_file()]
        result["files"] = sorted(set(kept) | set(made))
    return {**done, "task": result, "files": out["files"], "usage": usage}


def make_concise(j: JobCtx, task: Task) -> tuple[list[str], dict]:
    """产出超过建议篇幅时，完整版保留，另外生成简洁版（以前生成过的，完整版改过后跟着更新）。返回 (简洁版路径, 用量)。
    生成失败不影响交付：完整版才是正式产出。"""
    made, usage = [], {}
    for d, text in condense.needed(j.company, task.get("deliverables") or []):
        size, path = len(text.strip()), condense.concise_path(d["path"])
        why = (f"有 {size} 字，超过建议的 {d['max_chars']} 字：完整版保留，另外生成一份简洁版 `{path}`"
               if size > d["max_chars"] else f"改过了，简洁版 `{path}` 跟着更新")
        j.post(ALL, new_message("system", "event", f"`{d['path']}` {why}", task_id=task["id"]))
        try:
            made_path, used, note = condense.condense(j.company, task["assignee"], d, text, j.cancel)
        except Cancelled:
            raise
        except Exception as e:  # noqa: BLE001
            j.post(ALL, new_message("system", "error", f"生成 `{path}` 失败：{type(e).__name__}: {e}（完整版不受影响）",
                                    task_id=task["id"]))
            continue
        usage = add_usage(usage, {task["assignee"]: used})  # 生成简洁版的调用记在负责人名下
        j.post(ALL, new_message("system", "event", f"`{d['path']}`：{note}", task_id=task["id"]))
        if made_path:
            made.append(made_path)
    return made, usage


class Deliver:
    """finish_task：负责人提交交付时的机械检查。不通过就把哪里不合格告诉它，在同一段对话里改好再提交（不占审查轮数）；
    提交 j.rejects 次还不通过，照收并交给 CEO。"""

    def __init__(self, j: JobCtx, task: Task, view: Task):
        self.j, self.task = j, task
        self.rework = view["status"] == "rework"
        self.issues = open_issues(view) if self.rework else []
        self.ceo = self.rework and bool(pending_note(view))
        self.resync = bool(view.get("resync"))
        self.written: set[str] = set()   # 这次执行写过的文件
        self.rejects = 0
        self.result: dict | None = None

    def observe(self, summary: dict) -> None:
        if summary.get("ok") and summary.get("name") in ("write_file", "edit_file") and summary.get("path"):
            self.written.add(summary["path"])

    def finished(self) -> bool:
        return self.result is not None

    def problems(self, args: dict) -> list[str]:
        company, deliverables = self.j.company, self.task.get("deliverables") or []
        out = []
        if not str(args.get("summary") or "").strip():
            out.append("summary 不能为空：写明做了什么、产出了哪些文件、关键决定和待确认的事项")
        out += checks.deliverable_problems(company, deliverables)
        out += checks.files_format_problems(company, self.written | {d["path"] for d in deliverables})
        if self.rework:
            files = sorted(set(self.task.get("files") or []) | self.written | {d["path"] for d in deliverables})
            tag = self.task.get("base_tag")
            changed = bool(company.diff_since(tag, files)) if tag else bool(self.written)
            out += checks.rework_problems(len(self.issues), self.ceo, args.get("responses"), changed)
        if self.resync and not str(args.get("upstream") or "").strip():
            out.append("上游有改动：在 upstream 里写明你的产出受了哪些影响、怎么处理的（不受影响也要说明原因）")
        return out

    def __call__(self, args: dict) -> str:
        if self.result is not None:
            return "已经提交过了"
        problems = self.problems(args)
        if problems and self.rejects + 1 < self.j.rejects:
            self.rejects += 1
            return ("错误：交付检查没通过，请改好后重新调用 finish_task：\n" + "\n".join(f"- {p}" for p in problems)
                    + f"\n（还可以提交 {self.j.rejects - self.rejects} 次，之后会交给 CEO 处理）")
        self.result = {**args, "failed": problems}
        return "交付检查仍没有通过，已交给 CEO 处理" if problems else "已提交，交付检查通过"

    def headline(self) -> str:
        """给 CEO 的一两句话结论；没写的话取汇报的第一句。"""
        r = self.result or {}
        text = str(r.get("headline") or "").strip()
        if not text:
            first = next((line.strip(" #*-") for line in str(r.get("summary") or "").splitlines() if line.strip(" #*-")), "")
            text = first.split("。")[0] + ("。" if "。" in first else "")
        return text[:200]

    def decisions(self) -> list[str]:
        items = (self.result or {}).get("decisions") or []
        return [str(x).strip()[:200] for x in items if str(x).strip()][:10] if isinstance(items, list) else []

    def report(self) -> str:
        """频道里的交付消息，也是审查人看到的汇报：汇报 + 逐条回应 + 上游改动的处理。"""
        r = self.result or {}
        parts = [str(r.get("summary") or "").strip()]
        responses = [x for x in r.get("responses") or [] if isinstance(x, dict)]
        if responses:
            parts += ["", "**逐条处理：**"]
            for x in responses:
                name = "CEO 的意见" if str(x.get("no")) == "0" else f"第 {x.get('no')} 条"
                parts.append(f"- {name}：{'已修改' if x.get('action') == 'fixed' else '不同意'}。{x.get('detail', '')}")
        if str(r.get("upstream") or "").strip():
            parts += ["", f"**上游改动的处理：**{r['upstream'].strip()}"]
        return "\n".join(parts).strip() or "（没有汇报）"


def _limited(j: JobCtx, task: Task, out: dict, msg_id: str) -> Task:
    """执行任务步数用完：对话停在 interrupt，写好的文件都保留。没超过次数就退回去马上接着做，否则交给 CEO 裁决。"""
    role_id = task["assignee"]
    files = sorted(set(task.get("files") or []) | set(out["files"]))
    base = {**task, "files": files, "interrupted": True, "limited": True}
    if task.get("extensions", 0) < j.extensions:
        j.post(ALL, new_message("system", "event", f"{_role_name(role_id)}的《{task['title']}》步数用完了，"
                                "接着做（对话和写好的文件都保留）", task_id=task["id"]))
        return {**base, "status": task.get("run_from") or "pending", "extensions": task.get("extensions", 0) + 1}
    j.post(ALL, new_message(role_id, "text", out["text"], id=msg_id, reasoning=out["reasoning"], tools=out["tools"],
                            files=out["files"], task_id=task["id"]))
    record = {"round": task.get("round", 0), "verdict": "incomplete", "issues": [],
              "summary": f"连续 {j.extensions + 1} 次用完步数，还没有做完"}
    return {**base, "status": "escalated", "result": out["text"][:3000],
            "reviews": list(task.get("reviews") or []) + [record]}


# ---------------------------------------------------------------- 审查

def run_review(j: JobCtx) -> dict:
    task, payload = j.task, j.payload()
    reviewer = task["reviewer"]
    round_no = task.get("round", 0) + 1
    msg_id = new_id()
    j.emit({"type": "status", "role": reviewer, "status": "working", "task_id": task["id"]})
    files = sorted(f for f in set(task.get("files") or []) | {d["path"] for d in task.get("deliverables") or []}
                   if not condense.is_concise(f))  # 简洁版是系统生成的，不审查
    tag = task.get("base_tag")
    previous, diff, scope = None, "", None
    must_read = [f for f in files if _exists(j.company, f)]
    if tag and task.get("reviewed"):  # 复审：只看上一轮的问题和这次的改动
        previous = open_issues(task)
        full = j.company.diff_since(tag, files, limit=10 ** 9)
        diff = full if len(full) <= DIFF_LIMIT else full[:DIFF_LIMIT] + f"\n…（改动对比太长，只显示前 {DIFF_LIMIT} 字，完整内容请读文件）"
        scope = checks.added_text(full)
        if len(full) <= DIFF_LIMIT:
            must_read = []  # 改动都在说明里了，不强制重读全文
    closing = bool(task.get("closing")) and previous is not None
    guard = ReviewGuard(j, task, files, previous, scope, must_read, closing=closing)

    notes = j.log.notes(j.directive.get("id"), reviewer)
    brief = (review_brief(payload, round_no, j.company, previous, diff, must_read)
             + ("\n\n" + CLOSING_RULE if closing else "") + ("\n\n" + format_notes(notes) if notes else ""))
    users = [t for t in j.tasks.values() if task["id"] in t.get("depends_on", []) + t.get("starts_after", [])]
    if users and previous is None:  # 第一次审查：这份产出是别人的依据，站在使用者的角度把关，下游开始前把会让人做错的问题找出来
        brief += ("\n\n这份产出是后面这些任务的依据：" + "、".join(f"{_role_name(t['assignee'])}《{t['title']}》" for t in users)
                  + "。请站在它们负责人的角度检查：能不能据此直接开展工作？有没有会让他们做错的矛盾、"
                  "缺失的关键规则？这些是必须修改的问题，要在这一步找出来，不要留到下游做到一半再返工。")
    done = {"type": "job_done", "job": j.job, "kind": "review", "task_id": task["id"]}
    try:
        out = run_role(j.company, reviewer, [{"role": "user", "content": brief}], j.emit, msg_id, ALL, j.cancel,
                       j.max_role_steps, listen=j.inbox.listener(reviewer, {m["id"] for m in notes}),
                       extra_tools=[REVIEW_TOOL], handlers={"submit_review": guard}, stop=j.stop, saver=j.saver,
                       readonly=True, finished=guard.finished, observe=guard.observe,  # 审查只看不改：结论用 submit_review 提交
                       require_finish=True, finish_tool="submit_review")  # 没提交就想结束时提醒，最后一步只能提交
    except Cancelled:
        return {**done, "cancelled": True}
    except Exception as e:  # 审查人调用失败：不卡住流程，交给 CEO 裁决
        out = {"text": f"审查失败：{type(e).__name__}: {e}", "reasoning": "", "tools": [], "files": [],
               "usage": {"prompt": 0, "completion": 0, "cached": 0, "calls": 0}}
        guard.verdict = {"verdict": "error", "issues": [], "summary": out["text"]}
    finally:
        j.emit({"type": "status", "role": reviewer, "status": "idle"})
    if out.get("paused"):  # 所审的任务受变更影响：这次审查不算数，之后重新审查
        (j.saver or _DEFAULT_SAVER).delete_thread(out["thread"])
        return {**done, "paused": True, "usage": {reviewer: out["usage"]}}
    verdict = guard.verdict or {"verdict": "unclear", "issues": [], "summary": "审查人没有提交明确结论"}
    # 没有提交结论：不能当作通过，也不能凭空退回，交给 CEO 判断
    record = {"round": round_no, **verdict}
    if pending_note(task):
        record["ceo_note"] = pending_note(task)
    used = round_no - task.get("round_base", 0)  # 这一轮返工以来审查了几轮
    if verdict["verdict"] == "pass":
        status = "done"
    elif verdict["verdict"] == "changes" and used < j.max_review_rounds:
        status = "rework"
    elif verdict["verdict"] == "changes" and used < j.max_review_rounds + EXTRA_ROUNDS and converging(verdict):
        status = "rework"  # 在收敛：多给一轮，不用 CEO 裁决
        record["extra"] = True
    else:
        status = "escalated"
    closing_next = status == "rework" and converging(verdict) and not closing  # 下一轮复审收尾
    j.post(ALL, new_message(reviewer, "review", out["text"], id=msg_id, reasoning=out["reasoning"], tools=out["tools"],
                            files=out["files"], task_id=task["id"], verdict=verdict["verdict"], issues=verdict["issues"],
                            round=round_no, summary=verdict["summary"], suggestions=verdict.get("suggestions"),
                            previous=verdict.get("previous")))
    if closing_next:
        extra = "多给一轮，先不用 CEO 裁决；" if record.get("extra") else ""
        j.post(ALL, new_message("system", "event", f"《{task['title']}》的复审在收敛（上一轮的问题都解决了，只剩 "
                                f"{len(verdict['issues'])} 个新问题）：{extra}下一轮是收尾复审，只核对这几个问题，新发现记为建议",
                                task_id=task["id"]))
    result = {**task, "status": status, "round": round_no, "reviews": list(task.get("reviews") or []) + [record],
              "closing": closing_next}
    if verdict["verdict"] in ("pass", "changes"):  # 审查过的版本拍快照：复审只看之后的改动，返工核对文件是否真的改过
        result.update(base_tag=j.company.snapshot(f"{task['id']}-r{round_no}-{new_id()[:4]}", files), reviewed=True,
                      note_checked=True)  # CEO 的意见已经核对过（记在这轮审查里），之后的返工不再要求回应它
    return {**done, "task": result, "usage": {reviewer: out["usage"]}}


DIFF_LIMIT = 12_000  # 复审说明里改动对比最多放多少字，太长时要求审查人读文件


CLOSING_RULE = ("# 这是收尾复审\n上一轮复审时这份产出已经在收敛：之前的问题都解决了，只剩上面列出的几个问题。这一轮只核对"
                "这几个问题有没有解决（previous 逐条标 resolved / unresolved），**不再提出新的必须修改的问题**：新发现的问题写进 "
                "suggestions，不会退回修改。上面的问题都解决了就给 pass。")


def converging(verdict: dict) -> bool:
    """复审在收敛：上一轮的问题逐条确认都解决了，这次只剩很少的新问题（出在改动里）。"""
    previous = verdict.get("previous") or []
    return (bool(previous) and all(p["status"] == "resolved" for p in previous)
            and 0 < len(verdict.get("issues") or []) <= CONVERGING_ISSUES)


def _exists(company: Company, path: str) -> bool:
    try:
        return company.resolve(path).is_file()
    except Exception:
        return False


def _issue_text(i: dict) -> str:
    text = f"{i['where']}：{i['problem']}" if i.get("where") else i["problem"]
    if i.get("expected"):
        text += f" 期望：{i['expected']}"
    if i.get("quote"):
        text += f"（原文：「{i['quote'][:80]}」）"
    return text


class ReviewGuard:
    """submit_review：审查结论的机械核对，防止审查偷懒或编造。不合格退回给审查人重交，最后一次照收（去掉不合格的部分）。

    - 第一次审查必须读过要审的文件
    - 每条问题引用的原文要真的在文件里
    - 复审要逐条说明上一轮的问题解决了没有；新问题要出在这次改动的部分，改动之外的记为建议（保证收敛）
    - 结论和问题要一致：有必须修改的问题（或没解决的上轮问题）就是 changes，否则是 pass
    """

    def __init__(self, j: JobCtx, task: Task, files: list[str], previous: list[dict] | None, scope: str | None,
                 must_read: list[str], closing: bool = False):
        self.j, self.task, self.files, self.closing = j, task, files, closing
        self.previous, self.scope, self.must_read = previous, scope, must_read
        self.reads: set[str] = set()
        self.rejects = 0
        self.verdict: dict | None = None

    def observe(self, summary: dict) -> None:
        if summary.get("name") == "read_file" and summary.get("ok") and summary.get("path"):
            self.reads.add(_norm_path(summary["path"]))

    def finished(self) -> bool:
        return self.verdict is not None

    def _texts(self) -> list[str]:
        """可以被引用的文字：这个任务的产出，以及这条指令下其他任务的产出（问题可能出在和上游的矛盾上）。"""
        paths = set(self.files)
        for t in self.j.tasks.values():
            paths |= set(t.get("files") or [])
        out = []
        for p in sorted(paths):
            try:
                out.append(self.j.company.read_file(p))
            except Exception:
                continue
        return out

    def __call__(self, args: dict) -> str:
        if self.verdict is not None:
            return "已经提交过了"
        v = args.get("verdict")
        if v not in ("pass", "changes"):
            return "错误：verdict 只能是 pass 或 changes"
        issues = []
        for i in args.get("issues") or []:
            if isinstance(i, dict):
                issues.append({k: str(i.get(k) or "").strip() for k in ("where", "quote", "problem", "expected")})
            elif str(i).strip():
                issues.append({"where": "", "quote": "", "problem": str(i).strip(), "expected": ""})
        texts = self._texts()
        bad = [n for n, i in enumerate(issues, 1) if not i["problem"] or not checks.quote_found(i["quote"], texts)]
        statuses: dict[int, dict] = {}
        for p in args.get("previous") or []:
            try:
                statuses[int(p.get("no"))] = p
            except (TypeError, ValueError, AttributeError):
                continue
        prev = self.previous or []
        missing = [n for n in range(1, len(prev) + 1) if n not in statuses]
        unresolved = [n for n in range(1, len(prev) + 1) if statuses.get(n, {}).get("status") == "unresolved"]

        problems = []
        unread = [p for p in self.must_read if _norm_path(p) not in self.reads]
        if unread:
            problems.append(f"还没有读过要审查的文件：{'、'.join(unread)}。先用 read_file 读完再提交")
        if bad:
            problems.append("这些问题的 quote 在文件里找不到（或者没写 problem）：" + "、".join(f"第 {n} 条" for n in bad)
                            + "。quote 要逐字复制原文；缺了什么就复制应该写它的那一节的标题")
        if missing:
            problems.append("previous 要逐条给出上一轮问题的处理情况（resolved / unresolved），缺："
                            + "、".join(f"第 {n} 条" for n in missing))
        if v == "pass" and (issues or unresolved):
            problems.append("结论是 pass，却列出了必须修改的问题或没解决的上轮问题：要么改成 changes，要么把问题移到 suggestions")
        if v == "changes" and not issues and not unresolved:
            problems.append("结论是 changes，却没有列出必须修改的问题：在 issues 里写出来，或者在 previous 里标出没解决的上轮问题")
        if problems and self.rejects + 1 < self.j.rejects:
            self.rejects += 1
            return ("错误：审查结论没通过核对，请改好后重新调用 submit_review：\n" + "\n".join(f"- {p}" for p in problems)
                    + f"\n（还可以提交 {self.j.rejects - self.rejects} 次）")

        # 收下结论（核对次数用完时：去掉不合格的问题，没给出处理情况的上轮问题按没解决算）
        unresolved = sorted(set(unresolved) | set(missing))
        valid = [i for n, i in enumerate(issues, 1) if n not in bad]
        dropped = [i for n, i in enumerate(issues, 1) if n in bad]
        blocking, moved, closing = [], [], []
        for i in valid:  # 复审时新问题要出在这次改动的部分；收尾复审不收新问题
            if self.closing:
                closing.append(i)
            else:
                (moved if self.scope is not None and not checks.quote_found(i["quote"], [self.scope]) else blocking).append(i)
        carried = [{"text": f"（上轮第 {n} 条没有解决）{prev[n - 1]['text']}"
                            + (f" —— {statuses[n]['note']}" if statuses.get(n, {}).get("note") else "")} for n in unresolved]
        final = carried + [{"text": _issue_text(i), "quote": i["quote"]} for i in blocking]
        verdict = "changes" if final else "pass"
        suggestions = [str(s).strip() for s in args.get("suggestions") or [] if str(s).strip()]
        suggestions += [_issue_text(i) + "（不在这次改动的范围内，记为建议）" for i in moved]
        suggestions += [_issue_text(i) + "（收尾复审的新发现，记为建议）" for i in closing]
        suggestions += [_issue_text(i) + "（引用的原文核对不上，没有作为必须修改的问题）" for i in dropped]
        self.verdict = {"verdict": verdict, "issues": final, "summary": str(args.get("summary") or ""),
                        "suggestions": suggestions,
                        "previous": [{"no": n, "status": "unresolved" if n in unresolved else "resolved",
                                      "note": str(statuses.get(n, {}).get("note") or "")} for n in range(1, len(prev) + 1)]}
        msg = "已提交审查结论"
        if moved:
            msg += f"；有 {len(moved)} 条问题不在这次改动的范围内，已记为建议"
        if verdict != v:
            msg += f"；按规则结论是{'通过' if verdict == 'pass' else '要求修改'}"
        return msg


def _norm_path(path: str) -> str:
    return str(path or "").strip().replace("\\", "/").lstrip("./")


# ---------------------------------------------------------------- 私聊

def topic_brief(task: Task) -> str:
    """围绕某项工作讨论时，放在对话开头的上下文：任务要求、汇报、产出文件、每轮审查意见、CEO 的意见。"""
    parts = [f"# CEO 想和你讨论你负责过的这项工作：《{task['title']}》", f"任务要求：{task['detail']}",
             f"状态：{task['status']}", f"产出文件：{'、'.join(task.get('files') or []) or '无'}",
             f"你的汇报：{(task.get('result') or '（还没有）')[:3000]}"]
    for r in task.get("reviews") or []:
        parts.append(f"第 {r['round']} 轮审查（{_role_name(task.get('reviewer', ''))}）："
                     f"{'通过' if r['verdict'] == 'pass' else '要求修改'}。{r.get('summary', '')}")
        parts += [f"  - {i['text']}" for i in r.get("issues", [])]
    if task.get("ceo_note"):
        parts.append(f"CEO 之前的意见：{task['ceo_note']}")
    parts += ["", "需要时用 read_file 查看产出文件。这次是讨论：回答 CEO 的问题，说明你的考虑，指出你认为可以改进的地方；"
              "不要修改文件。如果需要改，说清楚你建议怎么改，CEO 确认后会安排返工（依赖它的同事也会跟着同步）。"]
    return "\n".join(parts)


def run_chat(j: JobCtx) -> dict:
    """CEO 单独和某个角色对话：带着这个频道最近的对话。和团队的工作同时进行，不经过调度图。

    围绕某项工作讨论时（topic），只带这项工作的讨论记录，开头放上这项工作的完整上下文，并且只能看不能改：
    要改动走变更单（调度助手整理、CEO 确认），这样依赖它的同事也会跟着暂停和同步。
    """
    role_id = j.role
    topic_id = j.topic["id"] if j.topic else None
    history = [m for m in j.log.read(role_id) if m.get("kind") == "text" and m.get("topic") == topic_id][-DIRECT_HISTORY:]
    conversation = [{"role": "user" if m["sender"] == "ceo" else "assistant", "content": m["text"]} for m in history]
    while conversation and conversation[0]["role"] != "user":  # 对话必须从用户消息开始
        conversation.pop(0)
    if j.topic and conversation:
        conversation[0] = {"role": "user", "content": topic_brief(j.topic) + "\n\n# CEO 说\n" + conversation[0]["content"]}
    msg_id = new_id()
    j.emit({"type": "status", "role": role_id, "status": "working", "chat": True})
    done = {"type": "chat_done", "job": j.job, "role": role_id}
    try:
        out = run_role(j.company, role_id, conversation, j.emit, msg_id, role_id, j.cancel, j.max_role_steps,
                       listen=j.inbox.listener(role_id, set()), saver=j.saver, readonly=bool(j.topic))
    except Cancelled:
        return {**done, "cancelled": True}
    except Exception as e:
        j.post(role_id, new_message(role_id, "error", f"调用失败：{type(e).__name__}: {e}", id=msg_id))
        return done
    finally:
        j.emit({"type": "status", "role": role_id, "status": "idle"})
    j.post(role_id, new_message(role_id, "text", out["text"], id=msg_id, reasoning=out["reasoning"],
                                tools=out["tools"], files=out["files"], topic=topic_id))
    return {**done, "usage": {role_id: out["usage"]}}


# ---------------------------------------------------------------- 和同事协作

REPORT_TOOL = {"type": "function", "function": {
    "name": "report_upstream_issue",
    "description": "只在发现上游同事的产出有**严重问题**时上报：按它做会做错（前后矛盾、规则互相冲突），或者缺了关键信息根本做不下去，"
                   "并且问题确实出在上游。小的遗漏、措辞、可以合理推断的细节不要上报：自己做合理假设，写进你的文档继续做。"
                   "上报会让上游返工、依赖它的同事全部暂停重做，代价很大。每次执行只能上报一次，把所有问题一次写全。"
                   "上报后先按最合理的理解继续，并在文档里写明这个假设。",
    "parameters": {"type": "object", "properties": {
        "task": {"type": "string", "description": "有问题的上游任务的标题（任务说明里列出的）"},
        "problem": {"type": "string", "description": "具体是什么问题、在哪里（文件和章节）"},
        "impact": {"type": "string", "description": "为什么是上游的问题、不改会导致什么错误（说不清就不要上报）"},
        "suggestion": {"type": "string", "description": "你建议对方怎么改"}},
        "required": ["task", "problem", "impact"]}}}

ASK_STEPS = 6  # 被问的同事最多读几步；最后一步不给工具，必须给出回答

COLLAB_RULE = ("# 和同事协作\n"
               "- 上游的产出已经审查过，原则上按它做。小的遗漏、可以合理推断的细节：自己做合理假设，写进文档的"
               "「待确认」里继续做，不要上报\n"
               "- 只有严重问题（按它做会做错、或者根本做不下去，并且确实是上游的问题）才用 report_upstream_issue 上报，"
               "每次执行只能上报一次；不要自己改同事的文件，也不要将错就错\n"
               "- 需要确认同事的意图或细节：用 ask_colleague 问他（次数有限），不要靠猜")


def ask_tool(company: Company, role_id: str, asks: int = 3) -> dict:
    others = [r for r in company.role_ids if r != role_id]
    return {"type": "function", "function": {
        "name": "ask_colleague",
        "description": f"问同事一个问题，例如需求里某条规则的含义、设计里某个交互的意图。对方会根据自己的产出简短回答。最多问 {asks} 次。",
        "parameters": {"type": "object", "properties": {
            "role": {"type": "string", "enum": others, "description": "问谁：" + "、".join(f"{r}（{_role_name(r)}）" for r in others)},
            "question": {"type": "string", "description": "具体的问题"}},
            "required": ["role", "question"]}}}


class Collab:
    """执行任务时和同事协作的两个工具：上报上游的问题（做成调整建议，CEO 确认后上游返工）、问同事。"""

    def __init__(self, j: JobCtx, role_id: str, task: Task):
        self.j, self.role_id, self.task = j, role_id, task
        self.usage: dict[str, dict] = {}   # 被问的同事的用量
        self.asks = 0
        self.reported = False              # 每次执行只能上报一次
        self.pending: dict[str, list[str]] = {}  # 这一步收集到的上报：上游任务 id -> 问题（这一步结束时合并成调整建议）

    def upstream(self) -> list[Task]:
        ids = list(self.task.get("depends_on") or []) + list(self.task.get("starts_after") or [])
        return [self.j.tasks[i] for i in ids if i in self.j.tasks]

    def report(self, args: dict) -> str:
        ups = self.upstream()
        name, problem = str(args.get("task") or "").strip(), str(args.get("problem") or "").strip()
        impact = str(args.get("impact") or "").strip()
        if self.reported:
            return ("这次执行已经上报过一次了，不能再上报。其余的问题自己做合理假设，写进文档的「待确认」里，"
                    "并在最后的回复里说明")
        if not problem or not impact:
            return "错误：需要写清楚是什么问题（problem）和不改会导致什么错误（impact）；说不清后果的就不是严重问题，不要上报"
        match = [t for t in ups if name and (name in t["title"] or t["title"] in name)]
        if len(match) != 1:
            titles = "、".join(f"《{t['title']}》" for t in ups)
            return f"错误：task 要写成你依赖的任务之一的标题：{titles}"
        text = f"{problem}\n不改的后果：{impact}"
        if str(args.get("suggestion") or "").strip():
            text += f"\n建议：{args['suggestion'].strip()}"
        self.pending.setdefault(match[0]["id"], []).append(text)
        return ("已上报给 CEO：全公司频道里会有一张调整建议，CEO 确认后对方返工。现在先按你认为最合理的理解继续完成任务，"
                "并在文档里写明这个假设；如果 CEO 决定让对方返工，你会被暂停，等对方改完后带着改动对比再同步。")

    def flush(self) -> None:
        """这一步的工具都执行完了：同一步里的上报合并，每个上游任务一张调整建议（同一步连着报几条也不会丢）。"""
        if not self.pending:
            return
        from .triage import describe  # 避免循环引用
        for tid, problems in self.pending.items():
            target = self.j.tasks[tid]
            feedback = f"{_role_name(self.role_id)}发现：" + ("\n\n".join(f"{i}. {p}" for i, p in enumerate(problems, 1))
                                                          if len(problems) > 1 else problems[0])
            orders = [{"kind": "rework", "task_id": tid, "feedback": feedback}]
            text, affected = describe(orders, list(self.j.tasks.values()))
            orders = [{**orders[0], "title": target["title"], "assignee": target["assignee"]}]
            self.j.post(ALL, new_message("manager", "proposal", text, orders=orders, affected=affected,
                                         reporter=self.role_id, reporter_task=self.task["title"], task_id=self.task["id"]))
        self.pending, self.reported = {}, True

    def ask(self, args: dict) -> str:
        role, question = str(args.get("role") or "").strip(), str(args.get("question") or "").strip()
        if role not in self.j.company.role_ids or role == self.role_id:
            return "错误：role 要写公司里另一位同事的角色 id"
        if not question:
            return "错误：需要写清楚问题（question）"
        if self.asks >= self.j.asks:
            return f"这次已经问了 {self.j.asks} 次，不能再问了。按你最合理的理解继续，并在文档里写明假设"
        self.asks += 1
        q = new_message(self.role_id, "text", f"问{_role_name(role)}：{question}", task_id=self.task["id"], consult=True)
        self.j.post(ALL, q)
        work = [t for t in self.j.tasks.values() if role in (t["assignee"], t.get("reviewer"))]
        mine = "\n".join(f"- 《{t['title']}》（{'审查' if t['assignee'] != role else '负责'}，{t['status']}）："
                         f"产出 {'、'.join(t.get('files') or []) or '无'}" for t in work) or "（这条指令下你还没有任务）"
        prompt = (f"同事{_role_name(self.role_id)}正在做《{self.task['title']}》，问你：{question}\n\n"
                  f"你在这条指令下的工作：\n{mine}\n\n"
                  "需要时用 read_file 查看产出，然后简短、具体地回答。不确定的就说不确定，不要编造；这次只回答问题，不要修改文件。")
        msg_id = new_id()
        try:
            out = run_role(self.j.company, role, [{"role": "user", "content": prompt}], self.j.emit, msg_id, ALL,
                           self.j.cancel, ASK_STEPS, readonly=True, answer_last=True)
        except Cancelled:
            raise
        except Exception as e:
            return f"错误：问同事失败（{type(e).__name__}: {e}）"
        self.j.post(ALL, new_message(role, "text", out["text"], id=msg_id, reasoning=out["reasoning"], tools=out["tools"],
                                     task_id=self.task["id"], consult=True, reply_to=q["id"]))
        u = self.usage.setdefault(role, {"prompt": 0, "completion": 0, "cached": 0, "calls": 0})
        for k in u:
            u[k] += out["usage"].get(k, 0)
        return f"{_role_name(role)}的回答：{out['text']}"


RUNNERS = {"work": run_work, "review": run_review, "chat": run_chat}
