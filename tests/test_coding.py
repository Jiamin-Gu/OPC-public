"""工程师接入 Coding-agent 的测试。真的运行编码 agent（只有模型是假的）：写文件 → 运行命令 → 汇报。"""
import json
import sys
import threading
from types import SimpleNamespace

import pytest
from langgraph.checkpoint.memory import InMemorySaver

from opc import agents, coding
from opc.company import CompanyStore
from opc.graph import build_graph
from opc.jobs import JobCtx, run_chat
from opc.messages import MessageLog
from opc.state import new_message

pytestmark = pytest.mark.skipif(not coding.AVAILABLE, reason="没有安装 Coding-agent")

HELLO_CMD = f'"{sys.executable}" code/hello.py'


def call(id_, name, **args):
    return SimpleNamespace(id=id_, function=SimpleNamespace(name=name, arguments=json.dumps(args, ensure_ascii=False)))


def reply(content="", tool_calls=None):
    return SimpleNamespace(content=content, reasoning_content=None, tool_calls=tool_calls,
                           usage=SimpleNamespace(prompt=100, completion=10, cached=50))


class EngineerFake:
    """工程师和它调用的编码 agent 共用这个假模型，按系统提示词区分是谁在调用。"""

    def __init__(self, log):
        self.log = log

    def chat(self, messages, tools=None, on_delta=None, json_mode=False, on_tool_delta=None):
        names = [t["function"]["name"] for t in tools or []]
        system = messages[0]["content"]
        self.log.append({"who": "coder" if system.startswith("You are a coding agent") else "engineer",
                         "tools": names, "last": messages[-1]})
        if on_delta:
            on_delta("text", "…")
        if system.startswith("You are a coding agent"):
            last_user = max(i for i, m in enumerate(messages) if m["role"] == "user")
            step = sum(1 for m in messages[last_user:] if m["role"] == "assistant")
            if step == 0:
                return reply("先写代码", [call("w1", "write_file", path="code/hello.py", content="print('hi from code')\n")])
            if step == 1 and "run_command" in names:
                return reply("运行验证", [call("r1", "run_command", command=HELLO_CMD)])
            return reply("hello.py 已完成，运行输出 hi from code")
        if messages[-1]["role"] != "tool":
            return reply("交给编码 agent", [call("c1", "code_agent", task="写 code/hello.py 并运行验证")])
        return reply("工程师：代码已完成并通过运行验证")


@pytest.fixture
def setup(tmp_path):
    log = []
    store = CompanyStore(tmp_path / "home", llm_factory=lambda cfg, role: EngineerFake(log))

    def make(allow_run=True):
        company = store.create("测试", "x", [{"id": "engineer"}], allow_run=allow_run)
        return company, build_graph(InMemorySaver()), {"configurable": {"thread_id": company.id}}
    return make, log


def ask_engineer(graph, cfg, company, text="写个 hello", cancel=None):
    """CEO 私聊工程师（私聊是一个工作运行，见 jobs.run_chat）。"""
    log = MessageLog(graph.store, company.id)
    log.append("engineer", new_message("ceo", "text", text))
    events = []
    result = run_chat(JobCtx(company=company, log=log, emit=events.append, cancel=cancel or threading.Event(),
                             kind="chat", role="engineer"))
    return events, {"usage": result.get("usage", {}), "result": result}


