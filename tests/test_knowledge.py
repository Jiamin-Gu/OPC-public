"""知识库：切块、分词、BM25、融合、增量同步、任务记录、检索模式、网页接口；以及用真实向量模型在真实产出上的检索效果。"""
import hashlib
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from opc.knowledge import KnowledgeBase, task_documents
from opc.knowledge.base import query_weights
from opc.knowledge.chunking import chunk_file
from opc.knowledge.search import BM25, rrf, tokenize

from fakes import World, make_store

EXAMPLE = Path(__file__).resolve().parent.parent / "examples" / "todo-tool-with-coding-agent" / "files"


class FakeEmbedder:
    """按字符二元组做哈希的假向量：意思相近（共享词）的文字向量相近，测试不需要真实模型。"""
    name = "fake:v1"

    def _vec(self, text):
        v = [0.0] * 64
        for t in tokenize(text):
            v[int(hashlib.md5(t.encode()).hexdigest(), 16) % 64] += 1.0
        return v

    def embed_documents(self, texts):
        return [self._vec(t) for t in texts]

    def embed_query(self, text):
        return self._vec(text)


# ---------------------------------------------------------------- 切块与分词

def test_chunk_markdown_by_heading_with_path():
    text = "# 标题\n介绍\n\n## 1. 金额\n" + "金额规则。\n" * 50 + "## 2. 报表\n```\n# 不是标题\n```\n月报。"
    chunks = chunk_file("docs/spec.md", text)
    titles = [c["title"] for c in chunks]
    assert "标题 / 1. 金额" in titles and "标题 / 2. 报表" in titles
    report = next(c for c in chunks if c["title"] == "标题 / 2. 报表")
    assert "# 不是标题" in report["text"] and report["end"] == text.count("\n") + 1
    lines = text.splitlines()
    for c in chunks:  # 行号能对回原文
        assert c["text"] == "\n".join(lines[c["start"] - 1:c["end"]])


def test_chunk_python_by_definition_and_split_long():
    body = "\n".join(f"    x{i} = {i}" for i in range(400))
    src = f'"""模块说明。"""\nimport os\n\n\ndef small():\n    return 1\n\n\nclass Big:\n{body}\n'
    chunks = chunk_file("pkg/mod.py", src)
    kinds = [c["kind"] for c in chunks]
    assert "class" in kinds and all(len(c["text"]) <= 1500 for c in chunks)  # 太长的类按行切开
    assert chunks[0]["start"] == 1 and "模块说明" in chunks[0]["text"]
    big = [c for c in chunks if c["title"] == "class Big"]
    assert len(big) > 1 and big[0]["start"] < big[1]["start"]


def test_tokenize_identifiers_and_chinese():
    assert {"low_stock", "low", "stock"} <= set(tokenize("low_stock()"))
    assert {"parsemoney", "parse", "money"} <= set(tokenize("parseMoney"))
    assert tokenize("退款金额") == ["退款", "款金", "金额"] and tokenize("钱") == ["钱"]


def test_bm25_and_rrf():
    bm = BM25()
    bm.add(1, "订单取消后退款")
    bm.add(2, "优惠券到期当天可用")
    bm.add(3, "退款 退款 金额 等于 付款")
    assert [d for d, _ in bm.search("退款", 5)] == [3, 1]
    bm.remove(3)
    assert [d for d, _ in bm.search("退款", 5)] == [1]
    scores = rrf([[1, 2, 3], [3, 1]], weights=[1.0, 1.0])
    assert max(scores, key=scores.get) == 1
    assert query_weights("InvalidFileError 在哪") == (1.0, 0.5) and query_weights("退款怎么处理") == (0.7, 1.0)


# ---------------------------------------------------------------- 同步与检索

def make_files(root: Path):
    (root / "docs").mkdir(parents=True)
    (root / "docs" / "PRD.md").write_text("# PRD\n## 退款\n取消订单时退回全部付款金额。\n## 优惠券\n到期当天仍可使用。\n",
                                          encoding="utf-8")
    (root / "code").mkdir()
    (root / "code" / "orders.py").write_text('"""订单。"""\n\n\ndef cancel_order(order_id):\n    """取消订单并退款。"""\n    return refund(order_id)\n',
                                             encoding="utf-8")
    (root / "code" / "logo.png").write_bytes(b"\x89PNG\0\0binary")


