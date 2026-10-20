"""把一家公司的内容同步进它的知识库。知识库就是公司的长期记忆，收录：

- 公司文件区的文档和代码（source 是相对路径）
- 资料库：CEO 上传的公司资料（lib:，只属于这家公司）和个人资料（me:，所有公司共用），支持 PDF、Word 和文本
- 每个任务的记录（task:）：要求、汇报、审查意见、CEO 裁决
- 每条指令的结论（directive:）：CEO 的原话和插话、计划、同事问答里定下的口径、各任务的结论、最终汇报。
  下一条指令规划时检索得到"以前定过什么"，指令之间不再互相失忆

知识库存在公司目录下的 kb/ 里。这里只读取公司数据，不修改公司、图或任何角色的状态。
"""
from pathlib import Path

from ..roles import ROLES
from .base import KnowledgeBase

VERDICT = {"pass": "通过", "changes": "要求修改", "unclear": "没有给出结论", "error": "审查失败"}
STATUS = {"done": "完成", "failed": "失败", "skipped": "跳过", "cancelled": "取消", "escalated": "等 CEO 裁决"}
# 资料库：(source 前缀, kind, 显示名)
LIBRARIES = {"company": ("lib:", "lib", "公司资料"), "personal": ("me:", "me", "个人资料")}


def open_kb(company, embedder=None, **kw) -> KnowledgeBase:
    kwargs = {} if embedder is None else {"embedder": embedder}
    return KnowledgeBase(company.root / "kb", **kwargs, **kw)


def library_root(company, scope: str) -> Path:
    """公司资料放在公司目录的 library/，个人资料放在数据目录的 library/（所有公司共用）。
    都不在公司文件区里：角色只能检索和阅读，写文件的工具碰不到。"""
    return company.root / "library" if scope == "company" else company.home / "library"


def _who(role_id: str) -> str:
    return ROLES[role_id].name if role_id in ROLES else role_id


def task_documents(state: dict) -> dict[str, tuple[str, str]]:
    """把 LangGraph 状态里的任务整理成 {source: (标题, Markdown)}：谁做的、要求、汇报、审查意见、CEO 裁决。"""
    directives = {m.get("directive_id"): m["text"] for m in state.get("channels", {}).get("all", [])
                  if m.get("sender") == "ceo" and m.get("directive_id")}
    out = {}
    for tid, t in (state.get("tasks") or {}).items():
        if t.get("status") == "cancelled" and not t.get("result"):
            continue
        who = _who(t["assignee"])
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


def directive_documents(state: dict) -> dict[str, tuple[str, str]]:
    """每条指令整理成一篇结论：{directive:<id>: (标题, Markdown)}。

    任务记录是"每个人做了什么"，这里是"这件事最后定了什么"：CEO 的要求和插话、计划、同事问答里定下的口径、
    每个任务的结论和需要 CEO 决定的事项、CEO 的裁决、最终汇报。"""
    msgs = state.get("channels", {}).get("all", [])
    tasks = state.get("tasks") or {}
    task_directive = {tid: t.get("directive_id") for tid, t in tasks.items()}

    def did_of(m: dict) -> str | None:
        return m.get("directive_id") or task_directive.get(m.get("task_id") or "")

    out = {}
    for d in [m for m in msgs if m.get("sender") == "ceo" and m.get("kind") == "text" and m.get("directive_id")]:
        did = d["directive_id"]
        mine = [m for m in msgs if did_of(m) == did]
        plans = [m for m in mine if m.get("kind") == "plan"]
        reports = [m for m in mine if m.get("kind") == "report"]
        notes = [m for m in mine if m.get("kind") == "interject"]
        questions = {m["id"]: m for m in mine if m.get("consult")}
        answers = [(questions[m["reply_to"]], m) for m in mine if m.get("consult") and m.get("reply_to") in questions]
        own = [t for t in tasks.values() if t.get("directive_id") == did
               and not (t.get("status") == "cancelled" and not t.get("result"))]

        lines = ["# 指令结论", "", f"- 时间：{d.get('ts', '')}",  # 原话在下面一节：标题短一些，出处里不重复
                 f"- 状态：{'已完成（有最终汇报）' if reports else '进行中或没有完成'}", "", "## CEO 的指令", d["text"]]
        if notes:
            lines += ["", "## CEO 中途的插话和补充"] + [f"- {m['text']}" for m in notes]
        if plans:
            lines += ["", "## 计划", plans[-1]["text"]]
        if answers:
            lines += ["", "## 同事问答里定下的口径"]
            lines += [f"- {_who(q['sender'])}问{_who(a['sender'])}：{q['text'][:300]}\n  回答：{a['text'][:800]}"
                      for q, a in answers]
        if own:
            lines += ["", "## 各任务的结论"]
            for t in own:
                head = (t.get("headline") or "").strip()
                lines.append(f"- 《{t['title']}》（{_who(t['assignee'])}，{STATUS.get(t['status'], t['status'])}）"
                             + (f"：{head}" if head else ""))
                if t.get("files"):
                    lines.append(f"  产出文件：{'、'.join(t['files'])}")
                lines += [f"  需要 CEO 决定：{x}" for x in t.get("decisions") or []]
                if t.get("ceo_note"):
                    lines.append(f"  CEO 裁决：{t['ceo_note']}")
        if reports:
            lines += ["", "## 最终汇报", reports[-1]["text"]]
        out[f"directive:{did}"] = (f"指令《{d['text'][:30]}》", "\n".join(lines))
    return out


def sync_company(kb: KnowledgeBase, company, state: dict | None = None) -> dict:
    """同步公司文件区和资料库；给了状态就同时同步任务记录和指令结论。返回各部分的统计。"""
    result = {"files": kb.sync_files(company.files_root)}
    for scope, (prefix, kind, _) in LIBRARIES.items():
        result[kind] = kb.sync_files(library_root(company, scope), prefix=prefix, kind=kind, documents=True)
    if state is not None:
        result["tasks"] = kb.sync_items("task:", task_documents(state))
        result["directives"] = kb.sync_items("directive:", directive_documents(state))
    return result
