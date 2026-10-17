"""角色提示词（v0.18 起）：一律用完整版（原文），后面接上"你做不到的事"；审查规范用 OPC 自己的；旧版本设置兼容。"""
import pytest

from opc import prompts
from opc.agents import system_prompt
from opc.company import CompanyStore
from opc.jobs import review_brief
from opc.roles import LIBRARY, PRESET_ROLES, ROLES


def test_role_prompt_is_complete_plus_boundary():
    for role_id in (r.id for r in PRESET_ROLES):
        text = prompts.role_prompt(role_id)
        complete = (prompts.DIR / f"{role_id}.complete.md").read_text(encoding="utf-8").strip()
        assert text.startswith(complete) and not text.startswith("---"), role_id  # 原文，去掉了头信息
        assert text.count("## 你做不到的事") == 1, role_id                     # 接上了能力边界
    assert prompts.role_prompt("pm", "concise") == prompts.role_prompt("pm") == prompts.role_prompt("pm", "unknown")


def test_every_role_has_boundary():
    for role_id in ROLES:  # 包括角色库的 277 个角色：写明边界，避免假装做过访谈、看过数据
        assert "## 你做不到的事" in prompts.role_prompt(role_id), role_id
    lib = next(r for r in ROLES.values() if r.department)
    assert prompts.role_prompt(lib.id).startswith(
        (LIBRARY / lib.department / f"{lib.id}.complete.md").read_text(encoding="utf-8").strip())


def test_boundary_extraction():
    text = "# A\n## 关键规则\n1. x\n## 你做不到的事\n不能访谈用户。\n## 附录\ny"
    assert prompts.boundary(text) == "## 你做不到的事\n不能访谈用户。"
    assert prompts.boundary("# 没有这一节") == ""


def test_review_rules_are_opc_rules():
    rules = prompts.review_rules()
    assert "必须修改" in rules and "submit_review" in rules and rules == prompts.review_rules("complete")


def test_company_prompt_level_is_always_complete(tmp_path):
    store = CompanyStore(tmp_path)
    company = store.create("公司", "目标", [{"id": "pm"}, {"id": "qa", "prompt_level": "concise"}], prompt_level="concise")
    assert company.prompt_level("pm") == company.prompt_level("qa") == "complete"
    assert prompts.role_prompt("pm") in system_prompt(company, "pm")
    assert "测试计划模板" not in system_prompt(company, "qa")   # 格式跟着任务走，不放在角色里
    payload = {"directive": {"text": "做个记账工具"},
               "task": {"assignee": "pm", "reviewer": "qa", "title": "PRD", "detail": "写 PRD", "files": [], "result": ""}}
    assert "问题分级" in review_brief(payload, 1, company)     # OPC 的审查规范
    with pytest.raises(ValueError):
        store.create("公司", "", [{"id": "pm", "prompt_level": "huge"}])


def test_legacy_level_names(tmp_path):
    """以前的版本名（full / original / brief / minimal / concise）：已经创建的公司里可能存着，都当作完整版。"""
    store = CompanyStore(tmp_path)
    company = store.create("公司", "", [{"id": "pm", "prompt_level": "original"}], prompt_level="brief")
    for old in ("full", "brief", "minimal", "concise", "original"):
        company.meta["prompt_level"] = old          # 模拟磁盘上的旧数据
        assert company.prompt_level("qa") == "complete"
