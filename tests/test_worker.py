"""角色执行循环（worker.py）：每步一个检查点；暂停用 interrupt，恢复时带着原对话接着做；
CEO 停止、插话、步数上限；进程重启后也能从检查点恢复；做完清掉检查点。"""
import sqlite3
import threading

import pytest
from langgraph.checkpoint.sqlite import SqliteSaver

from opc.agents import Cancelled
from opc.company import CompanyStore
from opc.worker import has_checkpoint, run

from fakes import call, reply


class ScriptLLM:
    """按顺序给出回复；每次调用前执行 hooks[第几次调用]（用来在中途发暂停信号等）。"""

    def __init__(self, replies, hooks=None):
        self.replies, self.hooks, self.calls, self.tools = list(replies), hooks or {}, [], []

    def chat(self, messages, tools=None, on_delta=None, json_mode=False, on_tool_delta=None):
        self.calls.append([dict(m) for m in messages])
        self.tools.append([t["function"]["name"] for t in tools] if tools else None)
        n = len(self.calls)
        if n in self.hooks:
            self.hooks[n]()
        r = self.replies.pop(0)
        for ch in r.content or "":  # 逐字推送：暂停信号在这里打断
            on_delta("text", ch)
        return r


@pytest.fixture
def company(tmp_path):
    llm = {}
    c = CompanyStore(tmp_path, llm_factory=lambda cfg, role_id: llm["x"]).create("公司", "", [{"id": "pm"}])
    c._llm = llm
    return c


def go(company, llm, **kw):
    company._llm["x"] = llm
    events = []
    out = run(company, "pm", kw.pop("conversation", [{"role": "user", "content": "写 PRD"}]), events.append, "m1", "all",
              kw.pop("cancel", threading.Event()), kw.pop("max_steps", 6), **kw)
    return out, events


def test_runs_tools_until_final_reply_and_cleans_up(company):
    llm = ScriptLLM([reply("先写文件", [call("c1", "write_file", path="docs/PRD.md", content="# PRD")]), reply("写好了")])
    out, events = go(company, llm, thread_id="t1")
    assert out["text"] == "先写文件\n\n写好了" and out["files"] == ["docs/PRD.md"] and out["usage"]["calls"] == 2
    assert company.read_file("docs/PRD.md") == "# PRD" and not out.get("paused")
    assert any(e["type"] == "tool" and e["name"] == "write_file" for e in events)
    assert not has_checkpoint(None, "t1")                                  # 做完清掉了


def test_pause_mid_generation_then_resume_with_original_conversation(company):
    stop = threading.Event()
    llm = ScriptLLM([reply("先写文件", [call("c1", "write_file", path="docs/PRD.md", content="# 初稿")]),
                     reply("这一段写到一半就被暂停了，会作废"),
                     reply("按上游改动更新好了")],
                    hooks={2: stop.set})                                   # 第 2 次调用模型时 CEO 暂停了这个任务
    out, _ = go(company, llm, stop=stop, thread_id="t2")
    assert out["paused"] and out["thread"] == "t2" and out["files"] == ["docs/PRD.md"]
    assert "作废" not in out["text"] and has_checkpoint(None, "t2")         # 写了一半的那一步丢掉了，停在检查点
    stop.clear()
    out, _ = go(company, llm, stop=stop, thread_id="t2", resume_note="上游改了：取消规则改成 2 小时", conversation=[])
    assert not out.get("paused") and out["text"].endswith("按上游改动更新好了")
    resumed = llm.calls[-1]                                                # 恢复后的这次调用：原对话 + 恢复说明
    assert resumed[1]["content"] == "写 PRD" and resumed[2]["role"] == "assistant"
    assert resumed[-1] == {"role": "user", "content": "上游改了：取消规则改成 2 小时"}
    assert not has_checkpoint(None, "t2")


def test_resume_after_restart_from_sqlite_checkpoint(company, tmp_path):
    """暂停后程序重启（新的数据库连接）：检查点在磁盘上，照样接着做。"""
    path = tmp_path / "cp.sqlite"
    stop = threading.Event()
    llm = ScriptLLM([reply("第一步", [call("c1", "list_files")]), reply("作废"), reply("完成")], hooks={2: stop.set})
    out, _ = go(company, llm, stop=stop, saver=SqliteSaver(sqlite3.connect(path, check_same_thread=False)), thread_id="t3")
    assert out["paused"]
    saver2 = SqliteSaver(sqlite3.connect(path, check_same_thread=False))  # "重启"
    assert has_checkpoint(saver2, "t3")
    out, _ = go(company, llm, saver=saver2, thread_id="t3", resume_note="", conversation=[])
    assert out["text"] == "第一步\n\n完成" and not has_checkpoint(saver2, "t3")


