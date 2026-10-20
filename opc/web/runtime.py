"""Web 运行时：管理当前打开的公司，把浏览器的操作交给执行引擎（engine.py），把引擎的事件推给浏览器。

- 公司的状态（任务看板、用量、等待审批的计划）在调度图的检查点里，消息在 LangGraph 的 Store 里，都直接读，
  不在这里另外维护一份副本
- 对全公司下令、审批、裁决、看板上的返工 / 暂停 / 继续，都作为事件交给引擎；团队工作时对全公司说话是插话
- 私聊：那位成员正在做任务就是插话（下一步就收到），空闲就马上开始和它对话，不用等团队做完；
  可以选一项工作来讨论（topic），那次对话带着这项工作的完整上下文，只能看不能改
- 调度助手（triage.py）：有任务时，CEO 在任何频道说的话先由它判断是不是要改动已有的工作。是的话在那个频道出一张
  确认卡片，CEO 确认后和看板上的操作走同一套变更单；不是的话照常处理。项目经理频道（manager）里它还会回复
"""
import asyncio
import sqlite3
import threading
import time

from langgraph.checkpoint.sqlite import SqliteSaver

from .. import coding, effort, model_settings, providers, web_tools
from ..company import Company, CompanyStore
from ..engine import Engine
from ..graph import ALL, build_graph
from .. import tracing
from ..knowledge import service as kb_service
from ..llm import NETWORK
from ..messages import MessageLog, migrate_legacy, open_sqlite_store
from ..roles import ROLES, library_info, preset_info
from ..state import add_directive_usage, add_usage, new_id, new_message
from ..triage import describe, triage

MANAGER = "manager"   # 项目经理频道：和调度助手对话