def test_engineer_delegates_to_coding_agent_which_runs_code(setup):
    make, log = setup
    company, graph, cfg = make(allow_run=True)
    events, state = ask_engineer(graph, cfg, company)

    assert (company.files_root / "code" / "hello.py").read_text() == "print('hi from code')\n"
    assert company.list_files()[0]["author"] == "engineer"
    # 编码 agent 真的运行了命令，结果交回给工程师
    back_to_engineer = [e for e in log if e["who"] == "engineer"][-1]["last"]["content"]
    assert f"$ {HELLO_CMD} → 退出码 0" in back_to_engineer and "hi from code" in back_to_engineer
    # 工程师的消息记录了编码 agent 的每一步，界面也收到了实时事件
    msg = MessageLog(graph.store, company.id).read("engineer")[-1]
    run_steps = [t for t in msg["tools"] if t["name"] == "run_command"]
    assert run_steps == [{"name": "run_command", "via": "coder", "ok": True, "command": HELLO_CMD, "exit": 0}]
    assert msg["files"] == ["code/hello.py"]
    assert any(e["type"] == "tool" and e.get("via") == "coder" for e in events)
    assert any(e["type"] == "file" and e["path"] == "code/hello.py" for e in events)
    # 用量合并：工程师 2 次 + 编码 agent 3 次
    assert state["usage"]["engineer"]["calls"] == 5
    # 交给编码 agent 的任务带上了安全约束（不装包、不碰工作区以外的文件）
    coder_task = next(e for e in log if e["who"] == "coder")["last"]["content"]
    assert coder_task.startswith("写 code/hello.py") and "不要用 pip" in coder_task


def test_without_permission_coding_agent_cannot_run_commands(setup):
    make, log = setup
    company, graph, cfg = make(allow_run=False)
    ask_engineer(graph, cfg, company)
    coder_tools = next(e["tools"] for e in log if e["who"] == "coder")
    assert "run_command" not in coder_tools and "edit_file" in coder_tools
    assert "start_process" not in coder_tools  # 后台进程也能执行命令，同样要去掉
    back = [e for e in log if e["who"] == "engineer"][-1]["last"]["content"]
    assert "没有允许运行代码" in back


def test_engineer_prompt_and_tools(setup, monkeypatch):
    make, _ = setup
    company, _, _ = make(allow_run=True)
    assert "code_agent" in [t["function"]["name"] for t in agents.tools_for("engineer")]
    assert "code_agent" not in [t["function"]["name"] for t in agents.tools_for("pm")]
    assert "code_agent" in agents.system_prompt(company, "engineer")
    monkeypatch.setattr(coding, "AVAILABLE", False)  # 没装 Coding-agent 时退回只写文件
    assert "code_agent" not in [t["function"]["name"] for t in agents.tools_for("engineer")]
    assert "code_agent" not in agents.system_prompt(company, "engineer")


def test_stop_while_coding_agent_works(setup):
    make, _ = setup
    company, graph, cfg = make()
    cancel = threading.Event()
    cancel.set()
    _, state = ask_engineer(graph, cfg, company, cancel=cancel)
    assert state["result"].get("cancelled")


# ---------------------------------------------------------------- 测试角色运行测试（run_command）

PYTEST_CMD = f'"{sys.executable}" -m pytest -q -p no:cacheprovider tests/test_add.py'


class QAFake:
    """测试角色：先写一个测试文件，再用 run_command 运行，最后根据运行结果汇报。"""

    def __init__(self, log):
        self.log = log

    def chat(self, messages, tools=None, on_delta=None, json_mode=False, on_tool_delta=None):
        names = [t["function"]["name"] for t in tools or []]
        self.log.append({"tools": names, "system": messages[0]["content"], "last": messages[-1]})
        step = sum(1 for m in messages if m["role"] == "assistant")
        if step == 0 and "write_file" in names:
            return reply("先写测试", [call("w1", "write_file", path="tests/test_add.py",
                                          content="def test_add():\n    assert 1 + 1 == 2\n")])
        if step <= 1 and "run_command" in names:
            return reply("运行测试", [call("r1", "run_command", command=PYTEST_CMD)])
        return reply("测试：1 个用例通过")


@pytest.fixture
def tester(tmp_path):
    log = []
    store = CompanyStore(tmp_path / "home", llm_factory=lambda cfg, role: QAFake(log))

    def make(allow_run=True):
        company = store.create("测试", "x", [{"id": "engineer"}, {"id": "qa"}], allow_run=allow_run)
        return company, build_graph(InMemorySaver())
    return make, log


