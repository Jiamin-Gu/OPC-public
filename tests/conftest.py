"""测试不读本机的 MCP 配置（~/.opc/mcp.json）、不连本机的 MCP 服务器：默认没有配置任何服务器。"""
import pytest

from opc import web_tools


@pytest.fixture(autouse=True)
def no_real_mcp(monkeypatch, tmp_path_factory):
    monkeypatch.setenv("OPC_MCP_CONFIG", str(tmp_path_factory.mktemp("mcp") / "mcp.json"))
    monkeypatch.delenv("OPC_MCP_URL", raising=False)
    web_tools.reset()
    yield
    web_tools.reset()
