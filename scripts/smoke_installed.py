"""冒烟测试（CD 流水线用）：在一个全新的虚拟环境里装好打出来的安装包之后运行，确认装出来的东西能用。

只检查"包装对了没有"：模块都能导入、数据文件（角色库、提示词、交付模板、网页）都打进了包里、命令行入口存在。
功能本身由测试负责（CI）。要在仓库目录以外运行，免得导入的是仓库里的源码而不是装好的包。
"""
import importlib.util
import shutil
import sys
from pathlib import Path

import opc
from opc import prompts, roles, task_formats
from opc.web import server  # noqa: F401  网页服务和它依赖的运行时、引擎、知识库都能导入

pkg = Path(opc.__file__).resolve().parent
problems = []
if "site-packages" not in str(pkg) and "dist-packages" not in str(pkg):
    problems.append(f"导入的不是装好的包：{pkg}")
if len(roles.LIBRARY_ROLES) < 200:
    problems.append(f"角色库只有 {len(roles.LIBRARY_ROLES)} 个角色（应该有 277 个左右）：数据文件没打进包里")
if not prompts.role_prompt("pm"):
    problems.append("读不到预设角色的提示词")
if not task_formats.catalog(["pm"]):
    problems.append("读不到交付模板")
if not (pkg / "web" / "static" / "index.html").is_file():
    problems.append("网页文件（web/static）没打进包里")
if importlib.util.find_spec("opc.knowledge.service") is None:
    problems.append("知识库模块缺失")
if not shutil.which("opc"):
    problems.append("命令行入口 opc 没有装上")

if problems:
    print("冒烟测试没通过：\n- " + "\n- ".join(problems))
    sys.exit(1)
print(f"冒烟测试通过：opc {opc.__version__}，{len(roles.ROLES)} 个角色，装在 {pkg}")
