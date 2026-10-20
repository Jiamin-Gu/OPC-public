"""角色 agent：每个角色有自己的系统提示词、上下文和模型，通过工具读写公司共享文件。"""
import json
import os
import re
import threading
import time
from typing import Callable

from . import coding, prompts, web_tools
from .company import Company, FileError
from .roles import ROLES

# 一次 read_file 最多返回多少字符。DeepSeek V4 Flash 的上下文窗口是 100 万 token，20 万字（约 15 万 token）
# 足够一次读完正常的文档；更大的文件分段读。换成上下文小的模型时可以用 OPC_MAX_READ_CHARS 调小
MAX_READ_CHARS = int(os.getenv("OPC_MAX_READ_CHARS") or 200_000)

TOOLS = [
    {"type": "function", "function": {
        "name": "list_files", "description": "列出公司共享文件区的所有文件（路径、作者、大小）。",
        "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {
        "name": "read_file",
        "description": "读取共享文件区中的一个文件。正常大小的文件一次返回全部内容；特别大的文件会分段返回，"
                       "结果末尾会说明怎样用 start_line 继续读。",
        "parameters": {"type": "object", "properties": {
            "path": {"type": "string", "description": "文件路径，例如 docs/PRD.md"},
            "start_line": {"type": "integer", "description": "从第几行开始读（默认 1）。只在分段读大文件时需要"}},
            "required": ["path"]}}},
    {"type": "function", "function": {
        "name": "write_file", "description": "创建一个新文件，或者整体重写一个文件，写入完整内容。只改其中几处时用 edit_file。",
        "parameters": {"type": "object", "properties": {
            "path": {"type": "string", "description": "文件路径，文档放 docs/，代码放 code/"},
            "content": {"type": "string", "description": "完整的文件内容"}}, "required": ["path", "content"]}}},
    {"type": "function", "function": {
        "name": "edit_file",
        "description": "局部修改已有文件：在 edits 里列出要改的每一处（原文 → 新内容），其余内容保持不变。"
                       "同一个文件的所有修改放在一次调用里提交，按顺序生效。"
                       "old_string 必须和文件里的原文完全一致（包括空格、换行和标点），并且只出现一次；不唯一时多带几行上下文。"
                       "任何一处找不到或不唯一，这次的修改全部不生效，改正后重新提交全部修改。"
                       "要删除一段，new_string 留空；要在某处后面插入，把那一段原文作为 old_string，new_string 写成原文加新内容。",
        "parameters": {"type": "object", "properties": {
            "path": {"type": "string", "description": "文件路径"},
            "edits": {"type": "array", "description": "要做的修改，按顺序生效", "items": {
                "type": "object", "properties": {
                    "old_string": {"type": "string", "description": "要被替换的原文"},
                    "new_string": {"type": "string", "description": "替换后的新内容"},
                    "replace_all": {"type": "boolean", "description": "原文出现多次时全部替换（默认 false）"}},
                "required": ["old_string", "new_string"]}}},
            "required": ["path", "edits"]}}},
]


# 公司知识库（长期记忆）的检索工具：公司打开了知识库时给所有角色（只读，讨论和审查时也能用）
KB_TOOL = {"type": "function", "function": {
    "name": "kb_search",
    "description": "检索公司知识库（公司的长期记忆）：以前的文档和代码、任务记录（汇报、审查意见、CEO 裁决）、"
                   "每条指令的结论（CEO 的要求和插话、定下的口径、最终汇报），以及 CEO 提供的公司资料和个人资料。"
                   "返回最相关的片段和出处。当前任务的上游成果已经写在任务说明里，不用检索。",
    "parameters": {"type": "object", "properties": {
        "query": {"type": "string", "description": "要找什么：用一句话或几个关键词描述；查函数名、错误名、专有名词时直接写这个名字"},
        "scope": {"type": "string", "enum": ["all", "files", "records", "library"],
                  "description": "在哪里找：all 全部（默认）；files 共享文件区的文档和代码；records 任务记录和指令结论"
                                 "（以前做过什么、定过什么、CEO 怎么裁决的）；library CEO 提供的公司资料和个人资料"},
        "k": {"type": "integer", "description": "返回几条，默认 5，最多 10"}},
        "required": ["query"]}}}
