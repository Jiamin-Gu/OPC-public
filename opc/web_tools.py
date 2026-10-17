"""联网能力：经 MCP 服务器搜索、读网页（或者任何你配置的外部工具）。

配置写在 OPC 数据目录下的 mcp.json（默认 ~/.opc/mcp.json，可以用 OPC_MCP_CONFIG 指定别的文件），格式是通用的
mcpServers 格式（Cursor、Claude 等工具都用这种），和 Coding-agent 的 mcp.json 一样：

    {"mcpServers": {
        "fetch":  {"command": "python", "args": ["-m", "mcp_server_fetch"]},
        "search": {"type": "http", "url": "https://.../mcp", "headers": {"Authorization": "Bearer ${TOKEN}"}},
        "browser": {"command": "npx", "args": ["@playwright/mcp@latest", "--headless"], "tools": ["browser_navigate"]},
        "slow":   {"command": "npx", "args": ["-y", "some-server"], "disabled": true}
    }}

- 支持 stdio（启动服务器进程）和 Streamable HTTP（连一个地址），见 mcp_client.py
- 每个服务器的工具都给角色用，名字是"服务器名__工具名"；工具多的服务器用 "tools" 只挑需要的（OPC 的扩展字段，
  其他客户端会忽略）——工具定义每次调用模型都要带上，太多会占上下文
- 公司勾选了"允许联网"才给角色这些工具；整个 OPC 进程共用一组连接，多个角色可以同时调用
- 兼容旧配置：设置了 OPC_MCP_URL 时，把它当作一个叫 web 的 HTTP 服务器
- 服务器在后台启动（第一次用 npx 可能要下载一会儿），打开网页不用等；角色真正要用时才等它启动完
"""
import atexit
import json
import os
import re
import threading
import time
from pathlib import Path

from .config import opc_home
from .mcp_client import START_TIMEOUT, MCPError, MCPServer, render_result

MAX_RESULT_CHARS = 20_000
RETRY_AFTER = 30  # 全部服务器都连不上时，这么多秒内不再重试（免得每次刷新页面都重新启动）


class WebError(Exception):
    pass


def config_path() -> Path:
    return Path(os.getenv("OPC_MCP_CONFIG") or opc_home() / "mcp.json")


def load_config() -> dict[str, dict]:
    """读 mcp.json，加上旧的 OPC_MCP_URL；去掉 disabled 的服务器。配置有错时抛 WebError。"""
    servers: dict[str, dict] = {}
    path = config_path()
    if path.is_file():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except ValueError as e:
            raise WebError(f"{path} 不是合法的 JSON：{e}")
        servers.update({k: v for k, v in (data.get("mcpServers") or {}).items() if isinstance(v, dict)})
    if os.getenv("OPC_MCP_URL") and "web" not in servers:
        servers["web"] = {"type": "http", "url": os.environ["OPC_MCP_URL"]}
    return {k: v for k, v in servers.items() if not v.get("disabled")}


def tool_name(server: str, name: str) -> str:
    """OpenAI 的函数名只能是字母、数字、_、-，最长 64 个字符。"""
    return re.sub(r"[^A-Za-z0-9_-]", "_", f"{server}__{name}")[:64]


