"""知识库服务：每家公司一个知识库实例，网页搜索、角色的 kb_search、规划时的自动召回、编码 agent 共用。

- 检索前先增量同步（按内容哈希，没改动的不重建）；同一家公司 SYNC_INTERVAL 秒内只同步一次，
  避免几个角色同时检索时重复扫描
- 任务记录和指令结论要读调度图的状态和消息：执行引擎打开公司时登记一个读取函数（register），
  没有登记时（命令行、测试）只同步文件和资料库
- 向量模型全进程共用一份（加载要几秒、占几百 MB 内存）
"""
import re
import threading
import time
from pathlib import Path

from .company import LIBRARIES, library_root, open_kb, sync_company
from .embedding import default_embedder
from .rerank import default_reranker
from .loaders import LIBRARY_SUFFIXES

SYNC_INTERVAL = 2.0
CATEGORY_NAMES = {"file": "文件", "task": "任务记录", "directive": "指令结论", "lib": "公司资料", "me": "个人资料"}
SCOPES = {"all": None, "files": {"file"}, "records": {"task", "directive"}, "library": {"lib", "me"}}
AGENT_TEXT = 900   # 给角色的每条结果最多带多少字原文
RECALL_TEXT = 400  # 规划时自动召回的每条结果

_lock = threading.RLock()
_kbs: dict[Path, object] = {}
_synced: dict[Path, float] = {}
_providers: dict[str, object] = {}
_embedder = None
_reranker = None


def category(source: str) -> str:
    head = source.split(":", 1)[0] if ":" in source else ""
    return head if head in CATEGORY_NAMES else "file"


def register(company_id: str, provider) -> None:
    """执行引擎登记：provider() 返回 {"tasks": 任务看板, "channels": {"all": 全公司频道的消息}}。"""
    with _lock:
        _providers[company_id] = provider


def unregister(company_id: str, provider=None) -> None:
    with _lock:
        if provider is None or _providers.get(company_id) is provider:
            _providers.pop(company_id, None)


def get(company):
    """这家公司的知识库实例（不同步）。"""
    global _embedder, _reranker
    with _lock:
        if _embedder is None:
            _embedder = default_embedder() or False
        if _reranker is None:
            _reranker = default_reranker() or False
        kb = _kbs.get(company.root)
        if kb is None:
            kb = _kbs[company.root] = open_kb(company, embedder=_embedder or None, reranker=_reranker or None)
        return kb


def sync(company, state: dict | None = None, force: bool = False) -> dict:
    """增量同步。state 不给时用执行引擎登记的读取函数；SYNC_INTERVAL 秒内同步过就跳过（force 除外）。"""
    kb = get(company)
    now = time.monotonic()
    with _lock:
        if not force and state is None and now - _synced.get(company.root, -1e9) < SYNC_INTERVAL:
            return {}
        _synced[company.root] = now
        provider = _providers.get(company.id)
    if state is None and provider is not None:
        try:
            state = provider()
        except Exception:  # 读状态失败不影响文件和资料的检索
            state = None
    return sync_company(kb, company, state)


def search(company, query: str, scope: str = "all", k: int = 5, mode: str = "hybrid",
           exclude=lambda source: False, state: dict | None = None) -> list[dict]:
    kb = get(company)
    sync(company, state)
    kinds = SCOPES.get(scope)
    return kb.search(query, k, mode, source_filter=lambda s: (kinds is None or category(s) in kinds) and not exclude(s))


def label(r: dict) -> str:
    """一条结果的出处，写给人和模型看，能直接拿来引用。"""
    source, cat = r["source"], category(r["source"])
    if cat == "file":
        return f"{source} 第 {r['start']}-{r['end']} 行" + (f"（{r['title']}）" if r.get("title") else "")
    if cat in ("lib", "me"):
        name = source.split(":", 1)[1]
        where = r.get("title") or ""
        if Path(name).suffix.lower() not in (".pdf", ".docx"):  # 文本资料的行号就是原文的行号
            where = f"第 {r['start']}-{r['end']} 行" + (f"（{where}）" if where else "")
        return f"{CATEGORY_NAMES[cat]}《{name}》{where}"
    return f"{CATEGORY_NAMES[cat]}：{r.get('title') or source}"