def test_incremental_sync_and_search(tmp_path):
    files = tmp_path / "files"
    make_files(files)
    kb = KnowledgeBase(tmp_path / "kb", embedder=FakeEmbedder())
    s = kb.sync_files(files)
    assert (s["added"], s["unchanged"]) == (2, 0) and s["embedded"] == kb.stats()["chunks"] > 0  # 二进制文件跳过
    assert kb.sync_files(files)["unchanged"] == 2  # 没变的文件不重建
    hit = kb.search("cancel_order", 3, "keyword")[0]
    # 模块开头太短，和函数合并成一块；类型取内容多的部分（函数）
    assert hit["source"] == "code/orders.py" and hit["kind"] == "function" and hit["start"] <= 4 <= hit["end"]
    assert kb.search("退款", 3, "hybrid")[0]["source"] in ("docs/PRD.md", "code/orders.py")
    assert kb.search("退款", 3, "vector") and kb.search("  ", 3) == []
    with pytest.raises(ValueError):
        kb.search("x", 3, "bogus")

    (files / "docs" / "PRD.md").write_text("# PRD\n## 发票\n开具电子发票。\n", encoding="utf-8")
    (files / "code" / "orders.py").unlink()
    s = kb.sync_files(files)
    assert (s["updated"], s["removed"]) == (1, 1)
    assert all(r["source"] != "code/orders.py" for r in kb.search("退款 订单", 5, "keyword"))
    assert kb.search("发票", 3, "keyword")[0]["source"] == "docs/PRD.md"

    count = kb.stats()["chunks"]
    kb.close()
    again = KnowledgeBase(tmp_path / "kb", embedder=FakeEmbedder())  # 重新打开：从数据库恢复索引和向量
    assert again.stats()["chunks"] == count and again.stats()["vectors"] == count
    assert again.search("发票", 3, "vector")


def test_switching_embedder_invalidates_vectors(tmp_path):
    files = tmp_path / "files"
    make_files(files)
    kb = KnowledgeBase(tmp_path / "kb", embedder=FakeEmbedder())
    kb.sync_files(files)
    kb.close()

    class Other(FakeEmbedder):
        name = "fake:v2"
    kb2 = KnowledgeBase(tmp_path / "kb", embedder=Other())
    assert kb2.stats()["vectors"] == 0  # 旧向量和新模型不兼容，全部作废
    assert kb2.sync_files(files)["embedded"] == kb2.stats()["chunks"]


def test_without_embedder_or_broken_embedder_falls_back_to_keyword(tmp_path):
    files = tmp_path / "files"
    make_files(files)
    plain = KnowledgeBase(tmp_path / "kb1", embedder=None)
    plain.sync_files(files)
    assert plain.search("退款", 3, "hybrid") and plain.search("退款", 3, "vector") == []

    class Broken(FakeEmbedder):
        name = "broken"

        def embed_documents(self, texts):
            raise RuntimeError("模型下载失败")
    kb = KnowledgeBase(tmp_path / "kb2", embedder=Broken())
    kb.sync_files(files)
    assert "模型下载失败" in kb.stats()["vector_error"] and kb.search("退款", 3, "hybrid")


def test_task_documents_and_prefix_sync(tmp_path):
    state = {"channels": {"all": [{"sender": "ceo", "directive_id": "d1", "text": "做记账 App"}]},
             "tasks": {"t1": {"title": "写代码", "assignee": "engineer", "status": "done", "directive_id": "d1",
                              "detail": "实现记账", "result": "已完成，金额用 Decimal", "files": ["code/a.py"],
                              "reviews": [{"round": 1, "verdict": "changes", "summary": "缺测试",
                                           "issues": [{"text": "没有测负数金额"}]}], "ceo_note": "先这样"},
                       "t2": {"title": "取消的", "assignee": "pm", "status": "cancelled", "detail": "", "result": ""}}}
    docs = task_documents(state)
    assert list(docs) == ["task:t1"]
    title, text = docs["task:t1"]
    assert "工程师" in title and "做记账 App" in text and "没有测负数金额" in text and "先这样" in text
    kb = KnowledgeBase(tmp_path / "kb", embedder=None)
    assert kb.sync_items("task:", docs)["added"] == 1
    assert kb.search("负数金额", 3, "keyword")[0]["source"] == "task:t1"
    assert kb.sync_items("task:", {})["removed"] == 1 and kb.stats()["chunks"] == 0


def test_web_search_endpoint(tmp_path, monkeypatch):
    monkeypatch.setenv("OPC_EMBED_BACKEND", "none")  # 网页测试不加载真实模型
    from opc.web.runtime import Hub
    from opc.web.server import create_app
    world = World()
    hub = Hub(make_store(tmp_path, world))
    client = TestClient(create_app(hub))
    assert client.get("/api/kb/search", params={"q": "x"}).status_code == 404  # 没有打开的公司
    cid = client.post("/api/companies", json={"name": "知识库测试", "roles": [{"id": "pm"}]}).json()["id"]
    assert hub.open_company(cid) is None
    hub.company.write_file("docs/PRD.md", "# PRD\n## 退款\n取消订单时退回全部付款。\n", author="pm")
    data = client.get("/api/kb/search", params={"q": "退款", "mode": "hybrid"}).json()
    assert data["results"][0]["source"] == "docs/PRD.md" and data["stats"]["embedder"] is None
    assert data["synced"]["files"]["added"] == 1
    assert client.get("/api/kb/search", params={"q": "x", "mode": "bad"}).status_code == 400
    assert client.delete(f"/api/companies/{cid}").status_code == 200  # 知识库的数据库先关闭，目录能删掉


# ---------------------------------------------------------------- 真实模型、真实数据上的检索效果

def real_embedder():
    pytest.importorskip("fastembed")
    from opc.knowledge.embedding import LocalEmbedder
    emb = LocalEmbedder()
    cache = Path(emb.cache_dir)
    if not any(cache.glob("models--*bge-small-zh*")):
        pytest.skip("本地向量模型还没下载（~/.opc/models），跳过真实效果测试")
    return emb


