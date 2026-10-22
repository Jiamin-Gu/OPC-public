"""公开检索评测集上的 RAG 评测：用 C-MTEB 的 DuRetrieval（百度搜索，10 万段）和 T2Retrieval（搜狗搜索，11.9 万段）
衡量 OPC 知识库的检索质量和性能，并和向量模型的公开成绩对照。

    python scripts/rag_bench.py fetch                         # 下载评测集到数据目录（约 225 MB，许可证 Apache 2.0）
    python scripts/rag_bench.py embed DuRetrieval             # 算好所有段落的向量并缓存（CPU 上约半小时、显卡上几分钟，可以中断后接着算）
    python scripts/rag_bench.py eval DuRetrieval [--queries 500] [--rerank 200] [--grid]

- 按 MTEB 的做法：每一段是一个检索单位（不再切块），指标是 nDCG@10、Recall@10、Recall@100、MRR@10
- 用的是 OPC 知识库本身的检索代码：BM25（opc.knowledge.search）、向量模型（opc.knowledge.embedding）、
  混合融合（opc.knowledge.base.fuse）、重排序（opc.knowledge.rerank）
- 同时记建索引的时间、每次检索的延迟和内存占用；--grid 另外对比几种融合方式
数据目录默认 ~/.opc/rag-data，可以用 RAG_DATA 改；模型目录用 OPC_MODELS_DIR；OPC_EMBED_DEVICE=cuda 时用显卡算。
v0.24 的结果见 examples/rag-bench-v0.24 和 docs/design.md 决策 27。
"""
import argparse
import json
import math
import os
import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

DATA = Path(os.getenv("RAG_DATA") or Path.home() / ".opc" / "rag-data")
DATASETS = ("DuRetrieval", "T2Retrieval")
CHUNK = 5000  # 向量分批缓存：每 5000 段一个文件，中断后接着算
# bge-small-zh-v1.5 在 MTEB 上的公开成绩（embeddings-benchmark/results，mteb 1.1.0，dev 集，百分制）
PUBLISHED = {"DuRetrieval": {"ndcg@10": 77.28, "recall@10": 78.95, "recall@100": 93.03, "mrr@10": 86.44},
             "T2Retrieval": {"ndcg@10": 76.44, "recall@10": 75.14, "recall@100": 90.46, "mrr@10": 87.68}}


def fetch() -> None:
    import urllib.request
    for name in DATASETS:
        for repo in (name, name + "-qrels"):
            tree = json.load(urllib.request.urlopen(
                f"https://huggingface.co/api/datasets/C-MTEB/{repo}/tree/main?recursive=true", timeout=60))
            for item in tree:
                if item.get("type") != "file" or not item["path"].endswith(".parquet"):
                    continue
                out = DATA / repo / (Path(item["path"]).name.split("-")[0] + ".parquet")
                if out.exists() and out.stat().st_size == item.get("size"):
                    continue
                out.parent.mkdir(parents=True, exist_ok=True)
                url = f"https://huggingface.co/datasets/C-MTEB/{repo}/resolve/main/{item['path']}"
                with urllib.request.urlopen(url, timeout=900) as r, open(out, "wb") as f:
                    while chunk := r.read(1 << 20):
                        f.write(chunk)
                print(out, f"{out.stat().st_size / 1e6:.1f} MB", flush=True)


def load(name: str):
    import pandas as pd
    corpus = pd.read_parquet(DATA / name / "corpus.parquet")
    queries = pd.read_parquet(DATA / name / "queries.parquet")
    qrels = pd.read_parquet(DATA / (name + "-qrels") / "dev.parquet")
    gold: dict[str, set[str]] = {}
    for qid, pid, score in qrels.itertuples(index=False):
        if score > 0:
            gold.setdefault(str(qid), set()).add(str(pid))
    qtext = {str(q): t for q, t in queries.itertuples(index=False)}
    qs = [(q, qtext[q]) for q in sorted(gold) if q in qtext]
    return [str(x) for x in corpus["id"]], corpus["text"].tolist(), qs, gold


def cache_dir(name: str, model: str) -> Path:
    return DATA / "cache" / f"{name}-{model.replace('/', '_')}"


