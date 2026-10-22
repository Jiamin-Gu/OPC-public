# 公开评测集上的检索评测（v0.24）

用 C-MTEB 的两个中文检索评测集评测 OPC 知识库的检索代码（关键词 BM25、向量、混合融合、重排序），并和向量模型的公开成绩对照。
设计和结论见 [设计决策 27](../../docs/design.md)，脚本是 [scripts/rag_bench.py](../../scripts/rag_bench.py)。

| 评测集 | 来源 | 段落 | 评测的问题 | 许可证 |
|---|---|---|---|---|
| DuRetrieval | 百度搜索（DuReader-retrieval） | 100,001 | 全部 2,000 | Apache 2.0 |
| T2Retrieval | 搜狗搜索（T2Ranking） | 118,605 | 22,812 里固定种子抽 2,000 | Apache 2.0 |

向量模型 `BAAI/bge-small-zh-v1.5`，重排序 `BAAI/bge-reranker-base`（混合检索的前 15 个候选），显卡 RTX 4060。

## 结果

| nDCG@10 | DuRetrieval | T2Retrieval |
|---|---|---|
| bge-small-zh-v1.5 公开成绩（向量） | 77.28 | 76.44 |
| 关键词（BM25） | 56.83 | 62.96 |
| 向量 | 77.77 | 77.21 |
| 混合（关键词权重 0.7，默认） | 73.33 | 76.62 |
| 混合（关键词权重 0.3） | 76.14 | 78.33 |
| 混合（0.7）+ 重排序（默认） | 82.31 | 80.58 |
| 向量 + 重排序 | 83.66 | 80.65 |
| 混合（0.3）+ 重排序 | **83.92** | **81.56** |

Recall@10、Recall@100、MRR@10、各种融合方式的完整结果和延迟、内存见 [DuRetrieval.json](DuRetrieval.json)、[T2Retrieval.json](T2Retrieval.json)
（`hybrid` 是默认的融合，`融合 关键词权重… 保第一名 / 不保第一名` 是对比的几种）。

- 向量检索和公开成绩相差不到 1 分：评测流程没有写错
- 重排序提升最多，比纯向量高 3～5 分
- 这两个集上关键词弱，关键词权重 0.3 最好；但在 OPC 自己的 38 题上 0.3 反而略差（MRR 0.737 → 0.723），所以默认的 0.7 没有改
- 10 万段时向量检索约 6 毫秒，不需要向量数据库；BM25 的内存（2.5～6.4 GB）才是规模上去以后要先解决的

## 复现

```bash
pip install -r requirements.txt -r requirements-kb.txt pandas pyarrow psutil
python scripts/rag_bench.py fetch                 # 下载评测集（约 225 MB）
python scripts/rag_bench.py embed DuRetrieval      # 算好向量并缓存（CPU 约半小时，显卡几分钟）
python scripts/rag_bench.py eval DuRetrieval --rerank 2000 --grid --out DuRetrieval.json
python scripts/rag_bench.py embed T2Retrieval
python scripts/rag_bench.py eval T2Retrieval --queries 2000 --rerank 2000 --grid --out T2Retrieval.json
```

用显卡：按 `requirements-kb-gpu.txt` 建单独的虚拟环境，设 `OPC_EMBED_DEVICE=cuda`。数据默认放在 `~/.opc/rag-data`（`RAG_DATA` 可以改）。
