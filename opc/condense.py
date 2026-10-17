"""文档超过建议篇幅时，另外生成一份简洁版：完整版原样保留，两个版本都交付。

篇幅上限只是任务说明里的建议，不做硬性检查：真的写超了，可能是任务确实需要这么多内容，退回去硬删反而容易删掉
有用的东西、带出新问题。所以超了不退回，而是把完整版交给模型压缩出一份简洁版（<名字>.简洁版.md），给 CEO 和同事
快速阅读；完整版仍然是正式产出，审查和下游都以它为准。之后完整版再改，简洁版跟着重新生成，两份不会对不上。
"""
import re
from pathlib import PurePosixPath

from . import checks
from .agents import Cancelled
from .company import Company

SUFFIX = ".简洁版"
SLACK = 1.25     # 简洁版允许比目标字数多这么多（压缩时很难卡得刚好）
RATIO = 0.6      # 目标字数最多是完整版的这么多
MIN_SAVING = 0.3  # 至少比完整版短这么多才算简洁版，否则不保存（试跑里"简洁版"2.05 万字、完整版 2.3 万字，没有意义）

PROMPT = """你负责把同事写的一份 {size} 字的文档压缩成 {target} 字以内的简洁版，给 CEO 和同事快速了解要点。
完整版会原样保留，细节以完整版为准，所以简洁版只需要讲清楚"结论是什么、关键规则是什么"。

怎么压缩：
1. 每个章节只留 3～6 条要点，每条一两句话；保留核心结论、关键规则、关键数字和需要确认的事项
2. 表格不要照搬：只保留最关键的几行，或者改写成一两句话
3. 删掉背景铺陈、举例、长清单、重复的内容和套话；需要细节的地方写"详见完整版"
4. 保留原来的章节标题（文字不变），很短的小节合并进上一级
5. 不新增原文没有的内容，不改变原文的意思
6. 只输出简洁版的 Markdown 正文，总长不超过 {target} 字。第一行写：> 这是《{name}》的简洁版，内容以完整版 `{path}` 为准"""


def concise_path(path: str) -> str:
    p = PurePosixPath(path)
    return str(p.with_name(p.stem + SUFFIX + p.suffix))


def is_concise(path: str) -> bool:
    return PurePosixPath(path).stem.endswith(SUFFIX)


def _read(company: Company, path: str) -> str | None:
    try:
        return company.read_file(path)
    except Exception:
        return None


def needed(company: Company, deliverables: list[dict]) -> list[tuple[dict, str]]:
    """需要（重新）生成简洁版的产出：超过了建议篇幅，或者以前生成过简洁版（完整版改过，简洁版要跟着更新）。"""
    out = []
    for d in deliverables or []:
        if not d.get("max_chars") or is_concise(d["path"]):
            continue
        text = _read(company, d["path"])
        if text and (len(text.strip()) > d["max_chars"] or _read(company, concise_path(d["path"])) is not None):
            out.append((d, text))
    return out


def target(d: dict, size: int) -> int:
    return max(500, min(d["max_chars"], int(size * RATIO)))


def problems(text: str, d: dict, size: int) -> list[str]:
    out = []
    n, goal = len(text.strip()), target(d, size)
    if n > goal * SLACK or n > size * (1 - MIN_SAVING):
        out.append(f"有 {n} 字，要压到 {goal} 字以内（完整版 {size} 字）：每节只留几条要点，表格和举例删掉或改写成一句话")
    found = [checks.norm(h) for h in checks.headings(text)]
    missing = [s for s in d.get("sections") or [] if checks.norm(s) and not any(checks.norm(s) in h for h in found)]
    if missing:
        out.append(f"缺少这些章节（标题要保留）：{'、'.join(missing)}")
    return out


def condense(company: Company, role_id: str, d: dict, text: str, cancel=None) -> tuple[str | None, dict, str]:
    """生成简洁版并写进文件区，返回 (简洁版路径, 用量, 说明)。用这个角色自己的模型；不合格的告诉它问题重写一次。
    还是没压下来（没比完整版短出一截）就不保存，以前的简洁版也删掉（不然和改过的完整版对不上），路径返回 None。
    简洁版只是方便阅读，完整版才是正式产出，没有简洁版不影响交付。"""
    llm = company.llm_for(role_id)
    name, size = PurePosixPath(d["path"]).name, len(text.strip())
    messages = [{"role": "system", "content": PROMPT.format(size=size, target=target(d, size), name=name, path=d["path"])},
                {"role": "user", "content": text}]
    usage = {"prompt": 0, "completion": 0, "cached": 0, "calls": 0}

    def on_delta(kind: str, chunk: str) -> None:
        if cancel is not None and cancel.is_set():
            raise Cancelled()

    out = ""
    for _ in range(2):
        reply = llm.chat(messages, on_delta=on_delta)
        usage["calls"] += 1
        if reply.usage:
            usage["prompt"] += reply.usage.prompt
            usage["completion"] += reply.usage.completion
            usage["cached"] += getattr(reply.usage, "cached", 0)
        out = re.sub(r"^```(?:markdown)?\n|\n```$", "", (reply.content or "").strip()).strip()
        bad = problems(out, d, size)
        if not bad:
            break
        messages += [{"role": "assistant", "content": out},
                     {"role": "user", "content": "简洁版不符合要求：" + "；".join(bad) + "。请重新输出完整的简洁版。"}]
    path = concise_path(d["path"])
    n = len(out.strip())
    if n > size * (1 - MIN_SAVING):
        company.remove_file(path)
        return None, usage, f"简洁版没压下来（{n} 字，完整版 {size} 字），没有保存，以完整版为准"
    company.write_file(path, out + "\n", author=role_id)
    return path, usage, f"简洁版 {n} 字（完整版 {size} 字）"
