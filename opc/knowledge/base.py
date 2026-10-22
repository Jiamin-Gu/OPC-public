"""知识库：一个目录一个 SQLite 文件（kb.sqlite），存所有块的原文、位置和向量。

数据来源（source）：
- 公司文件区的文件：source 是相对路径，例如 docs/PRD.md、code/todo/cli.py。按内容哈希增量同步，只重建改动过的文件
- 资料库的文件：同样按文件同步，source 带前缀：lib:<文件名>（公司资料）、me:<文件名>（个人资料，所有公司共用）
- 其他条目：source 带前缀，例如 task:<任务id>（任务说明、汇报和审查记录）、directive:<指令id>（一条指令的结论）。
  按前缀整体同步

检索：
- keyword：BM25 关键词检索，精确匹配函数名、专有名词、错误信息
- vector：向量相似度，按意思找（"退款怎么处理"能找到"取消订单时退回付款金额"）
- hybrid（默认）：两路各取前若干名，用倒数排名融合（RRF）合并。向量不可用时自动退回 keyword
- 重排序（v0.22，可选，rerank=True）：hybrid 先取前 RERANK_POOL 个候选，再用交叉编码器逐条打分取前 k（见 rerank.py）。
  只用在人看的搜索上：排序更准，但前 k 条的召回略降、每次多约 2.5 秒；给模型的检索（模型会看全部 k 条）不用
"""
import hashlib
import os
import re
import sqlite3
import threading
from datetime import datetime
from pathlib import Path

import numpy as np

from .chunking import TEXT_SUFFIXES, chunk_file
from .loaders import LoadError, chunk_name, load_text
from .embedding import Embedder, default_embedder
from .search import BM25, rrf, tokenize

SKIP_DIRS = {".git", "__pycache__", ".pytest_cache", ".mypy_cache", "node_modules", ".venv", "venv", ".coder"}
MAX_FILE_BYTES = 1_000_000
CANDIDATES = 5  # 混合检索时每一路取 k × 这个数的候选再融合
RERANK_POOL = 15  # 重排序时从混合检索的前多少名里重新排（评测：15 个候选、每个 600 字最均衡）
MODES = ("hybrid", "keyword", "vector")
_DEFAULT = object()

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS sources (source TEXT PRIMARY KEY, hash TEXT, kind TEXT, updated TEXT);
CREATE TABLE IF NOT EXISTS chunks (
    id INTEGER PRIMARY KEY AUTOINCREMENT, source TEXT, start INTEGER, "end" INTEGER,
    kind TEXT, title TEXT, text TEXT, vector BLOB);
