"""执行引擎：一家公司的调度图 + 并行的工作运行。网页运行时（web/runtime.py）和测试都通过它驱动公司。

- 事件队列：所有事件（下令、审批、任务做完、变更单、裁决、停止……）按顺序交给调度图，一次处理一个，
  调度图的 state 不会被并发修改
- 工作运行：调度图派发的执行、审查在各自的线程里并行运行，结束时把 job_done 放回事件队列。
  一个任务做完，调度图马上派发依赖它的任务，不用等同时在做的其他任务
- 私聊：和团队的工作同时进行，不经过调度图
- 停止：所有工作运行在当前这一步停下后，送一个 stopped 事件，把没完成的任务标记为已取消

回调：on_event(ev) 收到实时事件（流式文字、工具调用、新消息、角色状态）；
on_update(update) 收到调度图每个节点对任务、用量的更新；on_idle() 在全部停下时调用。
"""
import queue
import threading
from typing import Callable

from .agents import Cancelled
from .changes import Either, TaskStops, affected_running, should_stop
from .company import Company
from . import effort
from .graph import ALL, Ctx
from .inbox import Inbox
from .jobs import RUNNERS, JobCtx
from . import tracing
from .knowledge import service as kb_service
from .roles import ROLES
from .messages import MessageLog
from .state import new_id

_STOP = object()
PRUNE_EVERY = 20       # 每处理这么多个事件清理一次调度图的旧检查点
KEEP_CHECKPOINTS = 30  # 调度图只保留最近这么多个检查点（读取状态只用最新的，旧的只会让数据库越来越大）


