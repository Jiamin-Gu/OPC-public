"""Web 层测试：创建公司、WebSocket 事件流、审批、单独对话、停止、文件接口。"""
import pytest
from fastapi.testclient import TestClient

from opc.web.runtime import Hub
from opc.web.server import create_app

from fakes import World, make_store


@pytest.fixture
def app(tmp_path):
    world = World()
    hub = Hub(make_store(tmp_path, world))
    return TestClient(create_app(hub)), hub, world


def create(client, roles=("pm", "engineer", "designer")):
    res = client.post("/api/companies", json={"name": "测试公司", "mission": "做点东西",
                                              "roles": [{"id": r} for r in roles]})
    assert res.status_code == 200
    return res.json()["id"]


def recv_until(ws, pred, limit=500):
    events = []
    for _ in range(limit):
        ev = ws.receive_json()
        events.append(ev)
        if pred(ev):
            return events
    raise AssertionError("没有等到期望的事件")


def idle(ev):
    return ev["type"] == "status" and not ev["busy"]


def awaiting(ev):
    """有事等 CEO 处理（审批计划 / 裁决），并且团队已经停下。"""
    return ev["type"] == "status" and ev.get("review") and not ev["busy"]


def test_create_validation(app):
    client, _, _ = app
    assert client.post("/api/companies", json={"name": " ", "roles": [{"id": "pm"}]}).status_code == 400
    assert client.post("/api/companies", json={"name": "x", "roles": []}).status_code == 400
    assert client.post("/api/companies", json={"name": "x", "roles": [{"id": "ceo"}]}).status_code == 400


def test_full_flow_over_websocket(app):
    client, hub, world = app
    cid = create(client)
    with client.websocket_connect("/ws") as ws:
        home = ws.receive_json()
        assert home["type"] == "home" and home["companies"][0]["id"] == cid and len(home["presets"]) == 6
        ws.send_json({"type": "open_company", "id": cid})
        snap = recv_until(ws, lambda e: e["type"] == "snapshot")[-1]
        assert [r["id"] for r in snap["company"]["roles"]] == ["pm", "engineer", "designer"]

        ws.send_json({"type": "send", "text": "做一个 App", "target": "all"})
        events = recv_until(ws, awaiting)
        assert any(e["type"] == "message" and e["message"]["kind"] == "plan" for e in events)

        # 有待审批的计划时不能发新指令
        ws.send_json({"type": "send", "text": "插个队", "target": "all"})
        assert "审批" in recv_until(ws, lambda e: e["type"] == "notice")[-1]["text"]

        ws.send_json({"type": "review", "action": "approve"})
        events = recv_until(ws, idle)
    types = {e["type"] for e in events}
    assert {"delta", "tool", "files", "tasks", "role_status", "usage"} <= types
    # 进度显示：任务事件带进入当前状态的时间；执行中的状态带运行开始时间，结束后清空
    assert all(set(e["since"]) == set(e["tasks"]) for e in events if e["type"] == "tasks")
    assert any(e["type"] == "status" and e["busy"] and e["run_started"] for e in events)
    assert events[-1]["run_started"] is None and events[-1]["server_now"] > 0
    assert any(e["type"] == "message" and e["message"]["kind"] == "report" for e in events)
    assert all(t["status"] == "done" for t in hub.state()["tasks"].values())
    assert client.get("/api/file", params={"path": "docs/pm.md"}).json()["content"] == "# pm 的产出"
    assert client.get("/api/file", params={"path": "../../company.json"}).status_code == 404


def test_reconnect_and_reopen_restores_state(app, tmp_path):
    client, hub, world = app
    cid = create(client, roles=("pm",))
    with client.websocket_connect("/ws") as ws:
        ws.receive_json()
        ws.send_json({"type": "open_company", "id": cid})
        recv_until(ws, lambda e: e["type"] == "snapshot")
        ws.send_json({"type": "send", "text": "你好", "target": "pm"})
        recv_until(ws, lambda e: e["type"] == "message" and e["message"]["sender"] == "pm")  # 私聊不算团队在忙：等它回复
    assert hub.engine.wait_idle(5)
    # 模拟重启：新的 Hub 从 SQLite 检查点恢复
    hub2 = Hub(hub.store)
    assert hub2.open_company(cid) is None
    assert [m["sender"] for m in hub2.log.read("pm")] == ["ceo", "pm"]