def test_tester_writes_and_runs_tests(tester):
    make, log = tester
    company, graph = make(allow_run=True)
    mlog = MessageLog(graph.store, company.id)
    mlog.append("qa", new_message("ceo", "text", "给加法写个测试并运行"))
    events = []
    run_chat(JobCtx(company=company, log=mlog, emit=events.append, cancel=threading.Event(), kind="chat", role="qa"))

    assert "run_command" in log[0]["tools"] and "code_agent" not in log[0]["tools"]
    assert "run_command" in log[0]["system"] and "不要自己去改实现代码" in log[0]["system"]
    back = log[-1]["last"]["content"]  # 运行结果交回给测试角色：命令、在哪运行、退出码和输出
    assert back.startswith(f"$ {PYTEST_CMD}（在本机运行）") and "退出码：0" in back and "1 passed" in back
    msg = mlog.read("qa")[-1]
    assert [t for t in msg["tools"] if t["name"] == "run_command"] == [
        {"name": "run_command", "command": PYTEST_CMD, "exit": 0, "ok": True}]
    assert any(e["type"] == "tool" and e.get("name") == "run_command" and e.get("exit") == 0 for e in events)


def test_tester_cannot_run_without_permission(tester):
    make, log = tester
    company, graph = make(allow_run=False)
    assert not agents.runs_tests(company, "qa")
    assert "run_command" not in agents.system_prompt(company, "qa")
    mlog = MessageLog(graph.store, company.id)
    mlog.append("qa", new_message("ceo", "text", "运行一下测试"))
    run_chat(JobCtx(company=company, log=mlog, emit=lambda e: None, cancel=threading.Event(), kind="chat", role="qa"))
    assert all("run_command" not in e["tools"] for e in log)


def test_tester_can_run_tests_in_readonly_review(tester):
    """审查和只读讨论不能改文件，但测试角色可以运行测试核实（不给写文件的工具）。"""
    from opc import worker
    make, log = tester
    company, _ = make(allow_run=True)
    worker.run(company, "qa", [{"role": "system", "content": "x"}, {"role": "user", "content": "核实一下"}],
               lambda e: None, "m1", "qa", threading.Event(), 4, readonly=True)
    assert "run_command" in log[0]["tools"] and "write_file" not in log[0]["tools"]
    assert "run_command" not in [t["function"]["name"] for t in agents.tools_for("engineer")]  # 工程师用编码 agent 运行


def test_command_runner_failure_long_output_and_errors(tmp_path):
    company = SimpleNamespace(files_root=tmp_path)
    runner = coding.CommandRunner(company)
    text, summary = runner.run({"command": f'"{sys.executable}" -c "print(\'x\' * 30000); raise SystemExit(3)"'})
    assert summary["exit"] == 3 and summary["ok"] is False
    assert "退出码：3" in text and "中间省略" in text and len(text) < coding.MAX_OUTPUT + 500
    assert runner.run({})[1]["ok"] is False  # 没给命令
    text, summary = runner.run({"command": "rm -rf /"})  # 危险命令由 Coding-agent 的规则拦下
    assert summary["ok"] is False and text.startswith("错误")
    runner.close()


def test_run_command_blocked_after_suspicious_content():
    from opc import security
    guard = security.Guard()
    assert guard.blocks("run_command", {"command": "pytest"}, set()) is None
    guard.tainted = True
    assert "不能运行命令" in guard.blocks("run_command", {"command": "pytest"}, set())
    guard.trusted = True  # CEO 信任并重做
    assert guard.blocks("run_command", {"command": "pytest"}, set()) is None


def test_testing_roles_and_planner_hint(tester):
    from opc.planner import _roles_text
    from opc.roles import ROLES
    assert ROLES["qa"].can_test and not ROLES["qa"].can_code
    assert ROLES["testing-reality-checker"].can_test           # 角色库测试部门里不写代码的角色
    assert not ROLES["testing-api-tester"].can_test             # 会写代码的用编码 agent
    make, _ = tester
    company, _ = make(allow_run=True)
    assert "可以运行命令和测试" in _roles_text(company)
    company, _ = make(allow_run=False)
    assert "可以运行命令和测试" not in _roles_text(company)


# ---------------------------------------------------------------- v1.0 验收发现的问题

class FakeSandbox:
    """假的沙箱：命令其实在本机执行（接口和 Coding-agent 的执行器一样），只用来确认沙箱被交给了编码 agent。"""

    def __init__(self, root):
        from coder.tools.shell import LocalExecutor
        self._local = LocalExecutor()
        self.supports_network = False
        self.stopped = False

    def __getattr__(self, name):
        return getattr(self._local, name)

    def stop(self):
        self.stopped = True


