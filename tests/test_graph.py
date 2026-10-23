"""调度图和执行引擎的流程：规划 → 审批 → 按依赖并行执行（一满足条件就派发）→ 汇报；修改意见；取消；私聊；规划出错。"""
import time

import pytest

from harness import Env


@pytest.fixture
def env(tmp_path):
    return Env(tmp_path)


def test_plan_waits_for_approval_then_runs_in_dependency_order(env):
    env.world.delay = 0.3
    env.directive()
    approval = env.state()["approval"]
    assert approval["type"] == "plan_review" and len(approval["task_ids"]) == 3
    assert all(t["status"] == "pending" for t in env.tasks().values()) and not env.engine.jobs

    env.decide_plan("approve")
    state = env.state()
    assert [t["status"] for t in state["tasks"].values()] == ["done"] * 3 and not state.get("approval")
    # 依赖顺序：PRD 完成后，工程师和设计师才开始，并且两者并行
    spans = {r: (s, e) for r, s, e in env.world.spans}
    assert spans["engineer"][0] >= spans["pm"][1] and spans["designer"][0] >= spans["pm"][1]
    assert spans["engineer"][0] < spans["designer"][1] and spans["designer"][0] < spans["engineer"][1]
    # 工程师拿到了上游 PRD 的信息
    eng_brief = env.briefs("engineer")[0]
    assert "产品经理《写 PRD》" in eng_brief and "docs/pm.md" in eng_brief
    # 文件写入共享区并记录作者；频道里有计划、成果和汇报；用量按角色统计
    assert {f["path"]: f["author"] for f in env.company.list_files()}["docs/pm.md"] == "pm"
    kinds = [m["kind"] for m in env.chans()["all"]]
    assert kinds[0] == "text" and kinds[1] == "plan" and kinds[-1] == "report"
    assert state["usage"]["engineer"]["calls"] == 4   # 自己的任务 2 次 + 审查 PRD 2 次
    # 实时事件：有流式片段、工具调用、文件、角色状态、新消息
    assert {e["type"] for e in env.events} >= {"delta", "tool", "file", "status", "message"}
    # 执行结束：任务上不再挂着工作运行，执行循环的检查点也清掉了
    assert all(not t.get("job") and not t.get("worker") for t in state["tasks"].values())


def test_dependents_start_as_soon_as_their_upstream_is_done(tmp_path):
    """没有"整批等待"：E 只依赖 B，B 做完马上开始，不用等同时在做、很慢的 C。"""
    env = Env(tmp_path, plans=[{"summary": "s", "tasks": [
        {"key": "a", "title": "A", "detail": "", "assignee": "pm", "depends_on": []},
        {"key": "b", "title": "B 快", "detail": "", "assignee": "engineer", "depends_on": ["a"]},
        {"key": "c", "title": "C 慢", "detail": "", "assignee": "designer", "depends_on": ["a"]},
        {"key": "e", "title": "E 只依赖 B", "detail": "", "assignee": "qa", "depends_on": ["b"]}]}], auto_approve=True)
    env.world.on_call = lambda role, messages: time.sleep(1.2 if role == "designer" else 0.05)
    env.directive()
    assert all(t["status"] == "done" for t in env.tasks().values())
    b_end, c_end, e_start = env.spans("engineer")[0][1], env.spans("designer")[0][1], env.spans("qa")[0][0]
    assert e_start - b_end < 0.5 and e_start < c_end          # 以前要等到 C 结束（多等 2 秒多）


def test_revise_replans_with_feedback(env):
    env.directive()
    env.decide_plan("revise", "不需要设计师")
    planner_inputs = [m for r, m in env.world.calls if r is None]
    assert "不需要设计师" in planner_inputs[-1][1]["content"]
    assert sum(t["status"] == "cancelled" for t in env.tasks().values()) == 3
    assert env.state()["approval"]  # 新计划再次等待审批


def test_cancel_ends_run(env):
    env.directive()
    env.decide_plan("cancel")
    assert not env.state().get("approval") and all(t["status"] == "cancelled" for t in env.tasks().values())


def test_failed_role_skips_dependents(tmp_path):
    env = Env(tmp_path, auto_approve=True)
    env.world.fail_role = "pm"
    env.directive()
    status = {t["assignee"]: t["status"] for t in env.tasks().values()}
    assert status == {"pm": "failed", "engineer": "skipped", "designer": "skipped"}