def test_stop_marks_tasks_cancelled(app):
    client, hub, world = app
    world.delay = 0.5
    world.plans = [{"summary": "s", "tasks": [  # 计划只能用公司里有的角色
        {"key": "t1", "title": "PRD", "detail": "", "assignee": "pm", "depends_on": []},
        {"key": "t2", "title": "方案", "detail": "", "assignee": "engineer", "depends_on": ["t1"]}]}]
    cid = create(client, roles=("pm", "engineer"))
    with client.websocket_connect("/ws") as ws:
        ws.receive_json()
        ws.send_json({"type": "open_company", "id": cid})
        recv_until(ws, lambda e: e["type"] == "snapshot")
        ws.send_json({"type": "send", "text": "做一个 App", "target": "all"})
        recv_until(ws, awaiting)
        ws.send_json({"type": "review", "action": "approve"})
        recv_until(ws, lambda e: e["type"] == "delta")
        ws.send_json({"type": "stop"})
        recv_until(ws, idle)
    statuses = [t["status"] for t in hub.state()["tasks"].values()]
    assert statuses and all(s in ("cancelled", "done") for s in statuses) and "cancelled" in statuses
    assert hub.log.read("all")[-1]["text"] == "CEO 停止了执行"
    # 没有任务还挂着工作运行，引擎停下了，可以继续下令
    assert not any(t.get("job") for t in hub.state()["tasks"].values()) and not hub.busy and not hub.engine.cancel.is_set()


def test_api_keys_are_not_sent_to_browser(app):
    client, hub, _ = app
    res = client.post("/api/companies", json={"name": "x", "roles": [{"id": "pm", "api_key": "sk-secret", "model": "m1"}]})
    company = hub.store.load(res.json()["id"])
    public = company.public_meta()
    assert "sk-secret" not in str(public) and public["roles"][0] == {
        "id": "pm", "model": "m1", "base_url": company.llm_config("pm").base_url,
        "provider": company.llm_config("pm").provider, "model_ref": None, "legacy_model": True,
        "prompt_level": "complete", "price": None}


def test_delete_company(app):
    client, hub, _ = app
    cid = create(client, roles=("pm",))
    assert client.delete(f"/api/companies/{cid}").status_code == 200
    assert hub.store.load(cid) is None


def test_interject_while_running_and_escalation_decision(app):
    client, hub, world = app
    world.delay = 0.3
    world.plans = [{"summary": "s", "tasks": [
        {"key": "t1", "title": "PRD", "detail": "", "assignee": "pm", "depends_on": []},
        {"key": "t2", "title": "方案", "detail": "", "assignee": "engineer", "depends_on": ["t1"], "reviewer": "pm"}]}]
    world.verdicts = {"方案": [("changes", ["缺少数据库设计"]), ("changes", ["还是缺少"])]}  # PRD 的自动审查通过
    cid = create(client, roles=("pm", "engineer"))
    with client.websocket_connect("/ws") as ws:
        ws.receive_json()
        ws.send_json({"type": "open_company", "id": cid})
        recv_until(ws, lambda e: e["type"] == "snapshot")
        ws.send_json({"type": "send", "text": "做一个 App", "target": "all"})
        recv_until(ws, awaiting)
        ws.send_json({"type": "review", "action": "approve"})
        recv_until(ws, lambda e: e["type"] == "delta")
        # 执行中发言：不会被拒绝，作为插话实时推送
        ws.send_json({"type": "send", "text": "数据要能导出", "target": "all"})
        ev = recv_until(ws, lambda e: e["type"] == "message" and e["message"]["sender"] == "ceo")[-1]
        assert ev["message"]["kind"] == "interject" and ev["message"]["directive_id"]  # 插话属于当前指令
        # 两轮审查不通过 → 上报 CEO
        events = recv_until(ws, awaiting)
        assert events[-1]["review"]["type"] == "escalation"
        assert any(e["type"] == "message" and e["message"]["kind"] == "review" for e in events)
        ws.send_json({"type": "review", "action": "approve"})  # 裁决时只接受 accept / rework / drop
        ws.send_json({"type": "review", "action": "accept", "feedback": "先这样"})
        recv_until(ws, lambda e: idle(e) and not e.get("review"))
    tasks = list(hub.state()["tasks"].values())
    assert [t["status"] for t in tasks] == ["done", "done"] and tasks[1]["ceo_note"] == "先这样"
    msgs = hub.log.read("all")
    assert [m["text"] for m in msgs].count("数据要能导出") == 1  # 镜像里没有重复
    saved = hub.log.read("all")
    assert [m["text"] for m in saved].count("数据要能导出") == 1  # 持久化了
    assert any("数据要能导出" in str(m) for r, m in world.calls if r == "engineer")


