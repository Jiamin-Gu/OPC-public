"""重排序（rerank）：混合检索先多取一些候选，再用交叉编码器（cross-encoder）逐条给"问题 + 片段"打相关度分，取前几名。

为什么要这一步：BM25 和向量检索都是把问题和片段各自变成表示再比较，快，但粗；交叉编码器把问题和片段放在一起读，
判断准得多，只是慢，所以只用在少量候选上。评测里"任务记录和指令结论"这一组，向量检索拖累了混合检索
（混合 0.70，不如纯关键词 0.90），这正是重排序要解决的问题（结果见 docs/design.md 决策 24）。

模型：BAAI/bge-reranker-base（中文效果好，CPU 上跑，约 1 GB），用 fastembed 加载。开关（环境变量 OPC_RERANK）：
- auto（默认）：模型已经下载过才启用，不会因为装了 fastembed 就突然下载 1 GB
- local：启用，第一次用到时下载
- none：不用
模型放在 OPC_MODELS_DIR（默认数据目录的 models/）。出错时检索退回不重排的结果，不报错。
"""
import os
from pathlib import Path

DEFAULT_MODEL = "BAAI/bge-reranker-base"
MAX_CHARS = 600  # 每个候选送进模型的字数（模型一次最多读 512 个 token；评测里 600 字和 1000 字效果相当、快得多）


class Reranker:
    name = "none"

    def scores(self, query: str, texts: list[str]) -> list[float]:
        raise NotImplementedError


class LocalReranker(Reranker):
    def __init__(self, model: str = DEFAULT_MODEL, cache_dir: str | None = None):
        self.model_name = model
        self.name = f"local:{model}"
        self.cache_dir = cache_dir or models_dir()
        self._model = None

    def _load(self):
        if self._model is None:
            from fastembed.rerank.cross_encoder import TextCrossEncoder
            from .embedding import onnx_providers
            providers = onnx_providers()
            self._model = TextCrossEncoder(self.model_name, cache_dir=self.cache_dir,
                                           **({"providers": providers} if providers else {}))
        return self._model

    def scores(self, query, texts):
        return [float(s) for s in self._load().rerank(query, [t[:MAX_CHARS] for t in texts])]


def models_dir() -> str:
    return os.getenv("OPC_MODELS_DIR") or str(Path(os.getenv("OPC_HOME") or Path.home() / ".opc") / "models")


def downloaded(model: str = DEFAULT_MODEL, cache_dir: str | None = None) -> bool:
    """模型文件在不在本地（fastembed / Hugging Face 的缓存目录名是 models--组织--模型名）。"""
    root = Path(cache_dir or models_dir())
    return any(root.glob(f"models--*{model.split('/')[-1]}*"))


def default_reranker() -> Reranker | None:
    mode = (os.getenv("OPC_RERANK") or "auto").strip().lower()
    if mode == "none":
        return None
    try:
        import fastembed  # noqa: F401
    except ImportError:
        if mode == "local":
            raise RuntimeError("OPC_RERANK=local 需要先安装：pip install -r requirements-kb.txt") from None
        return None
    model = os.getenv("OPC_RERANK_MODEL") or DEFAULT_MODEL
    if mode == "auto" and not downloaded(model):
        return None
    return LocalReranker(model)
