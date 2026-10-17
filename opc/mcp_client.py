"""MCP（Model Context Protocol）客户端的协议部分：连接一个 MCP 服务器、握手、列工具、调用工具。

和同一作者的 Coding-agent 里的 MCP 客户端是同一套实现（自己实现协议，不依赖官方 SDK），JSON-RPC 2.0，两种传输：
- stdio：启动服务器进程，每行一条 JSON 消息（大多数服务器，比如 fetch、Playwright）
- Streamable HTTP：向一个 URL POST JSON-RPC，响应可能是 JSON，也可能是 SSE 流

一个连接可以被多个线程同时使用（多个角色并行调用工具）：请求按 id 对上各自的响应。
"""
import itertools
import json
import os
import re
import shutil
import subprocess
import threading
import time
from collections import deque
from pathlib import Path

import httpx

from . import __version__

PROTOCOL_VERSION = "2025-06-18"
REQUEST_TIMEOUT = 300     # 一次请求最多等多久（秒）：调研类工具会自己搜索、读好几个网页
START_TIMEOUT = 60        # 启动 + 握手最多等多久（第一次用 npx / uvx 可能要下载）


class MCPError(Exception):
    pass


def expand(value: str) -> str:
    """配置里的 ${VAR} 换成环境变量（密钥不用写进配置文件）。"""
    return re.sub(r"\$\{(\w+)\}", lambda m: os.environ.get(m.group(1), ""), value)


def _kill_tree(proc: subprocess.Popen) -> None:
    """结束进程和它启动的子进程（npx 启动的服务器是子进程）。"""
    if os.name == "nt":
        subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)], capture_output=True)
    else:
        proc.kill()


# ---------- 传输 ----------

