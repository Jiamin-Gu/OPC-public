"""角色。每个角色 = 图中的一类节点 + 一个独立的 agent（自己的提示词、上下文和模型）。

- 6 个预设角色：逐个手工改写的提示词（role_prompts/）
- 角色库：从开源角色库 agency-agents-zh 导入的约 270 个角色，按部门分组（role_library/，见 scripts/import_role_library.py）
"""
import json
from dataclasses import asdict, dataclass
from pathlib import Path

LIBRARY = Path(__file__).parent / "role_library"


@dataclass(frozen=True)
class Role:
    id: str
    name: str
    icon: str
    tagline: str        # 一句话介绍，显示在界面上
    duties: str         # 职责与产出规范，写进这个角色的系统提示词
    default_file: str   # 典型产出文件
    can_code: bool = False
    department: str = ""        # 角色库的部门（预设角色为空）
    department_name: str = ""
    can_test: bool = False      # 能运行测试（run_command，只运行不改代码）：测试角色。会写代码的角色用编码 agent 运行


PRESET_ROLES: list[Role] = [
    Role("pm", "产品经理", "📋", "梳理需求、拆分功能、写 PRD",
         "负责把 CEO 的想法变成清晰的需求：目标用户与使用场景、要解决的问题、核心功能与优先级（P0/P1/P2）、"
         "用户故事、验收标准，以及明确不做的范围。PRD 要具体到工程师和设计师能直接开工。",
         "docs/PRD.md"),
    Role("engineer", "工程师", "💻", "技术方案与代码实现",
         "负责技术实现：先写技术方案（架构、技术选型及理由、数据模型、接口设计、关键难点、开发里程碑），"
         "需要时在 code/ 目录下写代码。代码要完整、可以直接运行，附上 code/README.md 说明如何运行；不要写省略号或伪代码。",
         "docs/tech-design.md", can_code=True),
    Role("designer", "设计师", "🎨", "交互流程与界面设计",
         "负责用户体验：信息架构、主要用户流程、每个关键页面的布局与交互说明（可以用文字和 ASCII 线框图），"
         "以及视觉风格指南（色彩、字体、组件风格）。设计要和 PRD 的功能对应。",
         "docs/design.md"),
    Role("qa", "测试", "🧪", "测试计划、用例与风险评审",
         "负责质量：根据 PRD 的验收标准写测试计划和测试用例（含正常流程、边界情况、异常情况），"
         "并评审其他同事的产出（需求是否矛盾、方案是否有漏洞、代码是否有明显 bug），列出风险和改进建议。",
         "docs/test-plan.md", can_test=True),
    Role("marketing", "市场", "📣", "市场定位、竞品与推广",
         "负责市场：目标市场与用户画像、主要竞品及差异化、产品定位与核心卖点、上线推广方案（渠道、节奏、预算思路）、"
         "以及可以直接使用的宣传文案（标语、介绍、社交媒体帖子）。",
         "docs/marketing-plan.md"),
    Role("ops", "运营", "🤝", "用户增长、留存与客服",
         "负责运营：冷启动和用户增长策略、留存与活跃手段、社群运营方案、关键数据指标及目标，"
         "以及客服 FAQ 和常见问题的回复话术。",
         "docs/operations.md"),
]



# 角色库里需要调整的角色（重新导入角色库也会保留）。用户本人是 CEO：库里的 CEO 改成给 CEO 出主意的顾问
OVERRIDES = {
    "chief-executive-officer": {"name": "CEO 顾问", "tagline": "站在 CEO 的高度想问题：方向取舍、资源配置、节奏和高管分工，给你出主意，由你拍板",
                                "note": "在这家公司里，CEO 是正在给你下指令的用户本人。你是 CEO 顾问：下面是一位首席执行官的思维方式和工作方法，"
                                        "你用它来帮 CEO 想问题、给出方案和取舍建议，但决定由 CEO 做；不要以 CEO 的身份给同事下命令。"},
}


def _load_library() -> list[Role]:
    try:
        index = json.loads((LIBRARY / "index.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    roles = []
    for r in index:
        o = OVERRIDES.get(r["id"], {})
        tagline = o.get("tagline", r.get("tagline", ""))
        roles.append(Role(r["id"], o.get("name", r["name"]), r.get("icon") or "🧩", tagline, tagline,
                          r.get("default_file") or "docs/notes.md", bool(r.get("can_code")), r["department"],
                          r.get("department_name") or r["department"],
                          can_test=r["department"] == "testing" and not r.get("can_code")))
    return roles


LIBRARY_ROLES: list[Role] = [r for r in _load_library() if r.id not in {p.id for p in PRESET_ROLES}]
ROLES = {r.id: r for r in PRESET_ROLES + LIBRARY_ROLES}


def prompt_file(role_id: str, level: str) -> Path | None:
    """角色库角色的提示词文件；预设角色返回 None（提示词在 role_prompts/）。"""
    role = ROLES.get(role_id)
    if not role or not role.department:
        return None
    return LIBRARY / role.department / f"{role_id}.{level}.md"


def preset_info() -> list[dict]:
    return [asdict(r) for r in PRESET_ROLES]


def library_info() -> list[dict]:
    """角色库清单（给创建公司的界面），不含提示词正文。"""
    return [{k: v for k, v in asdict(r).items() if k != "duties"} for r in LIBRARY_ROLES]
