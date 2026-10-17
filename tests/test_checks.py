"""机械检查（传统工作流的验收）：交付检查、返工核对、审查核对；格式要求跟着任务走，不放在角色提示词里。"""
from types import SimpleNamespace

import pytest

from opc import checks, task_formats
from opc.agents import system_prompt
from opc.company import CompanyStore
from opc.jobs import ReviewGuard, open_issues
from opc.planner import parse_deliverables
from opc.roles import ROLES

from harness import Env


@pytest.fixture
def company(tmp_path):
    return CompanyStore(tmp_path).create("公司", "目标", [{"id": r} for r in ("pm", "engineer", "qa")])


# ---------------------------------------------------------------- 交付检查

def test_deliverable_checks(company):
    company.write_file("docs/PRD.md", "# PRD\n## 用户故事\n会员可以预约。\n```\n## 验收标准（代码块里的不算标题）\n```\n", author="pm")
    d = {"path": "docs/PRD.md", "sections": ["用户故事", "验收标准"], "must_mention": ["预约", "候补"], "min_chars": 500}
    problems = checks.deliverable_problems(company, [d, {"path": "docs/missing.md"}])
    assert any("缺少章节：验收标准" in p for p in problems)
    assert any("没有写到：候补" in p for p in problems) and not any("预约" in p for p in problems)
    assert any("要求至少 500 字" in p for p in problems) and any("没有产出文件 docs/missing.md" in p for p in problems)
    company.write_file("docs/PRD.md", "# PRD\n## 2. 用户故事（P0）\n预约、候补。\n## 验收标准\n" + "内容" * 300, author="pm")
    assert checks.deliverable_problems(company, [d]) == []           # 标题里包含要求的词就算有


def test_format_checks():
    assert checks.format_problems("a.md", "# 标题\n```python\nx = 1\n") == ["a.md：有代码块（```）没有闭合"]
    assert checks.format_problems("a.md", "```\nx\n```\n") == []
    assert "不是合法的 JSON" in checks.format_problems("a.json", "{'a': 1}")[0]
    assert "Python 语法错误" in checks.format_problems("a.py", "def f(:\n")[0]
    assert checks.format_problems("a.py", "def f():\n    return 1\n") == []


def test_rework_checks():
    ok = [{"no": 0, "action": "fixed", "detail": "改了"}, {"no": 1, "action": "fixed", "detail": "改了"},
          {"no": 2, "action": "disagree", "detail": "原因是……"}]
    assert checks.rework_problems(2, True, ok, changed=True) == []
    problems = checks.rework_problems(2, True, ok[1:2] + [{"no": 2, "action": "disagree", "detail": ""}], changed=True)
    assert any("CEO 的意见（第 0 条）" in p for p in problems) and any("不同意要写明理由：第 2 条" in p for p in problems)
    assert any("一字没变" in p for p in checks.rework_problems(2, True, ok, changed=False))   # 说改了但没改


def test_quote_matching():
    text = "## 5. 候补规则\n满员后可以加入候补，**先到先得**；开课前 2 小时关闭候补。"
    assert checks.quote_found("满员后可以加入候补，先到先得", [text])          # 忽略 Markdown 符号和标点
    assert checks.quote_found("满员后可以加入候补……开课前 2 小时关闭", [text])  # 省略号连接的几段按顺序都在
    assert not checks.quote_found("满员后不能候补", [text]) and not checks.quote_found("候补", [text])  # 太短不算


# ---------------------------------------------------------------- 提示词分层