def test_network_outage_pauses_and_resumes_without_redoing_work(app, monkeypatch):
    """模型服务连不上时团队原地暂停：界面收到暂停状态，已完成的任务不重做，恢复后同一个任务接着做完。"""
    import httpx
    import threading
    from openai import APIConnectionError
    from opc.llm import NETWORK, NetworkGate
    monkeypatch.setattr(NetworkGate, "PROBE_SECONDS", 30)
    client, hub, world = app
    up = threading.Event()
    err = APIConnectionError(request=httpx.Request("POST", "http://x"))

    def outage(role, messages):  # 和 LLM.chat 重试用完后一样：每个探测周期醒来重试一次，直到网络恢复
        if role == "engineer":
            while not up.is_set():
                NETWORK.wait(err, None)
    world.on_call = outage
    cid = create(client, roles=("pm", "engineer"))
    world.plans = [{"summary": "s", "tasks": [
        {"key": "t1", "title": "PRD", "detail": "", "assignee": "pm", "depends_on": []},
        {"key": "t2", "title": "方案", "detail": "", "assignee": "engineer", "depends_on": ["t1"]}]}]
    with client.websocket_connect("/ws") as ws:
        ws.receive_json()
        ws.send_json({"type": "open_company", "id": cid})
        recv_until(ws, lambda e: e["type"] == "snapshot")
        ws.send_json({"type": "send", "text": "做一个 App", "target": "all"})
        recv_until(ws, awaiting)
        ws.send_json({"type": "review", "action": "approve"})
        ev = recv_until(ws, lambda e: e["type"] == "status" and e.get("paused"))[-1]
        assert ev["busy"] and "连不上模型服务" in ev["paused"]["reason"]
        pm_calls = sum(1 for r, _ in world.calls if r == "pm")
        up.set()
        ws.send_json({"type": "retry_now"})          # 不用等 30 秒
        recv_until(ws, lambda e: e["type"] == "status" and not e.get("paused"))
        recv_until(ws, idle)
    assert sum(1 for r, _ in world.calls if r == "pm") == pm_calls   # PM 的任务没有重做
    assert all(t["status"] == "done" for t in hub.state()["tasks"].values())
    assert NETWORK.state is None


def test_task_action_rework_from_board(app):
    """看板上让上游返工：团队空闲时从汇合点接着跑，上游改完后依赖它的同事带着改动对比同步，最后重新汇报。"""
    client, hub, world = app
    cid = create(client)
    with client.websocket_connect("/ws") as ws:
        ws.receive_json()
        ws.send_json({"type": "open_company", "id": cid})
        recv_until(ws, lambda e: e["type"] == "snapshot")
        ws.send_json({"type": "send", "text": "做一个 App", "target": "all"})
        recv_until(ws, awaiting)
        ws.send_json({"type": "review", "action": "approve"})
        recv_until(ws, idle)
        pm_task = next(t for t in hub.state()["tasks"].values() if t["assignee"] == "pm")
        calls = {r: sum(1 for x, _ in world.calls if x == r) for r in ("pm", "engineer")}
        ws.send_json({"type": "task_action", "action": "rework", "task_id": pm_task["id"], "feedback": "加上候补规则"})
        events = recv_until(ws, lambda e: e["type"] == "status" and e["busy"])
        events += recv_until(ws, idle)
    assert sum(1 for x, _ in world.calls if x == "pm") > calls["pm"]
    assert sum(1 for x, _ in world.calls if x == "engineer") > calls["engineer"]
    texts = [m["text"] for m in hub.log.read("all")]
    assert any("返工：加上候补规则" in t for t in texts) and hub.log.read("all")[-1]["kind"] == "report"
    assert all(t["status"] == "done" for t in hub.state()["tasks"].values())
    assert any(e["type"] == "tasks" for e in events)


@pytest.mark.filterwarnings("ignore::pytest.PytestUnhandledThreadExceptionWarning")  # 模拟进程被关掉：那个线程直接结束
def test_resume_unfinished_run_after_restart(app):
    """执行到一半程序被关掉：重新打开公司时提示没跑完，点继续接着跑，已完成的任务不重做。"""
    import time
    client, hub, world = app
    cid = create(client)
    crashed = []

    def crash(role, messages):  # 工程师干活时进程"死掉"（不是普通异常，工作运行来不及交回结果）
        if role == "engineer" and not crashed and "请你审查" not in messages[1]["content"]:
            crashed.append(1)
            raise SystemExit("模拟程序被关掉")
    world.on_call = crash
    assert hub.open_company(cid) is None
    hub.send("做一个 App", "all")
    for _ in range(200):
        if hub.review and not hub.busy:
            break
        time.sleep(0.02)
    hub.resolve_review("approve", "")
    for _ in range(200):
        if crashed and not hub.busy:
            break
        time.sleep(0.02)
    eng = next(t for t in hub.state()["tasks"].values() if t["assignee"] == "engineer")
    assert eng["status"] == "running" and eng["job"]          # 检查点里：工程师的任务还挂着工作运行
    pm_calls = sum(1 for r, _ in world.calls if r == "pm")

    hub2 = type(hub)(hub.store)                                # 重新启动
    assert hub2.open_company(cid) is None and hub2.unfinished
    assert hub2.snapshot_event()["unfinished"] is True
    hub2.handle({"type": "resume_run"})
    assert hub2.engine.wait_idle(10)
    assert not hub2.unfinished
    assert all(t["status"] == "done" for t in hub2.state()["tasks"].values())
    assert sum(1 for r, _ in world.calls if r == "pm") == pm_calls   # 产品经理的任务没有重做
    assert hub2.log.read("all")[-1]["kind"] == "report"
