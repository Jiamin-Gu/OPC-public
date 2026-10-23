"""工程师的编码能力：把写代码、运行和测试交给 Coding-agent（https://github.com/Jiamin-Gu/Coding-agent-public）。

工程师 agent 多了一个 code_agent 工具。调用它时，会在公司共享文件区里启动一个完整的编码 agent：
它能阅读文档、精确编辑代码、运行命令和测试，直到完成后把结果交回给工程师。
编码 agent 的每一步（编辑文件、运行命令及退出码）都会实时显示在工程师的消息里。

Coding-agent 没有安装时，工程师退回到只写文件、不运行代码的模式。

公司打开了知识库时，编码 agent 也多一个 kb_search 工具（KBSearchTool）：自己查需求、以前的决定和已有代码。
这个工具写在 OPC 里，按 Coding-agent 的工具接口实现后注入进去，Coding-agent 本身不用改。
"""
import re
import threading
import time

from . import tracing

try:
    import coder
    from coder.agent import Agent as CodingAgent
    from coder.permissions import PermissionManager
    from coder.tools import ProcessManager, ToolError, build_tools
    from coder.tools.base import Tool as CoderTool
    from coder.workspace import Workspace
    try:  # Docker 沙箱（Coding-agent v2.4 起有）
        from coder.sandbox import DockerSandbox, docker_status
    except ImportError:
        DockerSandbox, docker_status = None, None
    AVAILABLE = tuple(int(x) for x in coder.__version__.split(".")[:2]) >= (1, 0)
    VERSION = coder.__version__
except ImportError:
    AVAILABLE, VERSION, CoderTool, ToolError = False, None, object, Exception
    DockerSandbox, docker_status = None, None

_sandbox_cache: dict = {"at": 0.0, "status": (False, "")}


def sandbox_status() -> tuple[bool, str]:
    """运行代码能不能放进 Docker 沙箱：(能不能, 说明)。查一次要一两秒（docker info），结果缓存 60 秒。
    OPC_CODE_SANDBOX=0 时不用沙箱（只用于对比和排查问题）。"""
    import os
    if not AVAILABLE or docker_status is None:
        return False, "装的 Coding-agent 没有沙箱功能（需要 v2.4 以上）"
    if os.getenv("OPC_CODE_SANDBOX", "1").strip() == "0":
        return False, "OPC_CODE_SANDBOX=0：没有用沙箱"
    now = time.monotonic()
    if now - _sandbox_cache["at"] > 60:
        try:
            _sandbox_cache["status"] = docker_status()
        except Exception as e:
            _sandbox_cache["status"] = (False, f"检查 Docker 失败：{e}")
        _sandbox_cache["at"] = now
    return _sandbox_cache["status"]


def sandbox_for(company, root) -> tuple[object, str]:
    """这一次运行命令用的沙箱：(沙箱或 None, 不能运行的原因)。
    这家公司以前用过沙箱、现在 Docker 用不了（比如中途退出了）时，不悄悄改在本机运行：返回原因，调用方只写不运行。
    v1.0 验收时 Docker Desktop 中途退出，编码 agent 的命令就没有任何提示地改在了本机执行。
    从来没用过沙箱的公司（没装 Docker）照旧在本机运行，创建公司时已经提示过；OPC_CODE_SANDBOX=0 是明确不用沙箱。"""
    import os
    ok, why = sandbox_status()
    if ok:
        if hasattr(company, "mark_sandboxed"):
            company.mark_sandboxed()
        return DockerSandbox(root), ""
    if (getattr(company, "meta", None) or {}).get("sandboxed") and os.getenv("OPC_CODE_SANDBOX", "1").strip() != "0":
        return None, f"Docker 沙箱现在用不了（{why}），为了安全不改在本机运行命令；Docker 恢复后再试"
    return None, ""

CODER_MAX_STEPS = 30
# 编码 agent 的上下文超过这么多 token 就压缩（它自己的默认值是 6 万）。
# 实测一次任务里编码 agent 调用几十次模型，每次都带着完整上下文，压缩得早一些能明显省钱
CODER_CONTEXT_LIMIT = 30_000