def test_cancel_interjection_and_step_limit(company):
    cancel = threading.Event()
    llm = ScriptLLM([reply("开始", [call("c1", "list_files")]), reply("不会到这里")], hooks={2: cancel.set})
    with pytest.raises(Cancelled):                                          # CEO 停止了整个公司
        go(company, llm, cancel=cancel)

    notes = iter(["", "CEO：记得写验收标准"])                                # 第 2 次检查（正要收尾时）收到
    llm = ScriptLLM([reply("初稿写完了"), reply("补上验收标准了")])
    out, events = go(company, llm, listen=lambda: next(notes, ""))          # 正要收尾时收到插话：先处理再结束
    assert out["text"] == "初稿写完了\n\n补上验收标准了" and any(e["type"] == "heard" for e in events)
    assert {"name": "heard", "ok": True} in out["tools"]

    llm = ScriptLLM([reply(f"第 {i} 步", [call(f"c{i}", "list_files")]) for i in range(3)])
    out, _ = go(company, llm, max_steps=3)
    assert "已达到 3 步上限" in out["text"] and len(llm.calls) == 3


def test_invalid_tool_arguments_are_not_sent_back_to_the_model(company):
    """真实模型试跑时遇到的：模型生成的工具参数不是合法 JSON，带着它的对话下一次调用会被服务商拒绝（400）。
    现在不执行这次调用，对话里换成合法的空参数，并告诉模型重新调用。"""
    import json
    from types import SimpleNamespace
    bad = SimpleNamespace(id="b1", function=SimpleNamespace(name="write_file", arguments='{"path": "docs/a.md", "content": "未结束'))
    llm = ScriptLLM([reply("写文件", [bad]), reply("重新写", [call("c1", "write_file", path="docs/a.md", content="# A")]),
                     reply("写好了")])
    out, events = go(company, llm)
    second = llm.calls[1]
    sent_call = next(m for m in second if m.get("tool_calls"))["tool_calls"][0]
    assert json.loads(sent_call["function"]["arguments"]) == {}           # 发给服务商的是合法 JSON
    assert "参数不是合法的 JSON" in second[-1]["content"] and second[-1]["role"] == "tool"
    assert company.read_file("docs/a.md") == "# A" and out["text"].endswith("写好了")
    assert {"name": "write_file", "ok": False} in out["tools"]


def test_truncated_reply_is_not_treated_as_finished(company):
    from types import SimpleNamespace
    cut = SimpleNamespace(**vars(reply("这是一份很长的说明，写到一半")), finish_reason="length")
    llm = ScriptLLM([cut, reply("接着写完了")])
    out, _ = go(company, llm)
    assert len(llm.calls) == 2 and "达到了输出上限被截断" in llm.calls[1][-1]["content"]
    assert out["text"].endswith("接着写完了")


def test_answer_last_forces_a_reply_on_the_final_step(company):
    """问同事时步数有限：最后一步不给工具，要求直接回答，不会以"达到步数上限"空手而归。"""
    llm = ScriptLLM([reply("先看看", [call("c1", "list_files")]), reply("答案是先到先得")])
    out, _ = go(company, llm, max_steps=2, readonly=True, answer_last=True)
    assert llm.tools[0] and llm.tools[1] is None and "最后一步" in llm.calls[1][-1]["content"]
    assert out["text"].endswith("答案是先到先得") and "步上限" not in out["text"]


def test_readonly_run_cannot_write(company):
    """审查和回答同事时只读：只提供查看的工具，想写文件也会被拒绝。"""
    llm = ScriptLLM([reply("顺手改一下", [call("c1", "write_file", path="docs/x.md", content="改了")]), reply("看完了")])
    out, _ = go(company, llm, readonly=True)
    assert set(llm.tools[0]) == {"list_files", "read_file", "kb_search"}  # 检索知识库也是只读的
    assert {"name": "write_file", "ok": False} in out["tools"] and not company.list_files()


def test_activity_events_show_what_the_step_is_doing(company):
    """进度：每一步推送在等模型、在写回复、在调用哪个工具，前端据此显示"第 N 步 · 正在做什么"。"""
    llm = ScriptLLM([reply("先写文件", [call("c1", "write_file", path="docs/PRD.md", content="# PRD")]), reply("写好了")])
    _, events = go(company, llm)
    acts = [(e["step"], e["phase"], e.get("tool")) for e in events if e["type"] == "activity"]
    assert acts[0] == (1, "waiting", None) and (1, "writing", None) in acts
    assert any(a[1] == "tool" and a[2] == "write_file" for a in acts) and (2, "waiting", None) in acts
    tool_ev = next(e for e in events if e["type"] == "activity" and e["phase"] == "tool")
    assert tool_ev.get("path") == "docs/PRD.md"


def test_must_submit_with_the_finish_tool(company):
    """审查人没提交结论就想结束：提醒它提交；到了最后一步只给提交工具（试跑 4 里审查人读完文档就结束了）。"""
    submitted = []
    submit = {"type": "function", "function": {"name": "submit_review", "description": "", "parameters": {"type": "object"}}}
    llm = ScriptLLM([reply("我看完了"), reply("看", [call("c1", "list_files")]), reply("再看", [call("c2", "list_files")]),
                     reply("提交", [call("c3", "submit_review")])])
    out, _ = go(company, llm, max_steps=4, readonly=True, extra_tools=[submit],
                handlers={"submit_review": lambda a: submitted.append(1) or "已提交"},
                finished=lambda: bool(submitted), require_finish=True, finish_tool="submit_review")
    assert "还没有调用 submit_review" in llm.calls[1][-1]["content"]
    assert llm.tools[3] == ["submit_review"] and "这是最后一步" in llm.calls[3][-1]["content"]
    assert submitted