KB_SCOPES = ("all", "files", "records", "library")


class Cancelled(Exception):
    """CEO 点了停止。"""


def uses_coder(role_id: str) -> bool:
    """会写代码的角色，在装了 Coding-agent 时用它来写代码、运行和测试。"""
    return ROLES[role_id].can_code and coding.AVAILABLE


def tools_for(role_id: str) -> list[dict]:
    return TOOLS + [coding.CODE_TOOL] if uses_coder(role_id) else TOOLS


def code_rule(company: Company, role_id: str) -> str:
    if not ROLES[role_id].can_code:
        return "写代码不是你的职责"
    if not uses_coder(role_id):
        return "需要写代码时用 write_file 写"
    run = ("它能运行命令和测试" if company.allow_run
           else "但这家公司没有允许运行代码，所以它只能编写和修改代码，不能运行")
    return ("写代码、改代码、运行和测试时，调用 code_agent 工具交给编码 agent（" + run + "），不要自己用 write_file 写代码。"
            "给它的任务说明里写清楚要实现什么、依据哪些文档、如何验证；"
            "拿到结果后检查验证是否通过，没通过就再次调用 code_agent 修复")


def web_rule(company: Company) -> str:
    tools = web_tools.status()["tools"] if company.web else []
    if not tools:  # 没勾选联网，或者 MCP 服务器没有提供工具
        return ""
    return (f"\n6. 你可以联网：{web_tools.rule_text(tools)}。只在确实需要时联网；"
            "文档里用到网上的信息要注明来源网址，"
            "不要编造数据或链接。")


def kb_rule(company: Company, number: int) -> str:
    if not company.kb:
        return ""
    return (f"\n{number}. 公司有知识库（kb_search）：需要以前的决定和口径、已有的文档或代码、CEO 提供的资料时先检索，"
            "不要凭印象写；当前任务的上游成果已经在任务说明里，不用再检索。用到检索结果时写明出处"
            "（例如「依据：公司资料《品牌规范.pdf》第 3 页」）；检索不到就说明没找到，不要编造。"
            "CEO 提供的公司资料和个人资料是 CEO 给定的依据，比同事写的文档更权威：两者冲突时以资料为准，并在汇报里指出；"
            "资料之间互相矛盾、拿不准时，列为需要 CEO 决定的事项。")


def system_prompt(company: Company, role_id: str) -> str:
    role = ROLES[role_id]
    roster = "\n".join(f"- {ROLES[r].icon} {ROLES[r].name}：{ROLES[r].tagline}" for r in company.role_ids)
    rule = code_rule(company, role_id)
    web = web_rule(company)
    return f"""你是「{company.meta['name']}」公司的{role.name}。
公司目标：{company.meta.get('mission') or '（CEO 还没有设定）'}
公司成员：
{roster}
你的上级是 CEO，也就是正在给你下达指令的用户。

# 你的职责
{prompts.role_prompt(role_id, company.prompt_level(role_id))}

# 工作方式
1. 公司所有文档和代码都在共享文件区。动手前先用 list_files 看看已有哪些文件，再用 read_file 阅读和任务相关的内容（尤其是同事的产出），保持一致，避免重复。
2. 产出要写成文件：公司的约定是文档用 Markdown 放在 docs/、代码放在 code/，文件名要有意义；具体交付哪些文件、用什么格式，以任务说明为准。{rule}。
3. 更新已有文件前先读取它。只改其中一部分时用 edit_file 改需要改的地方，同一个文件的所有修改放进一次调用的 edits 列表；不要用 write_file 把整份文件重写一遍（重写既慢，又容易把原本正确的内容改坏）。不要改动其他同事的文件，除非任务明确要求。
4. 内容要具体、可执行，不要空话套话。信息不足时做合理假设，并在文档里注明。
5. 不要声称做了你没有做的事。全部使用中文，包括工具调用之间的进度说明（例如"我先读一下需求文档"）。{web}{kb_rule(company, 7 if web else 6)}"""