def test_formats_follow_the_task_not_the_role(company):
    for role_id in ("pm", "engineer", "qa"):
        prompt = system_prompt(company, role_id)
        assert "模板（docs/" not in prompt and "典型产出" not in prompt, role_id
    task = {"assignee": "pm", "acceptance": ["每个 P0 功能都有验收标准"],
            "deliverables": [{"path": "docs/PRD.md", "template": "prd", "sections": ["验收标准"],
                              "must_mention": ["候补"], "min_chars": 1500}]}
    brief = task_formats.requirements_brief(task)
    assert "`docs/PRD.md`" in brief and "必须有这些章节" in brief and "必须写到：候补" in brief and "至少 1500 字" in brief
    assert "# PRD：[产品 / 功能名]" in brief                           # 默认用 OPC 改写的中文模板
    assert "Problem Statement" in task_formats.requirements_brief(task, "complete")  # 指定时可以用原文的模板
    assert "1. 每个 P0 功能都有验收标准" in brief
    assert task_formats.template("tech-design", "complete") == task_formats.template("tech-design", "concise")  # 没有就用另一个版本


def test_planner_deliverables_are_sanitized():
    out = parse_deliverables([{"path": "/docs/PRD.md", "template": "prd", "sections": ["验收标准", "", "验收标准"],
                               "min_chars": "2000"}, {"path": "../etc/passwd"}, {"path": "docs/a.md", "template": "nope"}, "x"])
    assert out == [{"path": "docs/PRD.md", "template": "prd", "sections": ["验收标准"], "must_mention": [], "min_chars": 2000,
                    "max_chars": 12000},
                   {"path": "docs/a.md", "template": "", "sections": [], "must_mention": [], "min_chars": 0, "max_chars": 12000}]


def test_document_length_limit():
    """文档限篇幅（没写用默认，写得太大收紧到上限，至少是最低字数的两倍）；代码文件不限。"""
    out = parse_deliverables([{"path": "docs/a.md", "max_chars": 99999}, {"path": "docs/b.md", "min_chars": 15000},
                              {"path": "docs/c.md", "max_chars": 6000, "min_chars": 2000}, {"path": "code/x.py", "max_chars": 50}])
    assert [d["max_chars"] for d in out] == [20000, 20000, 6000, 0]
    assert out[1]["min_chars"] == 10000  # 最低字数不能超过上限的一半


def test_length_is_only_a_suggestion(tmp_path):
    """篇幅上限只是建议：超了不退回（超过时另外生成简洁版，见 test_condense.py）。"""
    company = CompanyStore(tmp_path).create("公司", "", [{"id": "pm"}])
    company.write_file("docs/a.md", "# A\n" + "字" * 300, author="pm")
    assert not checks.deliverable_problems(company, [{"path": "docs/a.md", "max_chars": 200}])
    brief = task_formats.requirements_brief({"deliverables": [{"path": "docs/a.md", "min_chars": 100, "max_chars": 200}]})
    assert "至少 100 字，建议不超过 200 字" in brief and "超过上限会被退回" not in brief


# ---------------------------------------------------------------- 审查核对

def guard(company, previous=None, scope=None, must_read=("docs/PRD.md",)):
    company.write_file("docs/PRD.md", "# PRD\n## 候补\n满员后可以加入候补，先到先得。\n## 取消\n开课前两小时关闭预约。\n",
                       author="pm")
    task = {"id": "a", "files": ["docs/PRD.md"]}
    j = SimpleNamespace(company=company, tasks={"a": task}, rejects=3)
    return ReviewGuard(j, task, ["docs/PRD.md"], previous, scope, list(must_read))


ISSUE = {"where": "docs/PRD.md 候补", "quote": "满员后可以加入候补，先到先得", "problem": "没写候补上限，会无限排队"}


def test_review_must_read_and_quote_real_text(company):
    g = guard(company)
    assert "还没有读过" in g({"verdict": "changes", "summary": "s", "issues": [ISSUE]})      # 没看就提交
    g.observe({"name": "read_file", "path": "docs/PRD.md", "ok": True})
    msg = g({"verdict": "changes", "summary": "s", "issues": [{**ISSUE, "quote": "满员后不能候补"}]})
    assert msg.startswith("错误") and "quote 在文件里找不到" in msg                          # 编造的原文
    assert g({"verdict": "changes", "summary": "s", "issues": [ISSUE]}) == "已提交审查结论"
    g2 = guard(company, must_read=())
    assert "要么改成 changes" in g2({"verdict": "pass", "summary": "s", "issues": [ISSUE]})  # 结论和问题矛盾
    assert g.verdict["verdict"] == "changes" and "没写候补上限" in g.verdict["issues"][0]["text"]


