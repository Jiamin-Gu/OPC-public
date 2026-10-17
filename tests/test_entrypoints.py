"""入口模块能被导入（防止只在启动时才暴露的语法错误）。"""
import importlib


def test_entrypoints_import():
    for name in ("opc.web.__main__", "opc.web.server", "opc.web.guard", "opc.web_tools", "opc.knowledge.__main__"):
        importlib.import_module(name)
