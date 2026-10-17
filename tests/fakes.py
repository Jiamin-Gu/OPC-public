"""测试用的假模型：按角色返回固定的回复，不调用真实 API。"""
import json
import threading
import re
import time
from types import SimpleNamespace

from opc.company import CompanyStore

DEFAULT_PLAN = {
    "summary": "先写需求，再并行做技术方案和设计",
    "tasks": [
        {"key": "t1", "title": "写 PRD", "detail": "产出 docs/PRD.md", "assignee": "pm", "depends_on": []},
        {"key": "t2", "title": "技术方案", "detail": "产出 docs/tech.md", "assignee": "engineer", "depends_on": ["t1"]},
        {"key": "t3", "title": "界面设计", "detail": "产出 docs/design.md", "assignee": "designer", "depends_on": ["t1"]},
    ],
}


def call(id_, name, **args):
    return SimpleNamespace(id=id_, function=SimpleNamespace(name=name, arguments=json.dumps(args, ensure_ascii=False)))


def reply(content="", tool_calls=None, reasoning=None):
    return SimpleNamespace(content=content, reasoning_content=reasoning, tool_calls=tool_calls,
                           usage=SimpleNamespace(prompt=100, completion=20))


class FakeLLM:
    """角色：第一步写一个文件，第二步回复完成。规划：返回 plan。汇报：返回固定文字。"""

    def __init__(self, role_id, world):
        self.role_id, self.world = role_id, world

    def chat(self, messages, tools=None, on_delta=None, json_mode=False, on_tool_delta=None):
        w = self.world
        with w.lock:
            w.calls.append((self.role_id, [dict(m) for m in messages]))  # 存副本：调用方之后还会往列表里追加
            w.tools_seen.append((self.role_id, [t["function"]["name"] for t in tools or []]))
        system = messages[0]["content"]
        if self.role_id is None and "调度助手" in system:  # 理解 CEO 的话：默认不是改动
            with w.lock:
                t = w.triages.pop(0) if w.triages else {"change": False, "orders": [], "reply": "收到"}
            return reply(json.dumps(t, ensure_ascii=False))
        if self.role_id is None and "规划助手" in system:
            plan = w.plans.pop(0) if w.plans else DEFAULT_PLAN
            return reply(json.dumps(plan, ensure_ascii=False) if isinstance(plan, dict) else plan,
                         reasoning="想一想怎么分工")
        if self.role_id is None:
            if on_delta:
                on_delta("text", "汇报：")
            return reply("汇报：全部完成")
        if "压缩成" in system and "简洁版" in system:  # 生成简洁版：保留标题，正文截短
            with w.lock:
                if len(messages) == 2:  # 只记第一次（不合格重写的那次不算）
                    w.condensed.append(self.role_id)
            first = messages[1]["content"].splitlines()[0]
            return reply(f"> 这是简洁版\n{first}\n要点")
        if w.fail_role == self.role_id:
            raise RuntimeError("模拟 API 失败")
        if w.on_call:
            w.on_call(self.role_id, messages)
        names = {t["function"]["name"] for t in tools or []}
        if "submit_review" in names:  # 扮演审查人：先读要求读的文件，再提交（问题引用文件里的原文）
            return self._review(messages)
        if self.role_id in w.endless:  # 一直在做、不收尾（测试步数用完）
            return reply(f"{self.role_id} 还在做", [call(f"l{len(messages)}", "list_files")])
        start = time.perf_counter()
        time.sleep(w.delay)
        with w.lock:
            w.spans.append((self.role_id, start, time.perf_counter()))
        if on_delta:
            on_delta("reasoning", "思考")
            on_delta("text", f"{self.role_id} 在工作")
        revised = "（修改版）" if "这是返工" in messages[1]["content"] and self.role_id not in w.lazy else ""  # 返工后内容不同，用来检查改动对比
        with w.lock:
            scripted = w.first_calls.pop(self.role_id, None) if messages[-1]["role"] != "tool" else None
        if scripted:  # 测试指定的第一步（例如上报上游问题、问同事）
            return reply(f"{self.role_id} 先协作", scripted)
        if messages[-1]["role"] != "tool":  # 第一步：写文件
            if revised:  # 第二次及以后的返工加上编号：每次返工文件都确实改过
                with w.lock:
                    w.writes[self.role_id] = n = w.writes.get(self.role_id, 0) + 1
                revised = "（修改版）" if n == 1 else f"（修改版 {n}）"
            content = f"# {self.role_id} 的产出" + revised + w.padding.get(self.role_id, "")
            if revised:  # 返工只能局部修改（不能用 write_file 整篇重写）：把上一版的标题换成这一版的
                old = f"# {self.role_id} 的产出" + ("" if n == 1 else "（修改版）" if n == 2 else f"（修改版 {n - 1}）")
                return reply(f"{self.role_id} 开始改", [call("c1", "edit_file", path=f"docs/{self.role_id}.md",
                                                             edits=[{"old_string": old, "new_string": f"# {self.role_id} 的产出" + revised}])],
                             reasoning="只改审查指出的地方")
            return reply(f"{self.role_id} 开始", [call("c1", "write_file", path=f"docs/{self.role_id}.md", content=content)],
                         reasoning="先写文件")
        if "finish_task" not in names:  # 回答同事、私聊：直接回复
            return reply(f"{self.role_id} 完成了任务")
        said = "\n".join(m.get("content") or "" for m in messages if m["role"] == "user")
        args = {"summary": f"{self.role_id} 完成了任务"}
        if "这是返工" in said or "第 0 条" in said:  # 逐条回应（多回应几条无所谓）
            args["responses"] = [{"no": n, "action": "fixed", "detail": "已修改"} for n in range(0, 11)]
        if "上游有改动" in said:
            args["upstream"] = "对照上游的改动检查过了"
        return reply(f"{self.role_id} 提交", [call("f1", "finish_task", **args)])

    def _review(self, messages):
        w = self.world
        brief = messages[1]["content"]
        if messages[-1]["role"] == "tool" and messages[-1]["content"].startswith("错误：审查结论"):
            return reply(f"{self.role_id} 审查完毕")  # 被退回就不再提交（测试里不应该发生）
        m = re.search(r"提交前必须用 read_file 读过要审查的文件：(.+)", brief)
        need = m.group(1).split("、") if m else []
        read = {json.loads(c["function"]["arguments"]).get("path") for x in messages if x.get("tool_calls")
                for c in x["tool_calls"] if c["function"]["name"] == "read_file"}
        unread = [f for f in need if f not in read]
        if unread:
            return reply("先读产出", [call(f"rd{i}", "read_file", path=f) for i, f in enumerate(unread)])
        with w.lock:
            queue = w.verdicts
            if isinstance(queue, dict):  # 按任务指定：审查的是哪个任务（审查说明第一行写着）
                t = re.search(r"请你审查.*?的任务《(.+?)》", brief)
                queue = queue.get(t.group(1), []) if t else []
            item = queue.pop(0) if queue else ("pass", [])
            verdict, issues = item[0], item[1]
            resolved = len(item) > 2 and item[2] == "resolved"  # 上一轮的问题都解决了，但还有新问题
        files = re.search(r"产出文件：(.+)", brief).group(1).split("、")
        quote = f"{files[0].split('/')[-1].rsplit('.', 1)[0]} 的产出"  # 假模型写的文件第一行是 "# <角色> 的产出"
        prev = len(re.findall(r"【上轮问题 \d+】", brief))
        return reply("我看过了", [call("r1", "submit_review", verdict=verdict, summary=f"{self.role_id} 的审查结论",
                                         issues=[{"where": files[0], "quote": quote, "problem": i} for i in issues],
                                         previous=[{"no": n, "status": "unresolved" if verdict == "changes" and not resolved
                                                    else "resolved"}
                                                   for n in range(1, prev + 1)])])


