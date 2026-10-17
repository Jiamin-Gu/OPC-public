"""一键启动（start.bat / start.sh 调用）：检查 Python 版本、第一次运行时安装依赖、创建配置文件，然后启动网页。

只用标准库（这时依赖可能还没装）。中文提示都从这里输出：Windows 批处理文件里写中文，cmd 执行完外部程序后
会从错误的位置接着读文件，把中文行截断成乱码，所以 start.bat 里只留英文。
"""
import platform
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MIRROR = "https://pypi.tuna.tsinghua.edu.cn/simple"
DEPS = ["langgraph", "langgraph.checkpoint.sqlite", "fastapi", "uvicorn", "openai", "dotenv"]


def python(*args: str, quiet: bool = False) -> int:
    kw = {"stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL} if quiet else {}
    return subprocess.call([sys.executable, *args], cwd=ROOT, **kw)


def importable(modules: list[str]) -> bool:
    return python("-c", "import " + ", ".join(modules), quiet=True) == 0


def main() -> int:
    if sys.version_info < (3, 10):
        print(f"[错误] Python 版本太低，需要 3.10 或更高版本，当前是 {platform.python_version()}。")
        print("请从 https://www.python.org/downloads/ 安装新版本后重新运行。")
        return 1
    if not importable(DEPS):
        print("第一次运行，正在安装依赖，请稍候……")
        if python("-m", "pip", "install", "-r", "requirements.txt"):
            print("\n[错误] 依赖安装失败，请检查网络后重试。国内网络可以改用镜像：")
            print(f"  {sys.executable} -m pip install -r requirements.txt -i {MIRROR}")
            return 1
    if not importable(["coder"]):  # 可选：编码 agent（工程师用来运行和测试代码），装不上也不影响使用
        print("正在安装编码 agent（工程师用来运行和测试代码，需要 Git）……")
        if python("-m", "pip", "install", "-r", "requirements-coder.txt"):
            print("\n[提示] 编码 agent 没有装上（可能没有安装 Git，或者连不上 GitHub）：工程师只写代码、不运行，"
                  "其他功能不受影响。\n")
    env = ROOT / ".env"
    if not env.exists() and (ROOT / ".env.example").exists():
        shutil.copy(ROOT / ".env.example", env)  # 模型也可以不写在这里：网页里会提示添加
    print("正在启动 OPC，浏览器会自动打开。关闭这个窗口即可停止服务。\n")
    return python("-m", "opc.web", *sys.argv[1:])


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(0)