def _clip(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[:limit] + "……"


def format_results(results: list[dict], query: str) -> str:
    """kb_search 工具返回给角色的文字。"""
    if not results:
        return (f"知识库里没有找到和「{query}」相关的内容。可以换个说法或关键词再试；确实没有就按没有处理，不要编造。")
    parts = [f"找到 {len(results)} 条（按相关程度排序）："]
    for i, r in enumerate(results, 1):
        parts += ["", f"[{i}] {label(r)}", _clip(r["text"], AGENT_TEXT)]
    parts += ["", "引用时写明出处（例如「依据：上面的出处」）。共享文件区的文件要看全文用 read_file；"
                  "资料和记录需要更多内容时换个更具体的说法再检索。"]
    return "\n".join(parts)


def recall(company, query: str, state: dict | None = None, exclude=lambda source: False, k: int = 5):
    """规划时自动召回：返回 (写进规划上下文的文字, 出处列表)。知识库是空的或检索失败时返回 ("", [])。"""
    try:
        results = search(company, query, "all", k, exclude=exclude, state=state)
    except Exception:
        return "", []
    if not results:
        return "", []
    labels = [label(r) for r in results]
    lines = [f"[{i}] {lab}\n{_clip(r['text'], RECALL_TEXT)}" for i, (lab, r) in enumerate(zip(labels, results), 1)]
    return "\n\n".join(lines), labels


# ---------------------------------------------------------------- 资料库

def safe_name(name: str) -> str:
    """上传的文件名：只取文件名本身，去掉路径和 Windows 不允许的字符。不支持的格式抛 ValueError。"""
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", Path((name or "").replace("\\", "/")).name).strip().strip(".")
    if not name or name.startswith("~$"):
        raise ValueError("文件名不能为空")
    if Path(name).suffix.lower() not in LIBRARY_SUFFIXES:
        raise ValueError("不支持这种格式。支持 PDF、Word（.docx）和文本文件（"
                         + "、".join(sorted(s for s in LIBRARY_SUFFIXES if s not in (".pdf", ".docx"))) + "）")
    return name


def list_library(company) -> dict:
    """两个资料库的文件：{company: [...], personal: [...]}，每项 {name, size, chunks, error, updated}。"""
    kb = get(company)
    sync(company, force=True)
    out = {}
    for scope, (prefix, kind, _) in LIBRARIES.items():
        info = kb.source_info(kind)
        root = library_root(company, scope)
        items = []
        for p in sorted(root.iterdir()) if root.is_dir() else []:
            if not p.is_file() or p.name.startswith((".", "~$")) or p.suffix.lower() not in LIBRARY_SUFFIXES:
                continue
            i = info.get(prefix + p.name, {})
            error = i.get("error") or ("" if i.get("chunks") else "没有提取到文字")
            items.append({"name": p.name, "size": p.stat().st_size, "chunks": i.get("chunks", 0),
                          "error": error, "updated": i.get("updated", "")})
        out[scope] = items
    return out


def add_document(company, scope: str, name: str, data: bytes) -> dict:
    """存进资料库并立即入库，返回这份资料的情况（切了几块、解析失败的原因）。同名文件会被替换。"""
    if scope not in LIBRARIES:
        raise ValueError("scope 只能是 company 或 personal")
    name = safe_name(name)
    root = library_root(company, scope)
    root.mkdir(parents=True, exist_ok=True)
    tmp = root / f".{name}.uploading"
    tmp.write_bytes(data)
    tmp.replace(root / name)
    items = {i["name"]: i for i in list_library(company)[scope]}
    return items.get(name, {"name": name, "chunks": 0, "error": "入库失败"})


def remove_document(company, scope: str, name: str) -> bool:
    if scope not in LIBRARIES:
        raise ValueError("scope 只能是 company 或 personal")
    p = library_root(company, scope) / safe_name(name)
    if not p.is_file():
        return False
    p.unlink()
    sync(company, force=True)
    return True


def reset() -> None:
    """关闭所有知识库（测试、删除公司前用）。"""
    global _embedder, _reranker
    with _lock:
        for kb in _kbs.values():
            try:
                kb.close()
            except Exception:
                pass
        _kbs.clear()
        _synced.clear()
        _providers.clear()
        _embedder = _reranker = None


def close(root: Path) -> None:
    """关掉一家公司的知识库（删除公司前：否则 Windows 上数据库文件被占用，删不掉公司目录）。"""
    with _lock:
        kb = _kbs.pop(Path(root), None)
        _synced.pop(Path(root), None)
    if kb is not None:
        kb.close()