def test_retrieval_quality_on_real_output(tmp_path):
    """在真实的产出（PRD、技术方案、代码、测试）上，用 20 个带标准答案的问题比较三种检索方式。"""
    from kb_queries import evaluate
    kb = KnowledgeBase(tmp_path / "kb", embedder=real_embedder())
    kb.sync_files(EXAMPLE)
    keyword, vector, hybrid = (evaluate(kb, m, 5) for m in ("keyword", "vector", "hybrid"))
    assert hybrid["recall"] >= 0.9 and hybrid["recall"] >= max(keyword["recall"], vector["recall"])
    assert hybrid["mrr"] >= max(keyword["mrr"], vector["mrr"])


def test_retrieval_quality_on_mixed_knowledge_base(tmp_path):
    """v0.21 的混合知识库：两个项目的文件 + 一次真实运行的任务记录和指令结论 + 一份 Word 公司资料，38 题。
    记录在这里：全部 38 题 recall@5 关键词 0.84 / 向量 0.68 / 混合 0.95，MRR 0.69 / 0.56 / 0.74（见 docs/design.md 决策 24）。"""
    import kb_queries as Q
    from opc.knowledge.company import directive_documents
    kb = KnowledgeBase(tmp_path / "kb", embedder=real_embedder())
    root = EXAMPLE.parent.parent
    lib = tmp_path / "lib"
    lib.mkdir()
    Q.write_library_docx(lib / Q.LIBRARY_NAME)
    kb.sync_files(EXAMPLE)
    kb.sync_files(root / "real-pilot-v0.12" / "files", prefix="gym/", kind="gym")
    state = Q.gym_state(root / "real-pilot-v0.12")
    kb.sync_items("task:", task_documents(state))
    kb.sync_items("directive:", directive_documents(state))
    kb.sync_files(lib, prefix="lib:", kind="lib", documents=True)
    queries = Q.QUERIES + Q.RECORD_QUERIES + Q.LIBRARY_QUERIES
    keyword, vector, hybrid = (Q.evaluate(kb, m, 5, queries) for m in ("keyword", "vector", "hybrid"))
    assert hybrid["recall"] >= 0.9 and hybrid["recall"] >= max(keyword["recall"], vector["recall"])
    assert hybrid["mrr"] >= max(keyword["mrr"], vector["mrr"])
    assert Q.evaluate(kb, "hybrid", 5, Q.LIBRARY_QUERIES)["recall"] >= 0.85  # 资料的小节合并后标题不能丢（v0.21 修过）


def test_rerank_reorders_candidates_only_when_asked(tmp_path, monkeypatch):
    """重排序：从混合检索的候选里重新排；默认不重排（给模型的检索），出错时退回原来的顺序；auto 只用已经下载的模型。"""
    from opc.knowledge import rerank

    class FakeReranker(rerank.Reranker):
        name = "fake"

        def __init__(self, broken=False):
            self.broken, self.calls = broken, 0

        def scores(self, query, texts):
            self.calls += 1
            if self.broken:
                raise RuntimeError("模型坏了")
            return [10.0 if "到账" in t else 0.0 for t in texts]

    root = tmp_path / "files"
    root.mkdir()
    (root / "a.md").write_text("# 退款\n\n退款申请提交后由客服审核。\n", encoding="utf-8")
    (root / "b.md").write_text("# 到账时间\n\n审核通过后 3 个工作日到账，退款原路返回。\n", encoding="utf-8")
    fake = FakeReranker()
    kb = KnowledgeBase(tmp_path / "kb", embedder=None, reranker=fake)
    kb.sync_files(root)
    plain = kb.search("退款", 2)
    assert fake.calls == 0 and "rerank_score" not in plain[0]                     # 默认不重排
    ranked = kb.search("退款", 2, rerank=True)
    assert ranked[0]["source"] == "b.md" and ranked[0]["rerank_score"] == 10.0 and kb.stats()["reranker"] == "fake"
    broken = KnowledgeBase(tmp_path / "kb2", embedder=None, reranker=FakeReranker(broken=True))
    broken.sync_files(root)
    assert [r["source"] for r in broken.search("退款", 2, rerank=True)] == [r["source"] for r in plain]
    assert "模型坏了" in broken.stats()["rerank_error"]

    import sys
    import types
    monkeypatch.setitem(sys.modules, "fastembed", types.ModuleType("fastembed"))  # 不依赖本机装没装 fastembed（CI 上没装）
    monkeypatch.setenv("OPC_MODELS_DIR", str(tmp_path / "models"))
    monkeypatch.setenv("OPC_RERANK", "auto")
    assert rerank.default_reranker() is None                                       # 没下载过：auto 不启用
    (tmp_path / "models" / "models--BAAI--bge-reranker-base").mkdir(parents=True)
    assert isinstance(rerank.default_reranker(), rerank.LocalReranker)
    monkeypatch.setenv("OPC_RERANK", "none")
    assert rerank.default_reranker() is None
