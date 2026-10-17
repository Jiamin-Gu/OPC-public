"""测试用的迷你 MCP 服务器（stdio）：握手、分页列工具、调用工具，会反过来问客户端 roots。

工具：web_search（搜索）、read_page（读网页，url 里有 bad 就报错）、research（调研）、noise（对角色没用的工具）。
"""
import json
import sys
import time

TOOLS = [
    {"name": "web_search", "description": "搜索", "inputSchema": {
        "$schema": "http://json-schema.org/draft-07/schema#", "type": "object",
        "properties": {"query": {"type": "string"}}, "required": ["query"]}},
    {"name": "read_page", "description": "读网页", "inputSchema": {"type": "object", "properties": {"url": {"type": "string"}}}},
    {"name": "research", "description": "调研助手", "inputSchema": {"type": "object", "properties": {"question": {"type": "string"}}}},
    {"name": "noise", "description": "没用的工具", "inputSchema": {"type": "object", "properties": {}}},
]


def send(msg):
    sys.stdout.write(json.dumps(msg, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stdin.reconfigure(encoding="utf-8")
    for line in sys.stdin:
        msg = json.loads(line)
        method, rid = msg.get("method"), msg.get("id")
        if rid is None or "result" in msg:  # 通知，或者客户端对 roots/list 的回答
            continue
        if method == "initialize":
            send({"jsonrpc": "2.0", "id": 900, "method": "roots/list"})
            send({"jsonrpc": "2.0", "id": rid, "result": {"protocolVersion": msg["params"]["protocolVersion"],
                                                          "capabilities": {"tools": {}}, "serverInfo": {"name": "fake"}}})
        elif method == "tools/list":  # 分两页
            cursor = (msg.get("params") or {}).get("cursor")
            send({"jsonrpc": "2.0", "id": rid, "result": {"tools": TOOLS[2:] if cursor else TOOLS[:2],
                                                          **({} if cursor else {"nextCursor": "p2"})}})
        elif method == "tools/call":
            name, args = msg["params"]["name"], msg["params"].get("arguments", {})
            if name == "web_search":
                time.sleep(0.05)
                result = {"content": [{"type": "text", "text": f"关于「{args['query']}」的结果：竞品 A、竞品 B"}]}
            elif name == "read_page" and "bad" in args.get("url", ""):
                result = {"content": [{"type": "text", "text": "页面打不开"}], "isError": True}
            elif name == "read_page":
                result = {"content": [{"type": "text", "text": "正文"}, {"type": "image", "data": "", "mimeType": "image/png"}]}
            else:
                result = {"content": [{"type": "text", "text": f"{name} 的结果"}]}
            send({"jsonrpc": "2.0", "id": rid, "result": result})


if __name__ == "__main__":
    main()
