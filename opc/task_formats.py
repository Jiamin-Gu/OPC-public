"""任务的交付格式：交付物模板库，以及根据任务的交付要求拼出的说明。

提示词分三层，每次执行时组合在一起，互不混放：
- 角色（系统提示词，见 agents.system_prompt）：这个人是谁、懂什么、怎么做事、做不到什么。不含任何产出格式
- 任务类型（执行 / 审查 / 私聊 / 回答同事）：这类工作怎么交付，例如执行任务做完要调用 finish_task 提交
- 任务（任务说明，见 jobs.task_brief）：这一次要交付哪些文件、每个文件的格式（模板）、必须有的章节、
  必须写到的内容、最低字数和验收标准。提交时系统按这些逐条机械检查（见 checks.py），所以说明里写的就是检查的

模板放在 task_templates/<名字>.<版本>.md，版本和角色提示词一样分简洁版和完整版（完整版来自开源角色库的原文），
按负责人的提示词版本选用，没有这个版本时用另一个。规划助手为每个产出文件选模板。

角色库的角色从原文里拆出来的交付物模板放在 task_templates/library/<角色>.md（只有一个版本），名字写成
"role:<角色 id>"；公司里有这个角色时才出现在规划助手的清单里。
"""
import re
from functools import lru_cache
from pathlib import Path

from . import prompts
from .roles import ROLES

DIR = Path(__file__).parent / "task_templates"

# 名字 -> (标题, 一句话说明, 默认路径)
TEMPLATES = {
    "prd": ("需求文档（PRD）", "问题、目标、不做的范围、用户故事与验收标准、方案、风险", "docs/PRD.md"),
    "opportunity": ("机会评估", "要不要做一件事：为什么是现在、依据、收益成本、备选方案、结论", "docs/opportunity.md"),
    "roadmap": ("产品路线图", "现在 / 接下来 / 以后，不做的事", "docs/roadmap.md"),
    "gtm": ("上市简报（GTM）", "发布什么、目标受众、核心价值、发布清单、成功标准、回滚", "docs/gtm.md"),
    "sprint": ("迭代健康快照", "承诺与交付、阻塞、范围变更、风险", "docs/sprint.md"),
    "design": ("设计说明", "用户与核心任务、信息架构、用户流程、设计规范、关键页面、无障碍", "docs/design.md"),
    "design-system": ("设计系统", "设计令牌、组件库架构、响应式框架", "docs/design-system.md"),
    "tech-design": ("技术方案", "需求映射、架构、选型、数据模型、接口、风险、决策记录、里程碑", "docs/tech-design.md"),
    "adr": ("架构决策记录（ADR）", "单个技术决策：背景、决策、备选方案、影响", "docs/adr-001.md"),
    "test-plan": ("测试计划", "范围、重点与风险、测试用例、评审发现的问题、运行证据、交付结论", "docs/test-plan.md"),
    "integration-report": ("集成测试报告", "基于证据的系统集成检查与交付就绪评估", "docs/integration-report.md"),
    "marketing-plan": ("市场方案", "定位、竞品、用户画像、卖点、渠道、节奏、增长实验、文案、合规", "docs/marketing-plan.md"),
    "growth-experiments": ("增长实验看板", "实验的假设、指标、结果和待排期实验", "docs/growth-experiments.md"),
    "operations": ("运营方案", "冷启动、用户生命周期、社群、客服、指标、反馈闭环", "docs/operations.md"),
    "private-domain": ("私域运营方案", "企业微信 SCRM 配置、社群 SOP、生命周期自动化、转化漏斗", "docs/private-domain.md"),
}

LENGTH_RULE = ("写给同事和 CEO 用的，不是越长越好：只写结论、规则、依据和下游需要的细节，不要重复、不要铺陈背景，"
               "能用一句话说清的不写一段。建议篇幅是参考，确实需要更多内容才能完成任务时可以超过")
FORMAT_RULE = "Markdown 的代码块（```）要成对闭合；JSON、YAML 文件要能解析；Python 文件要能编译"


LIBRARY = DIR / "library"
ROLE_PREFIX = "role:"


def _role_template_id(name: str) -> str:
    return name[len(ROLE_PREFIX):] if name.startswith(ROLE_PREFIX) else ""


@lru_cache(maxsize=None)
def _library(role_id: str) -> str | None:
    p = LIBRARY / f"{role_id}.md"
    return p.read_text(encoding="utf-8").strip() if role_id and p.is_file() else None