def execute_tool(company: Company, role_id: str, name: str, raw_args: str) -> tuple[str, dict, bool]:
    """执行一次工具调用，返回 (给模型的结果, 给界面的摘要, 是否写了文件)。错误也作为结果返回给模型。"""
    try:
        args = json.loads(raw_args or "{}")
    except json.JSONDecodeError as e:
        return f"错误：参数不是合法的 JSON（{e}）", {"name": name, "ok": False}, False
    summary = {"name": name, "path": args.get("path", ""), "ok": True}
    try:
        if name == "list_files":
            files = company.list_files()
            return ("\n".join(f"{f['path']}（作者：{f.get('author', '?')}，{f['size']} 字节）" for f in files)
                    or "（共享文件区还是空的）"), summary, False
        if name == "read_file":
            text = company.read_file(args["path"])
            return read_segment(text, int(args.get("start_line") or 1)), summary, False
        if name == "write_file":
            created = company.write_file(args["path"], args.get("content", ""), author=role_id)
            return f"已{'创建' if created else '更新'} {args['path']}", summary, True
        if name == "edit_file":
            return edit_file(company, role_id, args), summary, True
        return f"错误：没有工具 {name}", {**summary, "ok": False}, False
    except (FileError, KeyError) as e:
        return f"错误：{e}", {**summary, "ok": False}, False


def edit_file(company: Company, role_id: str, args: dict) -> str:
    """按顺序做一组"原文 → 新内容"的替换，全部成功才写回（一处不对就都不改）。错误抛 FileError，由调用方告诉模型。

    也接受只改一处的旧写法（直接给 old_string / new_string）。
    """
    path = args["path"]
    edits = args.get("edits")
    if edits is None:
        edits = [{k: args[k] for k in ("old_string", "new_string", "replace_all") if k in args}]
    if not isinstance(edits, list) or not edits or not all(isinstance(e, dict) for e in edits):
        raise FileError("edits 需要是非空的列表，每一项包含 old_string 和 new_string")

    def apply(text: str):
        replaced = 0
        for i, e in enumerate(edits, 1):
            old, new = e.get("old_string") or "", e.get("new_string") or ""
            where = f"第 {i} 处修改：" if len(edits) > 1 else ""
            fail = "。这次的修改都没有生效，请改正后重新提交全部修改" if len(edits) > 1 else ""
            if not old:
                raise FileError(f"{where}old_string 不能为空；新建文件或整体重写请用 write_file{fail}")
            if old == new:
                raise FileError(f"{where}old_string 和 new_string 一样，没有需要修改的内容{fail}")
            count = text.count(old)
            if count == 0:
                raise FileError(f"{where}{path} 里没有找到 old_string。请先用 read_file 看当前内容，"
                                f"原文要逐字一致（包括空格、换行和标点）；前面的修改会先生效，原文要按改过之后的写{fail}")
            if count > 1 and not e.get("replace_all"):
                raise FileError(f"{where}old_string 在 {path} 里出现了 {count} 次，请多带几行上下文让它唯一，"
                                f"或者设置 replace_all=true{fail}")
            text = text.replace(old, new) if e.get("replace_all") else text.replace(old, new, 1)
            replaced += count if e.get("replace_all") else 1
        return text, replaced

    replaced = company.modify_file(path, apply, author=role_id)
    return f"已修改 {path}（{len(edits)} 处修改，共替换 {replaced} 处文字）"


