"""关键词检索（BM25）和两路结果的融合（RRF）。纯 Python，不依赖任何第三方库。

分词：
- 英文、数字、代码标识符：转小写；low_stock、lowStock 这类标识符同时拆成 low、stock，搜单词也能命中
- 中文：没有空格分词，用相邻两个字（二元组）作为词，"退款金额" → 退款、款金、金额。
  不需要词典，对专有名词、新词也有效；单独一个汉字时保留这个字
"""
import heapq
import math
import re
from collections import Counter

CJK = re.compile(r"[一-鿿㐀-䶿]+")
CAMEL = re.compile(r"[A-Z]?[a-z]+|[A-Z]+(?![a-z])|\d+")


def tokenize(text: str) -> list[str]:
    tokens = []
    for raw in re.findall(r"[A-Za-z0-9_]+", text):
        parts = [p for p in raw.split("_") if p]
        whole = raw.lower()
        tokens.append(whole)
        sub = [w.lower() for p in parts for w in CAMEL.findall(p)]
        if len(sub) > 1 or (sub and sub[0] != whole):
            tokens += sub
    for run in CJK.findall(text):
        tokens += [run] if len(run) == 1 else [run[i:i + 2] for i in range(len(run) - 1)]
    return tokens


class BM25:
    """经典 BM25（k1 控制词频饱和，b 控制长度归一化），用倒排索引：词 → {文档: 词频}。

    检索时只看包含查询词的文档。以前每次检索都把所有文档算一遍：几百块时没感觉，
    在 10 万段的公开评测集上每次要 0.27 秒（scripts/rag_bench.py），改成倒排索引后约 5 毫秒，见 docs/design.md 决策 27。
    """

    def __init__(self, k1: float = 1.5, b: float = 0.75):
        self.k1, self.b = k1, b
        self.postings: dict[str, dict[int, int]] = {}
        self.lengths: dict[int, int] = {}
        self.terms: dict[int, tuple[str, ...]] = {}  # 每个文档有哪些词（删除时用）
        self.total = 0  # 所有文档的长度之和（算平均长度）

    def add(self, doc_id: int, text: str) -> None:
        if doc_id in self.lengths:
            self.remove(doc_id)
        tf = Counter(tokenize(text))
        for term, f in tf.items():
            self.postings.setdefault(term, {})[doc_id] = f
        length = sum(tf.values())
        self.lengths[doc_id], self.terms[doc_id] = length, tuple(tf)
        self.total += length

    def remove(self, doc_id: int) -> None:
        for term in self.terms.pop(doc_id, ()):
            docs = self.postings.get(term)
            if docs is not None:
                docs.pop(doc_id, None)
                if not docs:
                    del self.postings[term]
        self.total -= self.lengths.pop(doc_id, 0)

    def search(self, query: str, k: int, allowed=None) -> list[tuple[int, float]]:
        terms = set(tokenize(query))
        if not terms or not self.lengths:
            return []
        n, avg = len(self.lengths), self.total / len(self.lengths)
        k1, b, lengths = self.k1, self.b, self.lengths
        scores: dict[int, float] = {}
        for t in terms:
            docs = self.postings.get(t)
            if not docs:
                continue
            w = math.log(1 + (n - len(docs) + 0.5) / (len(docs) + 0.5))
            for doc_id, f in docs.items():
                if allowed is not None and doc_id not in allowed:
                    continue
                scores[doc_id] = scores.get(doc_id, 0.0) + w * f * (k1 + 1) / (f + k1 * (1 - b + b * lengths[doc_id] / avg))
        return heapq.nlargest(k, scores.items(), key=lambda x: x[1])


def rrf(rankings: list[list[int]], k: int = 60, weights: list[float] | None = None) -> dict[int, float]:
    """倒数排名融合：每一路结果按名次给分 权重/(k + 名次)，再相加。不需要两路分数在同一个量纲上。"""
    weights = weights or [1.0] * len(rankings)
    scores: dict[int, float] = {}
    for ranking, w in zip(rankings, weights):
        for rank, doc_id in enumerate(ranking, 1):
            scores[doc_id] = scores.get(doc_id, 0.0) + w / (k + rank)
    return scores
