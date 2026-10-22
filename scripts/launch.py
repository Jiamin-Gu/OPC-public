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
NEWEST_TESTED = (3, 13)  # CI 上测试过的最新版本；更新的版本刚发布时，有的依赖可能还没有对应的安装包
RECOMMENDED = "3.12"
BUILD_FAILED = ("Failed to build", "Failed building wheel", "Microsoft Visual C++", "error: subprocess-exited-with-error")
NETWORK_FAILED = ("timed out", "SSLError", "ConnectionError", "Max retries exceeded", "NewConnectionError",
                  "Failed to establish", "getaddrinfo failed", "unable to access", "Could not resolve host", "Failed to connect")
# 编码 agent 从 GitHub 装的两种方式，装上的是同一份代码：有 Git 先用 Git（走 Git 自己的代理设置），
# 不行再下载源码压缩包（requirements-coder.txt，不需要 Git，走 pip 的代理设置）
CODER_GIT = "coder-agent @ git+https://github.com/Jiamin-Gu/Coding-agent-public.git"
PIP_NET = ("--timeout", "60", "--retries", "5")
DEPS = ["langgraph", "langgraph.checkpoint.sqlite", "fastapi", "uvicorn", "openai", "dotenv"]


def python(*args: str, quiet: bool = False) -> int:
    kw = {"stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL} if quiet else {}
    return subprocess.call([sys.executable, *args], cwd=ROOT, **kw)


def importable(modules: list[str]) -> bool:
    return python("-c", "import " + ", ".join(modules), quiet=True) == 0


def install(*args: str) -> tuple[int, str]:
    """pip install，输出照常显示，同时留一份用来判断失败原因。"""
    proc = subprocess.Popen([sys.executable, "-m", "pip", "install", *args], cwd=ROOT,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, errors="replace")
    lines = []
    for line in proc.stdout:
        print(line, end="")
        lines.append(line)
    return proc.wait(), "".join(lines)


def explain_failure(output: str) -> None:
    if any(s in output for s in BUILD_FAILED):
        print("\n[错误] 有依赖包需要在这台电脑上从源码编译，但没有编译工具，所以装不上。")
        if sys.version_info[:2] > NEWEST_TESTED:
            print(f"原因是 Python 版本太新（当前 {platform.python_version()}）：新版本刚发布时，一些依赖包还没有提供对应的安装包，"
                  "pip 只能尝试从源码编译。")
        print(f"解决办法：安装 Python {RECOMMENDED}（https://www.python.org/downloads/，可以和现在的版本共存，安装时勾选 Add python.exe to PATH），"
              "删掉本目录下的 .venv 文件夹（如果有），再重新运行。")
        return
    print("\n[错误] 依赖安装失败，请检查网络后重试。国内网络可以改用镜像：")
    print(f"  {sys.executable} -m pip install -r requirements.txt -i {MIRROR}")


def local_coder() -> Path | None:
    """OPC 旁边的 Coding-agent 文件夹（比如两个项目下载到了同一个目录下）：有的话直接从本地装，不用联网。"""
    try:
        for folder in sorted(ROOT.parent.iterdir()):
            toml = folder / "pyproject.toml"
            if folder != ROOT and toml.is_file() and 'name = "coder-agent"' in toml.read_text(encoding="utf-8", errors="replace"):
                return folder
    except OSError:
        pass
    return None


def install_coder() -> None:
    """装编码 agent：先找本地的 Coding-agent 文件夹；没有就从 GitHub 装，有 Git 先用 Git，不行再下载源码压缩包。
    在 Coding-agent 的文件夹里运行过它，不等于装进了 Python：OPC 是在自己用的 Python 里 import coder 来判断的。"""
    folder = local_coder()
    if folder:
        print(f"正在从旁边的文件夹安装编码 agent（工程师用来运行和测试代码）：{folder}")
        code, output = install(str(folder))
    else:
        code, output = 1, ""
        if shutil.which("git"):
            print("正在用 Git 从 GitHub 安装编码 agent（工程师用来运行和测试代码）……")
            code, output = install(*PIP_NET, CODER_GIT)
            if code:
                print("\n用 Git 没有装上，改为下载源码压缩包……")
        if code:
            if not output:
                print("正在从 GitHub 下载安装编码 agent（工程师用来运行和测试代码）……")
            code, more = install(*PIP_NET, "-r", "requirements-coder.txt")
            output += more
    if not code:
        return
    print("\n[提示] 编码 agent 没有装上：工程师只写代码、不运行，其他功能不受影响。")
    if not folder and any(s in output for s in NETWORK_FAILED):
        print("原因是连不上 GitHub。可以在浏览器里打开 https://github.com/Jiamin-Gu/Coding-agent-public ，"
              "点 Code → Download ZIP，解压到 OPC 旁边（和 OPC 文件夹放在同一个目录下），再运行一次。")
    print(f"如果已经装过 Coding-agent 但用的是另一个 Python（这里用的是 Python {platform.python_version()}），"
          f"可以在 Coding-agent 的文件夹里运行：\n  {sys.executable} -m pip install .\n")


def main() -> int:
    if sys.version_info < (3, 10):
        print(f"[错误] Python 版本太低，需要 3.10 或更高版本，当前是 {platform.python_version()}。")
        print("请从 https://www.python.org/downloads/ 安装新版本后重新运行。")
        return 1
    if sys.version_info[:2] > NEWEST_TESTED:
        print(f"[提示] 当前 Python {platform.python_version()} 比本项目测试过的版本（3.10～{NEWEST_TESTED[0]}.{NEWEST_TESTED[1]}）新。"
              f"新版本刚发布时，个别依赖可能还没有对应的安装包；如果下面安装失败，请改用 Python {RECOMMENDED}。\n")
    if not importable(DEPS):
        print("第一次运行，正在安装依赖，请稍候……")
        code, output = install("-r", "requirements.txt")
        if code:
            explain_failure(output)
            return 1
    if not importable(["coder"]):  # 可选：编码 agent（工程师用来运行和测试代码），装不上也不影响使用
        install_coder()
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
