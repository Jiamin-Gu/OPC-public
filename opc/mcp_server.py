"""把公司知识库做成 MCP 服务端：别的 agent（Claude、Cursor、Coding-agent……）也能检索 OPC 公司的文档、记录和资料。

    python -m opc.mcp_server                      # stdio，用最近打开的公司
    python -m opc.mcp_server --company 公司id或名字
    python -m opc.mcp_server --http --port 8791   # Streamable HTTP，只监听本机，要带令牌

在别的工具的 mcpServers 配置里写：
    {"opc-kb": {"command": "python", "args": ["-m", "opc.mcp_server", "--company", "公司名"]}}

只读：提供两个工具，kb_search（检索）和 kb_sources（知识库里有什么）。结果里的密钥先遮蔽。
HTTP 方式只监听 127.0.0.1，请求要带 Authorization: Bearer <令牌>（令牌存在数据目录的 mcp_token，启动时打印）。
协议部分自己实现（JSON-RPC 2.0：initialize、tools/list、tools/call、ping），不依赖官方 SDK。
"""
import argparse
import json
import secrets
import sys
import threading
import time
from pathlib import Path

from . import __version__, security
from .company import CompanyStore
from .config import opc_home
from .knowledge import service
from .knowledge.company import LIBRARIES

PROTOCOL_VERSION = "2025-06-18"
TOOLS = [
    {"name": "kb_search",
     "description": "检索 OPC 公司的知识库：公司的文档和代码、任务记录（汇报、审查意见、CEO 裁决）、每条指令的结论，"
                    "以及 CEO 上传的公司资料和个人资料。返回最相关的片段和出处。",
     "inputSchema": {"type": "object", "properties": {
         "query": {"type": "string", "description": "要找什么：一句话或几个关键词"},
         "scope": {"type": "string", "enum": ["all", "files", "records", "library"],
                   "description": "all 全部（默认）；files 文档和代码；records 任务记录和指令结论；library 上传的资料"},
         "k": {"type": "integer", "description": "返回几条，默认 5，最多 10"}},
         "required": ["query"]},
     "annotations": {"readOnlyHint": True}},
    {"name": "kb_sources",
     "description": "列出知识库里有什么：各类来源的数量、上传的资料清单。",
     "inputSchema": {"type": "object", "properties": {}},
     "annotations": {"readOnlyHint": True}},
]


class KBServer:
    """JSON-RPC 的处理部分（和传输方式无关）。"""

    def __init__(self, store: CompanyStore, company):
        self.store, self.company = store, company
        self._state, self._state_at = None, 0.0
        self.lock = threading.Lock()

    def records(self) -> dict | None:
        """任务看板和全公司频道（同步任务记录和指令结论用），5 秒内复用。读不到（公司还没运行过）就只检索文件和资料。"""
        with self.lock:
            if time.monotonic() - self._state_at > 5:
                try:
                    from .knowledge.__main__ import load_state
                    self._state = load_state(self.store, self.company.id)
                except Exception:
                    self._state = None
                self._state_at = time.monotonic()
            return self._state

    def handle(self, msg: dict) -> dict | None:
        """处理一条消息，返回响应；通知（没有 id）返回 None。"""
        if not isinstance(msg, dict):
            return _error(None, -32600, "不是合法的 JSON-RPC 请求")
        method, rid = msg.get("method"), msg.get("id")
        if rid is None:  # 通知（initialized 等）
            return None
        try:
            if method == "initialize":
                return _result(rid, {"protocolVersion": msg.get("params", {}).get("protocolVersion") or PROTOCOL_VERSION,
                                     "capabilities": {"tools": {"listChanged": False}},
                                     "serverInfo": {"name": "opc-knowledge", "version": __version__},
                                     "instructions": f"OPC 公司「{self.company.meta['name']}」的知识库（只读）。"})
            if method == "ping":
                return _result(rid, {})
            if method == "tools/list":
                return _result(rid, {"tools": TOOLS})
            if method == "tools/call":
                params = msg.get("params") or {}
                text, ok = self.call(params.get("name"), params.get("arguments") or {})
                return _result(rid, {"content": [{"type": "text", "text": text}], "isError": not ok})
            return _error(rid, -32601, f"不支持的方法：{method}")
        except Exception as e:
            return _error(rid, -32603, f"{type(e).__name__}: {e}")

    def call(self, name: str, args: dict) -> tuple[str, bool]:
        if name == "kb_search":
            query = str(args.get("query") or "").strip()
            scope = args.get("scope") or "all"
            if not query:
                return "需要提供 query（要找什么）", False
            if scope not in service.SCOPES:
                return f"scope 只能是 {'、'.join(service.SCOPES)}", False
            try:
                k = max(1, min(int(args.get("k") or 5), 10))
            except (TypeError, ValueError):
                k = 5
            results = service.search(self.company, query, scope, k, state=self.records())
            return security.redact(service.format_results(results, query))[0], True
        if name == "kb_sources":
            kb = service.get(self.company)
            service.sync(self.company, self.records())
            s = kb.stats()
            names = {"file": "文档和代码", "task": "任务记录", "directive": "指令结论", "lib": "公司资料", "me": "个人资料"}
            lines = [f"公司「{self.company.meta['name']}」的知识库：{s['chunks']} 块"]
            lines += [f"- {names.get(kind, kind)}：{n} 份" for kind, n in sorted(s["sources"].items())]
            lib = service.list_library(self.company)
            for scope, (_, _, label) in LIBRARIES.items():
                if lib.get(scope):
                    lines.append(f"{label}：" + "、".join(i["name"] for i in lib[scope]))
            return "\n".join(lines), True
        return f"没有工具 {name}", False