class WebTools:
    """配置里的所有 MCP 服务器：后台启动，汇总工具，按名字调用。"""

    def __init__(self, config: dict[str, dict], transport_factory=None):
        self.config = config
        self.servers: dict[str, MCPServer] = {}
        self.errors: dict[str, str] = {}
        self.tools: list[dict] = []  # [{name, server, remote, description, inputSchema}]
        self.ready = threading.Event()
        self.started = time.time()
        self._factory = transport_factory
        self._lock = threading.Lock()

    def start(self) -> None:
        threading.Thread(target=self._start_all, daemon=True, name="mcp-start").start()

    def _start_all(self) -> None:
        threads = [threading.Thread(target=self._start_one, args=(n, s), daemon=True) for n, s in self.config.items()]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        tools = []
        for name in self.config:
            server = self.servers.get(name)
            if not server:
                continue
            wanted = self.config[name].get("tools")
            for t in server.tools:
                if wanted is None or t["name"] in wanted:
                    tools.append({**t, "name": tool_name(name, t["name"]), "server": name, "remote": t["name"]})
            missing = [w for w in wanted or [] if w not in {t["name"] for t in server.tools}]
            if missing:  # "tools" 里写的名字要和服务器自己报的一致，写错了就说清楚，不默默变成 0 个工具
                self.errors[name] = (f"tools 里这些工具不存在：{'、'.join(missing)}；"
                                     f"这个服务器有：{'、'.join(t['name'] for t in server.tools)}")
        self.tools = tools
        self.ready.set()

    def _start_one(self, name: str, spec: dict) -> None:
        try:
            server = MCPServer(name, spec, opc_home(), self._factory)
            try:
                server.start()
            except Exception:
                server.close()
                raise
            self.servers[name] = server
        except Exception as e:  # noqa: BLE001  一个服务器起不来不影响别的
            self.errors[name] = str(e)

    def wait(self, timeout: float = START_TIMEOUT + 5) -> bool:
        return self.ready.wait(timeout)

    def schemas(self) -> list[dict]:
        """转成模型的函数调用格式。"""
        return [{"type": "function", "function": {
            "name": t["name"], "description": f"[{t['server']}] {t.get('description', '')}"[:1024],
            "parameters": {k: v for k, v in (t.get("inputSchema") or {"type": "object"}).items() if k != "$schema"}}}
            for t in self.tools]

    def call(self, name: str, arguments: dict) -> tuple[str, bool]:
        """调用工具，返回 (给模型看的文字, 是否成功)。"""
        tool = next((t for t in self.tools if t["name"] == name), None)
        if not tool:
            raise WebError(f"没有工具 {name}")
        server = self.servers.get(tool["server"])
        if server is None or server.closed:
            raise WebError(f"MCP 服务器 {tool['server']} 已经停止（重启 OPC 后会重新连接）")
        try:
            result = server.call(tool["remote"], arguments)
        except MCPError as e:
            raise WebError(str(e))
        return render_result(result, MAX_RESULT_CHARS), not result.get("isError")

    def status(self) -> dict:
        servers = []
        for name in self.config:
            server = self.servers.get(name)
            state = "starting" if not self.ready.is_set() else ("ok" if server else "error")
            servers.append({"name": name, "state": state, "error": self.errors.get(name, ""),
                            "tools": sum(1 for t in self.tools if t["server"] == name)})
        return {"available": bool(self.tools), "starting": not self.ready.is_set(),
                "servers": servers, "tools": [t["name"] for t in self.tools]}

    def close(self) -> None:
        for server in list(self.servers.values()):
            try:
                server.close()
            except Exception:  # noqa: BLE001
                pass


_manager: WebTools | None = None
_signature: tuple | None = None
_failed_at = 0.0
_lock = threading.Lock()


def _current_signature() -> tuple:
    path = config_path()
    return (str(path), path.stat().st_mtime if path.is_file() else None, os.getenv("OPC_MCP_URL"))


def manager() -> WebTools | None:
    """当前配置对应的连接（整个进程共用）。配置文件改了会重新连接；没有配置返回 None。不等服务器启动完。"""
    global _manager, _signature, _failed_at
    with _lock:
        sig = _current_signature()
        if _manager is not None and sig == _signature:
            failed = _manager.ready.is_set() and not _manager.tools
            if not (failed and time.time() - _failed_at > RETRY_AFTER):
                return _manager
        if _manager is not None:
            _manager.close()
        _manager, _signature = None, sig
        try:
            config = load_config()
        except WebError:
            return None
        if not config:
            return None
        _manager = WebTools(config)
        _manager.start()
        _failed_at = time.time()
        return _manager


def get_client() -> WebTools | None:
    """角色要用联网工具时：等服务器启动完，有工具才返回。"""
    m = manager()
    if m is None or not m.wait() or not m.tools:
        return None
    return m


def status() -> dict:
    """给界面看的状态（不等服务器启动完）。"""
    out = {"config": str(config_path()), "available": False, "starting": False, "servers": [], "tools": []}
    try:
        load_config()
    except WebError as e:
        return {**out, "error": str(e)}
    m = manager()
    return {**out, **m.status()} if m else out


def reset() -> None:
    """测试用：关掉连接，下次重新读配置。"""
    global _manager, _signature, _failed_at
    with _lock:
        if _manager is not None:
            _manager.close()
        _manager, _signature, _failed_at = None, None, 0.0


atexit.register(reset)  # OPC 退出时关掉它启动的服务器进程


def summary(name: str, args: dict, ok: bool) -> dict:
    """界面上工具标签需要的信息：按参数认出是调研、搜索还是读网页。"""
    s = {"name": name, "ok": ok, "web": True}
    for key, limit in (("question", 120), ("query", 80), ("url", 120)):
        if args.get(key):
            s[key] = str(args[key])[:limit]
            break
    return s


def rule_text(tools: list[str]) -> str:
    """告诉角色怎么联网：按实际提供的工具写。"""
    research = next((t for t in tools if t.endswith("__research")), None)
    shown = "、".join(tools[:8]) + ("等" if len(tools) > 8 else "")
    if research:
        return (f"需要最新信息（市场、竞品、新闻、技术文档）时，优先用 {research} 调研：把问题写清楚交给它，"
                f"它会自己搜索、读网页，交回带来源的结论。可用的联网工具：{shown}")
    return f"需要最新信息（市场、竞品、新闻、技术文档）时，用这些联网工具：{shown}"
