"""向量模型后端：把文字变成向量，用于按意思检索。

三种选择（环境变量 OPC_EMBED_BACKEND）：
- local：本地模型，用 fastembed 在 CPU 上运行，默认 BAAI/bge-small-zh-v1.5（约 95 MB，第一次使用时下载到 ~/.opc/models，
  可以用 OPC_MODELS_DIR 换位置）
- api：任何提供 OpenAI 兼容 /embeddings 接口的服务（OPC_EMBED_BASE_URL、OPC_EMBED_API_KEY、OPC_EMBED_MODEL）
- none：不用向量，只做关键词检索
本地模型默认在 CPU 上算；OPC_EMBED_DEVICE=cuda 时用显卡（见 onnx_providers）。
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
        # OPC_MODELS_DIR：模型放在别的盘（C 盘空间紧时）；默认在数据目录的 models/ 下
        self.cache_dir = cache_dir or os.getenv("OPC_MODELS_DIR") or str(Path(os.getenv("OPC_HOME") or Path.home() / ".opc") / "models")
        self._model = None

    def _load(self):
        if self._model is None:
            from fastembed import TextEmbedding
            providers = onnx_providers()
            self._model = TextEmbedding(self.model_name, cache_dir=self.cache_dir,
                                        **({"providers": providers} if providers else {}))
        return self._model

    def embed_documents(self, texts):
        # 按长度排序后再分批：同一批里长度相近，补齐（padding）浪费的计算少。实测 3000 段从每秒 35 段提到 62 段
        order = sorted(range(len(texts)), key=lambda i: len(texts[i]))
        vectors = list(self._load().passage_embed([texts[i] for i in order], batch_size=BATCH))
        out = [None] * len(texts)
        for i, v in zip(order, vectors):
            out[i] = v.tolist()
        return out

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


def onnx_providers() -> list[str] | None:
    """向量模型和重排序模型在哪里算。OPC_EMBED_DEVICE=cuda 时用显卡（需要装 onnxruntime-gpu，见 requirements-kb-gpu.txt），
    装了但显卡用不了时退回 CPU；不设或设成 cpu 时用 CPU（fastembed 的默认做法）。"""
    if os.getenv("OPC_EMBED_DEVICE", "").strip().lower() not in ("cuda", "gpu"):
        return None
    try:
        import onnxruntime
        if hasattr(onnxruntime, "preload_dlls"):  # pip 装的 CUDA / cuDNN 在 site-packages 里，要先加载
            onnxruntime.preload_dlls()
        if "CUDAExecutionProvider" in onnxruntime.get_available_providers():
            return ["CUDAExecutionProvider", "CPUExecutionProvider"]
    except Exception:
        pass
    return None


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
