"""实时显示正在写的文档：解析还没生成完的工具参数，增量推送 writing 事件；返工用 edit_file 局部修改。"""
import json
import threading

from opc import agents
from opc.agents import partial_json_string, run_role
from opc.company import CompanyStore
from opc.demo import DemoLLM, demo_doc
from opc.roles import ROLES


def test_partial_json_string_decodes_what_has_arrived():
    raw = json.dumps({"path": "docs/PRD.md", "content": '# 标题\n第一行 "引号" \\ 反斜杠\t制表 é'}, ensure_ascii=True)
    full = partial_json_string(raw, "content")
    assert full == '# 标题\n第一行 "引号" \\ 反斜杠\t制表 é'
    assert partial_json_string(raw, "path", complete_only=True) == "docs/PRD.md"
    for cut in range(len(raw)):  # 任意位置截断都不报错，取出的总是完整结果的前缀
        part = partial_json_string(raw[:cut], "content")
        assert part is None or full.startswith(part)
    assert partial_json_string('{"path": "docs/P', "path", complete_only=True) is None
    assert partial_json_string('{"path": "a.md"', "content") is None


def test_document_streams_to_ui_and_rework_edits_in_place(tmp_path, monkeypatch):
    monkeypatch.setattr(agents, "LIVE_INTERVAL", 0.0)  # 每段都推送，方便检查拼接
    company = CompanyStore(tmp_path, llm_factory=lambda cfg, role_id: DemoLLM(role_id, delay=0)).create(
        "演示", "", [{"id": "pm"}])
    events = []
    out = run_role(company, "pm", [{"role": "user", "content": "写需求文档"}], events.append, "m1", "all",
                   threading.Event(), 5)
    path = ROLES["pm"].default_file
    writing = [e for e in events if e["type"] == "writing"]
    assert writing and all(e["path"] == path and e["tool"] == "write_file" for e in writing if e["path"])
    assert "".join(e["text"] for e in writing) == demo_doc(ROLES["pm"]) == company.read_file(path)
    assert writing[-1]["chars"] == len(demo_doc(ROLES["pm"])) and out["files"] == [path]

    events.clear()
    run_role(company, "pm", [{"role": "user", "content": "# 这是返工：补上验收标准"}], events.append, "m2", "all",
             threading.Event(), 5)
    edits = [e for e in events if e["type"] == "tool" and e["name"] == "edit_file"]
    assert edits and edits[0]["ok"]
    after = company.read_file(path)
    assert "验收标准（返工时补充）" in after and after.startswith(demo_doc(ROLES["pm"]).split("## 背景")[0])
    assert all(e["tool"] == "edit_file" for e in events if e["type"] == "writing")  # 预览的是修改的新内容
