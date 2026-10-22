"""联网能力：读 mcp.json（通用的 mcpServers 格式），经 MCP 服务器（stdio / Streamable HTTP）把工具交给角色。
stdio 用真的子进程（tests/fake_mcp_server.py），HTTP 用模拟的响应。"""
import json
import os
import sys
import threading
import time
from pathlib import Path

import httpx
import pytest

from opc import web_tools
from opc.agents import run_role, system_prompt
from opc.company import CompanyStore
from opc.mcp_client import HttpTransport

from fakes import call, reply

FAKE = str(Path(__file__).parent / "fake_mcp_server.py")


def write_config(servers: dict) -> Path:
    path = Path(os.environ["OPC_MCP_CONFIG"])
    path.write_text(json.dumps({"mcpServers": servers}, ensure_ascii=False), encoding="utf-8")
    return path


def fake(**extra) -> dict:
    return {"command": sys.executable, "args": [FAKE], **extra}


def ready_manager() -> web_tools.WebTools:
    m = web_tools.manager()
    assert m is not None and m.wait(30)
    return m


def test_no_config_means_no_web():
    s = web_tools.status()
    assert s["available"] is False and s["servers"] == [] and s["config"].endswith("mcp.json")
    assert web_tools.get_client() is None


def test_load_config(monkeypatch):
    write_config({"a": fake(), "off": fake(disabled=True), "bad": "不是对象"})
    assert list(web_tools.load_config()) == ["a"]
    monkeypatch.setenv("OPC_MCP_URL", "http://127.0.0.1:9/mcp")  # 旧配置：当作一个叫 web 的 HTTP 服务器
    assert web_tools.load_config()["web"] == {"type": "http", "url": "http://127.0.0.1:9/mcp"}
    Path(os.environ["OPC_MCP_CONFIG"]).write_text("{坏的", encoding="utf-8")
    with pytest.raises(web_tools.WebError):
        web_tools.load_config()
    assert "不是合法的 JSON" in web_tools.status()["error"]


def test_stdio_server_tools_and_calls():
    write_config({"fake": fake(tools=["web_search", "read_page", "research"])})  # noise 不给角色
    m = ready_manager()
    assert [t["name"] for t in m.tools] == ["fake__web_search", "fake__read_page", "fake__research"]  # 分页也读全了
    assert "$schema" not in json.dumps(m.schemas()) and m.schemas()[0]["function"]["description"].startswith("[fake]")
    text, ok = m.call("fake__web_search", {"query": "记账"})
    assert ok and "竞品 A" in text
    text, ok = m.call("fake__read_page", {"url": "https://bad.example"})
    assert not ok and text == "页面打不开"
    assert "显示不了" in m.call("fake__read_page", {"url": "https://a.example"})[0]  # 图片只写一句说明
    with pytest.raises(web_tools.WebError):
        m.call("fake__noise", {})
    results = []  # 多个角色同时调用：响应按 id 对上，不会串
    threads = [threading.Thread(target=lambda q=q: results.append((q, m.call("fake__web_search", {"query": q})[0])))
               for q in "ABCDEF"]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(results) == 6 and all(f"「{q}」" in text for q, text in results)
    s = web_tools.status()
    assert s["available"] and s["servers"] == [{"name": "fake", "state": "ok", "error": "", "tools": 3}]


def test_broken_server_does_not_block_others():
    write_config({"fake": fake(), "broken": {"command": "definitely-not-a-command-xyz"}})
    m = ready_manager()
    states = {s["name"]: s for s in web_tools.status()["servers"]}
    assert states["fake"]["state"] == "ok" and states["broken"]["state"] == "error" and states["broken"]["error"]
    assert len(m.tools) == 4


def test_config_change_reconnects():
    path = write_config({"fake": fake(tools=["web_search"])})
    first = ready_manager()
    path.write_text(json.dumps({"mcpServers": {"other": fake(tools=["research"])}}), encoding="utf-8")
    os.utime(path, (time.time() + 5, time.time() + 5))  # 保证修改时间变了
    second = ready_manager()
    assert second is not first and [t["name"] for t in second.tools] == ["other__research"]