class StdioTransport:
    """启动服务器进程，按行收发 JSON-RPC 消息。"""

    def __init__(self, name: str, spec: dict, root: Path, on_message):
        command = shutil.which(spec["command"]) or spec["command"]  # Windows 上 npx 是 npx.cmd
        # MCP 要求 UTF-8。Python 写的服务器在中文 Windows 上默认按 GBK 输出（stdout 跟随系统代码页），
        # 客户端按 UTF-8 解析就会失败；让它用 UTF-8（配置里明确写了的以配置为准）
        env = {**os.environ, "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8",
               **{k: expand(str(v)) for k, v in spec.get("env", {}).items()}}
        self.name, self.on_message = name, on_message
        self.stderr: deque[str] = deque(maxlen=50)  # 最近的错误输出，出问题时给用户看
        try:
            self.proc = subprocess.Popen(
                [command, *[expand(str(a)) for a in spec.get("args", [])]], cwd=spec.get("cwd") or root, env=env,
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                **({"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}))
        except OSError as e:
            raise MCPError(f"启动失败（{spec['command']}）：{e}")
        self.lock = threading.Lock()
        threading.Thread(target=self._read, daemon=True).start()
        threading.Thread(target=self._read_err, daemon=True).start()

    def _read(self) -> None:
        for line in self.proc.stdout:
            line = line.strip()
            if not line:
                continue
            try:
                self.on_message(json.loads(line))
            except ValueError:
                self.stderr.append(f"无法解析的输出：{line[:200]!r}")
        self.on_message(None)  # 进程退出

    def _read_err(self) -> None:
        for line in self.proc.stderr:
            self.stderr.append(line.decode("utf-8", errors="replace").rstrip())

    def send(self, message: dict) -> None:
        data = (json.dumps(message, ensure_ascii=False) + "\n").encode("utf-8")
        with self.lock:
            try:
                self.proc.stdin.write(data)
                self.proc.stdin.flush()
            except OSError as e:
                raise MCPError(f"服务器 {self.name} 已经退出：{self.last_error() or e}")

    def last_error(self) -> str:
        return "\n".join(list(self.stderr)[-5:])

    def close(self) -> None:
        if self.proc.poll() is None:
            try:
                self.proc.stdin.close()  # 按协议先关输入，给服务器机会自己退出
                self.proc.wait(2)
            except (OSError, subprocess.TimeoutExpired):
                _kill_tree(self.proc)


class HttpTransport:
    """Streamable HTTP：每条消息 POST 到同一个 URL；响应是 JSON，或者是 SSE 流（里面有我们要的那条响应）。"""

    def __init__(self, name: str, spec: dict, root: Path, on_message, client: httpx.Client | None = None):
        self.name, self.url, self.on_message = name, spec["url"], on_message
        self.headers = {k: expand(str(v)) for k, v in spec.get("headers", {}).items()}
        local = httpx.URL(self.url).host in ("127.0.0.1", "localhost")  # 本机地址不走代理
        self.client = client or httpx.Client(timeout=httpx.Timeout(REQUEST_TIMEOUT, connect=5), trust_env=not local)
        self.session_id: str | None = None
        self.protocol: str | None = None

    def send(self, message: dict) -> None:
        headers = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream",
                   **self.headers}
        if self.session_id:
            headers["Mcp-Session-Id"] = self.session_id
        if self.protocol:
            headers["MCP-Protocol-Version"] = self.protocol
        try:
            with self.client.stream("POST", self.url, json=message, headers=headers) as r:
                if r.status_code >= 400:
                    raise MCPError(f"服务器 {self.name} 返回 HTTP {r.status_code}：{r.read().decode(errors='replace')[:300]}")
                self.session_id = r.headers.get("mcp-session-id", self.session_id)
                kind = r.headers.get("content-type", "")
                if "text/event-stream" in kind:
                    data: list[str] = []
                    for line in r.iter_lines():
                        if line.startswith("data:"):
                            data.append(line[5:].strip())
                        elif not line and data:  # 一个事件结束
                            self.on_message(json.loads("\n".join(data)))
                            data = []
                    if data:
                        self.on_message(json.loads("\n".join(data)))
                elif "json" in kind:
                    body = json.loads(r.read() or b"null")
                    for m in body if isinstance(body, list) else [body]:
                        if m:
                            self.on_message(m)
        except httpx.ConnectError:
            raise MCPError(f"连不上 {self.url}（MCP 服务器没有启动，或者地址不对）")
        except httpx.HTTPError as e:
            raise MCPError(f"连接服务器 {self.name} 失败：{e}")

    def last_error(self) -> str:
        return ""

    def close(self) -> None:
        if self.session_id:  # 告诉服务器会话结束
            try:
                self.client.delete(self.url, headers={"Mcp-Session-Id": self.session_id, **self.headers}, timeout=5)
            except httpx.HTTPError:
                pass
        self.client.close()


# ---------- 会话 ----------

class MCPServer:
    """和一个 MCP 服务器的连接：握手、列工具、调用工具。"""

    def __init__(self, name: str, spec: dict, root: str | Path, transport_factory=None):
        self.name, self.spec, self.root = name, spec, Path(root).resolve()
        self.ids = itertools.count(1)
        self.pending: dict[int, dict] = {}
        self.cond = threading.Condition()
        self.closed = False
        self.tools: list[dict] = []
        self.info: dict = {}
        self.instructions = ""
        factory = transport_factory or (HttpTransport if spec.get("type") in ("http", "streamable-http") or "url" in spec
                                        else StdioTransport)
        self.transport = factory(name, spec, self.root, self._on_message)

    # 收到消息：响应交给等待的请求；服务器发来的请求（比如 roots/list）在这里回答
    def _on_message(self, msg: dict | None) -> None:
        if msg is None:
            with self.cond:
                self.closed = True
                self.cond.notify_all()
            return
        if "method" in msg:
            if "id" in msg:
                self._answer(msg)
            return
        with self.cond:
            if msg.get("id") in self.pending:
                self.pending[msg["id"]]["response"] = msg
                self.cond.notify_all()

    def _answer(self, req: dict) -> None:
        if req["method"] == "roots/list":  # 文件类服务器会问工作区在哪：OPC 没有给外部服务器的工作区
            reply = {"jsonrpc": "2.0", "id": req["id"], "result": {"roots": []}}
        elif req["method"] == "ping":
            reply = {"jsonrpc": "2.0", "id": req["id"], "result": {}}
        else:
            reply = {"jsonrpc": "2.0", "id": req["id"], "error": {"code": -32601, "message": "not supported"}}
        threading.Thread(target=self._safe_send, args=(reply,), daemon=True).start()

    def _safe_send(self, msg: dict) -> None:
        try:
            self.transport.send(msg)
        except MCPError:
            pass

    def request(self, method: str, params: dict | None = None, timeout: float = REQUEST_TIMEOUT) -> dict:
        rid = next(self.ids)
        slot: dict = {}
        with self.cond:
            self.pending[rid] = slot
        try:
            self.transport.send({"jsonrpc": "2.0", "id": rid, "method": method, **({"params": params} if params else {})})
            deadline = time.time() + timeout
            with self.cond:
                while "response" not in slot:
                    if self.closed:
                        raise MCPError(f"服务器 {self.name} 已经退出" + (f"：{self.transport.last_error()}"
                                                                     if self.transport.last_error() else ""))
                    left = deadline - time.time()
                    if left <= 0:
                        raise MCPError(f"服务器 {self.name} 超过 {timeout:.0f} 秒没有响应（{method}）")
                    self.cond.wait(min(left, 0.5))
        finally:
            with self.cond:
                self.pending.pop(rid, None)
        resp = slot["response"]
        if "error" in resp:
            err = resp["error"]
            raise MCPError(f"{method} 失败：{err.get('message')}（{err.get('code')}）")
        return resp.get("result") or {}

    def notify(self, method: str) -> None:
        self.transport.send({"jsonrpc": "2.0", "method": method})

    def start(self) -> None:
        result = self.request("initialize", {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {"roots": {"listChanged": False}},
            "clientInfo": {"name": "opc", "version": __version__},
        }, timeout=START_TIMEOUT)
        self.info = result.get("serverInfo", {})
        self.instructions = result.get("instructions", "")
        if isinstance(self.transport, HttpTransport):
            self.transport.protocol = result.get("protocolVersion", PROTOCOL_VERSION)
        self.notify("notifications/initialized")
        tools, cursor = [], None
        while True:
            page = self.request("tools/list", {"cursor": cursor} if cursor else None, timeout=START_TIMEOUT)
            tools += page.get("tools", [])
            cursor = page.get("nextCursor")
            if not cursor:
                break
        self.tools = tools

    def call(self, name: str, arguments: dict) -> dict:
        return self.request("tools/call", {"name": name, "arguments": arguments})

    def close(self) -> None:
        self.closed = True
        self.transport.close()


def render_result(result: dict, max_chars: int) -> str:
    """把工具结果转成给模型看的文字。图片、二进制资源只写一句说明（角色看不了图）。"""
    parts = []
    for item in result.get("content", []):
        kind = item.get("type")
        if kind == "text":
            parts.append(item.get("text", ""))
        elif kind == "image":
            parts.append("[工具返回了一张图片，这里显示不了]")
        elif kind == "resource":
            res = item.get("resource", {})
            parts.append(res.get("text") or f"[资源 {res.get('uri')}]")
        elif kind == "resource_link":
            parts.append(f"[资源链接 {item.get('uri')}：{item.get('name', '')}]")
    if not parts and result.get("structuredContent") is not None:
        parts.append(json.dumps(result["structuredContent"], ensure_ascii=False, indent=1))
    text = "\n".join(parts) or "（没有内容）"
    if len(text) > max_chars:
        text = text[:max_chars] + f"\n...（共 {len(text)} 字符，只显示前 {max_chars}）"
    return text
