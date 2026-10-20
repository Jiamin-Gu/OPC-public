"""链路追踪：步骤的嵌套和跨线程传递、一条指令的完整链路、模型调用的属性、OTLP 导出格式、网页接口。"""
import json
import threading
from types import SimpleNamespace

import httpx
from fastapi.testclient import TestClient

from opc import tracing
from opc.company import CompanyStore
from opc.config import LLMConfig
from opc.llm import LLM
from opc.web.runtime import Hub
from opc.web.server import create_app

from fakes import World, make_store
from harness import Env


def test_spans_nest_and_noop_without_trace(tmp_path):
    company = CompanyStore(tmp_path).create("公司", "", [{"id": "pm"}])
    with tracing.span("没有链路时什么也不记") as s:
        assert s is tracing.NOOP
    root = tracing.root(company, "directive:d1", "指令：做 App")
    with tracing.use(root):
        with tracing.span("规划", "chain") as plan:
            with tracing.span("模型调用", "llm") as call:
                call.set(**{"gen_ai.usage.input_tokens": 10})
        try:
            with tracing.span("工具 read_file", "tool"):
                raise ValueError("文件不存在")
        except ValueError:
            pass
    spans = {s["name"]: s for s in tracing.store_for(company).trace(root.trace_id)}
    assert spans["模型调用"]["parent_id"] == plan.span_id and spans["规划"]["parent_id"] == root.span_id
    assert spans["模型调用"]["attrs"]["gen_ai.usage.input_tokens"] == 10 and spans["规划"]["status"] == "ok"
    assert spans["工具 read_file"]["status"] == "error" and "文件不存在" in spans["工具 read_file"]["attrs"]["error"]
    assert spans["指令：做 App"]["end"] is None                 # 根还没结束
    again = tracing.root(company, "directive:d1", "别的名字")    # 同一条指令再来一个事件：还是同一个根
    assert again.span_id == root.span_id and again.name == "指令：做 App" and again.start == root.start
    root.finish()
    reopened = tracing.root(company, "directive:d1", "")         # 汇报之后又有动作：重新打开
    assert reopened.end is None and tracing.store_for(company).get(root.span_id)["status"] == "running"


def test_current_span_is_carried_into_threads_explicitly(tmp_path):
    company = CompanyStore(tmp_path).create("公司", "", [{"id": "pm"}])
    root = tracing.root(company, "chat:x", "私聊", kind="chat")
    seen = []
    with tracing.use(root):
        parent = tracing.current()
    t = threading.Thread(target=lambda: seen.append(tracing.current()))  # 新线程里没有当前步骤
    t.start(); t.join()
    assert seen == [None] and parent is root


def test_directive_trace_has_plan_jobs_tools_and_report(tmp_path):
    env = Env(tmp_path, roles=("pm", "engineer"), auto_approve=True, plans=[{"summary": "先写需求再出方案", "tasks": [
        {"key": "t1", "title": "写 PRD", "detail": "docs/pm.md", "assignee": "pm", "depends_on": []},
        {"key": "t2", "title": "技术方案", "detail": "docs/engineer.md", "assignee": "engineer", "depends_on": ["t1"],
         "reviewer": ""}]}])
    env.start("做一个记账 App", did="d1")
    env.engine.close()
    spans = tracing.store_for(env.company).trace(tracing.trace_id(env.company, "directive:d1"))
    by = {s["name"]: s for s in spans}
    root = next(s for s in spans if s["parent_id"] is None)
    assert root["name"] == "指令：做一个记账 App" and root["status"] == "ok" and root["end"]
    assert by["规划"]["parent_id"] == root["span_id"] and by["汇报"]["parent_id"] == root["span_id"]
    assert by["规划"]["attrs"]["opc.tasks"] == 2
    pm = by["执行《写 PRD》（产品经理）"]
    assert pm["parent_id"] == root["span_id"] and pm["attrs"]["opc.task_status"] and pm["end"]
    tools = [s for s in spans if s["kind"] == "tool" and s["parent_id"] == pm["span_id"]]
    assert {"工具 write_file", "工具 finish_task"} <= {s["name"] for s in tools}   # 角色执行循环里的工具挂在它下面
    assert any(s["name"] == "知识库召回" and s["parent_id"] == by["规划"]["span_id"] for s in spans)
    listed = tracing.store_for(env.company).traces()
    assert listed[0]["trace_id"] == root["trace_id"] and listed[0]["spans"] == len(spans)


