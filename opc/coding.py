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
    from coder.tools import ProcessManager, build_tools
    from coder.tools.base import Tool as CoderTool
    from coder.workspace import Workspace
    AVAILABLE = tuple(int(x) for x in coder.__version__.split(".")[:2]) >= (1, 0)
    VERSION = coder.__version__
except ImportError:
    AVAILABLE, VERSION, CoderTool = False, None, object

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

    def ask_permission(self, tool_name, preview):
        return "y", ""  # 不会被调用：编码 agent 以自动模式运行，是否能运行命令由工具列表决定


def run_code_agent(company, role_id: str, task: str, emit, base_event: dict, cancel: threading.Event,
                   allow_run: bool) -> dict:
    """运行一次编码 agent，返回 {result（交给工程师的文字）, files, steps, usage}。"""
    workspace = Workspace(company.files_root)
    processes = ProcessManager()  # 编码 agent 启动的后台进程（开发服务器等），结束时全部停掉
    tools = build_tools(workspace, processes)
    if not allow_run:  # 公司没有允许运行代码：只能读写。所有能执行命令的工具都要去掉，包括后台进程
        for name in RUN_TOOLS:
            tools.pop(name, None)
    if company.kb:
        tools["kb_search"] = KBSearchTool(workspace, company)
    ui = ProgressUI(company, role_id, emit, base_event, cancel)
    agent = CodingAgent(company.llm_for(role_id), tools, workspace,
                        PermissionManager(ask=ui.ask_permission, auto=True), ui, max_steps=CODER_MAX_STEPS,
                        context_limit=CODER_CONTEXT_LIMIT)
    emit({"type": "tool", **base_event, "name": "code_agent", "ok": True, "phase": "start"})
    try:
        agent.run(task + SAFETY_RULES)
    finally:
        processes.stop_all()  # 不留下编码 agent 启动的服务器

    lines = [f"编码 agent 已结束（调用模型 {agent.steps} 次）。"]
    lines.append("新建或修改的文件：" + ("、".join(ui.files) if ui.files else "无"))
    if ui.commands:
        lines.append("运行过的命令（按顺序）：")
        lines += [f"$ {c['command']} → 退出码 {c['exit']}" for c in ui.commands]
        last = ui.commands[-1]
        lines.append(f"最后一条命令的输出：\n{last['output']}")
    elif allow_run:
        lines.append("没有运行任何命令（代码未经实际运行验证）。")
    else:
        lines.append("公司没有允许运行代码，代码未经实际运行验证。")
    if agent.steps >= CODER_MAX_STEPS:
        lines.append(f"注意：已达到 {CODER_MAX_STEPS} 步上限，工作可能没有全部完成。")
    lines.append(f"编码 agent 的总结：{ui.final or '（无）'}")
    usage = {"prompt": agent.usage["prompt"], "completion": agent.usage["completion"],
             "cached": agent.usage["cached"], "calls": agent.steps}
    return {"result": "\n".join(lines), "files": ui.files, "steps": ui.log, "usage": usage}