def embed(name: str) -> None:
    from opc.knowledge.embedding import DEFAULT_LOCAL_MODEL, LocalEmbedder
    ids, texts, _, _ = load(name)
    emb, out = LocalEmbedder(), cache_dir(name, DEFAULT_LOCAL_MODEL)
    out.mkdir(parents=True, exist_ok=True)
    start = time.time()
    for i in range(0, len(texts), CHUNK):
        part = out / f"{i // CHUNK:04d}.npy"
        if part.exists():
            continue
        t = time.time()
        np.save(part, np.asarray(emb.embed_documents(texts[i:i + CHUNK]), dtype=np.float32))
        done = min(i + CHUNK, len(texts))
        print(f"{done}/{len(texts)} 段，这批 {CHUNK / (time.time() - t):.0f} 段/秒，累计 {(time.time() - start) / 60:.1f} 分钟",
              flush=True)


def vectors(name: str) -> np.ndarray:
    from opc.knowledge.embedding import DEFAULT_LOCAL_MODEL
    parts = sorted(cache_dir(name, DEFAULT_LOCAL_MODEL).glob("*.npy"))
    if not parts:
        sys.exit(f"还没有算向量：先运行 python scripts/rag_bench.py embed {name}")
    m = np.concatenate([np.load(p) for p in parts])
    return m / np.maximum(np.linalg.norm(m, axis=1, keepdims=True), 1e-12)


def metrics(ranked: list[str], gold: set[str]) -> dict:
    dcg = sum(1 / math.log2(i + 2) for i, d in enumerate(ranked[:10]) if d in gold)
    idcg = sum(1 / math.log2(i + 2) for i in range(min(len(gold), 10)))
    first = next((i for i, d in enumerate(ranked[:10], 1) if d in gold), None)
    return {"ndcg@10": dcg / idcg, "recall@10": len(gold & set(ranked[:10])) / len(gold),
            "recall@100": len(gold & set(ranked[:100])) / len(gold), "mrr@10": 1 / first if first else 0.0}


def rss_mb() -> float:
    try:
        import psutil
        return psutil.Process().memory_info().rss / 1e6
    except ImportError:
        return float("nan")


