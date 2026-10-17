"""机械检查（传统工作流的验收）：不调用模型，同样的输入永远得到同样的结果。

- 交付检查：产出文件存在、不为空、有规定的章节、写到了规定的内容、达到最低字数；格式正确
- 返工核对：每条审查意见都有回应；说"已修改"的，文件确实改过
- 审查核对：审查人引用的原文确实在文件里；复审时新问题是不是出在这次改动的部分

检查项来自任务的交付要求（规划时生成，CEO 审批时可见，任务说明里原样告诉负责人，见 task_formats.py）。
模型只负责规则判断不了的事（逻辑、矛盾、场景推演），而且它的结论也要先过这里的核对。
"""
import json
import re

from .company import Company, FileError

_HEADING = re.compile(r"^\s{0,3}#{1,6}\s+(.+?)\s*#*\s*$")
_FENCE = re.compile(r"^\s{0,3}(```|~~~)")
# 比较文字时忽略空白、Markdown 符号和标点：引用原文时少个空格、全角半角标点不同都不算不一致
_NOISE = re.compile(r"[\s*_`#>|\-—–:：,，.。;；、!！?？\"'“”‘’()（）\[\]【】《》<>~=+/\\]+")


def norm(text: str) -> str:
    return _NOISE.sub("", text or "").lower()


def headings(text: str) -> list[str]:
    """Markdown 标题（代码块里的不算）。"""
    out, fence = [], False
    for line in (text or "").splitlines():
        if _FENCE.match(line):
            fence = not fence
            continue
        m = None if fence else _HEADING.match(line)
        if m:
            out.append(m.group(1))
    return out


def format_problems(path: str, text: str) -> list[str]:
    """按文件类型检查格式。"""
    ext = path.rsplit(".", 1)[-1].lower() if "." in path else ""
    if ext in ("md", "markdown"):
        fences = sum(1 for line in text.splitlines() if _FENCE.match(line))
        if fences % 2:
            return [f"{path}：有代码块（```）没有闭合"]
    elif ext == "json":
        try:
            json.loads(text)
        except json.JSONDecodeError as e:
            return [f"{path}：不是合法的 JSON（第 {e.lineno} 行：{e.msg}）"]
    elif ext in ("yaml", "yml"):
        try:
            import yaml
        except ImportError:
            return []
        try:
            yaml.safe_load(text)
        except yaml.YAMLError as e:
            return [f"{path}：不是合法的 YAML（{str(e).splitlines()[0]}）"]
    elif ext == "py":
        try:
            compile(text, path, "exec")
        except SyntaxError as e:
            return [f"{path}：Python 语法错误（第 {e.lineno} 行：{e.msg}）"]
    return []


def _read(company: Company, path: str) -> str | None:
    try:
        return company.read_file(path)
    except FileError:
        return None


def deliverable_problems(company: Company, deliverables: list[dict]) -> list[str]:
    """按任务的交付要求检查每个产出文件。"""
    problems = []
    for d in deliverables or []:
        path = d["path"]
        text = _read(company, path)
        if text is None:
            problems.append(f"没有产出文件 {path}（路径要和交付要求一致）")
            continue
        if not text.strip():
            problems.append(f"{path} 是空的")
            continue
        found = [norm(h) for h in headings(text)]
        missing = [s for s in d.get("sections") or [] if norm(s) and not any(norm(s) in h for h in found)]
        if missing:
            problems.append(f"{path} 缺少章节：{'、'.join(missing)}（要有标题里包含这些词的章节，用 # 标题写出）")
        body = norm(text)
        absent = [m for m in d.get("must_mention") or [] if norm(m) and norm(m) not in body]
        if absent:
            problems.append(f"{path} 没有写到：{'、'.join(absent)}")
        size = len(text.strip())
        if d.get("min_chars") and size < d["min_chars"]:
            problems.append(f"{path} 只有 {size} 字，要求至少 {d['min_chars']} 字")
    return problems


def files_format_problems(company: Company, paths) -> list[str]:
    problems = []
    for path in sorted(set(paths)):
        text = _read(company, path)
        if text is not None:
            problems += format_problems(path, text)
    return problems


def rework_problems(issue_count: int, ceo_note: bool, responses, changed: bool) -> list[str]:
    """返工核对：审查意见编号 1..issue_count（有 CEO 的意见时它是第 0 条），每条都要回应；
    回应"已修改"（fixed）的，文件必须真的改过；"不同意"（disagree）要写理由。"""
    expected = ([0] if ceo_note else []) + list(range(1, issue_count + 1))
    got: dict[int, dict] = {}
    for r in responses or []:
        if not isinstance(r, dict):
            continue
        try:
            got[int(r.get("no"))] = r
        except (TypeError, ValueError):
            continue
    name = lambda n: "CEO 的意见（第 0 条）" if n == 0 else f"第 {n} 条"  # noqa: E731
    problems = []
    missing = [n for n in expected if n not in got]
    if missing:
        problems.append("responses 里没有回应：" + "、".join(name(n) for n in missing))
    bad = [n for n in expected if n in got and got[n].get("action") not in ("fixed", "disagree")]
    if bad:
        problems.append("action 只能是 fixed（已修改）或 disagree（不同意）：" + "、".join(name(n) for n in bad))
    silent = [n for n in expected if n in got and got[n].get("action") == "disagree"
              and not str(got[n].get("detail") or "").strip()]
    if silent:
        problems.append("不同意要写明理由：" + "、".join(name(n) for n in silent))
    fixed = [n for n in expected if n in got and got[n].get("action") == "fixed"]
    if fixed and not changed:
        problems.append("回应说已修改，但文件和返工前一字没变：请真的修改文件；确实不用改的，改成 disagree 并说明理由")
    return problems


def quote_found(quote: str, texts: list[str]) -> bool:
    """引用的原文是否真的在这些文字里（忽略空白和标点；可以用"……"省略中间的部分，每一段都要能找到，而且按顺序）。"""
    parts = [norm(p) for p in re.split(r"…+|\.{3,}", quote or "")]
    parts = [p for p in parts if p]
    if not parts or sum(len(p) for p in parts) < 4:
        return False
    for text in texts:
        t, pos = norm(text), 0
        for part in parts:
            i = t.find(part, pos)
            if i < 0:
                break
            pos = i + len(part)
        else:
            return True
    return False


def added_text(diff: str) -> str:
    """改动对比里新加的内容（+ 开头的行）。"""
    return "\n".join(line[1:] for line in (diff or "").splitlines() if line.startswith("+") and not line.startswith("+++"))
