"""测试不读本机的 MCP 配置（~/.opc/mcp.json）、不连本机的 MCP 服务器：默认没有配置任何服务器。
知识库默认不用向量模型（不加载、不下载），每个测试结束关掉打开的知识库。"""
import pytest

from opc import tracing, web_tools
from opc.knowledge import service as kb_service


@pytest.fixture(autouse=True)
def no_real_mcp(monkeypatch, tmp_path_factory):
    monkeypatch.setenv("OPC_MCP_CONFIG", str(tmp_path_factory.mktemp("mcp") / "mcp.json"))
    monkeypatch.delenv("OPC_MCP_URL", raising=False)
    monkeypatch.setenv("OPC_EMBED_BACKEND", "none")
    monkeypatch.setenv("OPC_RERANK", "none")
    monkeypatch.setenv("OPC_CODE_SANDBOX", "0")  # 测试不去查 Docker；沙箱另有专门的测试
    # 数据目录用一个还不存在的临时目录：不碰本机的 ~/.opc，也和 CI 的新机器一样（目录要靠程序自己建）
    monkeypatch.setenv("OPC_HOME", str(tmp_path_factory.mktemp("opc") / "home"))
    web_tools.reset()
    kb_service.reset()
    yield
    web_tools.reset()
    kb_service.reset()
    tracing.reset()
