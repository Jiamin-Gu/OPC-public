"""v0.10 代码审查发现的问题的回归测试：私聊不算团队在忙；新指令不覆盖还在执行的指令；
工作运行意外出错不无限重试；审查被暂停不算负责人做到一半；读插话只取这条指令的。"""
import time

from langgraph.store.memory import InMemoryStore

from opc import jobs
from opc.changes import dispatch, merge_job_result
from opc.messages import MessageLog
from opc.state import new_message
from opc.web.runtime import Hub

from fakes import World, make_store
from harness import Env


def wait(pred, timeout=10):
    t = time.time()
    while not pred():
        assert time.time() - t < timeout, "等待超时"
        time.sleep(0.02)


def test_directive_sent_while_chatting_still_starts_planning(tmp_path):
    world = World(delay=1.0)                     # 私聊回复慢：下令时私聊还没结束
    hub = Hub(make_store(tmp_path, world))
    hub.broadcast = lambda *e: None
    c = hub.store.create("测试", "x", [{"id": r} for r in ("pm", "engineer", "designer")])
    hub.open_company(c.id)
    hub.send("你好，介绍一下自己", "pm")
    wait(lambda: hub.busy)
    assert not hub.team_busy and hub.status_event()["busy"] is False   # 私聊不算团队在忙
    hub.send("做一个记账 App", "all")
    wait(lambda: hub.review)                     # 不是被当成插话：照常规划、等审批
    assert hub.review["type"] == "plan_review" and hub.state()["directive"]["text"] == "做一个记账 App"
    assert hub.log.read("all")[0]["kind"] == "text"
    assert hub.engine.wait_idle(10)


def test_stop_during_a_chat_keeps_the_tasks(tmp_path):
    """团队没在工作、只有私聊时点停止：只停私聊，等裁决的任务保留。"""
    env = Env(tmp_path, roles=("pm", "engineer", "qa"), plans=[{"summary": "s", "tasks": [
        {"key": "a", "title": "写代码", "detail": "", "assignee": "engineer", "depends_on": [], "reviewer": "qa"}]}])
    env.world.verdicts = [("changes", ["x"]), ("changes", ["y"])]
    env.start()
    assert env.task("a")["status"] == "escalated"
    env.world.delay = 2.0
    env.log.append("pm", new_message("ceo", "text", "你好"))
    env.engine.chat("pm")
    wait(lambda: env.engine.jobs)
    env.engine.stop()
    env.wait(5)
    assert env.task("a")["status"] == "escalated"                       # 没被取消
    assert not any(m["text"] == "CEO 停止了执行" for m in env.chans()["all"])
    assert not env.engine.cancel.is_set() and not env.engine.chat_cancel.is_set()


def test_new_directive_never_replaces_one_still_running(tmp_path):
    env = Env(tmp_path, roles=("pm", "engineer"), plans=[{"summary": "s", "tasks": [
        {"key": "a", "title": "A", "detail": "", "assignee": "pm", "depends_on": []}]}], auto_approve=True)
    env.world.delay = 0.8
    env.log.append("all", new_message("ceo", "text", "第一条"))
    env.engine.submit({"type": "directive", "directive": {"id": "d1", "text": "第一条", "target": "all"}})
    wait(lambda: env.engine.jobs)
    env.engine.submit({"type": "directive", "directive": {"id": "d2", "text": "第二条", "target": "all"}})
    env.wait()
    assert env.state()["directive"]["id"] == "d1" and env.task("a")["status"] == "done"
    assert any(m["kind"] == "error" and "上一条指令还有任务在执行" in m["text"] for m in env.chans()["all"])


def test_unexpected_job_crash_fails_the_task_instead_of_looping(tmp_path, monkeypatch):
    env = Env(tmp_path, roles=("pm", "engineer"), plans=[{"summary": "s", "tasks": [
        {"key": "a", "title": "A", "detail": "", "assignee": "pm", "depends_on": []},
        {"key": "b", "title": "B", "detail": "", "assignee": "engineer", "depends_on": ["a"]}]}], auto_approve=True)
    crashes = []

    def boom(j):
        crashes.append(1)
        raise KeyError("bug")
    monkeypatch.setitem(jobs.RUNNERS, "work", boom)
    env.directive()
    assert len(crashes) == 1                                           # 只出错一次，没有一直重新派发
    assert env.task("a")["status"] == "failed" and "工作运行出错" in env.task("a")["result"]
    assert env.task("b")["status"] == "skipped" and env.chans()["all"][-1]["kind"] == "report"


def test_paused_review_does_not_mark_owner_interrupted():
    t = dispatch({"id": "a", "status": "review", "depends_on": [], "files": ["docs/a.md"]}, "review", "j1")
    reset = {**t, "status": "rework", "ceo_note": "改一下"}            # 审查期间 CEO 让它返工
    merged = merge_job_result(reset, "j1", None, True, [], kind="review")
    assert merged["status"] == "rework" and not merged.get("interrupted") and not merged["job"]
    run = dispatch({"id": "b", "status": "pending", "depends_on": [], "files": []}, "work", "j2", thread="th")
    assert merge_job_result(run, "j2", None, True, [], kind="work")["interrupted"]


def test_notes_only_reads_interjections_of_the_directive():
    log = MessageLog(InMemoryStore(), "c1")
    log.append("all", new_message("ceo", "text", "下令", directive_id="d1"))
    log.append("all", new_message("ceo", "interject", "全公司插话", directive_id="d1"))
    log.append("all", new_message("ceo", "interject", "上一条指令的插话", directive_id="d0"))
    log.append("pm", new_message("ceo", "interject", "给产品经理的", directive_id="d1"))
    log.append("engineer", new_message("ceo", "interject", "给工程师的", directive_id="d1"))
    assert [m["text"] for m in log.notes("d1", "pm")] == ["全公司插话", "给产品经理的"]