# 追加在每个编码任务后面的固定约束：命令在用户自己的电脑上执行，不能影响工作区以外的环境
SAFETY_RULES = """

【必须遵守的约束】
- 只在当前工作区内读写文件；不要读取或修改工作区以外的文件（包括用户主目录下的数据文件）
- 不要用 pip/conda 等安装、升级或卸载任何包，也不要 pip install -e；只使用已经安装好的库（比如 pytest）
- 不要启动长期运行的服务或后台进程；测试和临时数据使用工作区内或系统临时目录里的文件
- 完成后说明：改了哪些文件、运行了哪些验证、结果如何"""

CODE_TOOL = {"type": "function", "function": {
    "name": "code_agent",
    "description": "把写代码、修改代码、运行和测试的工作交给编码 agent。它会在公司共享文件区里阅读文档、编写和修改代码、"
                   "运行命令和测试，反复修正直到完成，然后返回改了哪些文件、运行了哪些命令及结果。",
    "parameters": {"type": "object", "properties": {
        "task": {"type": "string", "description": "完整的编码任务说明：要实现或修复什么、依据哪些文档（写出路径）、"
                                                  "代码放在哪个目录、怎样验证（例如写测试并运行通过）"}},
        "required": ["task"]}}}


# 能执行命令的工具：公司没有允许运行代码时全部去掉（Coding-agent v2.3 起除了 run_command 还有后台进程工具）
RUN_TOOLS = ("run_command", "start_process", "process_output", "stop_process", "list_processes")

# 测试角色运行测试用的工具：只运行命令、看结果，不像编码 agent 那样会顺手改代码（发现问题要报告出来，交给负责人返工）
RUN_TOOL = {"type": "function", "function": {
    "name": "run_command",
    "description": "在公司共享文件区的根目录运行一条命令，返回退出码和输出。用来运行测试（例如 pytest -q、npm test）、"
                   "核实\"测试通过\"之类的说法。不能联网、不能安装依赖，只能用已经装好的库（Python 有 pytest）；"
                   "不要运行需要交互输入或者不会自己退出的命令（开发服务器等）。",
    "parameters": {"type": "object", "properties": {
        "command": {"type": "string", "description": "要运行的命令，例如 pytest -q tests/"},
        "timeout": {"type": "integer", "description": "超时秒数，默认 120，最多 600"}},
        "required": ["command"]}}}
MAX_OUTPUT = 12_000  # 命令输出太长时只留开头和结尾（测试失败的摘要一般在最后）


class CommandRunner:
    """测试角色的 run_command：有 Docker 就在沙箱里运行（只看得到公司文件区、断网、非 root），没有就在本机运行，
    和编码 agent 的规则一样。一次执行里的命令共用一个沙箱容器，第一次运行时才创建，执行结束时删掉。"""

    def __init__(self, company):
        self.company = company
        self.root = company.files_root
        self.sandbox = None
        self.tool = None

    def _ensure(self) -> None:
        if self.tool is None:
            workspace = Workspace(self.root)
            self.sandbox, refused = sandbox_for(self.company, workspace.root)
            if refused:  # 以前用沙箱、现在 Docker 用不了：这次不运行（下次调用再查）
                raise ToolError(refused)
            self.tool = build_tools(workspace, None, self.sandbox)["run_command"]

    def run(self, args: dict) -> tuple[str, dict]:
        """返回 (给模型的结果, 工具记录 {name, command, exit, ok})。"""
        command = str(args.get("command") or "").strip()
        if not command:
            return "错误：需要提供 command（要运行的命令）", {"name": "run_command", "ok": False}
        try:
            timeout = int(args.get("timeout") or 120)
        except (TypeError, ValueError):
            timeout = 120
        summary = {"name": "run_command", "command": command[:300]}
        try:
            self._ensure()
            out = self.tool.run(command=command, timeout=timeout)
        except ToolError as e:
            return f"错误：{e}", {**summary, "ok": False}
        except Exception as e:  # Docker 出问题等：告诉角色没能运行，不要当成测试结果
            return f"错误：命令没能运行（{type(e).__name__}: {e}）", {**summary, "ok": False}
        m = re.match(r"退出码：(-?\d+)", out)
        code = int(m.group(1)) if m else None
        if len(out) > MAX_OUTPUT:
            out = out[:3000] + f"\n…（中间省略 {len(out) - MAX_OUTPUT} 字）…\n" + out[-(MAX_OUTPUT - 3000):]
        where = "（在 Docker 沙箱里运行）" if self.sandbox is not None else "（在本机运行）"
        return f"$ {command}{where}\n{out}", {**summary, "exit": code, "ok": code == 0}

    def close(self) -> None:
        if self.sandbox is not None:
            try:
                self.sandbox.stop()
            except Exception:
                pass
            self.sandbox = None
        self.tool = None