class World:
    def __init__(self, delay=0.0):
        self.lock = threading.Lock()
        self.calls, self.spans, self.plans = [], [], []
        self.delay = delay
        self.fail_role = None
        # 审查人依次给出的结论；也可以按任务指定：{"任务标题": [(结论, 问题), ...]}，没指定的任务一律通过
        self.verdicts: list | dict = []
        self.on_call = None  # 每次角色调用模型时的回调（测试里用来模拟 CEO 中途插话）
        self.tools_seen: list[tuple] = []   # 每次调用时角色能用的工具
        self.first_calls: dict[str, list] = {}  # 角色 -> 第一步调用的工具（只用一次）
        self.endless: set[str] = set()  # 这些角色执行任务时一直调用工具、不给最终回复
        self.writes: dict[str, int] = {}  # 每个角色返工了几次（每次写的内容不同）
        self.lazy: set[str] = set()  # 这些角色返工时不改文件（但回应说已修改）
        self.condensed: list = []  # 生成过简洁版的角色
        self.padding: dict[str, str] = {}  # 角色 -> 写文件时追加的正文（测试长文档）
        self.triages: list[dict] = []  # 调度助手依次给出的判断（{"change", "orders": [{"task": "T1", ...}], "reply"}）

    def factory(self, cfg, role_id):
        return FakeLLM(role_id, self)


def make_store(tmp_path, world):
    return CompanyStore(tmp_path / "home", llm_factory=world.factory)
