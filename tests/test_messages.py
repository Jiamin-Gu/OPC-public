"""消息记录存在 LangGraph 的 Store 里：顺序、分页；检查点不再随对话变长而膨胀；旧版本（消息在检查点里）的数据迁移。"""
import sqlite3
from typing import Annotated, TypedDict

from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph import START, StateGraph
from langgraph.store.memory import InMemoryStore

from opc import messages as messages_module
from opc.messages import MessageLog
from opc.state import new_message
from opc.web.runtime import Hub

from fakes import World, make_store
from harness import Env


def test_log_keeps_order_and_pages(monkeypatch):
    monkeypatch.setattr(messages_module, "PAGE", 7)       # 分页读取：超过一页也按顺序拿全
    log = MessageLog(InMemoryStore(), "c1")
    for i in range(20):
        log.append("all", new_message("ceo", "text", f"第 {i} 条"))
    log.append("pm", new_message("ceo", "interject", "插话", directive_id="d1"))
    assert [m["text"] for m in log.read("all")] == [f"第 {i} 条" for i in range(20)]
    assert [m["text"] for m in log.read("all", last=3)] == ["第 17 条", "第 18 条", "第 19 条"]
    assert sorted(log.channels()) == ["all", "pm"] and log.notes("d1", "pm")[0]["text"] == "插话"
    assert log.notes("d1", "engineer") == [] and log.notes(None, "pm") == []
    assert MessageLog(log.store, "c2").all() == {}         # 公司之间互不影响
    log.delete_all()
    assert log.all() == {}


def test_checkpoints_do_not_grow_with_the_conversation(tmp_path):
    """之前每个检查点都带着全部对话：6 条长指令后单个检查点就有 0.37 MB。现在对话在 Store 里，检查点只有任务看板。"""
    conn = sqlite3.connect(tmp_path / "cp.sqlite", check_same_thread=False)
    env = Env(tmp_path, roles=("pm", "engineer"), checkpointer=SqliteSaver(conn))
    big = "很长的回复内容。" * 2500                           # 每条指令 2 万字
    sizes = []
    for i in range(4):
        env.world.plans = [{"summary": "s", "tasks": [
            {"key": "a", "title": "A", "detail": "", "assignee": "pm", "depends_on": []}]}]
        env.start(big, did=f"d{i}")
        conn.commit()
        sizes.append(conn.execute("select length(checkpoint) from checkpoints where thread_id=? order by rowid desc limit 1",
                                  (env.company.id,)).fetchone()[0])
    # 检查点里只有任务看板和当前这条指令（这里每条指令本身就有 6 万字节），不再累积之前的对话
    assert max(sizes) - min(sizes) < 3_000 and max(sizes) < 100_000, sizes
    assert len(env.log.read("all")) >= 4 * 3


def test_legacy_messages_in_checkpoint_are_migrated_once(tmp_path):
    """v0.7 以前消息存在检查点 state 的 channels 里：第一次打开公司时搬进 Store，之后不重复搬。"""
    world = World()
    store = make_store(tmp_path, world)
    company = store.create("老公司", "", [{"id": "pm"}])
    old = {"all": [new_message("ceo", "text", "以前的指令"), new_message("pm", "text", "以前的汇报")],
           "pm": [new_message("ceo", "text", "以前的私聊")]}

    class Old(TypedDict, total=False):  # 旧版本的 state：带 channels
        channels: Annotated[dict, lambda a, b: {**(a or {}), **(b or {})}]

    g = StateGraph(Old)
    g.add_node("w", lambda s: {"channels": old})
    g.add_edge(START, "w")
    conn = sqlite3.connect(store.checkpoint_path, check_same_thread=False)
    g.compile(checkpointer=SqliteSaver(conn)).invoke({}, {"configurable": {"thread_id": company.id}})
    conn.commit()

    hub = Hub(store)
    assert hub.open_company(company.id) is None
    assert [m["text"] for m in hub.log.read("all")] == ["以前的指令", "以前的汇报"]
    assert [m["text"] for m in hub.log.read("pm")] == ["以前的私聊"]
    assert hub.snapshot_event()["state"]["channels"]["pm"][0]["text"] == "以前的私聊"
    Hub(store).open_company(company.id)                    # 再打开一次（比如重启）：不重复
    assert len(hub.log.read("all")) == 2


