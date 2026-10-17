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