def evaluate(name: str, n_queries: int | None, n_rerank: int, seed: int = 0, grid: bool = False) -> dict:
    from opc.knowledge.base import fuse, query_weights
    from opc.knowledge.embedding import LocalEmbedder
    from opc.knowledge.search import BM25

    ids, texts, qs, gold = load(name)
    if n_queries and n_queries < len(qs):
        rng = np.random.default_rng(seed)
        qs = [qs[i] for i in sorted(rng.choice(len(qs), n_queries, replace=False))]
    report = {"dataset": name, "passages": len(ids), "queries": len(qs), "published_vector": PUBLISHED[name]}

    mem0 = rss_mb()
    t = time.time()
    bm25 = BM25()
    for i, text in enumerate(texts):
        bm25.add(i, text)
    report["bm25_build_s"] = round(time.time() - t, 1)
    report["bm25_mem_mb"] = round(rss_mb() - mem0, 0)
    t = time.time()
    mat = vectors(name)
    report["vector_load_s"], report["vector_mem_mb"] = round(time.time() - t, 1), round(mat.nbytes / 1e6, 0)
    emb = LocalEmbedder()

    def keyword(q, n):
        return [i for i, _ in bm25.search(q, n)]

    def vector(q, n):
        qv = np.asarray(emb.embed_query(q), dtype=np.float32)
        sims = mat @ (qv / (np.linalg.norm(qv) or 1.0))
        top = np.argpartition(-sims, n)[:n]
        return top[np.argsort(-sims[top])].tolist()

    results: dict[str, list] = {"keyword": [], "vector": [], "hybrid": []}
    latency: dict[str, list] = {k: [] for k in results}
    for qid, q in qs:
        t = time.time(); kw = keyword(q, 100); latency["keyword"].append(time.time() - t)
        t = time.time(); vec = vector(q, 100); latency["vector"].append(time.time() - t)
        t = time.time(); hy = fuse(kw, vec, query_weights(q), 100)[0]; latency["hybrid"].append(time.time() - t)
        for mode, ranked in (("keyword", kw), ("vector", vec), ("hybrid", hy)):
            results[mode].append(metrics([ids[i] for i in ranked], gold[qid]))

    if n_rerank:
        from opc.knowledge.base import RERANK_POOL
        from opc.knowledge.rerank import LocalReranker
        reranker = LocalReranker()
        sub = qs[:n_rerank]
        results["hybrid+rerank"], results["hybrid(同一批)"], latency["hybrid+rerank"] = [], [], []
        for qid, q in sub:
            kw, vec = keyword(q, 100), vector(q, 100)
            hy = fuse(kw, vec, query_weights(q), 100)[0]
            t = time.time()
            pool = hy[:RERANK_POOL]
            scores = reranker.scores(q, [texts[i] for i in pool])
            reranked = [c for _, c in sorted(zip(scores, pool), key=lambda x: -x[0])] + hy[RERANK_POOL:]
            latency["hybrid+rerank"].append(time.time() - t)
            results["hybrid+rerank"].append(metrics([ids[i] for i in reranked], gold[qid]))
            results["hybrid(同一批)"].append(metrics([ids[i] for i in hy], gold[qid]))

    if grid:  # 融合方式的对比：关键词权重、要不要保证每一路的第一名进结果，以及重排的候选来自混合还是向量
        cache = {qid: (keyword(q, 100), vector(q, 100)) for qid, q in qs}
        for kw_w in (1.0, 0.7, 0.5, 0.3):
            for keep in (True, False):
                name = f"融合 关键词权重{kw_w} {'保第一名' if keep else '不保第一名'}"
                results[name] = [metrics([ids[i] for i in fuse(*cache[qid], (kw_w, 1.0), 100, keep_top=keep)[0]], gold[qid])
                                 for qid, _ in qs]
        if n_rerank:
            from opc.knowledge.base import RERANK_POOL
            from opc.knowledge.rerank import LocalReranker
            reranker = LocalReranker()
            for label, pick in (("向量+重排", lambda kw, vec, q: vec),
                                ("混合(0.3,不保第一名)+重排", lambda kw, vec, q: fuse(kw, vec, (0.3, 1.0), 100, keep_top=False)[0])):
                results[label] = []
                for qid, q in qs[:n_rerank]:
                    ranked = pick(*cache[qid], q)
                    pool = ranked[:RERANK_POOL]
                    scores = reranker.scores(q, [texts[i] for i in pool])
                    ranked = [c for _, c in sorted(zip(scores, pool), key=lambda x: -x[0])] + ranked[RERANK_POOL:]
                    results[label].append(metrics([ids[i] for i in ranked], gold[qid]))

    report["metrics"] = {mode: {k: round(100 * float(np.mean([r[k] for r in rs])), 2) for k in rs[0]}
                         for mode, rs in results.items() if rs}
    report["latency_ms"] = {mode: {"p50": round(1000 * float(np.percentile(v, 50)), 1),
                                   "p95": round(1000 * float(np.percentile(v, 95)), 1)} for mode, v in latency.items() if v}
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description="C-MTEB 检索评测集上的 RAG 评测")
    parser.add_argument("command", choices=["fetch", "embed", "eval"])
    parser.add_argument("dataset", nargs="?", choices=DATASETS)
    parser.add_argument("--queries", type=int, help="只评其中一部分问题（随机抽，固定种子）")
    parser.add_argument("--rerank", type=int, default=0, help="再对前多少个问题评重排序（CPU 上每个约 2 秒）")
    parser.add_argument("--out", help="结果写到这个 JSON 文件")
    parser.add_argument("--grid", action="store_true", help="另外对比几种融合方式（关键词权重、是否保第一名、重排的候选来源）")
    args = parser.parse_args()
    if args.command == "fetch":
        return fetch()
    if not args.dataset:
        sys.exit("需要指定评测集：" + "、".join(DATASETS))
    if args.command == "embed":
        return embed(args.dataset)
    report = evaluate(args.dataset, args.queries, args.rerank, grid=args.grid)
    text = json.dumps(report, ensure_ascii=False, indent=1)
    print(text)
    if args.out:
        Path(args.out).write_text(text, encoding="utf-8")


if __name__ == "__main__":
    main()
