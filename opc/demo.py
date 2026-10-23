"""演示模式的假模型：不调用 API，按角色生成示例内容，用来体验界面和完整流程。"""
import json
import re
import time
from types import SimpleNamespace

from .roles import ROLES


def _call(id_, name, **args):
    return SimpleNamespace(id=id_, function=SimpleNamespace(name=name, arguments=json.dumps(args, ensure_ascii=False)))


class DemoLLM:
    def __init__(self, role_id, delay=0.015):
        self.role_id = role_id
        self.delay = delay

    def _stream(self, kind, text, on_delta):
        if not on_delta:
            return
        for i in range(0, len(text), 4):
            on_delta(kind, text[i:i + 4])
            time.sleep(self.delay)

    def _reply(self, content="", reasoning=None, tool_calls=None, on_delta=None, stream_text=True, on_tool_delta=None):
        if reasoning:
            self._stream("reasoning", reasoning, on_delta)
        if content and stream_text:
            self._stream("text", content, on_delta)
        for i, call in enumerate(tool_calls or []):  # 工具参数也一段段生成，界面上能看到正在写的文档
            if on_tool_delta:
                args = call.function.arguments
                for k in range(0, len(args), 6):
                    on_tool_delta(i, call.function.name, args[k:k + 6])
                    time.sleep(self.delay)
        return SimpleNamespace(content=content, reasoning_content=reasoning, tool_calls=tool_calls,
                               usage=SimpleNamespace(prompt=900, completion=180))

    def chat(self, messages, tools=None, on_delta=None, json_mode=False, on_tool_delta=None):
        system = messages[0]["content"]
        if self.role_id is None and "调度助手" in system:  # 演示：话里有"改 / 返工 / 不对"就当作让做完的第一个任务返工
            said = messages[-1]["content"].rsplit("CEO 刚说：", 1)[-1]
            done = re.findall(r"^- (T\d+) [^：]*：已完成", system, re.M)
            if done and any(w in said for w in ("改", "返工", "不对", "重做")):
                data = {"change": True, "orders": [{"task": done[0], "kind": "rework", "feedback": said}],
                        "reply": f"明白，安排 {done[0]} 返工，依赖它的任务先暂停，请确认。"}
            else:
                data = {"change": False, "orders": [], "reply": "演示模式：目前任务都在按计划推进，有需要调整的地方随时告诉我。"}
            return self._reply(json.dumps(data, ensure_ascii=False), "演示模式：理解 CEO 的话。", on_delta=on_delta,
                               stream_text=False)
        if self.role_id is None and "规划助手" in system:
            # 只认公司成员：规划提示词里还有"- small（小任务）"这样同样格式的行，成员少于 4 个时会被当成角色
            roles = [r for r in re.findall(r"^- ([\w-]+)（", system, re.M) if r in ROLES]
            first, rest = roles[0], roles[1:4]
            directive = (re.search(r"CEO 的指令：(.*)", messages[-1]["content"]) or ["", ""])[1].strip()
            if directive.startswith(("搜", "查", "问")) or directive.endswith(("？", "?")):  # 演示：提问类的指令按小任务安排
                plan = {"size": "small", "summary": f"小任务，交给{ROLES[first].name}直接做。", "tasks": [
                    {"key": "t1", "title": f"{ROLES[first].name}处理", "detail": directive, "assignee": first,
                     **_demo_requirements(first)}]}
                return self._reply(json.dumps(plan, ensure_ascii=False), "演示模式：提问类的指令，按小任务安排。",
                                   on_delta=on_delta, stream_text=False)
            tasks = [{"key": "t1", "title": f"{ROLES[first].name}先行", "detail": f"产出 {ROLES[first].default_file}",
                      "assignee": first, "depends_on": [], **_demo_requirements(first)}]
            tasks += [{"key": f"t{i + 2}", "title": f"{ROLES[r].name}跟进", "detail": f"在前面成果的基础上产出 {ROLES[r].default_file}",
                       "assignee": r, "depends_on": ["t1"], "reviewer": first if i == 0 else "", **_demo_requirements(r)}
                      for i, r in enumerate(rest)]
            plan = {"summary": f"先由{ROLES[first].name}打基础，其余 {len(rest)} 位同事并行跟进。", "tasks": tasks}
            return self._reply(json.dumps(plan, ensure_ascii=False), "演示模式：按公司成员生成一份示例计划。",
                               on_delta=on_delta, stream_text=False)
        if self.role_id is None:
            return self._reply("**结论**：演示计划已全部完成。\n\n- 各角色的产出都在右侧「文件」里\n- 这是演示模式，内容是示例\n\n"
                               "**下一步**：配置真实的 API Key 后，用 `python -m opc.web` 启动。", on_delta=on_delta)
        role = ROLES[self.role_id]
        names = {t["function"]["name"] for t in tools or []}
        if "submit_review" in names:  # 演示审查：先读文件；第一轮退回一个问题（引用原文），复审确认已解决后通过
            brief = messages[1]["content"]
            must = re.search(r"提交前必须用 read_file 读过要审查的文件：(.+)", brief)
            if must and messages[-1]["role"] != "tool":
                return self._reply("我先读一下产出文件。", "演示模式：系统要求先读完要审查的文件才能提交。",
                                   [_call(f"rd{i}", "read_file", path=f) for i, f in enumerate(must.group(1).split("、"))],
                                   on_delta=on_delta)
            if "这是复审" not in brief:
                args = {"verdict": "changes", "summary": "整体可以，但还不能交付",
                        "issues": [{"where": "要点", "quote": "这是演示模式生成的示例内容",
                                    "problem": "要点没有写清楚怎样算完成，审查人没法逐条核对", "expected": "补上验收标准"}],
                        "suggestions": ["背景部分可以再精简一些"]}
            else:
                args = {"verdict": "pass", "summary": "验收标准补上了，可以交付",
                        "previous": [{"no": 1, "status": "resolved", "note": "已补上验收标准"}]}
            return self._reply("对照任务要求和验收标准检查完了。", "演示模式：第一轮提一个问题，复审只看上轮问题和改动。",
                               [_call("r1", "submit_review", **args)], on_delta=on_delta)
        if messages[-1]["role"] != "tool" and "这是返工" in messages[1]["content"]:  # 演示返工：只改有问题的地方
            return self._reply("按审查意见补上验收标准，其余内容不动。", "审查只提了验收标准，用 edit_file 局部修改。",
                               [_call("c2", "edit_file", path=role.default_file, edits=[
                                   {"old_string": DEMO_ANCHOR,
                                    "new_string": DEMO_ANCHOR + "\n## 验收标准（返工时补充）\n\n"
                                                  "- 每个要点都写明怎样算完成\n- 审查人可以逐条核对\n"},
                                   {"old_string": "演示模式不调用模型", "new_string": "演示模式不调用模型（返工时只改了这两处）"}])],
                               on_delta=on_delta, on_tool_delta=on_tool_delta)
        if messages[-1]["role"] != "tool":
            return self._reply(f"收到，我来负责这部分，先把产出写到 {role.default_file}。", "看一下任务要求，然后写文档。",
                               [_call("c1", "write_file", path=role.default_file, content=demo_doc(role))],
                               on_delta=on_delta, on_tool_delta=on_tool_delta)
        if "finish_task" not in names:
            return self._reply(f"已完成，产出文件：`{role.default_file}`。演示模式下内容是示例。", on_delta=on_delta)
        args = {"headline": f"{role.name}的产出写好了（演示内容）", "summary": f"已完成，产出文件：`{role.default_file}`。演示模式下内容是示例。",
                "decisions": ["演示：这里会列出需要你决定的事"] if role.id == "pm" else []}
        if "这是返工" in messages[1]["content"]:
            args["responses"] = [{"no": 1, "action": "fixed", "detail": "在要点后面补上了验收标准，其余内容没动"}]
        return self._reply("做完了，提交交付。", "交付时系统会按交付要求自动检查。",
                           [_call("f1", "finish_task", **args)], on_delta=on_delta)


DEMO_ANCHOR = "- 真实使用时，内容会根据 CEO 的指令和同事的产出来写\n"


def _demo_requirements(role_id: str) -> dict:
    """演示计划的交付要求和验收标准：提交时系统会按它们机械检查。"""
    return {"deliverables": [{"path": ROLES[role_id].default_file, "sections": ["要点", "背景"], "min_chars": 100}],
            "acceptance": ["每个要点都写清楚怎样算完成", "内容和前面同事的产出一致"]}


def demo_doc(role) -> str:
    return (f"# {role.name}的产出（演示）\n\n## 要点\n\n- 这是演示模式生成的示例内容\n{DEMO_ANCHOR}\n"
            f"## 背景\n\n演示模式不调用模型，用固定的内容走一遍完整流程：规划、审批、并行执行、审查、返工、汇报。"
            f"写文件时内容会一段段出现，和真实模型生成长文档时一样。\n\n"
            f"## 说明\n\n| 项目 | 说明 |\n|---|---|\n| 职责 | {role.tagline} |\n| 文件 | {role.default_file} |\n")


def demo_factory(cfg, role_id):
    return DemoLLM(role_id)