def test_direct_chat_keeps_role_memory(env):
    env.chat("pm", "你好，我是 CEO")
    env.chat("pm", "刚才我说了什么？")
    pm_calls = env.calls("pm")
    last_input = pm_calls[-2]  # 第二次对话的第一步
    contents = [m["content"] for m in last_input if m["role"] in ("user", "assistant")]
    assert contents[0] == "你好，我是 CEO" and contents[-1] == "刚才我说了什么？"
    assert len(env.chans()["pm"]) == 4 and "all" not in env.chans()
    assert env.state()["usage"]["pm"]["calls"] == 4             # 私聊的用量也记上了


def test_direct_chat_runs_while_the_team_works(tmp_path):
    """私聊和团队的工作同时进行：团队在做任务时，空闲的成员马上就能回复。"""
    from opc.state import new_message
    env = Env(tmp_path, auto_approve=True)
    env.world.delay = 0.6
    env.log.append("all", new_message("ceo", "text", "做"))
    env.engine.submit({"type": "directive", "directive": {"id": "d1", "text": "做", "target": "all"}})
    deadline = time.time() + 5
    while not env.engine.jobs and time.time() < deadline:
        time.sleep(0.02)
    assert env.engine.jobs                                         # 团队正在工作
    env.log.append("qa", new_message("ceo", "text", "你好"))       # qa 这次没有任务，空闲
    env.engine.chat("qa")
    deadline = time.time() + 5
    while not env.chans().get("qa", [])[1:] and time.time() < deadline:
        time.sleep(0.02)
    assert env.chans()["qa"][-1]["sender"] == "qa"                 # qa 已经回复了
    assert any(t["status"] != "done" for t in env.tasks().values())  # 团队还没做完
    env.wait()


def test_invalid_plan_is_retried_then_reported(env):
    env.world.plans = ["不是 JSON", '{"summary": "x", "tasks": [{"title": "a", "assignee": "ceo"}]}']
    env.directive("做点什么")
    assert not env.state().get("approval") and env.chans()["all"][-1]["kind"] == "error"


def test_upstream_tasks_always_get_a_reviewer():
    """别人要依赖的任务一定有人审查：计划没安排时，让第一个要用它、且不是作者本人的下游同事审查。"""
    from opc.planner import require_review_for_upstream
    tasks = [{"key": "a", "assignee": "pm", "reviewer": "", "depends_on": [], "starts_after": []},
             {"key": "b", "assignee": "pm", "reviewer": "", "depends_on": ["a"], "starts_after": []},
             {"key": "c", "assignee": "designer", "reviewer": "", "depends_on": [], "starts_after": ["a"]},
             {"key": "d", "assignee": "engineer", "reviewer": "qa", "depends_on": ["b"], "starts_after": []}]
    require_review_for_upstream(tasks)
    assert [t["reviewer"] for t in tasks] == ["designer", "engineer", "", "qa"]   # 没人依赖的 c 不强制


# ---------------------------------------------------------------- CEO 审批时直接编辑计划

def _rows(env):
    """当前等待审批的计划，转成编辑窗口提交的格式。"""
    tasks = env.tasks()
    return [{"id": i, "title": tasks[i]["title"], "detail": tasks[i]["detail"], "assignee": tasks[i]["assignee"],
             "reviewer": tasks[i].get("reviewer") or "", "depends_on": list(tasks[i]["depends_on"]),
             "acceptance": list(tasks[i].get("acceptance") or []),
             "deliverables": [d["path"] for d in tasks[i].get("deliverables") or []]}
            for i in env.state()["approval"]["task_ids"]]


def test_ceo_edits_plan_then_approves(env):
    env.directive()
    rows = _rows(env)
    prd, tech, design = rows
    tech.update(title="技术方案和数据库设计", acceptance=["写清楚表结构"])
    rows = [prd, tech, {"key": "new1", "title": "测试计划", "detail": "根据技术方案写测试计划", "assignee": "qa",
                        "reviewer": "", "depends_on": [tech["id"]], "acceptance": [], "deliverables": ["docs/qa.md"]}]
    env.engine.submit({"type": "plan_edit", "tasks": rows, "approve": False})
    env.wait()

    tasks, approval = env.tasks(), env.state()["approval"]
    assert tasks[design["id"]]["status"] == "cancelled"
    assert tasks[tech["id"]]["title"] == "技术方案和数据库设计" and tasks[tech["id"]]["acceptance"] == ["写清楚表结构"]
    new = next(t for t in tasks.values() if t["title"] == "测试计划")
    assert new["status"] == "pending" and new["depends_on"] == [tech["id"]] and new["assignee"] == "qa"
    assert new["deliverables"][0]["path"] == "docs/qa.md"  # 新任务的交付文件：提交时检查它存在
    assert approval["task_ids"] == [prd["id"], tech["id"], new["id"]]
    plans = [m for m in env.chans()["all"] if m["kind"] == "plan"]
    assert len(plans) == 2 and plans[-1]["edited"] and approval["plan_msg"] == plans[-1]["id"]
    assert "删除《界面设计》" in plans[-1]["text"] and "新增《测试计划》" in plans[-1]["text"]
    assert "《技术方案和数据库设计》改了标题、验收标准" in plans[-1]["text"]  # 审查人是按规则补的，另有说明
    assert "《技术方案和数据库设计》有下游依赖，按规则安排了审查人" in plans[-1]["text"]
    assert not [r for r, _ in env.world.calls if r is None][1:]  # 没有再调用规划助手

    env.decide_plan("approve")
    status = {t["title"]: t["status"] for t in env.tasks().values()}
    assert status == {"写 PRD": "done", "技术方案和数据库设计": "done", "界面设计": "cancelled", "测试计划": "done"}
    spans = {r: (s, e) for r, s, e in env.world.spans}
    assert spans["qa"][0] >= spans["engineer"][1]  # 新任务等技术方案做完才开始
    assert "designer" not in spans


