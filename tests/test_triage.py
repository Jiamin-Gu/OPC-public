"""调度助手：CEO 在任何频道说"上游那份不对"，都整理成同样的变更单（确认后上游返工、下游暂停并同步）；
项目经理频道；不是改动时照常处理；按工作项私聊（带着那项工作的上下文、只能看不能改）。"""
import time

import pytest

from opc.triage import board, describe, parse
from opc.web.runtime import MANAGER, Hub

from fakes import World, make_store

PLAN = {"summary": "先写 PRD，再写技术方案", "tasks": [
    {"key": "a", "title": "写 PRD", "detail": "docs/pm.md", "assignee": "pm", "depends_on": []},
    {"key": "b", "title": "技术方案", "detail": "docs/engineer.md", "assignee": "engineer", "depends_on": ["a"]},
]}
REWORK_PRD = {"change": True, "orders": [{"task": "T1", "kind": "rework", "feedback": "候补规则改成先到先得"}],
              "reply": "安排产品经理返工，技术方案先暂停"}


def T(id_, status, deps=(), **kw):
    return {"id": id_, "title": id_.upper(), "assignee": "pm", "status": status, "depends_on": list(deps),
            "starts_after": [], "files": [], "reviewer": "", "result": "", **kw}


# ---------------------------------------------------------------- 纯函数

def test_parse_keeps_only_valid_orders():
    tasks = [T("a", "done"), T("b", "running", ["a"]), T("c", "pending", ["b"]), T("d", "done")]
    _, keys = board(tasks)
    res = parse({"change": True, "orders": [
        {"task": "t1", "kind": "rework", "feedback": "改一下"},   # 编号大小写都行
        {"task": "T9", "kind": "rework"},                          # 看板上没有
        {"task": "T3", "kind": "rework"},                          # 还没开始：不能返工
        {"task": "T4", "kind": "delete"},                          # 动作不合法
        {"task": "T4", "kind": "pause"},                           # 做完了：不用暂停
        {"task": "T2", "kind": "pause"}]}, keys)
    assert res["change"] and res["orders"] == [{"kind": "rework", "task_id": "a", "feedback": "改一下"},
                                               {"kind": "pause", "task_id": "b", "feedback": ""}]
    assert parse({"change": False, "orders": [{"task": "T1", "kind": "rework"}], "reply": "好"}, keys) == \
        {"change": False, "orders": [], "reply": "好"}                # 不是改动就不看 orders


def test_describe_lists_affected_downstream():
    tasks = [T("a", "done"), T("b", "running", ["a"], assignee="engineer"), T("c", "done", ["b"], assignee="qa"),
             T("x", "skipped", ["a"])]
    text, affected = describe([{"kind": "rework", "task_id": "a", "feedback": "候补规则改成先到先得"}], tasks)
    assert "返工：候补规则改成先到先得" in text and affected == ["工程师《B》", "测试《C》"]   # 跳过的不算


# ---------------------------------------------------------------- 完整流程

@pytest.fixture
def hub(tmp_path):
    world = World()
    world.plans = [PLAN]
    h = Hub(make_store(tmp_path, world))
    company = h.store.create("测试公司", "做一个约课小程序", [{"id": r} for r in ("pm", "engineer", "qa")])
    h.open_company(company.id)
    h.world = world
    h.send("做一个约课小程序", "all")
    wait(lambda: h.review and not h.busy)
    h.resolve_review("approve", "")
    assert h.engine.wait_idle(10)
    assert all(t["status"] == "done" for t in h.state()["tasks"].values())
    return h


def wait(pred, timeout=10):
    t = time.time()
    while not pred():
        assert time.time() - t < timeout, "等待超时"
        time.sleep(0.02)


def proposal(hub, channel):
    wait(lambda: any(m["kind"] == "proposal" for m in hub.log.read(channel)))
    return next(m for m in hub.log.read(channel) if m["kind"] == "proposal")


def calls(hub, role):
    return sum(1 for r, _ in hub.world.calls if r == role)


