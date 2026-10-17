"""知识库命令行：

    python -m opc.knowledge sync   公司id [--home 数据目录]
    python -m opc.knowledge search 公司id "查询" [-k 8] [--mode hybrid|keyword|vector] [--home 数据目录]
    python -m opc.knowledge stats  公司id [--home 数据目录]
"""
import argparse
import sqlite3
import sys

from .. import use_utf8_output
from ..company import CompanyStore
from .company import open_kb, sync_company


def load_state(store: CompanyStore, company_id: str) -> dict:
    """任务看板在调度图的检查点里，消息在 Store 里（见 messages.py）。"""
    from langgraph.checkpoint.sqlite import SqliteSaver

    from ..graph import build_graph
    from ..messages import MessageLog, migrate_legacy, open_sqlite_store
    checkpointer = SqliteSaver(sqlite3.connect(store.checkpoint_path, check_same_thread=False))
    messages = open_sqlite_store(store.checkpoint_path)
    migrate_legacy(checkpointer, messages, company_id)
    graph = build_graph(checkpointer, messages)
    state = graph.get_state({"configurable": {"thread_id": company_id}}).values or {}
    return {**state, "channels": MessageLog(messages, company_id).all()}


def main() -> None:
    use_utf8_output()
    parser = argparse.ArgumentParser(description="OPC 公司知识库")
    parser.add_argument("command", choices=["sync", "search", "stats"])
    parser.add_argument("company_id")
    parser.add_argument("query", nargs="?", default="")
    parser.add_argument("-k", type=int, default=8)
    parser.add_argument("--mode", default="hybrid", choices=["hybrid", "keyword", "vector"])
    parser.add_argument("--home", help="数据目录，默认 ~/.opc")
    args = parser.parse_args()

    store = CompanyStore(args.home)
    company = store.load(args.company_id)
    if not company:
        sys.exit(f"找不到公司：{args.company_id}")
    kb = open_kb(company)
    if args.command == "sync":
        result = sync_company(kb, company, load_state(store, args.company_id))
        for part, s in result.items():
            print(f"{part}：新增 {s['added']}，更新 {s['updated']}，删除 {s['removed']}，未变 {s['unchanged']}，"
                  f"生成向量 {s['embedded']} 块")
    elif args.command == "stats":
        pass
    else:
        if not args.query:
            sys.exit("请给出查询内容")
        for i, r in enumerate(kb.search(args.query, args.k, args.mode), 1):
            ranks = f"关键词第 {r['keyword_rank'] or '-'} 名，向量第 {r['vector_rank'] or '-'} 名"
            print(f"\n[{i}] {r['source']}:{r['start']}-{r['end']}  {r['title']}  （{ranks}）")
            print("    " + r["snippet"].replace("\n", "\n    "))
    s = kb.stats()
    print(f"\n知识库：{s['chunks']} 块，来源 {s['sources']}，向量 {s['vectors']} 块（{s['embedder'] or '未启用向量检索'}）"
          + (f"\n向量检索出错：{s['vector_error']}" if s["vector_error"] else ""))


if __name__ == "__main__":
    main()