def test_legacy_plan_waiting_for_approval_still_works(tmp_path):
    """v0.7 等审批时图停在 interrupt 上：新版本打开后审批卡片还在，批准后照常执行。"""
    from langgraph.graph import END
    from langgraph.types import interrupt

    world = World()
    store = make_store(tmp_path, world)
    company = store.create("老公司", "", [{"id": "pm"}])
    task = {"id": "d1-x-t1", "directive_id": "d1", "title": "写 PRD", "detail": "", "assignee": "pm", "depends_on": [],
            "status": "pending", "result": "", "files": [], "reviewer": "", "round": 0, "reviews": [], "ceo_note": ""}
    review = {"type": "plan_review", "directive_id": "d1", "summary": "先写 PRD", "task_ids": [task["id"]]}

    class Old(TypedDict, total=False):
        directive: dict
        tasks: Annotated[dict, lambda a, b: {**(a or {}), **(b or {})}]
        channels: Annotated[dict, lambda a, b: {**(a or {}), **(b or {})}]

    g = StateGraph(Old)
    g.add_node("plan", lambda s: {"directive": {"id": "d1", "text": "做 App", "target": "all"}, "tasks": {task["id"]: task},
                                  "channels": {"all": [new_message("planner", "plan", "先写 PRD", tasks=[task["id"]])]}})
    g.add_node("approve", lambda s: interrupt(review) and {})
    g.add_edge(START, "plan")
    g.add_edge("plan", "approve")
    g.add_edge("approve", END)
    conn = sqlite3.connect(store.checkpoint_path, check_same_thread=False)
    g.compile(checkpointer=SqliteSaver(conn)).invoke({}, {"configurable": {"thread_id": company.id}})
    conn.commit()

    hub = Hub(store)
    assert hub.open_company(company.id) is None
    assert hub.review == review and not hub.unfinished
    hub.resolve_review("approve", "")
    assert hub.engine.wait_idle(10)
    assert next(iter(hub.state()["tasks"].values()))["status"] == "done"
    assert hub.log.read("all")[-1]["kind"] == "report"


def test_legacy_run_interrupted_midway_can_be_resumed(tmp_path):
    """v0.7 执行到一半程序被关掉（任务还是 pending，没有在等 CEO）：打开时提示没跑完，继续后做完。"""
    world = World()
    store = make_store(tmp_path, world)
    company = store.create("老公司", "", [{"id": "pm"}])
    task = {"id": "d1-x-t1", "directive_id": "d1", "title": "写 PRD", "detail": "", "assignee": "pm", "depends_on": [],
            "status": "pending", "result": "", "files": [], "reviewer": "", "round": 0, "reviews": [], "ceo_note": ""}

    class Old(TypedDict, total=False):
        directive: dict
        tasks: Annotated[dict, lambda a, b: {**(a or {}), **(b or {})}]

    g = StateGraph(Old)
    g.add_node("plan", lambda s: {"directive": {"id": "d1", "text": "做 App", "target": "all"}, "tasks": {task["id"]: task}})
    g.add_edge(START, "plan")
    conn = sqlite3.connect(store.checkpoint_path, check_same_thread=False)
    g.compile(checkpointer=SqliteSaver(conn)).invoke({}, {"configurable": {"thread_id": company.id}})
    conn.commit()

    hub = Hub(store)
    assert hub.open_company(company.id) is None and hub.unfinished and hub.review is None
    hub.handle({"type": "resume_run"})
    assert hub.engine.wait_idle(10)
    assert next(iter(hub.state()["tasks"].values()))["status"] == "done" and not hub.unfinished