def test_review_takes_the_last_submission_without_invalid_issues(company):
    g = guard(company, must_read=())
    bad = {**ISSUE, "quote": "编造的原文"}
    for _ in range(2):
        assert g({"verdict": "changes", "summary": "s", "issues": [bad]}).startswith("错误")
    g({"verdict": "changes", "summary": "s", "issues": [bad]})                # 第三次照收：核对不上的问题不算
    assert g.verdict["verdict"] == "pass" and "核对不上" in g.verdict["suggestions"][0]


def test_rereview_only_looks_at_previous_issues_and_the_change(company):
    previous = [{"text": "没写候补上限"}, {"text": "没写取消规则"}]
    scope = "满员后可以加入候补，先到先得。"                                   # 这次改动新加的内容
    g = guard(company, previous=previous, scope=scope, must_read=())
    assert "缺：第 2 条" in g({"verdict": "pass", "summary": "s", "previous": [{"no": 1, "status": "resolved"}]})
    outside = {**ISSUE, "quote": "开课前两小时关闭预约", "problem": "关闭时间太早"}   # 改动之外的新发现
    g({"verdict": "changes", "summary": "s", "issues": [outside],
       "previous": [{"no": 1, "status": "resolved"}, {"no": 2, "status": "resolved"}]})
    assert g.verdict["verdict"] == "pass" and "不在这次改动的范围内" in g.verdict["suggestions"][0]

    g = guard(company, previous=previous, scope=scope, must_read=())
    g({"verdict": "changes", "summary": "s", "issues": [ISSUE],
       "previous": [{"no": 1, "status": "resolved"}, {"no": 2, "status": "unresolved", "note": "还是没写"}]})
    texts = [i["text"] for i in g.verdict["issues"]]
    assert g.verdict["verdict"] == "changes" and texts[0].startswith("（上轮第 2 条没有解决）没写取消规则 —— 还是没写")
    assert "没写候补上限，会无限排队" in texts[1]                                # 改动里的新问题照常退回


def test_waived_issues_are_not_open():
    task = {"round_base": 0, "reviews": [{"round": 1, "verdict": "changes", "issues": [{"text": "a"}]}]}
    assert open_issues(task) == [{"text": "a"}]
    task["reviews"][0]["waived"] = True
    assert open_issues(task) == []


# ---------------------------------------------------------------- 完整流程

PLAN = {"summary": "s", "tasks": [
    {"key": "a", "title": "写 PRD", "detail": "", "assignee": "pm", "depends_on": [],
     "deliverables": [{"path": "docs/pm.md", "sections": ["产出"], "min_chars": 5}]},
    {"key": "b", "title": "技术方案", "detail": "", "assignee": "engineer", "depends_on": ["a"], "reviewer": "qa",
     "deliverables": [{"path": "docs/engineer.md", "sections": ["接口设计"]}]}]}


