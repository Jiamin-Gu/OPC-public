"""知识库检索效果的评测集：在 examples/todo-tool-with-coding-agent 的真实产出上，问题 → 标准答案所在的位置。

标准答案是 (文件, 起始行, 结束行)；检索结果只要和其中任意一个有行号重叠就算命中。
一半是口语化的提问（考察按意思检索），一半带代码标识符或固定术语（考察精确匹配）。
"""
QUERIES = [
    # ---- 口语化提问
    ("删除一个不存在的编号会怎样", [("docs/PRD.md", 376, 411), ("code/DESIGN.md", 256, 264), ("code/README.md", 136, 153)]),
    ("数据文件默认放在哪个目录", [("docs/PRD.md", 135, 143), ("code/README.md", 40, 73)]),
    ("存储的 JSON 里有哪些字段", [("docs/PRD.md", 144, 178), ("code/DESIGN.md", 62, 76)]),
    ("怎么避免写到一半程序崩溃导致数据丢失", [("docs/PRD.md", 179, 192), ("code/DESIGN.md", 206, 222)]),
    ("各个退出码分别代表什么", [("code/DESIGN.md", 223, 232), ("code/README.md", 179, 188), ("docs/PRD.md", 113, 132)]),
    ("列表每一行输出的格式", [("docs/PRD.md", 210, 227), ("code/README.md", 99, 124)]),
    ("这一版明确不做哪些功能", [("docs/PRD.md", 504, 526)]),
    ("有哪些事情需要老板拍板", [("docs/PRD.md", 527, 549), ("code/DESIGN.md", 285, 293)]),
    ("把已经完成的事项再标记一次完成会怎样", [("docs/PRD.md", 337, 375), ("code/README.md", 125, 135)]),
    ("数据文件内容坏了程序怎么处理", [("docs/PRD.md", 434, 447), ("code/DESIGN.md", 206, 222), ("code/todo_cli/storage.py", 75, 145)]),
    ("为什么不用第三方命令行库", [("code/DESIGN.md", 44, 59)]),
    ("怎样运行测试", [("code/README.md", 189, 212), ("docs/PRD.md", 473, 503)]),
    # ---- 代码标识符、固定术语
    # 定义处和抛出它的校验函数都算正确答案（标注时漏了后者，看了检索结果后补上）
    ("InvalidFileError", [("code/todo_cli/errors.py", 59, 70), ("code/DESIGN.md", 84, 95), ("code/DESIGN.md", 112, 162),
                          ("code/todo_cli/storage.py", 75, 145)]),
    ("sanitize_text 做了什么", [("code/todo_cli/storage.py", 56, 66), ("code/DESIGN.md", 96, 99)]),
    ("TODO_FILE 环境变量", [("code/README.md", 40, 73), ("docs/PRD.md", 135, 143), ("code/todo_cli/storage.py", 27, 43)]),
    ("resolve_path 的优先级", [("code/todo_cli/storage.py", 27, 43), ("code/DESIGN.md", 112, 162), ("docs/PRD.md", 135, 143)]),
    ("TodoStore 类有哪些方法", [("code/todo_cli/models.py", 1, 400)]),
    ("_cmd_delete", [("code/todo_cli/cli.py", 183, 200)]),
    ("add_subparsers 子命令", [("code/todo_cli/cli.py", 53, 144), ("code/DESIGN.md", 44, 59)]),
    ("WriteFileError 什么时候抛出", [("code/todo_cli/errors.py", 71, 90), ("code/todo_cli/storage.py", 146, 200), ("code/DESIGN.md", 206, 232)]),
]


def hit(result: dict, targets) -> bool:
    return any(result["source"] == f and result["start"] <= hi and result["end"] >= lo for f, lo, hi in targets)


def evaluate(kb, mode: str, k: int = 5) -> dict:
    """返回 recall@k（标准答案出现在前 k 条里的比例）和 MRR（第一个命中的名次的倒数，取平均）。"""
    recall, mrr, misses = 0, 0.0, []
    for query, targets in QUERIES:
        results = kb.search(query, k, mode)
        rank = next((i for i, r in enumerate(results, 1) if hit(r, targets)), None)
        if rank:
            recall += 1
            mrr += 1 / rank
        else:
            misses.append(query)
    n = len(QUERIES)
    return {"recall": recall / n, "mrr": mrr / n, "misses": misses}