class Stop(Exception):
    """CEO 点了停止（由调用方转换成 OPC 的 Cancelled）。"""


class KBSearchTool(CoderTool):
    """给编码 agent 的公司知识库检索工具（只读，不需要确认）。参数和角色的 kb_search 一样。"""
    name = "kb_search"
    needs_permission = False

    def __init__(self, workspace, company):
        from .agents import KB_TOOL
        super().__init__(workspace)
        self.company = company
        self.description = KB_TOOL["function"]["description"]
        self.parameters = KB_TOOL["function"]["parameters"]

    def run(self, **kwargs) -> str:
        import json

        from .agents import call_kb
        return call_kb(self.company, json.dumps(kwargs, ensure_ascii=False))[0]


class ProgressUI:
    """实现 Coding-agent 的界面接口（和它的终端界面、网页界面是同一套），把每一步转成 OPC 的实时事件。"""

    def __init__(self, company, author: str, emit, base_event: dict, cancel: threading.Event):
        self.company, self.author, self.emit, self.base, self.cancel = company, author, emit, base_event, cancel
        self._current: tuple[str, dict] | None = None
        self.log: list[dict] = []        # 给界面和消息保存的步骤摘要
        self.commands: list[dict] = []   # 运行过的命令及退出码
        self.files: list[str] = []       # 新建或修改过的文件
        self.final = ""                  # 编码 agent 的最后一段说明
        self._chars = 0                  # 当前这次生成已输出的字数（用于进度提示）
        self._last_progress = 0.0

    def _check(self):
        if self.cancel.is_set():
            raise Stop()

    def delta(self, kind, text):
        """编码 agent 的逐字输出不直接显示（避免和工程师的消息混在一起），只每秒推送一次进度：
        它一次写一个大文件时可能要几十秒，没有进度提示看起来像卡住了。"""
        self._check()
        self._chars += len(text)
        now = time.monotonic()
        if now - self._last_progress >= 1.0:
            self._last_progress = now
            self.emit({"type": "progress", **self.base, "chars": self._chars})

    def assistant(self, text, reasoning=None):
        if text:
            self.final = text

    def tool_call(self, name, args):
        self._check()
        self._current = (name, args)
        self._chars = 0
        parent = tracing.current()  # 链路：编码 agent 的每次工具调用挂在 code_agent 这个步骤下面
        self._span = parent.child(f"编码 agent 工具 {name}", "tool", **{
            "gen_ai.tool.name": name, "opc.via": "coder", "opc.args_preview": tracing.preview(args, 200)}) if parent else None

    def tool_result(self, result):
        self._check()
        name, args = self._current or ("", {})
        ok = not result.startswith(("错误", "用户拒绝"))
        step = {"name": name, "via": "coder", "ok": ok}
        if name == "run_command":
            m = re.match(r"退出码：(-?\d+)", result)
            code = int(m.group(1)) if m else None
            step.update(command=args.get("command", ""), exit=code, ok=ok and code == 0)
            self.commands.append({"command": step["command"], "exit": code, "output": result[-1500:]})
        elif name in ("write_file", "edit_file"):
            path = args.get("path", "")
            step["path"] = path
            if ok:
                if path not in self.files:
                    self.files.append(path)
                self.company.record_author(path, self.author)
                self.emit({"type": "file", "path": path, "author": self.author})
        elif name == "kb_search":
            m = re.match(r"找到 (\d+) 条", result)
            step.update(query=args.get("query", ""), hits=int(m.group(1)) if m else 0)
        else:
            step["path"] = args.get("path") or args.get("pattern") or ""
        self.log.append(step)
        self.emit({"type": "tool", **self.base, **step})
        span, self._span = getattr(self, "_span", None), None
        if span is not None:
            span.set(**{k: v for k, v in step.items() if k in ("path", "command", "exit")})
            span.finish(error=result[:300] if not step["ok"] else None)

    def info(self, text):
        pass

    def warn(self, text):
        pass

    def error(self, text):
        pass

    def ask_permission(self, tool_name, preview, prefix=None, network=False):
        # 编码 agent 以自动模式运行，是否能运行命令由工具列表决定；只有沙箱里的命令要联网时会来问：一律拒绝
        # （没人在旁边确认，联网可能被诱导把代码或密钥发出去；需要的库应该已经装好）
        if network:
            return "n", "OPC 里的编码 agent 在沙箱中不能联网：只用已经装好的库，不要下载或安装东西"
        return "y", ""


