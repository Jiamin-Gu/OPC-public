"""知识库检索效果的评测集，问题 → 标准答案所在的位置。

QUERIES（v0.4，20 题）：examples/todo-tool-with-coding-agent 的真实产出（PRD、设计、代码、测试）。
一半是口语化的提问（考察按意思检索），一半带代码标识符或固定术语（考察精确匹配）。
标准答案是 (文件, 起始行, 结束行)；检索结果只要和其中任意一个有行号重叠就算命中。

v0.21 加了两组（长期记忆用到的来源），在"混合知识库"上评测：两个项目的文件 + 一次真实运行的任务记录和
指令结论 + 一份公司资料（Word），干扰项比单个项目多得多：
- RECORD_QUERIES（10 题）：examples/real-pilot-v0.12（健身房团课预约）的任务记录和指令结论——以前做过什么、
  定过什么、CEO 怎么裁决的、同事问答里定下的口径、审查发现了什么
- LIBRARY_QUERIES（8 题）：LIBRARY_DOC 生成的 Word 资料，口语化提问，答案在某一节里
这两组的标准答案是 (来源, None, None)（这个来源的任何一块都算）或 (来源, "title", 标题里要有的字)。
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


GYM = "e0e169"  # examples/real-pilot-v0.12 那条指令的 id；任务 id 是 e0e169-4fff-t1 … t4
T = lambda n: f"task:{GYM}-4fff-t{n}"  # noqa: E731
D = f"directive:{GYM}"

RECORD_QUERIES = [
    ("CEO 对候补转正是怎么裁决的", [(T(1), None, None), (D, None, None)]),
    ("候补转正以后还要不要会员自己确认", [(T(1), None, None), (D, None, None)]),
    ("微信订阅消息能不能申请长期订阅", [(D, None, None)]),
    ("教练标记到场缺席默认怎么操作", [(D, None, None)]),
    ("技术方案审查指出了哪些问题", [(T(3), None, None)]),
    ("测试计划里用例的时间算错了吗", [(T(4), None, None)]),
    ("上一阶段最后的结论和还没修的问题", [(D, None, None)]),
    ("PRD 审查了几轮，最后通过了吗", [(T(1), None, None), (D, None, None)]),
    ("交互设计的审查有没有给出结论", [(T(2), None, None), (D, None, None)]),
    ("这条指令当时是怎么分工的", [(D, None, None)]),
]

LIBRARY_NAME = "运营规范.docx"
L = f"lib:{LIBRARY_NAME}"
LIBRARY_DOC = [  # (标题级别, 文字)；0 表示正文
    (1, "公司运营规范（2026 版）"),
    (0, "本规范适用于公司所有线上产品。产品、设计、研发和运营在方案中涉及以下内容时，必须遵守本规范；确需例外的，由 CEO 书面批准。"),
    (2, "一、金额与计价"),
    (0, "所有金额字段一律以“分”为单位的整数存储和传输，禁止使用浮点数。界面展示时再换算成元，保留两位小数。"),
    (0, "价格计算中的折扣、满减按“先满减、后折扣”的顺序执行，计算结果向下取整到分。"),
    (2, "二、优惠券"),
    (0, "优惠券有效期最长 90 天，过期自动作废，不予延期。"),
    (0, "同一订单最多使用一张优惠券，优惠券之间不可叠加使用；优惠券不可兑换现金，也不找零。"),
    (2, "三、会员积分"),
    (0, "会员每实付 1 元获得 1 积分，退款时扣回相应积分。积分有效期为 12 个月，扣减时按获得时间先到期的先扣。"),
    (0, "积分不可转让、不可兑换现金，只能兑换平台提供的权益。"),
    (2, "四、隐私与数据"),
    (0, "手机号在任何页面上只显示后四位，其余用星号代替。身份证号不得在前端展示。"),
    (0, "用户数据自注销之日起保存 3 年后删除；日志中不得记录明文密码和完整手机号。"),
    (2, "五、客服与退款"),
    (0, "支持 7 天无理由退款。退款原路返回，3 个工作日内到账；客服须在 24 小时内首次响应用户的工单。"),
    (2, "六、品牌视觉"),
    (0, "品牌主色为“海盐蓝”（#1F6FB2），辅助色为暖白（#FAF7F0）。Logo 四周的留白不少于 Logo 高度的一半，不得拉伸变形。"),
]
LIBRARY_QUERIES = [
    ("价格字段在数据库里用什么类型存", [(L, "title", "金额")]),
    ("优惠券最长能用多久", [(L, "title", "优惠券")]),
    ("两张券能一起用吗", [(L, "title", "优惠券")]),
    ("积分什么时候会过期", [(L, "title", "积分")]),
    ("页面上怎么显示用户手机号", [(L, "title", "隐私")]),
    ("用户注销以后数据留多久", [(L, "title", "隐私")]),
    ("退款几天能到账", [(L, "title", "退款")]),
    ("我们的品牌主色是什么颜色", [(L, "title", "品牌")]),
]


def write_library_docx(path) -> None:
    import docx
    d = docx.Document()
    for level, text in LIBRARY_DOC:
        if level:
            d.add_heading(text, level=level)
        else:
            d.add_paragraph(text)
    d.save(str(path))


def gym_state(root) -> dict:
    """examples/real-pilot-v0.12 整理成知识库同步要的状态：{tasks, channels}。导出的任务没有 id，按顺序补上。"""
    import json
    from pathlib import Path
    root = Path(root)
    summary = json.loads((root / "summary.json").read_text(encoding="utf-8"))
    messages = json.loads((root / "messages.json").read_text(encoding="utf-8"))
    tasks = {}
    for i, t in enumerate(summary["tasks"], 1):
        tid = f"{GYM}-4fff-t{i}"
        tasks[tid] = {**t, "id": tid, "directive_id": GYM}
    return {"tasks": tasks, "channels": {"all": messages["all"]}}


def hit(result: dict, targets) -> bool:
    for f, lo, hi in targets:
        if result["source"] != f:
            continue
        if lo is None or (lo == "title" and hi in (result.get("title") or "")):
            return True
        if isinstance(lo, int) and result["start"] <= hi and result["end"] >= lo:
            return True
    return False


def evaluate(kb, mode: str, k: int = 5, queries=None) -> dict:
    """返回 recall@k（标准答案出现在前 k 条里的比例）和 MRR（第一个命中的名次的倒数，取平均）。"""
    queries = QUERIES if queries is None else queries
    recall, mrr, misses = 0, 0.0, []
    for query, targets in queries:
        results = kb.search(query, k, mode)
        rank = next((i for i, r in enumerate(results, 1) if hit(r, targets)), None)
        if rank:
            recall += 1
            mrr += 1 / rank
        else:
            misses.append(query)
    n = len(queries)
    return {"recall": recall / n, "mrr": mrr / n, "misses": misses}
