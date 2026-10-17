"""投入档位：控制一条指令最多花多少力气。和角色提示词的简洁版 / 完整版一样，创建公司时选，之后随时可以改。

只限制轮数和次数（审查几轮、每次执行几步、步数用完自动接着做几次、交付检查几次、问同事几次），不限制 token：
按 token 封顶会在做到一半时被截断，交出去不合格又要返工，成本反而更高。到了上限不会硬停，而是交给 CEO 裁决
（接受现状 / 再给一轮 / 放弃），和之前一样。
"""
from .config import MAX_REVIEW_ROUNDS, MAX_ROLE_STEPS

DEFAULT = "standard"

# 名字 -> (显示名, 一句话说明, 限制)
LEVELS = {
    "lean": ("节省", "审查 1 轮，只审有下游依赖的任务；步数用完不自动接着做。适合草稿、调研、内部讨论",
             {"review_rounds": 1, "steps": MAX_ROLE_STEPS, "extensions": 0, "rejects": 2, "asks": 1,
              "review_policy": "lean"}),
    "standard": ("标准", "审查 2 轮，重要的产出安排审查；步数用完自动接着做 1 次。大多数情况用这个",
                 {"review_rounds": MAX_REVIEW_ROUNDS, "steps": MAX_ROLE_STEPS, "extensions": 1, "rejects": 3, "asks": 3,
                  "review_policy": "standard"}),
    "thorough": ("充分", "审查 3 轮，每个任务都安排审查；步数更多，用完自动接着做 2 次。适合要交给别人的正式产出",
                 {"review_rounds": 3, "steps": max(MAX_ROLE_STEPS, 16), "extensions": 2, "rejects": 3, "asks": 5,
                  "review_policy": "thorough"}),
}

# 规划助手安排审查人的规则（标准档就是原来的规则，写在 planner.PLAN_PROMPT 里）
REVIEW_POLICY = {
    "lean": "这家公司选了「节省」档：只给有其他任务依赖它的产出安排审查人，其他任务 reviewer 留空。",
    "standard": "",
    "thorough": "这家公司选了「充分」档：每个任务都安排一位审查人（小任务除外）。",
}


def normalize(level: str | None) -> str:
    return level if level in LEVELS else DEFAULT


def limits(level: str | None) -> dict:
    return dict(LEVELS[normalize(level)][2])


def public() -> list[dict]:
    """给界面的档位清单。"""
    return [{"id": k, "name": n, "desc": d, **{x: v for x, v in lim.items() if x != "review_policy"}}
            for k, (n, d, lim) in LEVELS.items()]
