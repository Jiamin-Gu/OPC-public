"""审查返工循环、CEO 裁决（不再让整个流程停下）、CEO 实时插话。"""
import pytest

from opc.state import new_message

from harness import Env

PLAN = {"summary": "PRD 由工程师审查，代码由测试审查", "tasks": [
    {"key": "t1", "title": "写 PRD", "detail": "docs/PRD.md", "assignee": "pm", "depends_on": []},
    {"key": "t2", "title": "写代码", "detail": "code/", "assignee": "engineer", "depends_on": ["t1"], "reviewer": "qa"},
]}


@pytest.fixture
def env(tmp_path):
    return Env(tmp_path, roles=("pm", "engineer", "qa"), plans=[PLAN])


def test_review_pass_marks_done(env):
    env.start()
    t2 = env.task("t2")
    assert t2["status"] == "done" and t2["round"] == 1 and t2["reviews"][0]["verdict"] == "pass"
    review_msgs = [m for m in env.chans()["all"] if m["kind"] == "review" and m["task_id"] == t2["id"]]
    assert len(review_msgs) == 1 and review_msgs[0]["sender"] == "qa" and review_msgs[0]["verdict"] == "pass"
    assert "写代码" in env.review_briefs("qa")[0] and "submit_review" in env.review_briefs("qa")[0]
    # 有下游依赖的 PRD 自动安排了审查：由要用它的工程师审，站在下游的角度把关
    t1 = env.task("t1")
    assert t1["reviewer"] == "engineer" and t1["reviews"][0]["verdict"] == "pass"
    prd_review = env.review_briefs("engineer")[0]
    assert "这份产出是后面这些任务的依据" in prd_review and "工程师《写代码》" in prd_review


def test_changes_then_rework_then_pass(env):
    env.world.verdicts = {"写代码": [("changes", ["金额没有保留两位小数"]), ("pass", [])]}
    env.start()
    t2 = env.task("t2")
    assert t2["status"] == "done" and [r["verdict"] for r in t2["reviews"]] == ["changes", "pass"]
    eng = env.briefs("engineer")
    assert len(eng) == 2 and "这是返工" in eng[1] and "金额没有保留两位小数" in eng[1]
    assert "edit_file" in eng[1] and "不要用 write_file 重写整份文件" in eng[1]  # 返工只改有问题的部分
    qa = env.review_briefs("qa")
    assert "第 2 轮审查" in qa[1] and "金额没有保留两位小数" in qa[1]  # 复查时看得到上一轮的意见
    kinds = [m["kind"] for m in env.chans()["all"]]
    assert kinds.count("review") == 3 and kinds[-1] == "report"   # PRD 一次 + 代码两轮


def test_escalation_to_ceo_rework_with_note(env):
    env.world.verdicts = {"写代码": [("changes", ["缺少导出功能"]), ("changes", ["还是缺少导出"]), ("pass", [])]}
    env.start()
    t2 = env.task("t2")
    assert t2["status"] == "escalated" and len(t2["reviews"]) == 2 and not env.engine.busy  # 等 CEO 裁决
    env.decide(t2["id"], "rework", "导出先做 CSV 就行")
    t2 = env.task("t2")
    assert t2["status"] == "done" and t2["ceo_note"] == "导出先做 CSV 就行"
    assert "CEO 的意见（必须遵守）：导出先做 CSV 就行" in env.briefs("engineer")[-1]
    assert env.chans()["all"][-1]["kind"] == "report"


@pytest.mark.parametrize("action,status", [("accept", "done"), ("drop", "failed")])
def test_escalation_accept_or_drop(tmp_path, action, status):
    env = Env(tmp_path, roles=("pm", "engineer", "qa"), plans=[{**PLAN, "tasks": PLAN["tasks"] + [
        {"key": "t3", "title": "测试计划", "detail": "docs/test.md", "assignee": "qa", "depends_on": ["t2"]}]}])
    env.world.verdicts = {"写代码": [("changes", ["a"]), ("changes", ["b"])]}
    env.start()
    env.decide(env.task("t2")["id"], action)
    assert env.task("t2")["status"] == status
    assert env.task("t3")["status"] == ("done" if action == "accept" else "skipped")


def test_escalation_does_not_block_other_tasks(tmp_path):
    """等 CEO 裁决时，和它无关的任务照常做完（以前整张图停在 interrupt 上）。"""
    env = Env(tmp_path, roles=("pm", "engineer", "designer", "qa"), plans=[{"summary": "s", "tasks": [
        {"key": "t1", "title": "写代码", "detail": "", "assignee": "engineer", "depends_on": [], "reviewer": "qa"},
        {"key": "t2", "title": "做设计", "detail": "", "assignee": "designer", "depends_on": []},
        {"key": "t3", "title": "写 PRD", "detail": "", "assignee": "pm", "depends_on": ["t2"]}]}])
    env.world.verdicts = {"写代码": [("changes", ["a"]), ("changes", ["b"])]}
    env.world.on_call = lambda role, messages: __import__("time").sleep(0.3 if role == "designer" else 0)
    env.start()
    assert env.task("t1")["status"] == "escalated"
    assert env.task("t2")["status"] == "done" and env.task("t3")["status"] == "done"
    assert env.chans()["all"][-1]["kind"] != "report"            # 还有裁决没做：先不汇报


