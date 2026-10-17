"""测试用的驱动：一家公司 + 调度图 + 执行引擎，按"下令 → 审批 → 等全部停下"的方式推进，读状态和消息。"""
from langgraph.checkpoint.memory import InMemorySaver

from opc.engine import Engine
from opc.graph import build_graph
from opc.messages import MessageLog
from opc.state import new_message

from fakes import World, make_store

ALL = "all"


class Env:
    def __init__(self, tmp_path, roles=("pm", "engineer", "designer", "qa"), world: World | None = None,
                 plans=None, auto_approve=False, checkpointer=None, store=None, **engine_kw):
        self.world = world or World()
        if plans:
            self.world.plans = list(plans)
        self.store = make_store(tmp_path, self.world)
        self.company = self.store.create("测试公司", "做一个记账 App", [{"id": r} for r in roles],
                                         **engine_kw.pop("company_kw", {}))
        self.graph = build_graph(checkpointer or InMemorySaver(), store)
        self.events: list[dict] = []
        self.engine = Engine(self.company, self.graph, saver=engine_kw.pop("saver", None), auto_approve=auto_approve,
                             on_event=self.events.append, **engine_kw)
        self.log = MessageLog(self.graph.store, self.company.id)
        self.cfg = self.engine.cfg

    # ---------- 推进 ----------

    def wait(self, timeout=30):
        assert self.engine.wait_idle(timeout), "引擎没有在规定时间内停下"
        return self

    def directive(self, text="做一个记账 App", did="d1"):
        self.log.append(ALL, new_message("ceo", "text", text, directive_id=did))
        self.engine.submit({"type": "directive", "directive": {"id": did, "text": text, "target": ALL}})
        return self.wait()

    def decide_plan(self, action="approve", feedback=""):
        self.engine.submit({"type": "plan_decision", "action": action, "feedback": feedback})
        return self.wait()

    def start(self, text="做一个记账 App", did="d1"):
        self.directive(text, did)
        if self.state().get("approval"):
            self.decide_plan("approve")
        return self

    def decide(self, task_id, action, feedback=""):
        self.engine.submit({"type": "decision", "task_id": task_id, "action": action, "feedback": feedback})
        return self.wait()

    def change(self, kind, key, feedback=""):
        self.engine.change({"kind": kind, "task_id": self.task(key)["id"], "feedback": feedback})
        return self

    def chat(self, role, text):
        self.log.append(role, new_message("ceo", "text", text))
        self.engine.chat(role)
        return self.wait()

    # ---------- 读 ----------

    def state(self):
        return self.engine.state()

    def tasks(self):
        return self.state().get("tasks", {})

    def task(self, key):
        return next(t for t in self.tasks().values() if t["id"].endswith("-" + key))

    def chans(self):
        return self.log.all()

    def briefs(self, role):
        """这个角色每次执行自己任务拿到的说明（对话里只有系统提示词 + 一条说明的那次调用；不含审查别人的）。"""
        return [b for b in self._first_messages(role) if "请你审查" not in b]

    def review_briefs(self, role):
        """这个角色每次审查别人的任务拿到的说明。"""
        return [b for b in self._first_messages(role) if "请你审查" in b]

    def _first_messages(self, role):
        return [m[1]["content"] for r, m in self.world.calls if r == role and len(m) == 2]

    def calls(self, role):
        return [m for r, m in self.world.calls if r == role]

    def spans(self, role):
        """每次执行任务调用两次模型（写文件、汇报）：按任务合并成 (开始, 结束)。"""
        calls = [(s, e) for r, s, e in self.world.spans if r == role]
        return [(calls[i][0], calls[i + 1][1]) for i in range(0, len(calls) - 1, 2)]
