"""关键词检索（BM25）和两路结果的融合（RRF）。纯 Python，不依赖任何第三方库。

分词：
- 英文、数字、代码标识符：转小写；low_stock、lowStock 这类标识符同时拆成 low、stock，搜单词也能命中
- 中文：没有空格分词，用相邻两个字（二元组）作为词，"退款金额" → 退款、款金、金额。
  不需要词典，对专有名词、新词也有效；单独一个汉字时保留这个字
"""
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
    """经典 BM25。k1 控制词频饱和，b 控制长度归一化。"""

    def __init__(self, k1: float = 1.5, b: float = 0.75):
        self.k1, self.b = k1, b
        self.docs: dict[int, Counter] = {}
        self.lengths: dict[int, int] = {}
        self.df: Counter = Counter()

    def add(self, doc_id: int, text: str) -> None:
        tf = Counter(tokenize(text))
        self.docs[doc_id], self.lengths[doc_id] = tf, sum(tf.values())
        self.df.update(tf.keys())

    def remove(self, doc_id: int) -> None:
        tf = self.docs.pop(doc_id, None)
        self.lengths.pop(doc_id, None)
        if tf:
            self.df.subtract(tf.keys())

    def search(self, query: str, k: int, allowed=None) -> list[tuple[int, float]]:
        terms = set(tokenize(query))
        if not terms or not self.docs:
            return []
        n, avg = len(self.docs), sum(self.lengths.values()) / len(self.docs)
        idf = {t: math.log(1 + (n - self.df[t] + 0.5) / (self.df[t] + 0.5)) for t in terms if self.df[t] > 0}
        scores = []
        for doc_id, tf in self.docs.items():
            if allowed is not None and doc_id not in allowed:
                continue
            s = 0.0
            for t, w in idf.items():
                f = tf.get(t, 0)
                if f:
                    s += w * f * (self.k1 + 1) / (f + self.k1 * (1 - self.b + self.b * self.lengths[doc_id] / avg))
            if s > 0:
                scores.append((doc_id, s))
        return sorted(scores, key=lambda x: -x[1])[:k]


def rrf(rankings: list[list[int]], k: int = 60, weights: list[float] | None = None) -> dict[int, float]:
    """倒数排名融合：每一路结果按名次给分 权重/(k + 名次)，再相加。不需要两路分数在同一个量纲上。"""
    weights = weights or [1.0] * len(rankings)
    scores: dict[int, float] = {}
    for ranking, w in zip(rankings, weights):
        for rank, doc_id in enumerate(ranking, 1):
            scores[doc_id] = scores.get(doc_id, 0.0) + w / (k + rank)
    return scores