class Hub:
    def __init__(self, store: CompanyStore, demo: bool = False, auto_approve: bool = False):
        self.store = store
        self.demo = demo
        self.auto_approve = auto_approve
        conn = sqlite3.connect(store.checkpoint_path, check_same_thread=False)
        self.checkpointer = SqliteSaver(conn)
        self.messages = open_sqlite_store(store.checkpoint_path)  # 消息：同一个数据库文件里的 Store 表
        self.graph = build_graph(self.checkpointer, self.messages)
        self.company: Company | None = None
        self.engine: Engine | None = None
        self.unfinished = False                 # 打开公司时发现上次的执行没跑完，可以接着跑
        self.usage: dict = {}                   # 各角色累计用量（打开公司时从状态读，之后按更新累加）
        self.directive_usage: dict = {}         # 每条指令的用量
        self.run_started: float | None = None   # 这次忙起来的时间，界面显示已用时间
        self.phase_since: dict[str, float] = {}  # 任务 id -> 进入当前状态的时间，界面显示这一步用了多久
        self._phase: dict[str, str] = {}
        self.role_status: dict[str, dict] = {}
        self._clients: dict[asyncio.Queue, asyncio.AbstractEventLoop] = {}
        self._lock = threading.Lock()
        # 模型服务连不上时角色会原地暂停（见 llm.NetworkGate）：暂停和恢复都推送给浏览器
        NETWORK.listeners.append(lambda: self.broadcast(self.status_event()))

    # ---------- 推送 ----------

    def broadcast(self, *events: dict) -> None:
        with self._lock:
            for q, loop in self._clients.items():
                for ev in events:
                    loop.call_soon_threadsafe(q.put_nowait, ev)

    def attach(self, q: asyncio.Queue) -> None:
        with self._lock:
            q.put_nowait(self.home_event())
            if self.company:
                q.put_nowait(self.snapshot_event())
            self._clients[q] = asyncio.get_running_loop()

    def detach(self, q: asyncio.Queue) -> None:
        with self._lock:
            self._clients.pop(q, None)

    def home_event(self) -> dict:
        settings = model_settings.load(self.store.home)
        d = model_settings.default_config(settings)
        return {"type": "home", "companies": self.store.list(), "presets": preset_info(), "library": library_info(),
                "demo": self.demo, "coder": {"available": coding.AVAILABLE, "version": coding.VERSION},
                "web": web_tools.status(),
                "providers": providers.public_list(),
                "models": model_settings.public(settings), "efforts": effort.public(),
                "defaults": {"model": d.model, "base_url": d.base_url, "provider": d.provider,
                             "has_key": bool(d.api_key) or self.demo}}

    # ---------- 状态（直接读调度图和消息记录） ----------

    @property
    def log(self) -> MessageLog:
        return MessageLog(self.messages, self.company.id)

    @property
    def busy(self) -> bool:
        """有任何事在进行（包括私聊）：这时不能切换、删除公司。"""
        return bool(self.engine and self.engine.busy)

    @property
    def team_busy(self) -> bool:
        """团队在执行指令。私聊不算：私聊时照样可以对全公司下新指令。"""
        return bool(self.engine and self.engine.team_busy)

    def state(self) -> dict:
        return self.engine.state() if self.engine else {}

    def directive_tasks(self, state: dict | None = None) -> list[dict]:
        state = state if state is not None else self.state()
        did = (state.get("directive") or {}).get("id")
        return [t for t in state.get("tasks", {}).values() if t["directive_id"] == did]

    @property
    def review(self) -> dict | None:
        """等 CEO 处理的事：计划审批，或者审查谈不拢、需要裁决的任务（一次给一个）。"""
        state = self.state()
        if state.get("approval"):
            return state["approval"]
        t = next((t for t in self.directive_tasks(state) if t["status"] == "escalated"), None)
        if not t:
            return None
        return {"type": "escalation", "directive_id": t["directive_id"], "task_id": t["id"], "title": t["title"],
                "assignee": t["assignee"], "reviewer": t.get("reviewer", ""), "reviews": t.get("reviews", []),
                "result": t["result"][:1500], "files": t["files"]}

    def snapshot_event(self) -> dict:
        c = self.company
        state = self.state()
        view = {k: state.get(k) for k in ("directive", "tasks", "usage", "directive_usage", "plan_summary") if k in state}
        return {"type": "snapshot", "company": c.public_meta(), "coder_available": coding.AVAILABLE,
                "state": {**view, "channels": self.log.all()}, "review": self.review,
                "busy": self.team_busy, "running_target": ALL if self.team_busy else None, "role_status": self.role_status,
                "paused": NETWORK.state, "unfinished": self.unfinished, "files": c.list_files(), **self._timing()}

    def status_event(self) -> dict:
        busy = self.team_busy
        return {"type": "status", "busy": busy, "running_target": ALL if busy else None,
                "review": self.review if self.company else None, "role_status": self.role_status,
                "usage": self.usage, "paused": NETWORK.state if busy else None, "unfinished": self.unfinished,
                **self._timing()}

    def _timing(self) -> dict:
        return {"run_started": self.run_started, "phase_since": self.phase_since, "server_now": time.time()}

    def _track(self, tasks: dict) -> dict:
        """记录任务状态变化的时间，返回这些任务的 {id: 进入当前状态的时间}。"""
        for tid, t in tasks.items():
            if self._phase.get(tid) != t.get("status"):
                self._phase[tid] = t.get("status")
                self.phase_since[tid] = time.time()
        return {tid: self.phase_since[tid] for tid in tasks if tid in self.phase_since}

    def notice(self, text: str, level: str = "error") -> None:
        self.broadcast({"type": "notice", "level": level, "text": text})

    # ---------- 公司 ----------

    def open_company(self, company_id: str) -> str | None:
        if self.busy:
            return "当前公司正在执行任务，请先停止"
        company = self.store.load(company_id)
        if not company:
            return "公司不存在"
        if self.engine:
            self.engine.close()
        self.company = company
        migrate_legacy(self.checkpointer, self.messages, company.id)  # v0.7 以前的消息在检查点里：搬进 Store
        self._migrate_legacy_approval()
        self.engine = Engine(company, self.graph, saver=self.checkpointer, auto_approve=self.auto_approve,
                             on_event=self._on_event, on_update=self._on_update, on_idle=self._on_idle)
        state = self.state()
        self.usage = dict(state.get("usage") or {})
        self.directive_usage = dict(state.get("directive_usage") or {})
        # 上次执行到一半程序被关掉了：还挂着工作运行的任务，或者有没做完、又没有在等 CEO 处理的任务（不会再自己往下走）
        tasks = self.directive_tasks(state)
        stalled = (not state.get("approval") and not any(t["status"] == "escalated" for t in tasks)
                   and any(t["status"] in ("pending", "review", "rework") and not t.get("paused") for t in tasks))
        self.unfinished = any(t.get("job") for t in tasks) or stalled
        self.role_status, self.phase_since, self._phase, self.run_started = {}, {}, {}, None
        return None

    def _migrate_legacy_approval(self) -> None:
        """v0.7 等 CEO 审批计划时，图停在 interrupt 上；现在审批写在 state 的 approval 里。搬过来，否则审批卡片就丢了。"""
        cfg = {"configurable": {"thread_id": self.company.id}}
        tup = self.checkpointer.get_tuple(cfg)
        if not tup or tup.checkpoint.get("channel_values", {}).get("approval"):
            return
        for _, channel, value in tup.pending_writes or []:
            if channel != "__interrupt__":
                continue
            for item in value if isinstance(value, (list, tuple)) else [value]:
                v = getattr(item, "value", item)
                if isinstance(v, dict) and v.get("type") == "plan_review":
                    self.graph.update_state(cfg, {"approval": v}, as_node="plan")
                    return

    def close_company(self) -> None:
        if self.engine:
            self.engine.close()
        self.company, self.engine = None, None

    def delete_company(self, company_id: str) -> None:
        if self.company and self.company.id == company_id:
            if self.busy:
                raise ValueError("公司正在执行任务，请先停止")
            self.close_company()
        kb_service.close(self.store.dir / company_id)  # 先关掉知识库和链路的数据库，否则 Windows 上删不掉公司目录
        tracing.close(self.store.dir / company_id)
        self.store.delete(company_id)
        try:
            self.checkpointer.delete_thread(company_id)  # 同时删除这家公司在图里的历史状态
            MessageLog(self.messages, company_id).delete_all()
        except Exception:
            pass

    # ---------- 知识库（角色、规划和网页共用，见 knowledge/service.py） ----------

    def knowledge(self):
        """当前公司的知识库，先增量同步（公司文件区、资料库、任务记录、指令结论）。没有打开的公司时返回 None。"""
        company = self.company
        if not company:
            return None, {}
        state = {"tasks": dict(self.state().get("tasks") or {}), "channels": {"all": self.log.read(ALL)}}
        return kb_service.get(company), kb_service.sync(company, state)

    # ---------- 处理浏览器消息 ----------

    def handle(self, msg: dict) -> None:
        kind = msg.get("type")
        if kind == "open_company":
            err = self.open_company(str(msg.get("id", "")))
            if err:
                return self.notice(err)
            self.broadcast(self.snapshot_event())
        elif kind == "close_company":
            if self.busy:
                return self.notice("当前公司正在执行任务，请先停止")
            self.close_company()
            self.broadcast(self.home_event(), {"type": "closed"})
        elif kind == "send":
            self.send(str(msg.get("text", "")).strip(), str(msg.get("target") or ALL), msg.get("topic") or None)
        elif kind == "proposal":
            self.resolve_proposal(str(msg.get("channel", "")), str(msg.get("id", "")), str(msg.get("action", "")))
        elif kind == "review":
            self.resolve_review(str(msg.get("action", "")), str(msg.get("feedback", "")))
        elif kind == "resume_run":
            if self.unfinished and self.engine:
                self.unfinished = False
                self._submit({"type": "recover"})  # 没跑完的任务退回去重新派发（执行循环有检查点的接着做）
        elif kind == "task_action":
            self.task_action(str(msg.get("action", "")), str(msg.get("task_id", "")), str(msg.get("feedback", "")))
        elif kind == "stop":
            if self.busy:  # 团队在工作就停止整个公司；只有私聊在进行就只停私聊
                self.engine.stop()
                NETWORK.retry_now()  # 暂停中的角色立即醒来，看到停止标志后退出
        elif kind == "retry_now":
            if self.busy:
                NETWORK.retry_now()

    def _submit(self, event: dict) -> None:
        if not self.busy:
            self.run_started = time.time()
        self.engine.submit(event)
        self.broadcast(self.status_event())

    def send(self, text: str, target: str, topic: str | None = None) -> None:
        if not self.company or not text:
            return
        if target not in (ALL, MANAGER) and target not in self.company.role_ids:
            return self.notice("这个角色不在公司里")
        if target == ALL:
            review = self.review  # 先看有没有等 CEO 处理的事：计划一出来就要审批，这时调度图可能还没完全停下
            if review:
                what = "一份计划等你审批" if review.get("type") == "plan_review" else "一个问题等你裁决"
                return self.notice(f"还有{what}：请先在「全公司」频道处理")
        msg = self._log_ceo(target, text, topic)
        tasks = [t for t in self.directive_tasks() if t["status"] != "cancelled"]
        if target == MANAGER or tasks:  # 已经有任务：先让调度助手判断是不是要改动已有的工作
            threading.Thread(target=self._triage_then_route, args=(msg, target, topic, tasks), daemon=True).start()
        else:
            self._route(msg, target, topic)

    def _log_ceo(self, target: str, text: str, topic: str | None) -> dict:
        """CEO 的话马上显示出来。团队在做事时对正在工作的人说的是插话；对全公司下的新指令带上指令 id。"""
        directive = self.state().get("directive") or {}
        if target == ALL:
            msg = (new_message("ceo", "interject", text, directive_id=directive.get("id")) if self.team_busy
                   else new_message("ceo", "text", text, directive_id=new_id()[:6]))
        elif target != MANAGER and target in self.engine.working_roles():
            msg = new_message("ceo", "interject", text, directive_id=directive.get("id"))
        else:
            msg = new_message("ceo", "text", text, topic=topic)
        self.log.append(target, msg)
        self.broadcast({"type": "message", "channel": target, "message": msg})
        return msg

    def _route(self, msg: dict, target: str, topic: str | None) -> None:
        """不是改动：照常处理（插话 / 私聊回复 / 新指令）。"""
        if target == MANAGER:
            return
        if msg["kind"] == "interject":
            self.engine.inbox.post(target, msg)  # 正在工作的同事下一步就收到（之后才开始的任务从消息记录里读到）
            return
        if target != ALL:
            if not self.busy:
                self.run_started = time.time()
            self.engine.chat(target, topic)       # 私聊和团队的工作同时进行
            self.broadcast(self.status_event())
            return
        if self.team_busy:  # 调度助手判断的这几秒里团队开始工作了（比如看板上点了返工）：改为插话
            msg = {**msg, "kind": "interject", "directive_id": (self.state().get("directive") or {}).get("id")}
            self.engine.inbox.post(ALL, msg)
            return
        self.unfinished = False  # 下了新指令：上次没跑完的就不再继续
        self._submit({"type": "directive", "directive": {"id": msg["directive_id"], "text": msg["text"], "target": ALL}})

    def _triage_then_route(self, msg: dict, target: str, topic: str | None, tasks: list[dict]) -> None:
        state = self.state()
        where = ("全公司频道（对所有同事）" if target == ALL else "项目经理频道（对你）" if target == MANAGER
                 else f"和{role_name(target)}的私聊里（对{role_name(target)}说）")
        topic_task = state.get("tasks", {}).get(topic) if topic else None
        recent = [m for m in self.log.read(target, last=8) if m["id"] != msg["id"]][-6:]
        self.broadcast({"type": "triage", "channel": target, "status": "working"})
        try:
            res = triage(self.company, msg["text"], tasks, state.get("directive") or {}, where, topic=topic_task,
                         manager=target == MANAGER, recent=recent)
        except Exception as e:  # 理解失败就当作普通对话，不耽误事
            res = {"change": False, "orders": [], "reply": f"（调度助手出错：{type(e).__name__}）", "usage": {}}
        finally:
            self.broadcast({"type": "triage", "channel": target, "status": "idle"})
        try:
            if target == MANAGER and (res["reply"] or not res["change"]):
                self._post(MANAGER, new_message("manager", "text", res["reply"] or "收到。"))
            if res["change"]:
                text, affected = describe(res["orders"], tasks)
                by_id = {t["id"]: t for t in tasks}
                orders = [{**o, "title": by_id[o["task_id"]]["title"], "assignee": by_id[o["task_id"]]["assignee"]}
                          for o in res["orders"]]
                self._post(target, new_message("manager", "proposal", text, orders=orders, affected=affected,
                                               source_id=msg["id"]))
                return
            self._route(msg, target, topic)
        finally:
            # 调度助手的用量最后再记：记用量也是交给引擎的一个事件，先交的话 _route 会看到"团队在忙"，
            # 把一条新指令当成插话（v0.21 修复：上一条指令做完后，下新指令没有反应）
            if (res.get("usage") or {}).get("calls"):
                self.engine.submit({"type": "usage", "usage": {"planner": res["usage"]}})

    def _post(self, channel: str, msg: dict) -> None:
        self.log.append(channel, msg)
        self.broadcast({"type": "message", "channel": channel, "message": msg})

    def resolve_proposal(self, channel: str, proposal_id: str, action: str) -> None:
        """CEO 确认或取消调度助手整理的调整。确认后和看板上的操作走同一套变更单。"""
        if not self.company or action not in ("confirm", "cancel"):
            return
        msgs = self.log.read(channel)
        prop = next((m for m in msgs if m["id"] == proposal_id and m.get("kind") == "proposal"), None)
        if not prop:
            return self.notice("找不到这条调整建议")
        if any(m.get("proposal_id") == proposal_id for m in msgs):
            return self.notice("这条调整已经处理过了")
        if action == "cancel":
            return self._post(channel, new_message("system", "event", "已取消这次调整", proposal_id=proposal_id))
        if self.state().get("approval"):
            return self.notice("计划还在等你审批：请先在「全公司」频道处理")
        current = {t["id"] for t in self.directive_tasks()}
        orders = [o for o in prop.get("orders") or [] if o["task_id"] in current]
        if not orders:
            return self._post(channel, new_message("system", "event", "这些任务已经不在当前指令里，没有调整",
                                                   proposal_id=proposal_id))
        if not self.busy:
            self.run_started = time.time()
        for o in orders:
            self.engine.change({"kind": o["kind"], "task_id": o["task_id"], "feedback": o.get("feedback", "")})
        self._post(channel, new_message("system", "event", "已确认，马上生效", proposal_id=proposal_id))
        self.broadcast(self.status_event())

    def interject(self, text: str, target: str) -> None:
        """执行过程中 CEO 发言：不打断执行，实时送到正在工作的同事（全公司频道 = 所有人，角色频道 = 只给它）。"""
        directive = self.state().get("directive") or {}
        msg = new_message("ceo", "interject", text, directive_id=directive.get("id"))
        self.log.append(target, msg)        # 马上存下来；之后才开始的任务从消息记录里读到
        self.engine.inbox.post(target, msg)  # 正在工作的同事下一步就收到
        self.broadcast({"type": "message", "channel": target, "message": msg})

    def resolve_review(self, action: str, feedback: str) -> None:
        review = self.review
        if not review:
            return self.notice("没有等待审批的计划")
        if review["type"] == "plan_review":
            if action in ("approve", "revise", "cancel"):
                self._submit({"type": "plan_decision", "action": action, "feedback": feedback})
        elif action in ("accept", "rework", "drop"):
            self._submit({"type": "decision", "task_id": review["task_id"], "action": action, "feedback": feedback})

    def task_action(self, action: str, task_id: str, feedback: str) -> None:
        """CEO 在任务卡片上让某个任务返工 / 暂停 / 继续 / 重试：马上生效，受影响、正在工作的同事在当前这一步停下。"""
        if not self.company or action not in ("rework", "pause", "resume", "retry"):
            return
        task = next((t for t in self.directive_tasks() if t["id"] == task_id), None)
        if not task:
            return self.notice("只能调整当前这条指令的任务")
        if self.state().get("approval"):
            return self.notice("计划还在等你审批：请先在「全公司」频道处理")
        if action == "rework" and task["status"] == "pending" and not task.get("started"):
            return self.notice("这个任务还没开始，不需要返工；可以在「全公司」频道补充要求")
        if action == "retry" and task["status"] != "failed":
            return self.notice("只有失败的任务可以重试")
        if not self.busy:
            self.run_started = time.time()
        self.engine.change({"kind": action, "task_id": task_id, "feedback": feedback.strip()})
        self.broadcast(self.status_event())

    # ---------- 引擎的事件 ----------

    def _on_event(self, ev: dict) -> None:
        t = ev.get("type")
        if t == "status":
            if ev["status"] == "working":
                self.role_status[ev["role"]] = {"status": "working", "task_id": ev.get("task_id"), "chat": ev.get("chat")}
            else:
                self.role_status.pop(ev["role"], None)
            self.broadcast({"type": "role_status", "role_status": self.role_status})
        elif t == "file":
            self.broadcast({"type": "files", "files": self.company.list_files(), "changed": ev["path"]})
        else:  # 流式文字、工具调用、正在写的文档、新消息、提示：直接转发
            self.broadcast(ev)

    def _on_update(self, update: dict) -> None:
        events = []
        if update.get("tasks"):
            events.append({"type": "tasks", "tasks": update["tasks"], "since": self._track(update["tasks"])})
        if update.get("directive_usage"):
            self.directive_usage = add_directive_usage(self.directive_usage, update["directive_usage"])
        if update.get("usage"):
            self.usage = add_usage(self.usage, update["usage"])
            events.append({"type": "usage", "usage": self.usage, "directive_usage": self.directive_usage})
        if events:
            self.broadcast(*events)
        if "approval" in update or update.get("tasks"):  # 审批、裁决的卡片跟着变
            self.broadcast(self.status_event())

    def _on_idle(self) -> None:
        self.run_started = None
        self.role_status = {}
        NETWORK.clear()
        company = self.company
        if company:
            self.broadcast(self.status_event(), {"type": "files", "files": company.list_files()})


def role_name(role_id: str) -> str:
    return ROLES[role_id].name if role_id in ROLES else role_id
