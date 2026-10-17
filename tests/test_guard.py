"""OPC 网页访问控制（和 Coding-agent 同一套）：别的网站不能连 WebSocket、不能调用接口；DNS 重绑定（Host 不对）被拒绝；令牌换成 Cookie。"""
import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from opc.company import CompanyStore
from opc.demo import demo_factory
from opc.web.guard import COOKIE, load_token
from opc.web.runtime import Hub
from opc.web.server import create_app

TOKEN = "t" * 43
HOSTS = {"testserver", "127.0.0.1:8780"}


@pytest.fixture
def client(tmp_path):
    hub = Hub(CompanyStore(tmp_path / "home", llm_factory=demo_factory), demo=True)
    return TestClient(create_app(hub, token=TOKEN, hosts=HOSTS), follow_redirects=False)


def test_without_token_nothing_is_reachable(client):
    assert client.get("/api/file?path=x").status_code == 401
    assert client.post("/api/companies", json={}).status_code == 401
    page = client.get("/")
    assert page.status_code == 401 and "需要访问令牌" in page.text
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect("/ws") as ws:
            ws.receive_json()
    # 公开的只有版本信息和静态文件
    assert client.get("/api/info").status_code == 200
    assert client.get("/static/app.js").status_code == 200


def test_start_link_sets_strict_cookie_then_everything_works(client):
    r = client.get(f"/?token={TOKEN}")
    assert r.status_code == 303 and r.headers["location"] == "/"
    cookie = r.headers["set-cookie"]
    assert f"{COOKIE}={TOKEN}" in cookie and "HttpOnly" in cookie and "SameSite=Strict" in cookie
    client.cookies.set(COOKIE, TOKEN)
    assert client.get("/").status_code == 200 and client.get("/api/kb/search?q=x").status_code != 401  # 过了访问控制（没有公司时接口自己返回 404）
    with client.websocket_connect("/ws") as ws:
        assert ws.receive_json()["type"] == "home"
    assert client.get("/?token=wrong").status_code == 200  # 错误的令牌不会换掉 Cookie（已有正确的 Cookie）


def test_wrong_token_is_rejected(client):
    assert client.get("/?token=wrong").status_code == 401
    client.cookies.set(COOKIE, "wrong")
    assert client.get("/api/kb/search?q=x").status_code == 401


def test_cross_site_websocket_and_dns_rebinding_are_blocked(client):
    client.cookies.set(COOKIE, TOKEN)
    # 恶意网页连本机的 WebSocket：浏览器会带上 Origin
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect("/ws", headers={"origin": "https://evil.example"}) as ws:
            ws.receive_json()
    assert client.post("/api/companies", json={}, headers={"origin": "https://evil.example"}).status_code == 403
    # DNS 重绑定：请求发到本机，但 Host 是攻击者的域名
    assert client.get("/api/kb/search?q=x", headers={"host": "evil.example:8780"}).status_code == 403
    # 本服务自己的页面发的请求可以
    with client.websocket_connect("/ws", headers={"origin": "http://127.0.0.1:8780"}) as ws:
        assert ws.receive_json()["type"] == "home"


def test_token_is_persistent(tmp_path):
    first = load_token(tmp_path)
    assert len(first) >= 32 and load_token(tmp_path) == first and (tmp_path / "web_token").is_file()
