"""Web 界面入口：python -m opc.web [--demo] [--port 8780]"""
import argparse
import json
import os
import socket
import sys
import tempfile
import threading
import time
import urllib.request
import webbrowser

import uvicorn

from .. import __version__, model_settings, use_utf8_output
from ..company import CompanyStore
from ..config import opc_home
from .guard import allowed_hosts, load_token
from .runtime import Hub
from .server import create_app


TOKEN_NOTE = '  链接里的 token 是访问令牌：防止你浏览的其他网站在后台操作{what}。它保存在本机、不会上传，不要发给别人。\n  这个浏览器打开过一次之后，直接访问 {url} 也可以。'


def port_in_use(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        try:
            s.bind(("127.0.0.1", port))
            return False
        except OSError:
            return True


def fetch_info(url: str) -> dict | None:
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))  # 绕过系统代理
    try:
        with opener.open(url + "/api/info", timeout=1) as r:
            info = json.load(r)
            return info if info.get("app") == "opc" else None
    except (OSError, ValueError):
        return None


def open_browser_when_ready(url: str, link: str) -> None:
    """确认是本进程的服务在响应后才打开浏览器（用带令牌的链接），避免连到之前没关的旧服务。"""
    for _ in range(50):
        info = fetch_info(url)
        if info and info.get("pid") == os.getpid():
            webbrowser.open(link)
            return
        time.sleep(0.2)


def main() -> None:
    parser = argparse.ArgumentParser(description="OPC 一人公司")
    parser.add_argument("--demo", action="store_true", help="演示模式：用假模型，不需要 API Key")
    parser.add_argument("--auto-approve", action="store_true", help="计划自动批准，不等 CEO 审批")
    parser.add_argument("--port", type=int, default=8780)
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args()
    use_utf8_output()

    url = f"http://127.0.0.1:{args.port}"
    # 访问令牌：防止用户浏览的其他网站操作公司（见 guard.py）。保存在 ~/.opc，重启不变
    token = load_token(opc_home())
    link = f"{url}/?token={token}"
    if port_in_use(args.port):
        info = fetch_info(url)
        if info and info.get("version") == __version__ and info.get("demo") == args.demo:
            print(f"OPC 已经在运行：{url}")
            if not args.no_browser:
                webbrowser.open(link)
            return
        sys.exit(f"\n启动失败：端口 {args.port} 已被占用（可能是之前启动的 OPC 还没关闭）。\n"
                 f"  · 关闭之前的窗口后重新启动；\n  · 或者换一个端口：python -m opc.web --port {args.port + 1}\n")

    if args.demo:
        from ..demo import demo_factory
        store = CompanyStore(tempfile.mkdtemp(prefix="opc-demo-"), llm_factory=demo_factory)
    else:
        store = CompanyStore()

    hub = Hub(store, demo=args.demo, auto_approve=args.auto_approve)
    if args.demo:
        mode = "演示模式（假模型，不调用 API）"
    else:  # 默认模型：网页里添加的模型连接优先，没有就用 .env
        cfg = model_settings.default_config(model_settings.load(store.home))
        mode = f"默认模型：{cfg.model}" if cfg.api_key else "还没有配置模型：在打开的网页里按提示添加（填一个 API Key 就行）"
    print(f"OPC v{__version__} 已启动，浏览器会自动打开：{link}\n  {mode}\n  数据保存在：{store.home}")
    print(TOKEN_NOTE.format(what="你的公司", url=url))
    print("关闭服务：关掉这个窗口，或者按 Ctrl+C")
    if not args.no_browser:
        threading.Thread(target=open_browser_when_ready, args=(url, link), daemon=True).start()
    uvicorn.run(create_app(hub, token=token, hosts=allowed_hosts(args.port)), host="127.0.0.1", port=args.port,
                log_level="warning")


if __name__ == "__main__":
    main()
