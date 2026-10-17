"""导出公司成果：文件区 + 对话记录。"""
from fastapi.testclient import TestClient

from opc.export import export
from opc.web.runtime import Hub
from opc.web.server import create_app

from fakes import World, make_store


def test_export_files_and_transcript(tmp_path):
    world = World()
    hub = Hub(make_store(tmp_path, world))
    client = TestClient(create_app(hub))
    cid = client.post("/api/companies", json={"name": "导出测试", "mission": "测一下",
                                              "roles": [{"id": "pm"}, {"id": "engineer"}, {"id": "designer"}]}).json()["id"]
    with client.websocket_connect("/ws") as ws:
        ws.receive_json()
        ws.send_json({"type": "open_company", "id": cid})
        while ws.receive_json()["type"] != "snapshot":
            pass
        ws.send_json({"type": "send", "text": "做一个 App", "target": "all"})
        while not (ev := ws.receive_json())["type"] == "status" or not ev.get("review"):
            pass
        ws.send_json({"type": "review", "action": "approve"})
        # 等到汇报出来（批准前那条"空闲"的状态事件可能还在缓冲里，不能用它判断结束）
        while not ((ev := ws.receive_json())["type"] == "message" and ev["message"]["kind"] == "report"):
            pass
    out = export(cid, hub.store.home, tmp_path / "out")
    assert (out / "files" / "docs" / "pm.md").read_text(encoding="utf-8") == "# pm 的产出"
    md = (out / "transcript.md").read_text(encoding="utf-8")
    assert "# 导出测试：对话记录" in md and "做一个 App" in md
    assert "**执行计划**" in md and "**执行汇报**" in md and "产出文件：`docs/pm.md`" in md
    assert "| 📋 产品经理 | 2 |" in md