class Engine:
    def __init__(self, company: Company, graph, *, saver=None, auto_approve: bool = False,
                 on_event: Callable[[dict], None] | None = None, on_update: Callable[[dict], None] | None = None,
                 on_idle: Callable[[], None] | None = None,
                 max_role_steps: int | None = None, max_review_rounds: int | None = None):
        self.company, self.graph = company, graph
        self.cfg = {"configurable": {"thread_id": company.id}}
        self.log = MessageLog(graph.store, company.id)
        self.saver = saver
        self.auto_approve = auto_approve
        self.on_event = on_event or (lambda ev: None)
        self.on_update = on_update or (lambda update: None)
        self.on_idle = on_idle or (lambda: None)
        self.max_role_steps, self.max_review_rounds = max_role_steps, max_review_rounds  # 指定了就不按档位（测试用）
        self.cancel = threading.Event()   # CEO 停止了公司
        self.chat_cancel = threading.Event()  # 团队没在工作时点停止：只停正在进行的私聊
        self.stops = TaskStops()           # 按任务暂停
        self.inbox = Inbox()               # 插话：正在工作的角色下一步就收到
        self.jobs: dict[str, dict] = {}    # 正在运行的工作：job id -> {kind, task_id / role}
        self._queue: queue.Queue = queue.Queue()
        self._lock = threading.Condition()
        self._pending = 0                  # 还没处理完的事件数
        self._handled = 0                  # 处理过的事件数（定期清理旧检查点）
        self._stopping = False
        self._thread = threading.Thread(target=self._loop, daemon=True, name=f"engine-{company.id}")
        self._thread.start()
        # 知识库同步任务记录和指令结论时，从这里读任务看板和全公司频道
        kb_service.register(company.id, self._kb_records)

    def _kb_records(self) -> dict:
        return {"tasks": dict(self.state().get("tasks") or {}), "channels": {"all": self.log.read(ALL)}}

    # ---------- 对外 ----------

    def submit(self, event: dict) -> None:
        with self._lock:
            self._pending += 1
        self._queue.put(event)

    @property
    def busy(self) -> bool:
        """有任何事在进行（包括私聊）。"""
        with self._lock:
            return bool(self._pending or self.jobs)

    @property
    def team_busy(self) -> bool:
        """团队在执行指令：有事件在处理，或者有执行 / 审查在跑。私聊不算：私聊时照样可以对全公司下新指令。"""
        with self._lock:
            return bool(self._pending or any(j["kind"] != "chat" for j in self.jobs.values()))

    def wait_idle(self, timeout: float | None = 30) -> bool:
        with self._lock:
            return self._lock.wait_for(lambda: not self._pending and not self.jobs, timeout)

    def state(self) -> dict:
        return dict(self.graph.get_state(self.cfg).values or {})

    def working_roles(self) -> dict[str, dict]:
        """正在工作的角色：{角色 id: {"task_id" 或 "chat": True}}。"""
        out = {}
        tasks = self.state().get("tasks", {})
        with self._lock:
            for j in self.jobs.values():
                if j["kind"] == "chat":
                    out[j["role"]] = {"chat": True}
                elif j["task_id"] in tasks:
                    t = tasks[j["task_id"]]
                    out[t["reviewer"] if j["kind"] == "review" else t["assignee"]] = {"task_id": j["task_id"]}
        return out

    def change(self, order: dict) -> None:
        """变更单：受影响、正在执行的任务马上收到暂停信号（在当前这一步停下），调度图马上处理。

        这里按提交时的状态先发一次信号（尽快停下）；调度图处理完变更单后会按处理后的状态再补发一次（见 _coordinate），
        因为队列里可能排着别的事件，在变更单之前又派发了新的任务。
        """
        tasks = [t for t in self.state().get("tasks", {}).values()
                 if t["directive_id"] == (self.state().get("directive") or {}).get("id")]
        for tid in affected_running(tasks, order):
            self.stops.stop(tid)
        self.submit({"type": "change", "order": order})

    def chat(self, role_id: str, topic: str | None = None) -> str:
        """和一个角色私聊：CEO 的消息已经写进它的频道。和团队的工作同时进行。topic：围绕哪项工作讨论（任务 id）。"""
        job = new_id()
        jctx = self._job_ctx(job, "chat", role=role_id)
        jctx.cancel = Either(self.cancel, self.chat_cancel)
        if topic:
            jctx.topic = self.state().get("tasks", {}).get(topic)
        self._run_job(job, {"kind": "chat", "role": role_id}, jctx)
        return job

    def stop(self) -> None:
        """停止整个公司：所有工作运行在当前这一步停下，然后把没完成的任务标记为已取消。

        团队没在执行指令、只有私聊在进行时，只停私聊，不动任务（等裁决、暂停中的任务都保留）。
        """
        with self._lock:
            if not (self._pending or self.jobs):
                return
            team = bool(self._pending or any(j["kind"] != "chat" for j in self.jobs.values()))
            if team:
                self._stopping = True
        if not team:
            self.chat_cancel.set()
            return
        self.cancel.set()
        self._maybe_finish_stop()

    def close(self) -> None:
        kb_service.unregister(self.company.id, self._kb_records)
        self._queue.put(_STOP)

    # ---------- 调度图 ----------

    def _loop(self) -> None:
        while True:
            ev = self._queue.get()
            if ev is _STOP:
                return
            try:
                self._coordinate(ev)
            except Exception as e:  # 不让引擎线程静默崩溃
                self.on_event({"type": "notice", "level": "error", "text": f"调度出错：{type(e).__name__}: {e}"})
            finally:
                self._handled += 1
                if self._handled % PRUNE_EVERY == 0:
                    self._prune_checkpoints()
                with self._lock:
                    self._pending -= 1
                    idle = not self._pending and not self.jobs
                    self._lock.notify_all()
                self._maybe_finish_stop()
                if idle:
                    self.on_idle()

    def _prune_checkpoints(self) -> None:
        """调度图的检查点只保留最近 KEEP_CHECKPOINTS 个（检查点 id 按时间递增）。只动这家公司调度图自己的线程。
        只有 SQLite 存储需要清理（测试、演示用的内存存储随进程结束）；清理失败不影响执行。"""
        saver = self.graph.checkpointer
        conn, lock = getattr(saver, "conn", None), getattr(saver, "lock", None)
        if conn is None or lock is None:
            return
        keep = ("SELECT checkpoint_id FROM checkpoints WHERE thread_id = ? AND checkpoint_ns = '' "
                f"ORDER BY checkpoint_id DESC LIMIT {KEEP_CHECKPOINTS}")
        tid = self.company.id
        try:
            with lock:
                for table in ("writes", "checkpoints"):
                    conn.execute(f"DELETE FROM {table} WHERE thread_id = ? AND checkpoint_ns = '' "
                                 f"AND checkpoint_id NOT IN ({keep})", (tid, tid))
                conn.commit()
        except Exception as e:  # noqa: BLE001  清理只是省空间，出错就下次再试
            self.on_event({"type": "notice", "level": "warn", "text": f"清理旧检查点失败：{type(e).__name__}: {e}"})

    def _coordinate(self, ev: dict) -> None:
        if ev.get("type") == "stopped":
            self._stopping = False
        ctx = Ctx(company=self.company, cancel=self.cancel, auto_approve=self.auto_approve,
                  max_review_rounds=self.limits()["review_rounds"])
        try:
            for mode, chunk in self.graph.stream({"event": ev}, self.cfg, context=ctx, stream_mode=["custom", "updates"]):
                if mode == "custom":
                    self.on_event(chunk)
                else:
                    for update in chunk.values():
                        if update:
                            self.on_update(update)
        except Cancelled:  # 规划、汇报时 CEO 停止了公司
            return
        if ev.get("type") == "stopped":
            self.cancel.clear()
            return
        if self.cancel.is_set():  # 停止中：不再启动新的工作（派发了的任务会在 stopped 里标记为已取消）
            return
        state = self.state()
        if ev.get("type") == "change":  # 按处理后的状态补发暂停信号：变更单之前刚派发的任务也能停下
            for t in state.get("tasks", {}).values():
                if should_stop(t):
                    self.stops.stop(t["id"])
        for job in state.get("dispatch") or []:
            self._run_job(job["job"], job, self._job_ctx(job["job"], job["kind"], state=state, task_id=job["task_id"]))

    # ---------- 工作运行 ----------

    def limits(self) -> dict:
        """公司当前投入档位的限制（effort.py）；构造时指定了步数、审查轮数的以指定的为准。"""
        lim = effort.limits(self.company.effort)
        if self.max_role_steps is not None:
            lim["steps"] = self.max_role_steps
        if self.max_review_rounds is not None:
            lim["review_rounds"] = self.max_review_rounds
        return lim

    def _job_ctx(self, job: str, kind: str, state: dict | None = None, task_id: str = "", role: str = "") -> JobCtx:
        state = state or {}
        lim = self.limits()
        directive = state.get("directive") or {}
        tasks = {t["id"]: t for t in state.get("tasks", {}).values() if t["directive_id"] == directive.get("id")}
        return JobCtx(company=self.company, log=self.log, emit=self.on_event, cancel=self.cancel, job=job, kind=kind,
                      task=tasks.get(task_id), tasks=tasks, directive=directive,
                      stop=self.stops.event(task_id) if task_id else None, inbox=self.inbox, saver=self.saver,
                      role=role, max_role_steps=lim["steps"], max_review_rounds=lim["review_rounds"],
                      extensions=lim["extensions"], rejects=lim["rejects"], asks=lim["asks"])

    def _run_job(self, job: str, info: dict, jctx: JobCtx) -> None:
        with self._lock:
            self.jobs[job] = info
        if info.get("task_id"):
            self.stops.clear(info["task_id"])  # 之前的暂停信号已经处理过了
        span = self._job_span(job, info, jctx)

        def go():
            result = None
            try:
                with tracing.use(span):
                    result = RUNNERS[info["kind"]](jctx)
            except Exception as e:  # 理论上 RUNNERS 自己会处理；兜底，不让任务永远挂着
                text = f"工作运行出错：{type(e).__name__}: {e}"
                self.on_event({"type": "notice", "level": "error", "text": text})
                if info.get("task_id") and jctx.task:
                    # 不能当作"暂停"：那样会马上重新派发，同样的错误会一直重复。执行出错算失败，审查出错交给 CEO 裁决
                    failed = ({**jctx.task, "status": "escalated", "reviews": list(jctx.task.get("reviews") or []) + [
                                  {"round": jctx.task.get("round", 0) + 1, "verdict": "error", "issues": [], "summary": text}]}
                              if info["kind"] == "review" else {**jctx.task, "status": "failed", "result": text, "worker": None})
                    result = {"type": "job_done", "job": job, "kind": info["kind"], "task_id": info["task_id"],
                              "task": failed}
            finally:
                self._end_job_span(span, info, result)
                if result and result.get("type") == "job_done":
                    self.submit(result)
                elif result and result.get("usage"):  # 私聊：只记用量
                    self.submit({"type": "usage", "usage": result["usage"]})
                with self._lock:
                    self.jobs.pop(job, None)
                    idle = not self._pending and not self.jobs
                    if not any(j["kind"] == "chat" for j in self.jobs.values()):
                        self.chat_cancel.clear()
                    self._lock.notify_all()
                self._maybe_finish_stop()
                if idle:
                    self.on_idle()
        threading.Thread(target=go, daemon=True, name=f"job-{job}").start()

    def _job_span(self, job: str, info: dict, jctx: JobCtx):
        """这个工作运行在链路里的步骤：执行 / 审查挂在指令的链路下，私聊自成一条链路。"""
        try:
            name = lambda r: ROLES[r].name if r in ROLES else r  # noqa: E731
            if info["kind"] == "chat":
                root = tracing.root(self.company, f"chat:{job}", f"私聊：{name(jctx.role)}",
                                    kind="chat", **{"opc.role": jctx.role})
                return root
            task, did = jctx.task or {}, (jctx.directive or {}).get("id")
            if not did:
                return None
            root = tracing.root(self.company, f"directive:{did}", f"指令：{(jctx.directive.get('text') or '')[:40]}")
            role = task.get("reviewer") if info["kind"] == "review" else task.get("assignee")
            label = "审查" if info["kind"] == "review" else "执行"
            return root.child(f"{label}《{task.get('title', '')}》（{name(role)}）", "agent",
                              **{"opc.role": role, "opc.task_id": task.get("id"), "opc.job": info["kind"]})
        except Exception:  # 链路记录失败不影响工作
            return None

    @staticmethod
    def _end_job_span(span, info: dict, result: dict | None) -> None:
        if span is None:
            return
        task = (result or {}).get("task") or {}
        if task.get("status"):
            span.set(**{"opc.task_status": task["status"]})
        u = (result or {}).get("usage")
        if isinstance(u, dict) and "prompt" in u:
            span.set(**{"opc.calls": u.get("calls"), "opc.input_tokens": u.get("prompt"), "opc.output_tokens": u.get("completion")})
        failed = task.get("status") == "failed" or (result is None)
        span.finish(error=(task.get("result") or "工作运行没有结果") if failed else None,
                    status="paused" if task.get("status") in ("pending", "rework") and task.get("interrupted") else None)

    def _maybe_finish_stop(self) -> None:
        """停止中，所有工作运行都停下、之前的事件都处理完以后，送一个 stopped 事件收尾。"""
        with self._lock:
            ready = self._stopping and not self.jobs and not self._pending
            if ready:
                self._stopping = False
        if ready:
            self.submit({"type": "stopped", "channel": ALL})