CREATE INDEX IF NOT EXISTS chunks_source ON chunks(source);
"""


class KnowledgeBase:
    def __init__(self, directory, embedder=_DEFAULT, reranker=None):
        self.dir = Path(directory)
        self.dir.mkdir(parents=True, exist_ok=True)
        self.embedder: Embedder | None = default_embedder() if embedder is _DEFAULT else embedder
        self.reranker = reranker      # 重排序模型（rerank.py），None 表示不重排
        self._matrix = None           # 所有向量拼成的矩阵和对应的块 id：向量有变化时作废，下次检索时重建
        self.rerank_error = ""
        self.vector_error = ""  # 向量模型出错时记下原因，检索退回关键词
        self.load_errors: dict[str, str] = {}  # 资料解析失败或没有文字的文件：{source: 原因}
        self.lock = threading.RLock()
        self.db = sqlite3.connect(self.dir / "kb.sqlite", check_same_thread=False)
        self.db.executescript(SCHEMA)
        self.bm25 = BM25()
        self.chunks: dict[int, dict] = {}
        self.vectors: dict[int, np.ndarray] = {}
        self._load()

    # ---------------------------------------------------------------- 加载与写入

    def _load(self) -> None:
        stored = self._meta("embedder")
        current = self.embedder.name if self.embedder else ""
        if stored != current:  # 换了向量模型：旧向量不能和新查询比较，全部作废，同步时重新生成
            self.db.execute("UPDATE chunks SET vector = NULL")
            self._matrix = None
            self._set_meta("embedder", current)
            self.db.commit()
        for row in self.db.execute('SELECT id, source, start, "end", kind, title, text, vector FROM chunks'):
            self._index(*row)

    def _index(self, cid, source, start, end, kind, title, text, vector=None) -> None:
        self.chunks[cid] = {"id": cid, "source": source, "start": start, "end": end, "kind": kind,
                            "title": title, "text": text}
        self.bm25.add(cid, f"{source} {title}\n{text}")  # 文件名和标题也参与关键词检索
        if vector is not None:
            v = np.frombuffer(vector, dtype=np.float32)
            self.vectors[cid] = v / (np.linalg.norm(v) or 1.0)
            self._matrix = None

    def _meta(self, key: str) -> str | None:
        row = self.db.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
        return row[0] if row else None

    def _set_meta(self, key: str, value: str) -> None:
        self.db.execute("INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)", (key, value))

    def _remove_source(self, source: str) -> None:
        for (cid,) in self.db.execute("SELECT id FROM chunks WHERE source = ?", (source,)).fetchall():
            self.chunks.pop(cid, None)
            self.vectors.pop(cid, None)
            self._matrix = None
            self.bm25.remove(cid)
        self.db.execute("DELETE FROM chunks WHERE source = ?", (source,))
        self.db.execute("DELETE FROM sources WHERE source = ?", (source,))

    def _put_source(self, source: str, kind: str, digest: str, chunks: list[dict]) -> None:
        self._remove_source(source)
        self.db.execute("INSERT INTO sources (source, hash, kind, updated) VALUES (?, ?, ?, ?)",
                        (source, digest, kind, datetime.now().isoformat(timespec="seconds")))
        for c in chunks:
            cur = self.db.execute('INSERT INTO chunks (source, start, "end", kind, title, text) VALUES (?, ?, ?, ?, ?, ?)',
                                  (source, c["start"], c["end"], c["kind"], c["title"], c["text"]))
            self._index(cur.lastrowid, source, c["start"], c["end"], c["kind"], c["title"], c["text"])

    def _embed_missing(self) -> int:
        """给还没有向量的块生成向量。向量模型出错时记下原因、不中断同步（检索会退回关键词）。"""
        if not self.embedder:
            return 0
        todo = [cid for cid in self.chunks if cid not in self.vectors]
        if not todo:
            return 0
        try:
            vecs = self.embedder.embed_documents([self._embed_text(self.chunks[c]) for c in todo])
        except Exception as e:  # 模型下载失败、接口不可用等
            self.vector_error = f"{type(e).__name__}: {e}"
            return 0
        self.vector_error = ""
        for cid, v in zip(todo, vecs):
            arr = np.asarray(v, dtype=np.float32)
            self.db.execute("UPDATE chunks SET vector = ? WHERE id = ?", (arr.tobytes(), cid))
            self.vectors[cid] = arr / (np.linalg.norm(arr) or 1.0)
        self._matrix = None
        return len(todo)

    @staticmethod
    def _embed_text(chunk: dict) -> str:
        head = " / ".join(x for x in (chunk["source"], chunk["title"]) if x)
        return f"{head}\n{chunk['text']}"

    # ---------------------------------------------------------------- 同步

    def sync_files(self, root, prefix: str = "", kind: str = "file", documents: bool = False) -> dict:
        """把 root 下的文件同步进知识库：新增、修改的重新切块，删除的移出。返回统计。

        prefix / kind：资料库用（source 是 "lib:文件名"，kind 是 "lib"）。documents=True 时还解析 PDF、Word，
        并且不限文件大小（资料是用户主动给的，不因为太大拒绝）；解析失败、没有文字的记进 load_errors。"""
        root = Path(root)
        stats = {"added": 0, "updated": 0, "removed": 0, "unchanged": 0}
        if not root.is_dir():
            root = None
        with self.lock:
            known = dict(self.db.execute("SELECT source, hash FROM sources WHERE kind = ?", (kind,)).fetchall())
            seen = set()
            for path in sorted(self._walk(root, documents) if root else []):
                rel = path.relative_to(root).as_posix()
                source = prefix + rel
                data = path.read_bytes()
                if not documents and b"\0" in data[:8000]:  # 二进制文件
                    continue
                seen.add(source)
                digest = hashlib.sha256(data).hexdigest()
                if known.get(source) == digest:
                    stats["unchanged"] += 1
                    continue
                stats["updated" if source in known else "added"] += 1
                if documents:
                    try:
                        text = load_text(path)
                        self.load_errors.pop(source, None)
                        self.db.execute("DELETE FROM meta WHERE key = ?", (f"error:{source}",))
                    except LoadError as e:  # 记进数据库：重启后资料列表里仍然能看到原因
                        self.load_errors[source] = str(e)
                        self._set_meta(f"error:{source}", str(e))
                        text = ""
                else:
                    text = data.decode("utf-8", errors="replace").replace("\r\n", "\n")
                self._put_source(source, kind, digest, chunk_file(chunk_name(rel), text) if text else [])
            for source in set(known) - seen:
                self._remove_source(source)
                self.load_errors.pop(source, None)
                self.db.execute("DELETE FROM meta WHERE key = ?", (f"error:{source}",))
                stats["removed"] += 1
            stats["embedded"] = self._embed_missing()
            self.db.commit()
        return stats

    def source_info(self, kind: str) -> dict[str, dict]:
        """某一类来源的情况：{source: {chunks, updated, error}}（资料库列表用）。"""
        with self.lock:
            rows = self.db.execute("SELECT s.source, s.updated, COUNT(c.id) FROM sources s "
                                   "LEFT JOIN chunks c ON c.source = s.source WHERE s.kind = ? GROUP BY s.source",
                                   (kind,)).fetchall()
            return {src: {"chunks": n, "updated": updated,
                          "error": self.load_errors.get(src) or self._meta(f"error:{src}") or ""}
                    for src, updated, n in rows}

    def sync_items(self, prefix: str, items: dict[str, tuple[str, str]]) -> dict:
        """同步一组非文件条目：items 是 {source: (标题, Markdown 文本)}，source 都以 prefix 开头。
        这个前缀下不在 items 里的旧条目会被删除。"""
        stats = {"added": 0, "updated": 0, "removed": 0, "unchanged": 0}
        with self.lock:
            known = {s: h for s, h in self.db.execute("SELECT source, hash FROM sources").fetchall()
                     if s.startswith(prefix)}
            for source, (title, text) in items.items():
                digest = hashlib.sha256((title + "\n" + text).encode("utf-8")).hexdigest()
                if known.get(source) == digest:
                    stats["unchanged"] += 1
                    continue
                stats["updated" if source in known else "added"] += 1
                chunks = chunk_file(source + ".md", text)
                for c in chunks:
                    c["title"] = f"{title} / {c['title']}" if c["title"] else title
                self._put_source(source, prefix.rstrip(":"), digest, chunks)
            for source in set(known) - set(items):
                self._remove_source(source)
                stats["removed"] += 1
            stats["embedded"] = self._embed_missing()
            self.db.commit()
        return stats

    @staticmethod
    def _walk(root: Path, documents: bool = False):
        from .loaders import LIBRARY_SUFFIXES
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS and not d.startswith(".")]
            for name in filenames:
                p = Path(dirpath) / name
                if name.startswith(".") or name.startswith("~$"):  # 隐藏文件、Word 打开时的临时文件
                    continue
                if documents:
                    if p.suffix.lower() in LIBRARY_SUFFIXES:
                        yield p
                elif p.suffix.lower() in TEXT_SUFFIXES and p.stat().st_size <= MAX_FILE_BYTES:
                    yield p

    # ---------------------------------------------------------------- 检索

    def search(self, query: str, k: int = 8, mode: str = "hybrid", source_prefix: str | None = None,
               source_filter=None, rerank: bool = False) -> list[dict]:
        """返回最相关的 k 个块：来源、行号、标题、原文、片段，以及两路检索各自的名次。
        source_prefix / source_filter（source -> bool）：只在一部分来源里检索。"""
        if mode not in MODES:
            raise ValueError(f"mode 只能是 {MODES}")
        query = query.strip()
        if not query:
            return []
        with self.lock:
            allowed = None
            if source_prefix or source_filter:
                allowed = {cid for cid, c in self.chunks.items()
                           if (not source_prefix or c["source"].startswith(source_prefix))
                           and (not source_filter or source_filter(c["source"]))}
            n = k * CANDIDATES
            keyword = [cid for cid, _ in self.bm25.search(query, n, allowed)] if mode != "vector" else []
            vector = self._vector_search(query, n, allowed) if mode != "keyword" else []
            if mode == "keyword" or (mode == "hybrid" and not vector):
                ranked = keyword[:k]
            elif mode == "vector":
                ranked = vector[:k]
            else:
                ranked, order = fuse(keyword, vector, query_weights(query), k)
            scores = {}
            if mode == "hybrid" and rerank and self.reranker:
                pool = (order if vector else keyword)[:RERANK_POOL]
                scores = self._rerank(query, pool)
                if scores:
                    ranked = sorted(pool, key=lambda c: -scores[c])[:k]
            kw_rank = {c: i for i, c in enumerate(keyword, 1)}
            vec_rank = {c: i for i, c in enumerate(vector, 1)}
            return [{**self.chunks[c], "snippet": snippet(self.chunks[c]["text"], query),
                     "keyword_rank": kw_rank.get(c), "vector_rank": vec_rank.get(c),
                     **({"rerank_score": round(scores[c], 3)} if c in scores else {})} for c in ranked]

    def _rerank(self, query: str, pool: list[int]) -> dict[int, float]:
        """给候选打相关度分：{块 id: 分数}。模型出错时返回空，检索退回不重排的结果。"""
        if not pool:
            return {}
        texts = [" / ".join(x for x in (self.chunks[c]["source"], self.chunks[c]["title"]) if x)
                 + "\n" + self.chunks[c]["text"] for c in pool]
        try:
            values = self.reranker.scores(query, texts)
        except Exception as e:
            self.rerank_error = f"{type(e).__name__}: {e}"
            return {}
        self.rerank_error = ""
        return dict(zip(pool, values))

    def _vector_search(self, query: str, n: int, allowed) -> list[int]:
        if not self.embedder or not self.vectors:
            return []
        try:
            q = np.asarray(self.embedder.embed_query(query), dtype=np.float32)
        except Exception as e:
            self.vector_error = f"{type(e).__name__}: {e}"
            return []
        q /= np.linalg.norm(q) or 1.0
        if self._matrix is None or self._matrix[2] != len(self.vectors):
            all_ids = list(self.vectors)
            self._matrix = (all_ids, np.stack([self.vectors[c] for c in all_ids]), len(self.vectors))
        all_ids, mat, _ = self._matrix
        sims = mat @ q
        if allowed is not None:
            keep = np.fromiter((c in allowed for c in all_ids), dtype=bool, count=len(all_ids))
            sims = np.where(keep, sims, -np.inf)
            n = min(n, int(keep.sum()))
        if n <= 0:
            return []
        top = np.argpartition(-sims, n - 1)[:n] if n < len(sims) else np.arange(len(sims))
        return [all_ids[i] for i in top[np.argsort(-sims[top])] if np.isfinite(sims[i])]

    def get(self, chunk_id: int) -> dict | None:
        with self.lock:
            c = self.chunks.get(chunk_id)
            return dict(c) if c else None

    def stats(self) -> dict:
        with self.lock:
            sources = self.db.execute("SELECT kind, COUNT(*) FROM sources GROUP BY kind").fetchall()
            return {"chunks": len(self.chunks), "sources": dict(sources), "vectors": len(self.vectors),
                    "embedder": self.embedder.name if self.embedder else None, "vector_error": self.vector_error,
                    "reranker": self.reranker.name if self.reranker else None, "rerank_error": self.rerank_error}

    def close(self) -> None:
        self.db.close()


IDENTIFIER = re.compile(r"\b(?:[A-Za-z0-9]+_[A-Za-z0-9_]+|[a-z]+[A-Z][A-Za-z0-9]*|[A-Z][a-z0-9]+[A-Z][A-Za-z0-9]*)\b")


def fuse(keyword: list, vector: list, weights: tuple[float, float], k: int, keep_top: bool = True) -> tuple[list, list]:
    """混合检索的融合：两路名次用加权的倒数排名融合（RRF）合并，再保证每一路的第一名出现在前 k 里
    （一路非常确定的结果，不应该被两路都"还行"的结果挤掉）。返回 (前 k 个, 融合后的完整排序)。
    keep_top=False 不做这一步，只给评测对比用。知识库检索和公开评测集上的评测（scripts/rag_bench.py）用的是同一个函数。"""
    fused = rrf([keyword, vector], weights=list(weights))
    order = sorted(fused, key=lambda c: -fused[c])
    position = {c: i for i, c in enumerate(order)}
    must = list(dict.fromkeys(keyword[:1] + vector[:1])) if keep_top else []
    rest = [c for c in order if c not in must][:max(k - len(must), 0)]
    return sorted(must + rest, key=position.__getitem__)[:k], order


def query_weights(query: str) -> tuple[float, float]:
    """两路检索的权重 (关键词, 向量)。查询里有代码标识符（low_stock、parseMoney、InvalidFileError）时，
    要找的是精确出现这个名字的地方，更信关键词；纯自然语言的提问更信向量（按意思找）。
    自然语言的 0.7 是两种数据之间的折中（决策 27）：公开评测集上关键词弱，0.3 更好；OPC 自己的 38 题上关键词强，0.3 反而略差。"""
    return (1.0, 0.5) if IDENTIFIER.search(query) else (0.7, 1.0)


def snippet(text: str, query: str, width: int = 240) -> str:
    """从块里挑出和查询最相关的一段：命中查询词最多的那一行，前后补足到 width 个字符。"""
    terms = set(tokenize(query))
    lines = text.split("\n")
    best = max(range(len(lines)), key=lambda i: sum(t in lines[i].lower() for t in terms)) if terms else 0
    out, size, lo, hi = [lines[best]], len(lines[best]), best, best
    while size < width and (lo > 0 or hi < len(lines) - 1):
        if hi < len(lines) - 1:
            hi += 1
            out.append(lines[hi])
            size += len(lines[hi])
        if size < width and lo > 0:
            lo -= 1
            out.insert(0, lines[lo])
            size += len(lines[lo])
    text = "\n".join(out)
    return text if len(text) <= width * 2 else text[:width * 2] + "…"
