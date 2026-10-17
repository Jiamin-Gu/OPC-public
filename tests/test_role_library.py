"""角色库（v0.14）：从开源角色库导入的角色可以加入公司；交付物模板跟着任务走，只在公司里有这个角色时可选。"""
import json

from opc import prompts, task_formats
from opc.agents import system_prompt
from opc.company import CompanyStore
from opc.planner import parse_deliverables, parse_plan
from opc.roles import LIBRARY, LIBRARY_ROLES, PRESET_ROLES, ROLES

ROLE = "marketing-growth-hacker"  # 角色库里的增长黑客，原文带"增长实验看板"模板


def test_library_loaded():
    index = json.loads((LIBRARY / "index.json").read_text(encoding="utf-8"))
    assert len(LIBRARY_ROLES) == len(index) > 250
    assert all(r.id in ROLES for r in PRESET_ROLES + LIBRARY_ROLES)
    assert all(r.department and r.department_name for r in LIBRARY_ROLES)
    assert ROLES["engineering-frontend-developer"].can_code and not ROLES[ROLE].can_code


def test_every_library_role_has_both_versions():
    for r in LIBRARY_ROLES:
        folder = LIBRARY / r.department
        complete = (folder / f"{r.id}.complete.md").read_text(encoding="utf-8")
        concise = (folder / f"{r.id}.concise.md").read_text(encoding="utf-8")
        assert not complete.startswith("---") and "## 你做不到的事" in concise, r.id
        assert "```" not in concise, r.id  # 简洁版不带模板和代码块
        if not r.can_code:  # 编码 agent 只有写代码的角色才有
            assert "编码 agent" not in concise.split("## 你做不到的事")[0], r.id


def test_library_prompt_is_complete_plus_boundary():
    text = prompts.role_prompt(ROLE)
    assert text.startswith((LIBRARY / "marketing" / f"{ROLE}.complete.md").read_text(encoding="utf-8").strip())
    assert "## 你做不到的事" in text


def test_library_role_in_company(tmp_path):
    company = CompanyStore(tmp_path).create("公司", "", [{"id": "pm"}, {"id": ROLE}, {"id": ROLE}])
    assert company.role_ids == ["pm", ROLE]  # 重复的只保留一个
    prompt = system_prompt(company, ROLE)
    assert prompts.role_prompt(ROLE) in prompt and ROLES[ROLE].name in prompt
    assert "增长实验跟踪表" not in prompt  # 模板不在角色提示词里


def test_role_templates_follow_the_task():
    name = f"role:{ROLE}"
    assert name not in task_formats.catalog(["pm"]) and name in task_formats.catalog(["pm", ROLE])
    assert parse_deliverables([{"path": "docs/x.md", "template": name}], ["pm"])[0]["template"] == ""
    assert parse_deliverables([{"path": "docs/x.md", "template": name}], [ROLE])[0]["template"] == name
    brief = task_formats.requirements_brief({"deliverables": [{"path": "docs/x.md", "template": name}]})
    assert "增长实验" in brief and ROLES[ROLE].name in brief
    assert not task_formats.is_known("role:nope", ["nope"])


def test_plan_accepts_library_role_template():
    plan = json.dumps({"summary": "s", "tasks": [{"key": "t1", "title": "增长", "assignee": ROLE,
                                                  "deliverables": [{"path": "docs/growth.md", "template": f"role:{ROLE}"}]}]})
    _, tasks = parse_plan(plan, ["pm", ROLE])
    assert tasks[0]["deliverables"][0]["template"] == f"role:{ROLE}"


def test_ceo_becomes_advisor():
    """用户本人是 CEO：角色库里的 CEO 改成 CEO 顾问，提示词开头说明定位。"""
    r = ROLES["chief-executive-officer"]
    assert r.name == "CEO 顾问" and "由你拍板" in r.tagline
    text = prompts.role_prompt("chief-executive-officer")
    assert text.startswith("在这家公司里，CEO 是正在给你下指令的用户本人") and "## 你做不到的事" in text