def test_failed_delivery_goes_back_to_the_owner_then_to_the_ceo(tmp_path):
    env = Env(tmp_path, roles=("pm", "engineer", "qa"), plans=[PLAN])
    env.start()
    a, b = env.task("a"), env.task("b")
    assert a["status"] == "done" and a["deliverables"][0]["sections"] == ["产出"]      # 符合要求：照常完成
    assert "必须有这些章节" in env.briefs("engineer")[0]                                # 交付要求写在任务说明里
    assert b["status"] == "escalated" and b["reviews"][-1]["verdict"] == "checks"      # 缺章节：提交 3 次都被退回
    assert "缺少章节：接口设计" in b["reviews"][-1]["issues"][0]["text"]
    rejected = [m for r, msgs in env.world.calls if r == "engineer" for m in msgs
                if m["role"] == "tool" and m["content"].startswith("错误：交付检查没通过")]
    assert len({m["content"] for m in rejected}) >= 1 and not any(
        m["kind"] == "review" and m["task_id"] == b["id"] for m in env.chans()["all"])  # 没有浪费一次审查
    env.decide(b["id"], "accept")                                                      # CEO 接受：照常审查
    assert env.task("b")["status"] == "done" and env.task("b")["reviews"][-1]["verdict"] == "pass"


def test_rework_that_changes_nothing_is_sent_back(tmp_path):
    env = Env(tmp_path, roles=("pm", "engineer", "qa"), plans=[{"summary": "s", "tasks": PLAN["tasks"][:1] + [
        {**PLAN["tasks"][1], "deliverables": []}]}])
    env.world.verdicts = {"技术方案": [("changes", ["接口没有错误码"]), ("pass", [])]}
    env.world.lazy = {"engineer"}                                       # 返工时说改了，其实没改文件
    env.start()
    b = env.task("b")
    assert b["status"] == "escalated" and b["reviews"][-1]["verdict"] == "checks"
    assert "一字没变" in b["reviews"][-1]["issues"][0]["text"]


def test_second_review_is_incremental(tmp_path):
    env = Env(tmp_path, roles=("pm", "engineer", "qa"), plans=[{"summary": "s", "tasks": PLAN["tasks"][:1] + [
        {**PLAN["tasks"][1], "deliverables": []}]}])
    env.world.verdicts = {"技术方案": [("changes", ["接口没有错误码"]), ("pass", [])]}
    env.start()
    b = env.task("b")
    assert b["status"] == "done" and [r["verdict"] for r in b["reviews"]] == ["changes", "pass"]
    first, second = env.review_briefs("qa")
    assert "提交前必须用 read_file 读过要审查的文件：docs/engineer.md" in first and "这是复审" not in first
    assert "这是复审" in second and "【上轮问题 1】" in second and "接口没有错误码" in second
    assert "+# engineer 的产出（修改版）" in second and "提交前必须用 read_file" not in second   # 改动对比在说明里，不用重读
    assert b["reviews"][1]["previous"] == [{"no": 1, "status": "resolved", "note": ""}]
    assert "第 1 条：已修改" in b["result"]                                     # 负责人的逐条回应就是审查人看到的汇报


def test_headline_and_decisions_reach_the_task(tmp_path):
    """负责人交付时写给 CEO 的一句话结论和待确认事项：记在任务上（全公司频道的任务卡片、汇报用）；没写结论时取汇报的第一句。"""
    from fakes import call
    env = Env(tmp_path, roles=("pm", "engineer", "qa"), plans=[{"summary": "s", "tasks": [{**PLAN["tasks"][0], "deliverables": []}]}])
    env.world.first_calls["pm"] = [call("f1", "finish_task", headline="PRD 写好了，P0 共 5 项", summary="详细汇报",
                                        decisions=["年预算上限", "是否做海外"])]
    env.start()
    a = env.task("a")
    assert a["headline"] == "PRD 写好了，P0 共 5 项" and a["decisions"] == ["年预算上限", "是否做海外"]
    msg = next(m for m in env.chans()["all"] if m.get("task_id") == a["id"] and m["kind"] == "text")
    assert msg["headline"] == a["headline"] and msg["decisions"] == a["decisions"]


def test_files_have_titles(company):
    company.write_file("docs/a.md", "前言\n# 约课小程序 PRD\n正文", author="pm")
    company.write_file("code/x.py", "print(1)", author="engineer")
    titles = {f["path"]: f.get("title") for f in company.list_files()}
    assert titles == {"docs/a.md": "约课小程序 PRD", "code/x.py": None}