def run_code_agent(company, role_id: str, task: str, emit, base_event: dict, cancel: threading.Event,
                   allow_run: bool) -> dict:
    """运行一次编码 agent，返回 {result（交给工程师的文字）, files, steps, usage}。"""
    workspace = Workspace(company.files_root)
    # 运行代码时有 Docker 就进沙箱：容器里只看得到公司文件区，默认断网、非 root，碰不到这台电脑上别的东西
    sandbox, refused = sandbox_for(company, workspace.root) if allow_run else (None, "")
    if refused:  # 以前用沙箱、现在 Docker 用不了：这次只写不运行，并提醒 CEO
        allow_run = False
        emit({"type": "notice", "level": "error", "text": f"编码 agent：{refused}"})
    processes = ProcessManager(sandbox)  # 编码 agent 启动的后台进程（开发服务器等），结束时全部停掉
    tools = build_tools(workspace, processes, sandbox)
    if not allow_run:  # 公司没有允许运行代码：只能读写。所有能执行命令的工具都要去掉，包括后台进程
        for name in RUN_TOOLS:
            tools.pop(name, None)
    if company.kb:
        tools["kb_search"] = KBSearchTool(workspace, company)
    ui = ProgressUI(company, role_id, emit, base_event, cancel)
    # sandbox 也要交给编码 agent：它据此告诉模型命令在 Linux 容器里执行。v1.0 验收时没传，模型以为在 Windows 上，
    # 每次开头都先试 dir、where，还留下了一个叫 nul 的文件（2>nul）
    agent = CodingAgent(company.llm_for(role_id), tools, workspace,
                        PermissionManager(ask=ui.ask_permission, auto=True), ui, max_steps=CODER_MAX_STEPS,
                        context_limit=CODER_CONTEXT_LIMIT, sandbox=sandbox)
    emit({"type": "tool", **base_event, "name": "code_agent", "ok": True, "phase": "start"})
    try:
        agent.run(task + SAFETY_RULES)
    finally:
        processes.stop_all()  # 不留下编码 agent 启动的服务器
        if sandbox is not None:
            sandbox.stop()      # 每次编码任务用完就删掉容器

    where = ("（命令在 Docker 沙箱里执行）" if sandbox is not None else ("（命令在本机执行）" if allow_run else "")
             if not refused else f"（{refused}，这次只写代码、没有运行）")
    lines = [f"编码 agent 已结束（调用模型 {agent.steps} 次）{where}。"]
    lines.append("新建或修改的文件：" + ("、".join(ui.files) if ui.files else "无"))
    if ui.commands:
        lines.append("运行过的命令（按顺序）：")
        lines += [f"$ {c['command']} → 退出码 {c['exit']}" for c in ui.commands]
        last = ui.commands[-1]
        lines.append(f"最后一条命令的输出：\n{last['output']}")
    elif allow_run:
        lines.append("没有运行任何命令（代码未经实际运行验证）。")
    elif refused:
        lines.append("沙箱用不了，代码未经实际运行验证；在汇报里说明，等 Docker 恢复后再运行验证。")
    else:
        lines.append("公司没有允许运行代码，代码未经实际运行验证。")
    if agent.steps >= CODER_MAX_STEPS:
        lines.append(f"注意：已达到 {CODER_MAX_STEPS} 步上限，工作可能没有全部完成。")
    lines.append(f"编码 agent 的总结：{ui.final or '（无）'}")
    usage = {"prompt": agent.usage["prompt"], "completion": agent.usage["completion"],
             "cached": agent.usage["cached"], "calls": agent.steps}
    return {"result": "\n".join(lines), "files": ui.files, "steps": ui.log, "usage": usage}
