"""向量模型后端：把文字变成向量，用于按意思检索。

三种选择（环境变量 OPC_EMBED_BACKEND）：
- local：本地模型，用 fastembed 在 CPU 上运行，默认 BAAI/bge-small-zh-v1.5（约 95 MB，第一次使用时下载到 ~/.opc/models）
- api：任何提供 OpenAI 兼容 /embeddings 接口的服务（OPC_EMBED_BASE_URL、OPC_EMBED_API_KEY、OPC_EMBED_MODEL）
- none：不用向量，只做关键词检索
不设置时：装了 fastembed 就用 local，否则 none。向量检索不可用时知识库自动退回关键词检索，不会报错。
"""
import os
from pathlib import Path

DEFAULT_LOCAL_MODEL = "BAAI/bge-small-zh-v1.5"
BATCH = 32


class Embedder:
    name = "none"

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        raise NotImplementedError

    def embed_query(self, text: str) -> list[float]:
        raise NotImplementedError


class LocalEmbedder(Embedder):
    """fastembed 本地模型。第一次用到时才加载（加载要几秒），之后常驻内存。"""

    def __init__(self, model: str = DEFAULT_LOCAL_MODEL, cache_dir: str | None = None):
        self.model_name = model
        self.name = f"local:{model}"
        self.cache_dir = cache_dir or str(Path(os.getenv("OPC_HOME") or Path.home() / ".opc") / "models")
        self._model = None

    def _load(self):
        if self._model is None:
            from fastembed import TextEmbedding
            self._model = TextEmbedding(self.model_name, cache_dir=self.cache_dir)
        return self._model

    def embed_documents(self, texts):
        return [v.tolist() for v in self._load().passage_embed(texts, batch_size=BATCH)]

    def embed_query(self, text):
        # bge 这类模型对"短查询找长段落"有专门的查询格式，query_embed 会按模型的要求处理
        return next(iter(self._load().query_embed(text))).tolist()


class ApiEmbedder(Embedder):
    """OpenAI 兼容的 /embeddings 接口。"""

    def __init__(self, base_url: str, api_key: str, model: str):
        from openai import OpenAI
        self.client = OpenAI(base_url=base_url, api_key=api_key, max_retries=3, timeout=60)
        self.model = model
        self.name = f"api:{model}"

    def embed_documents(self, texts):
        out = []
        for i in range(0, len(texts), BATCH):
            resp = self.client.embeddings.create(model=self.model, input=texts[i:i + BATCH])
            out += [d.embedding for d in sorted(resp.data, key=lambda d: d.index)]
        return out

    def embed_query(self, text):
        return self.embed_documents([text])[0]


def default_embedder() -> Embedder | None:
    backend = os.getenv("OPC_EMBED_BACKEND", "").strip().lower()
    if backend == "none":
        return None
    if backend == "api":
        return ApiEmbedder(os.getenv("OPC_EMBED_BASE_URL", ""), os.getenv("OPC_EMBED_API_KEY", ""),
                           os.getenv("OPC_EMBED_MODEL", ""))
    try:
        import fastembed  # noqa: F401
    except ImportError:
        if backend == "local":
            raise RuntimeError("OPC_EMBED_BACKEND=local 需要先安装：pip install -r requirements-kb.txt") from None
        return None
    return LocalEmbedder(os.getenv("OPC_EMBED_MODEL") or DEFAULT_LOCAL_MODEL)