def test_http_server(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        msg = json.loads(request.content)
        if msg.get("method") == "initialize":
            return httpx.Response(200, json={"jsonrpc": "2.0", "id": msg["id"], "result": {"protocolVersion": "2025-06-18"}},
                                  headers={"Mcp-Session-Id": "s1"})
        if "id" not in msg:
            return httpx.Response(202)
        assert request.headers.get("mcp-session-id") == "s1" and request.headers.get("authorization") == "Bearer t0k"
        if msg["method"] == "tools/list":
            tools = [{"name": "research", "description": "调研", "inputSchema": {"type": "object"}}]
            return httpx.Response(200, json={"jsonrpc": "2.0", "id": msg["id"], "result": {"tools": tools}})
        result = {"jsonrpc": "2.0", "id": msg["id"], "result": {"content": [{"type": "text", "text": "结论"}]}}
        return httpx.Response(200, text=f"data: {json.dumps(result)}\n\n", headers={"content-type": "text/event-stream"})

    def factory(name, spec, root, on_message):
        return HttpTransport(name, spec, root, on_message, client=httpx.Client(transport=httpx.MockTransport(handler)))

    monkeypatch.setenv("TEST_TOKEN", "t0k")  # 配置里的 ${TEST_TOKEN} 换成环境变量
    m = web_tools.WebTools({"hub": {"type": "http", "url": "http://hub.test/mcp",
                                    "headers": {"Authorization": "Bearer ${TEST_TOKEN}"}}}, transport_factory=factory)
    m.start()
    assert m.wait(10) and [t["name"] for t in m.tools] == ["hub__research"]
    assert m.call("hub__research", {"question": "竞品"}) == ("结论", True)  # SSE 响应
    assert "优先用 hub__research" in web_tools.rule_text([t["name"] for t in m.tools])


class WebLLM:
    """市场角色：先搜索，再读一个网页（失败），然后写结论。"""

    def __init__(self):
        self.seen = []

    def chat(self, messages, tools=None, on_delta=None, json_mode=False, on_tool_delta=None):
        self.seen.append(([dict(m) for m in messages], [t["function"]["name"] for t in tools or []]))
        n = len(self.seen)
        if n == 1:
            return reply("先查一下", [call("s", "fake__web_search", query="记账 app 竞品")])
        if n == 2:
            return reply("", [call("f", "fake__read_page", url="https://bad.example/a")])
        return reply("调研完成")


def test_role_uses_web_tools_when_company_allows(tmp_path):
    write_config({"fake": fake(tools=["web_search", "read_page"])})
    llm = WebLLM()
    store = CompanyStore(tmp_path, llm_factory=lambda cfg, role: llm)
    company = store.create("公司", "做记账 app", [{"id": "marketing"}], web=True)
    out = run_role(company, "marketing", [{"role": "user", "content": "调研竞品"}], lambda e: None, "m1", "ceo",
                   threading.Event(), max_steps=5)
    assert {"fake__web_search", "fake__read_page"} <= set(llm.seen[0][1])
    assert "竞品 A" in llm.seen[1][0][-1]["content"]                     # 搜索结果交给了模型
    assert "页面打不开" in llm.seen[2][0][-1]["content"]                   # 失败也告诉模型
    assert llm.seen[2][0][-1]["content"].startswith("<<<外部内容-")         # 外部服务器的返回（包括报错）都标成外部内容
    assert out["tools"] == [{"name": "fake__web_search", "ok": True, "web": True, "query": "记账 app 竞品"},
                            {"name": "fake__read_page", "ok": False, "web": True, "url": "https://bad.example/a"}]
    prompt = system_prompt(company, "marketing")
    assert "你可以联网" in prompt and "fake__web_search" in prompt


def test_no_web_tools_without_permission(tmp_path):
    write_config({"fake": fake()})
    llm = WebLLM()
    store = CompanyStore(tmp_path, llm_factory=lambda cfg, role: llm)
    off = store.create("公司", "", [{"id": "marketing"}])  # 没有勾选联网
    run_role(off, "marketing", [{"role": "user", "content": "x"}], lambda e: None, "m", "ceo", threading.Event(), 1)
    assert not any(n.startswith("fake__") for n in llm.seen[0][1])
    assert "你可以联网" not in system_prompt(off, "marketing")
    on = store.create("公司", "", [{"id": "marketing"}], web=True)
    Path(os.environ["OPC_MCP_CONFIG"]).unlink()  # 勾选了联网，但没有配置任何服务器：提示词里也不说能联网
    assert "你可以联网" not in system_prompt(on, "marketing")


def test_summary_and_rule_text():
    assert web_tools.summary("x__research", {"question": "竞品有哪些"}, True) == {
        "name": "x__research", "ok": True, "web": True, "question": "竞品有哪些"}
    assert web_tools.summary("x__fetch", {"url": "https://a.example", "max_length": 5}, False)["url"] == "https://a.example"
    assert "优先用" not in web_tools.rule_text(["a__web_search", "a__fetch"])


def test_missing_cwd_is_explained():
    """配置里写了不存在的工作目录：说清楚原因；默认的数据目录不存在时自己建（新机器上第一次运行）。"""
    write_config({"fake": fake(), "lost": fake(cwd="/definitely/not/here/xyz")})
    ready_manager()
    states = {s["name"]: s for s in web_tools.status()["servers"]}
    assert states["fake"]["state"] == "ok" and "工作目录不存在" in states["lost"]["error"]
