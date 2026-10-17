"""把一家公司的内容同步进它的知识库：公司文件区的文档和代码，以及每个任务的说明、汇报和审查记录。

知识库存在公司目录下的 kb/ 里。这里只读取公司数据，不修改公司、图或任何角色的状态。
"""
from ..roles import ROLES
from .base import KnowledgeBase

VERDICT = {"pass": "通过", "changes": "要求修改", "unclear": "没有给出结论", "error": "审查失败"}


def open_kb(company, embedder=None, **kw) -> KnowledgeBase:
    kwargs = {} if embedder is None else {"embedder": embedder}
    return KnowledgeBase(company.root / "kb", **kwargs, **kw)


def task_documents(state: dict) -> dict[str, tuple[str, str]]:
    """把 LangGraph 状态里的任务整理成 {source: (标题, Markdown)}：谁做的、要求、汇报、审查意见、CEO 裁决。"""
    directives = {m.get("directive_id"): m["text"] for m in state.get("channels", {}).get("all", [])
                  if m.get("sender") == "ceo" and m.get("directive_id")}
    out = {}
    for tid, t in (state.get("tasks") or {}).items():
        if t.get("status") == "cancelled" and not t.get("result"):
            continue
        who = ROLES[t["assignee"]].name if t["assignee"] in ROLES else t["assignee"]
        lines = [f"# 任务：{t['title']}", "", f"- 负责人：{who}", f"- 状态：{t['status']}"]
        if directives.get(t.get("directive_id")):
            lines.append(f"- CEO 指令：{directives[t['directive_id']]}")
        if t.get("files"):
            lines.append(f"- 产出文件：{'、'.join(t['files'])}")
        lines += ["", "## 要求", t.get("detail") or "（无）", "", "## 汇报", t.get("result") or "（无）"]
        for r in t.get("reviews") or []:
            lines += ["", f"## 第 {r['round']} 轮审查：{VERDICT.get(r.get('verdict'), r.get('verdict'))}",
                      r.get("summary", "")]
            lines += [f"- {i.get('text', '')}" for i in r.get("issues") or []]
        if t.get("ceo_note"):
            lines += ["", "## CEO 裁决", t["ceo_note"]]
        out[f"task:{tid}"] = (f"任务《{t['title']}》（{who}）", "\n".join(lines))
    return out


def sync_company(kb: KnowledgeBase, company, state: dict | None = None) -> dict:
    """同步公司文件区；给了状态就同时同步任务记录。返回两部分的统计。"""
    result = {"files": kb.sync_files(company.files_root)}
    if state is not None:
        result["tasks"] = kb.sync_items("task:", task_documents(state))
    return result
