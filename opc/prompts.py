"""角色提示词：一律用完整版（v0.18 起），后面接上"你做不到的事"（角色库的角色见 roles.py）。

- 完整版：开源角色库里对应角色的原文，去掉头信息和交付物模板（模板跟着任务走，在 task_templates/）
- 原文是写给人或别的工具用的，没有 OPC 里 AI 同事的能力边界（有的还写着"做用户访谈""看后台数据"）。
  简洁版里专门写过一节"你做不到的事"（做不到时用公开资料或标明的假设代替，绝不编造），接在完整版后面

以前可以选简洁版（为了省 token）。用户的判断：提示词差的几万 token 和审查轮数带来的成本比起来微不足道，
效果优先，一直用完整版。简洁版文件只用来取"你做不到的事"这一节；已经创建的公司里存着的版本设置读取时都当作完整版。

审查规范不分版本：用 OPC 自己的审查规范（review.concise.md：问题分级、只有必须修改的才退回、用具体场景推演找漏洞）。
它不是为了省 token 的精简版，而是按 OPC 的流程写的，实测比原文的审查提示词找出的问题更深（见 examples/review-rules-ab）。
"""
import re
from functools import lru_cache
from pathlib import Path

from . import roles

DIR = Path(__file__).parent / "role_prompts"
LEVELS = {"complete": "完整版", "concise": "简洁版"}  # 文件的版本（任务模板也按这个找，没有完整版的用简洁版）
# 以前可选的版本名：已经创建的公司里可能存着，读取时都当作完整版
LEGACY = {"full": "complete", "original": "complete", "brief": "complete", "minimal": "complete", "concise": "complete"}
DEFAULT_LEVEL = "complete"
BOUNDARY = "## 你做不到的事"


def normalize(level: str | None) -> str:
    """角色提示词一律用完整版。"""
    return DEFAULT_LEVEL


def is_valid(level: str) -> bool:
    return level in LEVELS or level in LEGACY


@lru_cache(maxsize=None)
def _read(name: str) -> str | None:
    p = DIR / f"{name}.md"
    return p.read_text(encoding="utf-8").strip() if p.is_file() else None


@lru_cache(maxsize=None)
def _read_path(path: Path) -> str | None:
    return path.read_text(encoding="utf-8").strip() if path.is_file() else None


def _source(role_id: str, level: str) -> str | None:
    path = roles.prompt_file(role_id, level)
    return _read(f"{role_id}.{level}") if path is None else _read_path(path)


def boundary(text: str | None) -> str:
    """取出"你做不到的事"这一节（到下一个二级标题为止）。"""
    m = re.search(rf"^{BOUNDARY}\s*$(.*?)(?=^## |\Z)", text or "", re.M | re.S)
    return f"{BOUNDARY}\n{m.group(1).strip()}" if m and m.group(1).strip() else ""


@lru_cache(maxsize=None)
def role_prompt(role_id: str, level: str | None = None) -> str:
    """完整版原文 + "你做不到的事"（从简洁版里取）。完整版缺失时退回简洁版。"""
    complete, concise = _source(role_id, "complete"), _source(role_id, "concise")
    text = complete or concise
    if text is None:
        raise FileNotFoundError(f"角色 {role_id} 没有提示词文件")
    extra = boundary(concise) if complete and BOUNDARY not in complete else ""
    text = f"{text}\n\n{extra}" if extra else text
    note = roles.OVERRIDES.get(role_id, {}).get("note")
    return f"{note}\n\n{text}" if note else text


def review_rules(level: str | None = None) -> str:
    return _read("review.concise")
