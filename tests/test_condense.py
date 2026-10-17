"""超过建议篇幅的文档：不退回，完整版保留，另外生成简洁版，两个版本都交付（v0.16）；
简洁版没比完整版短出一截就不保存（v0.18：试跑里"简洁版"2.05 万字、完整版 2.3 万字）。"""
from opc import condense

from harness import Env

LONG = {"summary": "s", "tasks": [
    {"key": "t1", "title": "写 PRD", "detail": "", "assignee": "pm", "reviewer": "engineer",
     "deliverables": [{"path": "docs/pm.md", "max_chars": 50}]},
    {"key": "t2", "title": "写方案", "detail": "", "assignee": "engineer", "depends_on": ["t1"],
     "deliverables": [{"path": "docs/engineer.md"}]}]}
BODY = "\n" + "正文内容。" * 60  # 300 字，超过建议的 50 字


def env_with(tmp_path, padding=BODY):
    env = Env(tmp_path, roles=("pm", "engineer"), plans=[LONG], auto_approve=True)
    env.world.padding = {"pm": padding}
    return env


def test_concise_path_and_target():
    assert condense.concise_path("docs/PRD.md") == "docs/PRD.简洁版.md"
    assert condense.is_concise("docs/PRD.简洁版.md") and not condense.is_concise("docs/PRD.md")
    assert condense.target({"max_chars": 10000}, 23000) == 10000 and condense.target({"max_chars": 10000}, 12000) == 7200
    assert condense.problems("字" * 9000, {"max_chars": 10000}, 12000)        # 比目标长，也没短出 30%
    assert not condense.problems("字" * 7000, {"max_chars": 10000}, 12000)


def test_long_document_gets_concise_version(tmp_path):
    env = env_with(tmp_path)
    env.directive()
    t1, t2 = env.task("t1"), env.task("t2")
    assert t1["status"] == "done" and t2["status"] == "done"            # 超了不退回
    assert env.company.read_file("docs/pm.md").startswith("# pm 的产出")  # 完整版原样保留
    assert "这是简洁版" in env.company.read_file("docs/pm.简洁版.md")
    assert set(t1["files"]) == {"docs/pm.md", "docs/pm.简洁版.md"}       # 两个版本都交付
    assert env.world.condensed == ["pm"] and env.state()["usage"]["pm"]["calls"] >= 3  # 用负责人的模型，用量记在他名下
    texts = [m["text"] for m in env.chans()["all"]]
    assert any("另外生成一份简洁版" in t for t in texts) and any("简洁版" in t and "完整版 " in t for t in texts)
    review = env.review_briefs("engineer")[0]                            # 审查只看完整版
    assert "产出文件：docs/pm.md\n" in review and "不用审查，以完整版为准" in review
    assert "以完整版为准" in env.briefs("engineer")[0]                  # 下游知道哪份是简洁版
    assert "engineer" not in env.world.condensed                         # 没超的不生成


def test_concise_version_follows_rework(tmp_path):
    """完整版返工改过之后，简洁版跟着重新生成。"""
    env = env_with(tmp_path)
    env.world.verdicts = {"写 PRD": [("changes", ["少了验收标准"]), ("pass", [])]}
    env.directive()
    assert env.task("t1")["status"] == "done" and env.world.condensed == ["pm", "pm"]
    assert "（修改版）" in env.company.read_file("docs/pm.简洁版.md")


def test_concise_not_saved_when_not_shorter(tmp_path):
    """压不下来（没比完整版短出 30%）就不保存，交付照常。"""
    plan = {**LONG, "tasks": [{**LONG["tasks"][0], "deliverables": [{"path": "docs/pm.md", "max_chars": 10}]},
                              LONG["tasks"][1]]}
    env = Env(tmp_path, roles=("pm", "engineer"), plans=[plan], auto_approve=True)
    env.world.padding = {"pm": "\n" + "字" * 10}  # 约 20 字：超过建议的 10 字，但假模型的"简洁版"短不了多少
    env.directive()
    t1 = env.task("t1")
    assert t1["status"] == "done" and t1["files"] == ["docs/pm.md"]
    assert not env.company.resolve("docs/pm.简洁版.md").exists()
    assert any("没压下来" in m["text"] for m in env.chans()["all"])
