"""成员主动协作：发现上游的产出有问题就上报（做成调整建议，CEO 确认后上游返工、自己同步），需要确认细节就问同事；
被问的同事只能看不能改，每次执行最多问 3 次；只执行这次提供给模型的工具。"""
import pytest

from opc import effort

from fakes import call
from harness import Env

MAX_ASKS = effort.limits("standard")["asks"]

PLAN = {"summary": "先写 PRD，再写技术方案", "tasks": [
    {"key": "a", "title": "写 PRD", "detail": "docs/pm.md", "assignee": "pm", "depends_on": []},
    {"key": "b", "title": "技术方案", "detail": "docs/engineer.md", "assignee": "engineer", "depends_on": ["a"]},
]}


@pytest.fixture
def env(tmp_path):
    return Env(tmp_path, roles=("pm", "engineer", "qa"), plans=[PLAN])


def tool_results(env, role):
    """这个角色收到的所有工具结果。"""
    out = []
    for r, msgs in env.world.calls:
        if r == role:
            out += [m["content"] for m in msgs if m["role"] == "tool" and m["content"] not in out]
    return out


def test_report_upstream_issue_becomes_a_proposal_and_rework(env):
    env.world.first_calls["engineer"] = [call("x1", "report_upstream_issue", task="写 PRD",
                                              problem="第 3 节说满员后不能候补，第 5 节又说可以候补，前后矛盾",
                                              impact="不知道满员时该不该提供候补接口，技术方案没法定",
                                              suggestion="统一成满员后可以候补，先到先得")]
    env.start()
    eng = env.task("b")
    assert eng["status"] == "done"                                    # 上报后先按最合理的理解继续做完
    assert any("已上报给 CEO" in r for r in tool_results(env, "engineer"))
    prop = next(m for m in env.chans()["all"] if m["kind"] == "proposal")
    assert prop["reporter"] == "engineer" and prop["reporter_task"] == "技术方案"
    order = prop["orders"][0]
    assert order["kind"] == "rework" and order["task_id"] == env.task("a")["id"] and prop["affected"] == ["工程师《技术方案》"]
    assert "前后矛盾" in order["feedback"] and "建议：统一成满员后可以候补" in order["feedback"]
    assert len(env.briefs("pm")) == 1                                 # CEO 确认之前不会返工

    env.engine.change({"kind": order["kind"], "task_id": order["task_id"], "feedback": order["feedback"]})  # CEO 确认
    env.wait()
    pm = env.briefs("pm")
    assert len(pm) == 2 and "工程师发现：第 3 节说满员后不能候补" in pm[1]
    assert "上游有改动" in env.briefs("engineer")[-1] and env.task("b")["status"] == "done"


def test_report_needs_a_real_upstream_task(env):
    env.world.first_calls["engineer"] = [call("x1", "report_upstream_issue", task="不存在的任务", problem="有问题", impact="做不下去")]
    env.start()
    assert any(r.startswith("错误") and "《写 PRD》" in r for r in tool_results(env, "engineer"))
    assert not any(m["kind"] == "proposal" for m in env.chans()["all"])
    tools = [names for r, names in env.world.tools_seen if r == "pm"][0]
    assert "report_upstream_issue" not in tools and "ask_colleague" in tools   # 没有上游的任务不给上报工具


def test_ask_colleague_who_answers_read_only(env):
    env.world.first_calls["engineer"] = [call("x1", "ask_colleague", role="pm", question="候补满了之后怎么处理？")]
    env.start()
    msgs = env.chans()["all"]
    q = next(m for m in msgs if m.get("consult") and m["sender"] == "engineer")
    a = next(m for m in msgs if m.get("consult") and m["sender"] == "pm")
    assert q["text"] == "问产品经理：候补满了之后怎么处理？" and a["reply_to"] == q["id"]
    assert any(r.startswith("产品经理的回答：") for r in tool_results(env, "engineer"))
    consult_tools = [names for r, names in env.world.tools_seen if r == "pm"][-2]
    assert "read_file" in consult_tools and "write_file" not in consult_tools   # 被问的同事只能看
    assert {"name": "write_file", "ok": False} in a["tools"]          # 它想写文件也被拒绝了
    assert env.company.read_file("docs/pm.md") == "# pm 的产出"
    assert env.state()["usage"]["pm"]["calls"] == 4                  # 自己的任务 2 次 + 回答同事 2 次
    assert env.task("b")["status"] == "done"


def test_ask_colleague_is_limited(env):
    env.world.first_calls["engineer"] = [call(f"x{i}", "ask_colleague", role="pm", question=f"问题 {i}")
                                         for i in range(MAX_ASKS + 1)]
    env.start()
    results = tool_results(env, "engineer")
    assert sum(r.startswith("产品经理的回答") for r in results) == MAX_ASKS
    assert any("不能再问" in r for r in results)
    bad = [call("y1", "ask_colleague", role="engineer", question="问自己")]
    env2 = Env(env.store.home.parent / "b", roles=("pm", "engineer"), plans=[PLAN])
    env2.world.first_calls["engineer"] = bad
    env2.start()
    assert any(r.startswith("错误") for r in tool_results(env2, "engineer"))