def read_segment(text: str, start_line: int = 1, limit: int | None = None) -> str:
    """不超过上限时原样返回全文；超过时从 start_line 开始返回尽量多的整行，并说明怎样继续读。"""
    limit = limit or MAX_READ_CHARS
    if start_line <= 1 and len(text) <= limit:
        return text or "（空文件）"
    lines = text.splitlines(keepends=True)
    start = max(1, min(start_line, len(lines) or 1))
    chunk, used, end = [], 0, start - 1
    for line in lines[start - 1:]:
        if chunk and used + len(line) > limit:
            break
        chunk.append(line)
        used += len(line)
        end += 1
    note = f"\n…（文件共 {len(lines)} 行，这里是第 {start}～{end} 行"
    note += f"；继续读请用 start_line={end + 1}）" if end < len(lines) else "，已读到结尾）"
    return "".join(chunk) + note


# 实时显示哪些工具参数：写文件的完整内容、局部修改的新内容
LIVE_FIELDS = {"write_file": "content", "edit_file": "new_string"}
LIVE_INTERVAL = 0.3  # 最多每 0.3 秒推送一次，避免几万字的文档产生上万个事件


def partial_json_string(raw: str, key: str, complete_only: bool = False) -> str | None:
    """从还没生成完的 JSON 里取出某个字符串字段（第一次出现的）目前已经生成的部分（处理转义）。

    还没出现这个字段返回 None；complete_only 时只在字段已经生成完整时返回。
    """
    m = re.search(r'"' + re.escape(key) + r'"\s*:\s*"', raw)
    if not m:
        return None
    value, complete = _decode_from(raw, m.end())
    return value if complete or not complete_only else None


def partial_json_strings(raw: str, key: str) -> list[str]:
    """同一个字段出现多次时（例如批量修改的每个 new_string），按顺序取出每一个目前已经生成的部分。"""
    out = []
    for m in re.finditer(r'"' + re.escape(key) + r'"\s*:\s*"', raw):
        value, complete = _decode_from(raw, m.end())
        out.append(value)
        if not complete:
            break
    return out


def _decode_from(raw: str, i: int) -> tuple[str, bool]:
    """从 JSON 字符串值的开头（引号之后）解码，返回 (已解码的部分, 是否已经到了结尾的引号)。"""
    out, n = [], len(raw)
    escapes = {"n": "\n", "t": "\t", "r": "\r", "b": "\b", "f": "\f", '"': '"', "\\": "\\", "/": "/"}
    while i < n:
        ch = raw[i]
        if ch == '"':
            return "".join(out), True
        if ch != "\\":
            out.append(ch)
            i += 1
            continue
        if i + 1 >= n:  # 转义符刚好被截断，等下一段
            break
        nxt = raw[i + 1]
        if nxt == "u":
            if i + 6 > n:
                break
            try:
                out.append(chr(int(raw[i + 2:i + 6], 16)))
            except ValueError:
                pass
            i += 6
        else:
            out.append(escapes.get(nxt, nxt))
            i += 2
    return "".join(out), False


class LiveWriter:
    """把模型正在生成的写文件参数解析出来，增量推送给界面（type=writing）。"""

    def __init__(self, emit: Callable[[dict], None], base_event: dict):
        self.emit, self.base_event = emit, base_event
        self.calls: dict[int, dict] = {}

    def reset(self) -> None:
        self.calls = {}

    def feed(self, index: int, name: str, chunk: str) -> None:
        c = self.calls.setdefault(index, {"args": "", "sent": 0, "at": 0.0})
        c["name"] = name
        c["args"] += chunk
        if name in LIVE_FIELDS and time.monotonic() - c["at"] >= LIVE_INTERVAL:
            self._push(index, c)

    def flush(self) -> None:
        for index, c in self.calls.items():
            if c.get("name") in LIVE_FIELDS:
                self._push(index, c)

    def _push(self, index: int, c: dict) -> None:
        c["at"] = time.monotonic()
        if c["name"] == "edit_file":  # 批量修改：依次显示每一处的新内容
            parts = partial_json_strings(c["args"], "new_string")
            text = "\n……\n".join(parts) if parts else None
        else:
            text = partial_json_string(c["args"], LIVE_FIELDS[c["name"]])
        if text is None or len(text) <= c["sent"]:
            return
        path = partial_json_string(c["args"], "path", complete_only=True) or ""
        self.emit({"type": "writing", **self.base_event, "index": index, "tool": c["name"], "path": path,
                   "text": text[c["sent"]:], "chars": len(text)})
        c["sent"] = len(text)


