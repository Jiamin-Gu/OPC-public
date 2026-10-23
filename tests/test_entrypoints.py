"""入口模块能被导入（防止只在启动时才暴露的语法错误）。"""
import importlib


def test_entrypoints_import():
    for name in ("opc.web.__main__", "opc.web.server", "opc.web.guard", "opc.web_tools", "opc.knowledge.__main__"):
        importlib.import_module(name)


def test_demo_plan_with_three_roles(tmp_path):
    """演示模式的假规划助手：公司只有 3 个成员时也能出计划（以前把提示词里的"- small（小任务）"当成了角色）。"""
    from opc import planner
    from opc.company import CompanyStore
    from opc.demo import demo_factory
    store = CompanyStore(tmp_path / "home", llm_factory=demo_factory)
    company = store.create("轻记", "x", [{"id": r} for r in ("pm", "engineer", "qa")])
    _, specs, _ = planner.make_plan(company, "做一个命令行记账工具", "", [], None)
    assert {s["assignee"] for s in specs} == {"pm", "engineer", "qa"}