def test_llm_call_span_records_model_tokens_and_latency(tmp_path, monkeypatch):
    company = CompanyStore(tmp_path).create("公司", "", [{"id": "pm"}])
    llm = LLM(LLMConfig(api_key="k", base_url="http://127.0.0.1:9/v1", model="fake-model"))

    def once(messages, tools, on_delta, json_mode, on_tool_delta=None):
        on_delta("text", "你好")
        return SimpleNamespace(content="你好，我来写", reasoning_content=None, finish_reason="stop",
                               tool_calls=[SimpleNamespace(id="c", function=SimpleNamespace(name="write_file", arguments="{}"))],
                               usage=SimpleNamespace(prompt=1200, completion=80, cached=1000))
    monkeypatch.setattr(llm, "_chat_once", once)
    root = tracing.root(company, "directive:d9", "指令")
    with tracing.use(root):
        llm.chat([{"role": "user", "content": "写一份 PRD"}], tools=[{"type": "function"}])
    call = next(s for s in tracing.store_for(company).trace(root.trace_id) if s["kind"] == "llm")
    a = call["attrs"]
    assert call["name"] == "模型调用 fake-model" and a["gen_ai.request.model"] == "fake-model"
    assert (a["gen_ai.usage.input_tokens"], a["gen_ai.usage.output_tokens"], a["gen_ai.usage.cached_tokens"]) == (1200, 80, 1000)
    assert a["opc.tool_calls"] == ["write_file"] and a["opc.ttft_ms"] >= 0 and a["opc.input_preview"] == "写一份 PRD"
    assert a["gen_ai.system"] == "127.0.0.1"


def test_otlp_export_format(tmp_path, monkeypatch):
    company = CompanyStore(tmp_path).create("公司", "", [{"id": "pm"}])
    root = tracing.root(company, "directive:d1", "指令")
    with tracing.use(root):
        with tracing.span("模型调用 m", "llm", **{"gen_ai.usage.input_tokens": 5}) as call:
            pass
    sent = []

    def handler(request):
        sent.append((dict(request.headers), json.loads(request.content)))
        return httpx.Response(200)
    monkeypatch.setenv("OPC_OTLP_ENDPOINT", "http://collector:4318/v1/traces")
    monkeypatch.setenv("OPC_OTLP_HEADERS", "Authorization=Basic abc, X-Team=opc")
    tracing.export_otlp([root, call], client=httpx.Client(transport=httpx.MockTransport(handler)))
    headers, body = sent[0]
    assert headers["authorization"] == "Basic abc" and headers["x-team"] == "opc"
    spans = body["resourceSpans"][0]["scopeSpans"][0]["spans"]
    llm = next(s for s in spans if s["name"] == "模型调用 m")
    assert llm["parentSpanId"] == root.span_id and llm["kind"] == 3 and len(llm["traceId"]) == 32
    assert {"key": "gen_ai.usage.input_tokens", "value": {"intValue": "5"}} in llm["attributes"]
    assert int(llm["endTimeUnixNano"]) >= int(llm["startTimeUnixNano"])
    assert body["resourceSpans"][0]["resource"]["attributes"][0]["value"]["stringValue"] == "opc"


def test_old_traces_are_pruned(tmp_path):
    company = CompanyStore(tmp_path).create("公司", "", [{"id": "pm"}])
    for i in range(5):
        tracing.root(company, f"directive:{i}", f"指令 {i}").finish()
    tracing.store_for(company).prune(keep=3)
    assert [t["name"] for t in tracing.store_for(company).traces()] == ["指令 4", "指令 3", "指令 2"]


def test_trace_endpoints(tmp_path):
    world = World()
    hub = Hub(make_store(tmp_path, world), auto_approve=True)
    company = hub.store.create("测试公司", "", [{"id": "pm"}])
    hub.open_company(company.id)
    client = TestClient(create_app(hub))
    hub.send("写一份 PRD", "all")
    did = next(m["directive_id"] for m in hub.log.read("all") if m["sender"] == "ceo")
    assert hub.engine.wait_idle(20)
    traces = client.get("/api/traces").json()["traces"]
    assert traces and traces[0]["name"].startswith("指令：写一份 PRD")
    by_key = client.get("/api/trace", params={"key": f"directive:{did}"}).json()
    assert by_key["trace_id"] == traces[0]["trace_id"] and any(s["name"] == "规划" for s in by_key["spans"])
    assert client.get("/api/trace", params={"id": "nope"}).status_code == 404
    hub.close_company()