def role_templates(role_ids) -> dict[str, tuple[str, str]]:
    """这些角色自带的交付物模板：名字 -> (标题, 包含哪些部分)。"""
    out = {}
    for rid in role_ids:
        body = _library(rid)
        if body and rid in ROLES:
            heads = [h.strip() for h in re.findall(r"^###\s+(.+)$", body, re.M)][:6]
            out[ROLE_PREFIX + rid] = (f"{ROLES[rid].name}的交付物模板", "、".join(heads) or "原文附带的交付物格式")
    return out


def is_known(name: str, role_ids=()) -> bool:
    """规划助手选的模板是否可用：通用模板，或者公司里某个角色自带的模板。"""
    rid = _role_template_id(name)
    return name in TEMPLATES or (rid in role_ids and _library(rid) is not None)


def title(name: str) -> str:
    if name in TEMPLATES:
        return TEMPLATES[name][0]
    rid = _role_template_id(name)
    return f"{ROLES[rid].name}的交付物模板" if rid in ROLES else name


def catalog(role_ids=()) -> str:
    """给规划助手看的模板清单：通用模板 + 公司里各角色自带的模板。"""
    lines = [f"- {name}：{t}（{desc}）" for name, (t, desc, _) in TEMPLATES.items()]
    lines += [f"- {name}：{t}（{desc}）" for name, (t, desc) in role_templates(role_ids).items()]
    return "\n".join(lines)


@lru_cache(maxsize=None)
def _read(name: str, level: str) -> str | None:
    p = DIR / f"{name}.{level}.md"
    return p.read_text(encoding="utf-8").strip() if p.is_file() else None


TEMPLATE_LEVEL = "concise"  # OPC 改写的中文模板（带"依据：公开资料 / 标明的假设"）；没有的用原文模板


def template(name: str, level: str | None = None) -> str:
    """模板正文：优先用 OPC 改写的版本，没有就用原文的版本；没有这个模板返回空字符串。

    角色提示词从 v0.18 起一律用完整版（原文），但模板不跟着换：模板是任务的格式，原文模板中英混杂
    （Problem Statement、Stakeholders），也没有 OPC 要求的"注明依据、标明假设"。之前所有的真实运行都用改写的模板。
    level 参数保留给想对比原文模板的调用（"complete"）。"""
    if _role_template_id(name):
        return _library(_role_template_id(name)) or ""
    if name not in TEMPLATES:
        return ""
    first = level if level in prompts.LEVELS else TEMPLATE_LEVEL
    other = next(lv for lv in prompts.LEVELS if lv != first)
    return _read(name, first) or _read(name, other) or ""


def requirements_brief(task: dict, level: str | None = None) -> str:
    """任务说明里的"交付要求"：产出文件、格式模板、必须有的章节、必须写到的内容、最低字数、验收标准。
    这些就是提交时系统机械检查的内容（见 checks.py），说明和检查用同一份数据，不会对不上。"""
    parts = []
    deliverables = task.get("deliverables") or []
    if deliverables:
        parts += ["", "# 交付要求（提交时系统会逐条自动检查，不符合会退回给你改）"]
        for d in deliverables:
            reqs = []
            if d.get("sections"):
                reqs.append("必须有这些章节（用 # 标题写出，标题里要包含这些词）：" + "、".join(d["sections"]))
            if d.get("must_mention"):
                reqs.append("必须写到：" + "、".join(d["must_mention"]))
            if d.get("min_chars") and d.get("max_chars"):
                reqs.append(f"至少 {d['min_chars']} 字，建议不超过 {d['max_chars']} 字")
            elif d.get("min_chars"):
                reqs.append(f"至少 {d['min_chars']} 字")
            elif d.get("max_chars"):
                reqs.append(f"建议不超过 {d['max_chars']} 字")
            parts.append(f"- 产出文件 `{d['path']}`" + ("：" + "；".join(reqs) if reqs else ""))
        parts.append(f"- 格式：{FORMAT_RULE}")
        if any(d.get("max_chars") for d in deliverables):
            parts.append(f"- 篇幅：{LENGTH_RULE}")
        for d in deliverables:
            body = template(d.get("template") or "", level)
            if body:
                parts += ["", f"## `{d['path']}` 的格式参考：{title(d['template'])}",
                          "（参考这个结构写；上面列出的章节必须有，模板里不适用的部分可以省略并说明原因）",
                          "````markdown", body, "````"]
    if task.get("acceptance"):
        parts += ["", "# 验收标准（审查人会逐条检查）"] + [f"{i}. {a}" for i, a in enumerate(task["acceptance"], 1)]
    return "\n".join(parts)
