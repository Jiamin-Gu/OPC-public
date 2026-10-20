"""把文件切成检索用的小块。每块记住来源文件和行号，检索结果能对回原文。

- Markdown：按一到三级标题切，标题路径（"## 2. 报表 / ### 2.1 月报"）写进块的标题，保留上下文
- Python：用 ast 按顶层函数、类切；开头的文档字符串和 import 单独成块
- 其他文本：按空行分段，再把相邻段落合并到合适的长度
太长的块按行再切开，太短的相邻块合并，避免检索时一块里信息太少或太杂。
"""
import ast
import re

MAX_CHARS = 1500   # 单块最长字符数，超过就按行切开
MIN_CHARS = 200    # 比这短的相邻块（同一个文件里）合并
HEADING = re.compile(r"^(#{1,3})\s+(.+?)\s*#*\s*$")
TEXT_SUFFIXES = {".md", ".markdown", ".txt", ".rst", ".py", ".json", ".yaml", ".yml", ".toml", ".ini", ".cfg",
                 ".csv", ".html", ".css", ".js", ".ts", ".sql", ".sh", ".bat"}


def chunk_file(path: str, text: str) -> list[dict]:
    """按文件类型切块。返回 [{path, start, end, kind, title, text}]，行号从 1 开始、含两端。"""
    lower = path.lower()
    if lower.endswith((".md", ".markdown")):
        chunks = _markdown(text)
    elif lower.endswith(".py"):
        chunks = _python(text)
    else:
        chunks = _paragraphs(text)
    out = []
    for c in _merge_small(_split_long(chunks)):
        body = c["text"].strip("\n")
        if body.strip():
            out.append({"path": path, **c, "text": body})
    return out


def _block(lines: list[str], start: int, end: int, kind: str, title: str) -> dict:
    return {"start": start, "end": end, "kind": kind, "title": title, "text": "\n".join(lines[start - 1:end])}


def _markdown(text: str) -> list[dict]:
    lines = text.splitlines()
    chunks, stack, start, in_code = [], [], 1, False
    title = ""
    for no, line in enumerate(lines, 1):
        if line.lstrip().startswith("```"):
            in_code = not in_code
        m = None if in_code else HEADING.match(line)
        if m:
            if no > start:
                chunks.append(_block(lines, start, no - 1, "markdown", title))
            level = len(m.group(1))
            stack = [h for h in stack if h[0] < level] + [(level, m.group(2))]
            title = " / ".join(h[1] for h in stack)
            start = no
    if lines and start <= len(lines):
        chunks.append(_block(lines, start, len(lines), "markdown", title))
    return chunks


def _python(text: str) -> list[dict]:
    lines = text.splitlines()
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return _paragraphs(text)
    chunks, cursor = [], 1
    for node in tree.body:
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            continue
        start = min([d.lineno for d in node.decorator_list] + [node.lineno])
        if start > cursor:  # 两个定义之间的内容（文档字符串、import、常量）
            chunks.append(_block(lines, cursor, start - 1, "code", "模块开头" if cursor == 1 else "模块级代码"))
        kind = "class" if isinstance(node, ast.ClassDef) else "function"
        chunks.append(_block(lines, start, node.end_lineno, kind, f"{kind} {node.name}"))
        cursor = node.end_lineno + 1
    if cursor <= len(lines):
        chunks.append(_block(lines, cursor, len(lines), "code", "模块开头" if cursor == 1 else "模块级代码"))
    return chunks


def _paragraphs(text: str) -> list[dict]:
    lines = text.splitlines()
    chunks, start = [], None
    for no, line in enumerate(lines + [""], 1):
        if line.strip() and start is None:
            start = no
        elif not line.strip() and start is not None:
            chunks.append(_block(lines, start, no - 1, "text", ""))
            start = None
    return chunks


def _split_long(chunks: list[dict]) -> list[dict]:
    out = []
    for c in chunks:
        if len(c["text"]) <= MAX_CHARS:
            out.append(c)
            continue
        lines = c["text"].split("\n")
        piece, piece_start, size = [], c["start"], 0
        for i, line in enumerate(lines):
            if piece and size + len(line) > MAX_CHARS:
                out.append({**c, "start": piece_start, "end": piece_start + len(piece) - 1, "text": "\n".join(piece)})
                piece_start, piece, size = c["start"] + i, [], 0
            piece.append(line)
            size += len(line) + 1
        if piece:
            out.append({**c, "start": piece_start, "end": piece_start + len(piece) - 1, "text": "\n".join(piece)})
    return out


def _merge_small(chunks: list[dict]) -> list[dict]:
    out: list[dict] = []
    for c in chunks:
        prev = out[-1] if out else None
        # 相邻的小块合并（中间隔着空行也算相邻），避免配置文件、短段落被切成只有一两行的碎块
        if prev and len(prev["text"]) < MIN_CHARS and len(prev["text"]) + len(c["text"]) <= MAX_CHARS:
            gap = "\n" * max(c["start"] - prev["end"], 1)
            main = c if len(c["text"]) >= len(prev["text"]) else prev  # 类型取内容多的那一块
            # 标题两块的都要：只留一个的话，引用出处会指错（答案在"优惠券"一节，出处却写成"金额与计价"）
            out[-1] = {**prev, "end": c["end"], "text": prev["text"] + gap + c["text"],
                       "title": merge_titles(prev["title"], c["title"]), "kind": main["kind"]}
        else:
            out.append(c)
    return out


PAGE = re.compile(r"^第 (\d+)(?:-(\d+))? 页$")


def merge_titles(a: str, b: str) -> str:
    """合并两块的标题：共同的上级标题只写一次，后面的部分并列（"规范 / 一、金额；二、优惠券"）；
    PDF 的页码合并成范围（"第 2-3 页"）。"""
    if not a or not b or a == b:
        return a or b
    if b.startswith(a + " / "):  # 上级标题的开头一段和第一节合在一起：标题就是那一节的路径
        return b
    if a.startswith(b + " / "):
        return a
    pa, pb = a.split(" / "), b.split(" / ")
    k = 0
    while k < min(len(pa), len(pb)) - 1 and pa[k] == pb[k]:
        k += 1
    ta, tb = " / ".join(pa[k:]), " / ".join(pb[k:])
    ma, mb = PAGE.match(ta), PAGE.match(tb)
    if ma and mb:
        tail = f"第 {ma.group(1)}-{mb.group(2) or mb.group(1)} 页"
    else:
        tail = ta if ta.endswith(tb) else f"{ta}；{tb}"
    return " / ".join(pa[:k] + [tail])