def test_edit_and_approve_at_once_removing_an_upstream_task(env):
    env.directive()
    prd, tech, design = _rows(env)
    env.engine.submit({"type": "plan_edit", "tasks": [tech, design], "approve": True})
    env.wait()
    plan = [m for m in env.chans()["all"] if m["kind"] == "plan"][-1]
    assert "《技术方案》不再等待已删除的《写 PRD》" in plan["text"]
    status = {t["title"]: t["status"] for t in env.tasks().values()}
    assert status == {"写 PRD": "cancelled", "技术方案": "done", "界面设计": "done"}
    assert not env.state().get("approval")


def test_edit_plan_validation():
    from opc.planner import PlanError, edit_plan
    current = [{"id": "a", "title": "写 PRD", "detail": "x", "assignee": "pm", "reviewer": "engineer", "depends_on": [],
                "starts_after": [], "acceptance": [],
                "deliverables": [{"path": "docs/PRD.md", "template": "", "sections": ["验收标准"], "must_mention": [],
                                  "min_chars": 2000, "max_chars": 10000}]}]
    roles = ["pm", "engineer", "qa"]
    base = {"id": "a", "title": "写 PRD", "detail": "x", "assignee": "pm", "reviewer": "engineer", "depends_on": [],
            "acceptance": [], "deliverables": ["docs/PRD.md", "docs/extra.md"]}
    specs, notes = edit_plan([base], current, roles)
    kept, extra = specs[0]["deliverables"]
    assert kept["sections"] == ["验收标准"] and kept["min_chars"] == 2000  # 路径没变：保留规划助手定的检查
    assert extra["path"] == "docs/extra.md" and extra["min_chars"] == 0 and not extra["sections"]
    assert notes == ["《写 PRD》改了交付文件"]
    for bad, why in (({**base, "assignee": "ghost"}, "不是公司成员"), ({**base, "reviewer": "pm"}, "审查人"),
                     ({**base, "title": " "}, "没有标题")):
        with pytest.raises(PlanError, match=why):
            edit_plan([bad], current, roles)
    with pytest.raises(PlanError, match="至少要有一个任务"):
        edit_plan([], current, roles)
    # 依赖只能指向前面的任务；有下游依赖、没有审查人的，按规则补上
    specs, notes = edit_plan([{**base, "reviewer": ""},
                              {"key": "n1", "title": "测试", "assignee": "qa", "depends_on": ["a", "n9"]}], current, roles)
    assert specs[1]["depends_on"] == ["a"] and specs[0]["reviewer"] == "qa"
    assert "《写 PRD》有下游依赖，按规则安排了审查人" in notes and "新增《测试》" in notes


def test_plan_edit_error_reported_to_ceo(tmp_path):
    """网页提交的编辑有错时，马上告诉 CEO（编辑窗口不关），计划不变。"""
    from opc.web.runtime import Hub
    from fakes import World, make_store
    store = make_store(tmp_path, World())
    hub = Hub(store)
    company = store.create("测试公司", "x", [{"id": "pm"}, {"id": "engineer"}, {"id": "designer"}])
    sent = []
    hub.broadcast = lambda *evs: sent.extend(evs)
    hub.open_company(company.id)
    hub.engine.submit({"type": "directive", "directive": {"id": "d1", "text": "做个 App", "target": "all"}})
    assert hub.engine.wait_idle(30)
    before = hub.state()["approval"]
    hub.handle({"type": "plan_edit", "tasks": [{"id": before["task_ids"][0], "title": "写 PRD", "assignee": "ghost"}]})
    assert {"type": "plan_edit_error", "error": "《写 PRD》的负责人不是公司成员"} in sent
    assert hub.state()["approval"] == before
    hub.close_company()