def test_reported_issue_confirmed_from_the_web(tmp_path):
    """成员上报的调整建议，CEO 在网页上确认，和调度助手整理的建议走同一条路。"""
    import time
    from opc.web.runtime import Hub
    from fakes import World, make_store
    world = World()
    world.plans = [PLAN]
    world.first_calls["engineer"] = [call("x1", "report_upstream_issue", task="写 PRD", problem="候补规则前后矛盾", impact="候补接口没法设计")]
    hub = Hub(make_store(tmp_path, world))
    company = hub.store.create("测试公司", "x", [{"id": r} for r in ("pm", "engineer")])
    hub.open_company(company.id)
    hub.send("做一个约课小程序", "all")
    for _ in range(300):
        if hub.review and not hub.busy:
            break
        time.sleep(0.02)
    hub.resolve_review("approve", "")
    assert hub.engine.wait_idle(10)
    prop = next(m for m in hub.log.read("all") if m["kind"] == "proposal")
    hub.resolve_proposal("all", prop["id"], "confirm")
    assert hub.engine.wait_idle(10)
    assert sum(1 for r, m in world.calls if r == "pm" and len(m) == 2) == 2      # 产品经理返工了
    assert all(t["status"] == "done" for t in hub.state()["tasks"].values())
    assert any(m.get("proposal_id") == prop["id"] for m in hub.log.read("all"))


def test_reports_in_one_step_are_merged_and_only_once_per_run(env):
    """同一步连着报几条：合并成一张调整建议（不会丢）；之后这次执行不能再报，避免反复把问题推给上游。"""
    env.world.first_calls["engineer"] = [
        call("x1", "report_upstream_issue", task="写 PRD", problem="候补规则前后矛盾", impact="候补接口没法设计"),
        call("x2", "report_upstream_issue", task="写 PRD", problem="没写取消截止时间", impact="取消接口不知道怎么校验")]
    env.start()
    props = [m for m in env.chans()["all"] if m["kind"] == "proposal"]
    assert len(props) == 1
    feedback = props[0]["orders"][0]["feedback"]
    assert "1. 候补规则前后矛盾" in feedback and "2. 没写取消截止时间" in feedback and "不改的后果：取消接口" in feedback


def test_report_threshold_and_single_report():
    """上报要写清不改的后果；这次执行上报过以后再报会被拒绝，让它自己做合理假设、写进「待确认」。"""
    from types import SimpleNamespace
    from opc.jobs import Collab
    tasks = {"a": {"id": "a", "title": "写 PRD", "assignee": "pm", "depends_on": [], "starts_after": [], "status": "done"},
             "b": {"id": "b", "title": "技术方案", "assignee": "engineer", "depends_on": ["a"], "starts_after": [],
                   "status": "running"}}
    posted = []
    collab = Collab(SimpleNamespace(tasks=tasks, post=lambda ch, m: posted.append(m)), "engineer", tasks["b"])
    assert collab.report({"task": "写 PRD", "problem": "有个小问题"}).startswith("错误")   # 说不清后果
    assert collab.report({"task": "写 PRD", "problem": "矛盾", "impact": "没法设计"}).startswith("已上报")
    collab.flush()
    assert len(posted) == 1 and posted[0]["kind"] == "proposal"
    assert "已经上报过一次" in collab.report({"task": "写 PRD", "problem": "又一个", "impact": "也没法做"})
    collab.flush()
    assert len(posted) == 1


def test_cannot_edit_a_colleagues_file(env):
    """"上报而不是代改"由系统强制：执行任务时改别人的产出会被拒绝，提示用上报或提问；自己的文件、新文件照常写。"""
    env.world.first_calls["engineer"] = [call("x1", "write_file", path="docs/pm.md", content="工程师顺手改了 PRD"),
                                         call("x2", "write_file", path="docs/notes.md", content="新文件可以写")]
    env.start()
    refused = [r for r in tool_results(env, "engineer") if "不能改" in r]
    assert refused and "产品经理《写 PRD》的产出" in refused[0] and "report_upstream_issue" in refused[0]
    assert env.company.read_file("docs/pm.md") == "# pm 的产出" and env.company.read_file("docs/notes.md") == "新文件可以写"
    assert env.task("b")["status"] == "done"


THREE = {"summary": "s", "tasks": PLAN["tasks"] + [
    {"key": "c", "title": "测试计划", "detail": "docs/qa.md", "assignee": "qa", "depends_on": ["a", "b"]}]}


def test_answers_from_colleagues_become_part_of_the_briefs(tmp_path):
    """同事问答里定下的口径不能只留在对话里：下游同事的任务说明里写明（以这里为准），回答的人返工时要写进自己的文档。"""
    env = Env(tmp_path, roles=("pm", "engineer", "qa"), plans=[THREE])
    env.world.first_calls["engineer"] = [call("x1", "ask_colleague", role="pm", question="候补满了之后怎么处理？")]
    env.start()
    qa = env.briefs("qa")[0]
    assert "同事问答里已经定下的口径" in qa and "工程师问产品经理：候补满了之后怎么处理？" in qa and "产品经理的回答" in qa
    env.change("rework", "a", "补上取消规则").wait()
    pm = env.briefs("pm")[-1]
    assert "你在同事问答里给出的口径" in pm and "候补满了之后怎么处理？" in pm