def run_role(*args, **kwargs) -> dict:
    """运行一个角色直到它给出最终回复（或被暂停）。执行循环是一个小的 LangGraph 图，见 worker.py。

    参数：company, role_id, conversation, emit, msg_id, channel, cancel, max_steps,
    以及可选的 listen / extra_tools / handlers；暂停和恢复用 stop / saver / thread_id / resume_note。
    """
    from .worker import run
    return run(*args, **kwargs)


def call_kb(company: Company, raw_args: str) -> tuple[str, dict]:
    """执行 kb_search：返回 (给模型的结果, 给界面的摘要)。界面显示查了什么、找到几条、出处。"""
    from .knowledge import service
    try:
        args = json.loads(raw_args or "{}")
    except json.JSONDecodeError as e:
        return f"错误：参数不是合法的 JSON（{e}）", {"name": "kb_search", "ok": False}
    query = str(args.get("query") or "").strip()
    scope = args.get("scope") or "all"
    if not query:
        return "错误：需要提供 query（要找什么）", {"name": "kb_search", "ok": False}
    if scope not in KB_SCOPES:
        return f"错误：scope 只能是 {'、'.join(KB_SCOPES)}", {"name": "kb_search", "query": query, "ok": False}
    try:
        k = max(1, min(int(args.get("k") or 5), 10))
    except (TypeError, ValueError):
        k = 5
    try:
        results = service.search(company, query, scope, k)
    except Exception as e:  # 知识库出错不影响角色继续工作
        return f"错误：知识库检索失败（{type(e).__name__}: {e}）", {"name": "kb_search", "query": query, "ok": False}
    return service.format_results(results, query), {
        "name": "kb_search", "query": query, "scope": scope, "hits": len(results), "ok": True,
        "sources": [service.label(r) for r in results]}


def _call_web(hub, name: str, raw_args: str) -> tuple[str, dict]:
    try:
        args = json.loads(raw_args or "{}")
    except json.JSONDecodeError as e:
        return f"错误：参数不是合法的 JSON（{e}）", {"name": name, "ok": False}
    try:
        text, ok = hub.call(name, args)
    except web_tools.WebError as e:
        text, ok = f"错误：{e}", False
    return text, web_tools.summary(name, args, ok)


def _delegate_to_coder(company, role_id, raw_args, emit, base_event, cancel, tool_log, files, usage) -> str:
    """执行 code_agent 工具：运行一次编码 agent，把它的步骤、文件和用量并入当前角色。"""
    try:
        task = json.loads(raw_args or "{}").get("task", "").strip()
    except json.JSONDecodeError as e:
        return f"错误：参数不是合法的 JSON（{e}）"
    if not task:
        return "错误：需要提供 task（编码任务说明）"
    tool_log.append({"name": "code_agent", "ok": True, "phase": "start"})
    try:
        out = coding.run_code_agent(company, role_id, task, emit, base_event, cancel, company.allow_run)
    except coding.Stop:
        raise Cancelled()
    except Cancelled:
        raise
    except Exception as e:  # 编码 agent 调用失败（比如网络错误），把错误告诉工程师，让它决定怎么办
        return f"错误：编码 agent 运行失败（{type(e).__name__}: {e}）"
    tool_log.extend(out["steps"])
    files.extend(out["files"])
    for key in ("prompt", "completion", "cached", "calls"):
        usage[key] += out["usage"][key]
    return out["result"]