def test_ceo_interjection_reaches_working_role_and_later_tasks(env):
    note = new_message("ceo", "interject", "记账要支持多币种", directive_id="d1")

    def on_call(role, messages):
        if role == "pm" and not env.engine.inbox.for_role("pm"):  # PM 工作到一半时 CEO 发言（和运行时一样：先存下来，再送到正在工作的同事）
            env.log.append("all", note)
            env.engine.inbox.post("all", note)
    env.world.on_call = on_call
    env.start()
    pm_calls = env.calls("pm")
    assert any("记账要支持多币种" in str(x.get("content")) for x in pm_calls[1])  # 下一步就收到了
    assert "记账要支持多币种" in env.briefs("engineer")[0]                     # 之后的任务写在任务说明里
    assert "记账要支持多币种" in env.review_briefs("qa")[0]                          # 审查人也知道
    msgs = env.chans()["all"]
    assert [m["id"] for m in msgs].count(note["id"]) == 1                     # 存进消息记录且只存一次
    report_input = [m for r, m in env.world.calls if r is None][-1][1]["content"]
    assert "记账要支持多币种" in report_input                                  # 汇报也考虑了


def test_reviews_are_read_only(env):
    """审查人只能看、不能改别人的产出（试跑时测试在审查里自己写了测试计划）。"""
    env.start()
    review_tools = [names for r, names in env.world.tools_seen if "submit_review" in names]
    assert review_tools and all("write_file" not in n and "edit_file" not in n for n in review_tools)
    assert all(f["author"] == f["path"].split("/")[-1][:-3] for f in env.company.list_files())


def test_converging_rereview_gets_one_extra_round(env):
    """第二轮复审时上一轮的问题都解决了、只剩 1 个新问题：在收敛，多给一轮，不交给 CEO。"""
    env.world.verdicts = {"写代码": [("changes", ["金额没有保留两位小数"]), ("changes", ["导出缺表头"], "resolved"),
                                    ("pass", [])]}
    env.start()
    t2 = env.task("t2")
    assert t2["status"] == "done" and [r["verdict"] for r in t2["reviews"]] == ["changes", "changes", "pass"]
    assert t2["reviews"][1].get("extra") and any("复审在收敛" in m["text"] for m in env.chans()["all"])


def test_closing_rereview_only_checks_remaining_issues(env):
    """收敛之后的下一轮是收尾复审：只核对剩下的问题，新发现记为建议，不再退回（试跑里每轮都能挑出 1～2 个新问题，谈不拢）。"""
    env.world.verdicts = {"写代码": [("changes", ["a"]), ("changes", ["b"], "resolved"), ("changes", ["c"], "resolved")]}
    env.start()
    t2 = env.task("t2")
    assert t2["status"] == "done" and [r["verdict"] for r in t2["reviews"]] == ["changes", "changes", "pass"]
    assert any("：c" in s and "收尾复审的新发现" in s for s in t2["reviews"][2]["suggestions"])
    briefs = env.review_briefs("qa")
    assert "这是收尾复审" in briefs[-1] and not any("这是收尾复审" in b for b in briefs[:-1])
    assert any("下一轮是收尾复审" in m["text"] for m in env.chans()["all"])


def test_closing_rereview_with_unresolved_issue_goes_to_ceo(env):
    """收尾复审时剩下的问题没解决：照旧交给 CEO。不在收敛时（上一轮的问题没解决）也照旧交给 CEO。"""
    env.world.verdicts = {"写代码": [("changes", ["a"]), ("changes", ["b"], "resolved"), ("changes", [])]}
    env.start()
    t2 = env.task("t2")
    assert t2["status"] == "escalated" and len(t2["reviews"]) == 3


def test_not_converging_goes_to_ceo_without_closing(env):
    env.world.verdicts = {"写代码": [("changes", ["a"]), ("changes", ["b"])]}  # 上一轮的问题没解决：不在收敛
    env.start()
    t2 = env.task("t2")
    assert t2["status"] == "escalated" and len(t2["reviews"]) == 2 and not t2.get("closing")


def test_rework_cannot_rewrite_whole_file(env):
    """返工时不能用 write_file 整篇重写自己的文件，只能 edit_file 局部修改。"""
    env.world.verdicts = {"写代码": [("changes", ["金额没有保留两位小数"]), ("pass", [])]}
    env.world.first_calls["engineer"] = None
    env.start()
    from opc.jobs import write_guard
    from types import SimpleNamespace
    task = env.task("t2")
    j = SimpleNamespace(company=env.company, tasks=env.tasks())
    assert "不能用 write_file 重写" in write_guard(j, task, rework=True)(task["files"][0], "write_file")
    assert write_guard(j, task, rework=True)(task["files"][0], "edit_file") is None
    assert write_guard(j, task, rework=True)("docs/new.md", "write_file") is None   # 新文件可以写
    assert write_guard(j, task)(task["files"][0], "write_file") is None             # 不是返工时可以重写
    assert "edit_file" in [c["function"]["name"] for r, m in env.world.calls if r == "engineer"
                           for x in m if x.get("tool_calls") for c in x["tool_calls"]]