@pytest.mark.parametrize("channel,text", [
    ("engineer", "产品经理的 PRD 里候补规则不对，你先等等"),     # 对下游说
    ("pm", "你 PRD 里的候补规则要改成先到先得"),                  # 对上游说
    ("all", "PRD 的候补规则不对，要改成先到先得"),                # 对全公司说
    (MANAGER, "PRD 的候补规则不对，要改成先到先得"),              # 对项目经理说
])
def test_same_outcome_whoever_you_tell(hub, channel, text):
    hub.world.triages = [REWORK_PRD]
    before = {r: calls(hub, r) for r in ("pm", "engineer")}
    hub.send(text, channel)
    prop = proposal(hub, channel)
    assert prop["orders"][0]["title"] == "写 PRD" and prop["affected"] == ["工程师《技术方案》"]
    assert {r: calls(hub, r) for r in ("pm", "engineer")} == before      # 确认之前什么都不执行，也不转给角色
    triage_input = [m for r, m in hub.world.calls if r is None][-1]
    assert text in triage_input[-1]["content"] and "T1 产品经理《写 PRD》" in triage_input[0]["content"]
    if channel == MANAGER:
        assert any(m["sender"] == "manager" and m["kind"] == "text" for m in hub.log.read(MANAGER))

    hub.resolve_proposal(channel, prop["id"], "confirm")
    assert hub.engine.wait_idle(10)
    tasks = {t["assignee"]: t for t in hub.state()["tasks"].values()}
    assert tasks["pm"]["status"] == "done" and tasks["engineer"]["status"] == "done"
    assert calls(hub, "pm") > before["pm"] and calls(hub, "engineer") > before["engineer"]  # 上游返工，下游同步
    pm_brief = [m for r, m in hub.world.calls if r == "pm"][before["pm"]][1]["content"]
    assert "候补规则改成先到先得" in pm_brief
    eng_brief = [m for r, m in hub.world.calls
                 if r == "engineer" and "请你审查" not in m[1]["content"]][-1][1]["content"]  # 工程师同步时的说明（不含复审 PRD）
    assert "上游有改动" in eng_brief
    assert any(m.get("proposal_id") == prop["id"] for m in hub.log.read(channel))
    hub.resolve_proposal(channel, prop["id"], "confirm")                  # 同一条建议不会执行两次
    assert hub.engine.wait_idle(5) and calls(hub, "pm") == before["pm"] + 2


def test_cancelled_proposal_changes_nothing(hub):
    hub.world.triages = [REWORK_PRD]
    hub.send("PRD 要改", "engineer")
    prop = proposal(hub, "engineer")
    before = len(hub.world.calls)
    hub.resolve_proposal("engineer", prop["id"], "cancel")
    time.sleep(0.2)
    assert len(hub.world.calls) == before and all(t["status"] == "done" for t in hub.state()["tasks"].values())
    assert hub.log.read("engineer")[-1]["text"] == "已取消这次调整"


def test_not_a_change_is_handled_as_usual(hub):
    before = calls(hub, "engineer")
    hub.send("你的技术方案里用了什么数据库？", "engineer")       # 调度助手判断不是改动：工程师照常回复
    wait(lambda: hub.log.read("engineer")[-1]["sender"] == "engineer")
    assert calls(hub, "engineer") > before and not any(m["kind"] == "proposal" for m in hub.log.read("engineer"))
    hub.send("现在进度怎么样？", MANAGER)                         # 项目经理频道：只回复
    wait(lambda: hub.log.read(MANAGER)[-1]["sender"] == "manager")
    assert hub.log.read(MANAGER)[-1]["text"] == "收到"


def test_topic_chat_brings_the_work_context_and_is_read_only(hub):
    pm_task = next(t for t in hub.state()["tasks"].values() if t["assignee"] == "pm")
    hub.send("这份 PRD 里为什么这么设计候补？", "pm", topic=pm_task["id"])
    wait(lambda: hub.log.read("pm")[-1]["sender"] == "pm")
    convo = [m for r, m in hub.world.calls if r == "pm"][-1]
    first = convo[1]["content"]
    assert "《写 PRD》" in first and "docs/pm.md" in first and "不要修改文件" in first and first.endswith("这份 PRD 里为什么这么设计候补？")
    tools = [names for r, names in hub.world.tools_seen if r == "pm"][-1]
    assert "read_file" in tools and "write_file" not in tools and "edit_file" not in tools
    reply = hub.log.read("pm")[-1]
    assert reply["topic"] == pm_task["id"]
    hub.send("随便聊聊", "pm")                                     # 自由聊天：不带工作项的记录
    wait(lambda: hub.log.read("pm")[-1]["sender"] == "pm" and hub.log.read("pm")[-1]["id"] != reply["id"])
    free = [m for r, m in hub.world.calls if r == "pm"][-1]
    assert free[1]["content"] == "随便聊聊"


def test_retry_only_for_failed_tasks():
    keys = {"T1": {"id": "a", "status": "failed"}, "T2": {"id": "b", "status": "done"}}
    out = parse({"change": True, "orders": [{"task": "T1", "kind": "retry"}, {"task": "T2", "kind": "retry"}]}, keys)
    assert out["orders"] == [{"kind": "retry", "task_id": "a", "feedback": ""}]