def test_sandbox_is_given_to_coding_agent(setup, monkeypatch):
    """编码 agent 要知道命令在沙箱（Linux 容器）里执行：系统提示词里的环境说明随之改变。验收时没传，模型以为在 Windows 上。"""
    make, log = setup
    company, graph, cfg = make(allow_run=True)
    boxes = []
    monkeypatch.setattr(coding, "sandbox_for", lambda c, root: (boxes.append(FakeSandbox(root)) or boxes[-1], ""))
    seen = []

    class Spy(coding.CodingAgent):
        def __init__(self, *args, **kwargs):
            seen.append(kwargs.get("sandbox"))
            super().__init__(*args, **kwargs)

    monkeypatch.setattr(coding, "CodingAgent", Spy)
    ask_engineer(graph, cfg, company)
    assert seen == boxes and boxes[0].stopped  # 交给了编码 agent；用完就删掉容器
    assert "命令在 Docker 沙箱里执行" in [e for e in log if e["who"] == "engineer"][-1]["last"]["content"]


def test_sandbox_for_does_not_fall_back_once_used(tmp_path, monkeypatch):
    """用过沙箱的公司，Docker 用不了时不悄悄改在本机运行；没用过的照旧在本机；OPC_CODE_SANDBOX=0 是明确不用沙箱。"""
    monkeypatch.setenv("OPC_CODE_SANDBOX", "1")  # conftest 里为所有测试关掉了沙箱
    store = CompanyStore(tmp_path / "home", llm_factory=lambda cfg, role: None)
    company = store.create("测试", "x", [{"id": "engineer"}], allow_run=True)
    monkeypatch.setattr(coding, "DockerSandbox", lambda root: "沙箱")
    monkeypatch.setattr(coding, "sandbox_status", lambda: (False, "Docker 没有运行"))
    assert coding.sandbox_for(company, tmp_path) == (None, "")  # 从来没用过沙箱：本机运行
    monkeypatch.setattr(coding, "sandbox_status", lambda: (True, "就绪"))
    assert coding.sandbox_for(company, tmp_path) == ("沙箱", "")
    assert store.load(company.id).meta["sandboxed"]  # 记进公司配置，重启后也记得
    monkeypatch.setattr(coding, "sandbox_status", lambda: (False, "Docker 没有运行"))
    box, refused = coding.sandbox_for(company, tmp_path)
    assert box is None and "Docker 沙箱现在用不了（Docker 没有运行）" in refused
    monkeypatch.setenv("OPC_CODE_SANDBOX", "0")
    assert coding.sandbox_for(company, tmp_path) == (None, "")


def test_coding_agent_writes_only_when_sandbox_lost(setup, monkeypatch):
    make, log = setup
    company, graph, cfg = make(allow_run=True)
    monkeypatch.setattr(coding, "sandbox_for", lambda c, root: (None, "Docker 沙箱现在用不了（Docker 没有运行），为了安全不改在本机运行命令；Docker 恢复后再试"))
    events, _ = ask_engineer(graph, cfg, company)
    coder_tools = next(e["tools"] for e in log if e["who"] == "coder")
    assert "run_command" not in coder_tools and "start_process" not in coder_tools
    back = [e for e in log if e["who"] == "engineer"][-1]["last"]["content"]
    assert "Docker 沙箱现在用不了" in back and "未经实际运行验证" in back
    assert any(e["type"] == "notice" and "Docker 沙箱现在用不了" in e["text"] for e in events)  # 提醒 CEO


def test_tester_command_refused_when_sandbox_lost(tmp_path, monkeypatch):
    monkeypatch.setattr(coding, "sandbox_for", lambda c, root: (None, "Docker 沙箱现在用不了（x）"))
    runner = coding.CommandRunner(SimpleNamespace(files_root=tmp_path))
    text, summary = runner.run({"command": "echo hi"})
    assert text.startswith("错误：Docker 沙箱现在用不了") and summary["ok"] is False
