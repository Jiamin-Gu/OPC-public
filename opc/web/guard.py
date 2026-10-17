"""网页服务的访问控制：只有用户自己的浏览器能操作公司（和 Coding-agent 同一套做法）。

服务只监听 127.0.0.1，但这挡不住用户正在浏览的其他网站：
- 跨站 WebSocket：浏览器不限制网页连接 ws://127.0.0.1:端口，恶意网页的脚本可以连上来替用户下达任务，
  公司允许运行代码时就等于在用户电脑上执行任意命令
- DNS 重绑定：恶意域名先解析到攻击者的服务器、再改成解析到 127.0.0.1，浏览器把它当成同源，可以读写所有接口

三道检查（HTTP 请求和 WebSocket 都要过）：
1. Host 必须是 127.0.0.1 / localhost 加本服务的端口：挡住 DNS 重绑定（那时 Host 是攻击者的域名）
2. 带 Origin 的请求（浏览器发的跨站请求、所有 WebSocket 握手都带）必须来自本服务自己的页面
3. 访问令牌：启动时打开的网址带着 ?token=…，服务把它换成 Cookie（HttpOnly、SameSite=Strict，
   别的网站的请求不会带上它），之后每个请求都要有这个 Cookie。令牌保存在 ~/.opc/web_token，
   重启服务不变，浏览器拿到过一次 Cookie 之后，直接打开 http://127.0.0.1:端口 也能用
"""
import hmac
import os
import secrets
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

COOKIE = "opc_token"
COOKIE_MAX_AGE = 90 * 24 * 3600
PUBLIC_PATHS = {"/api/info", "/favicon.ico"}  # 启动时检查端口要用 /api/info，只返回版本号
PUBLIC_PREFIXES = ("/static/",)              # 页面的 CSS/JS，本身不含数据

DENIED_PAGE = """<!doctype html><html lang="zh"><meta charset="utf-8"><title>OPC</title>
<body style="font-family:system-ui,sans-serif;max-width:560px;margin:80px auto;line-height:1.7;padding:0 16px">
<h2>需要访问令牌</h2>
<p>为了防止其他网站操作你电脑上的公司，OPC 的页面需要用启动时打开的链接访问（链接里带着 <code>?token=…</code>）。</p>
<p>请重新运行 <code>start.bat</code> / <code>start.sh</code>（或 <code>python -m opc.web</code>），它会自动用正确的链接打开浏览器；
也可以复制终端里打印的那个链接。之后在这个浏览器里直接打开本地址就可以了。</p>
</body></html>"""


def load_token(home: Path) -> str:
    """读取（第一次则生成）访问令牌。文件只给当前用户读写。"""
    path = home / "web_token"
    try:
        token = path.read_text(encoding="utf-8").strip()
        if len(token) >= 32:
            return token
    except OSError:
        pass
    token = secrets.token_urlsafe(32)
    home.mkdir(parents=True, exist_ok=True)
    path.write_text(token, encoding="utf-8")
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    return token


def allowed_hosts(port: int) -> set[str]:
    return {f"127.0.0.1:{port}", f"localhost:{port}"}


class Guard:
    """ASGI 中间件。token 为 None 时不检查令牌（只在测试里这样用）。"""

    def __init__(self, app, token: str | None, hosts: set[str]):
        self.app, self.token, self.hosts = app, token, hosts

    async def __call__(self, scope, receive, send):
        if scope["type"] not in ("http", "websocket"):
            return await self.app(scope, receive, send)
        headers = {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope["headers"]}
        path = scope["path"]
        if headers.get("host", "") not in self.hosts:
            return await self._deny(scope, send, 403, "Host 不对：只允许通过 127.0.0.1 或 localhost 访问")
        origin = headers.get("origin")
        if origin and urlsplit(origin).netloc not in self.hosts:
            return await self._deny(scope, send, 403, "跨站请求被拒绝")
        if self.token is None or path in PUBLIC_PATHS or path.startswith(PUBLIC_PREFIXES):
            return await self.app(scope, receive, send)
        query = parse_qs(scope.get("query_string", b"").decode("latin-1"))
        if scope["type"] == "http" and path == "/" and self._ok(query.get("token", [""])[0]):
            return await self._set_cookie_and_redirect(send)  # 用启动链接打开：换成 Cookie，地址栏去掉令牌
        if self._ok(self._cookie(headers.get("cookie", ""))):
            return await self.app(scope, receive, send)
        if scope["type"] == "http" and path == "/":
            return await self._html(send, 401, DENIED_PAGE)
        return await self._deny(scope, send, 401, "缺少访问令牌")

    def _ok(self, value: str) -> bool:
        return bool(value) and hmac.compare_digest(value.encode(), self.token.encode())

    @staticmethod
    def _cookie(header: str) -> str:
        for part in header.split(";"):
            name, _, value = part.strip().partition("=")
            if name == COOKIE:
                return value
        return ""

    async def _set_cookie_and_redirect(self, send):
        cookie = f"{COOKIE}={self.token}; Path=/; Max-Age={COOKIE_MAX_AGE}; HttpOnly; SameSite=Strict"
        await send({"type": "http.response.start", "status": 303,
                    "headers": [(b"location", b"/"), (b"set-cookie", cookie.encode()),
                                (b"cache-control", b"no-store")]})
        await send({"type": "http.response.body", "body": b""})

    @staticmethod
    async def _html(send, status: int, html: str):
        body = html.encode("utf-8")
        await send({"type": "http.response.start", "status": status,
                    "headers": [(b"content-type", b"text/html; charset=utf-8"),
                                (b"content-length", str(len(body)).encode())]})
        await send({"type": "http.response.body", "body": body})

    @staticmethod
    async def _deny(scope, send, status: int, text: str):
        if scope["type"] == "websocket":
            await send({"type": "websocket.close", "code": 4403 if status == 403 else 4401, "reason": "denied"})
            return
        body = ('{"detail": "%s"}' % text).encode("utf-8")
        await send({"type": "http.response.start", "status": status,
                    "headers": [(b"content-type", b"application/json; charset=utf-8"),
                                (b"content-length", str(len(body)).encode())]})
        await send({"type": "http.response.body", "body": body})
