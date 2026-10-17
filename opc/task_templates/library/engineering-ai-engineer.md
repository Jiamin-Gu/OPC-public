## 技术交付物

### RAG 服务示例

```python
from dataclasses import dataclass
from typing import List
import numpy as np


@dataclass
class RetrievalConfig:
    top_k: int = 5
    similarity_threshold: float = 0.75
    chunk_size: int = 512
    chunk_overlap: int = 64


class RAGService:
    """检索增强生成服务"""

    def __init__(self, config: RetrievalConfig, vector_store, llm_client):
        self.config = config
        self.vector_store = vector_store
        self.llm = llm_client

    def query(self, question: str, filters: dict = None) -> dict:
        # 1. 检索相关文档
        docs = self.vector_store.search(
            query=question,
            top_k=self.config.top_k,
            filters=filters,
        )

        # 2. 过滤低相关度结果
        relevant = [
            d for d in docs
            if d.score >= self.config.similarity_threshold
        ]

        if not relevant:
            return {"answer": "未找到相关信息", "sources": []}

        # 3. 构建 prompt
        context = "\n\n".join(d.content for d in relevant)
        prompt = self._build_prompt(question, context)

        # 4. 生成回答
        response = self.llm.generate(
            prompt=prompt,
            max_tokens=1024,
            temperature=0.1,
        )

        return {
            "answer": response.text,
            "sources": [d.metadata for d in relevant],
            "tokens_used": response.usage.total_tokens,
        }

    def _build_prompt(self, question: str, context: str) -> str:
        return (
            f"基于以下参考资料回答问题。如果资料中没有答案，"
            f"请明确说明。\n\n"
            f"参考资料：\n{context}\n\n"
            f"问题：{question}\n\n"
            f"回答："
        )
```