def _result(rid, result: dict) -> dict:
    return {"jsonrpc": "2.0", "id": rid, "result": result}


def _error(rid, code: int, message: str) -> dict:
    return {"jsonrpc": "2.0", "id": rid, "error": {"code": code, "message": message}}


def pick_company(store: CompanyStore, name: str | None):
    companies = store.list()
    if not companies:
        sys.exit("还没有公司：先在 OPC 里创建一家")
    if name:
        match = [c for c in companies if c["id"] == name or c["name"] == name]
        if not match:
            sys.exit(f"找不到公司：{name}（有：{'、'.join(c['name'] for c in companies)}）")
        return store.load(match[0]["id"])
    return store.load(companies[0]["id"])  # 列表按最近使用排在前面


def serve_stdio(server: KBServer, stdin=None, stdout=None) -> None:
    """每行一条 JSON-RPC 消息。stdout 只写协议消息，提示信息写到 stderr。"""
    stdin = stdin or sys.stdin
    stdout = stdout or sys.stdout
    for line in stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            reply = _error(None, -32700, "不是合法的 JSON")
        else:
            reply = server.handle(msg)
        if reply is not None:
            stdout.write(json.dumps(reply, ensure_ascii=False) + "\n")
            stdout.flush()


def http_app(server: KBServer, token: str):
    """Streamable HTTP（只用到 JSON 响应）：POST /mcp。令牌不对返回 401。"""
    from fastapi import FastAPI, Request
    from fastapi.responses import JSONResponse, Response

    app = FastAPI(title="OPC 知识库 MCP 服务端")
    sessions: set[str] = set()

    @app.post("/mcp")
    async def mcp(request: Request):
        if request.headers.get("authorization", "") != f"Bearer {token}":
            return JSONResponse({"error": "需要令牌：Authorization: Bearer <令牌>"}, status_code=401)
        try:
            msg = await request.json()
        except Exception:
            return JSONResponse(_error(None, -32700, "不是合法的 JSON"), status_code=400)
        reply = server.handle(msg)
        if reply is None:
            return Response(status_code=202)
        headers = {}
        if isinstance(msg, dict) and msg.get("method") == "initialize":
            sid = secrets.token_hex(8)
            sessions.add(sid)
            headers["Mcp-Session-Id"] = sid
        return JSONResponse(reply, headers=headers)

    @app.get("/mcp")
    async def no_stream():
        return Response(status_code=405)  # 不提供服务器主动推送的流

    return app


def load_token(home: Path) -> str:
    path = home / "mcp_token"
    if path.is_file() and path.read_text(encoding="utf-8").strip():
        return path.read_text(encoding="utf-8").strip()
    home.mkdir(parents=True, exist_ok=True)
    token = secrets.token_urlsafe(24)
    path.write_text(token, encoding="utf-8")
    return token


def main() -> None:
    parser = argparse.ArgumentParser(description="OPC 公司知识库的 MCP 服务端（只读）")
    parser.add_argument("--company", help="公司 id 或名字，默认最近打开的公司")
    parser.add_argument("--home", help="数据目录，默认 ~/.opc")
    parser.add_argument("--http", action="store_true", help="用 Streamable HTTP（默认 stdio）")
    parser.add_argument("--port", type=int, default=8791)
    args = parser.parse_args()
    store = CompanyStore(args.home)
    company = pick_company(store, args.company)
    server = KBServer(store, company)
    if not args.http:
        if hasattr(sys.stdout, "reconfigure"):
            sys.stdin.reconfigure(encoding="utf-8")
            sys.stdout.reconfigure(encoding="utf-8")
        print(f"OPC 知识库 MCP 服务端（stdio）：公司「{company.meta['name']}」", file=sys.stderr)
        serve_stdio(server)
        return
    import uvicorn
    token = load_token(Path(args.home) if args.home else opc_home())
    print(f"OPC 知识库 MCP 服务端：http://127.0.0.1:{args.port}/mcp  公司「{company.meta['name']}」\n"
          f"  请求头：Authorization: Bearer {token}（令牌保存在数据目录的 mcp_token）", file=sys.stderr)
    uvicorn.run(http_app(server, token), host="127.0.0.1", port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
