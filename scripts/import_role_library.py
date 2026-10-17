"""把开源角色库 agency-agents-zh 导入成 OPC 的可选角色（开发时运行；原项目更新后可以重新导入）。

    python scripts/import_role_library.py D:\\agency-agents-zh

每个角色生成：
- opc/role_library/<部门>/<角色>.complete.md：完整版 = 原文，只去掉头信息和交付物模板章节
- opc/task_templates/library/<角色>.md：从原文里拆出来的交付物模板。按 OPC 的提示词分层原则，格式跟着任务走，
  不放在角色里；公司里有这个角色时，规划助手可以给任务选用它的模板
- opc/role_library/index.json：角色清单（名字、图标、一句话介绍、部门、能不能用编码 agent）

简洁版（<角色>.concise.md）由 scripts/rewrite_concise.py 用模型按 6 个预设角色的写法改写（另外运行）。
6 个预设角色（opc/role_prompts/）是逐个手工改写的，不由这个脚本生成。
原项目是 MIT 许可证，见仓库根目录的 THIRD_PARTY_NOTICES.md。
"""
import json
import re
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent / "opc"
OUT = ROOT / "role_library"
TEMPLATES = ROOT / "task_templates" / "library"
SKIP_DIRS = {"examples", "integrations", "scripts", "evals", "assets", ".github"}

DEPARTMENTS = {
    "product": "产品", "engineering": "工程", "design": "设计", "testing": "测试", "marketing": "营销",
    "paid-media": "付费投放", "sales": "销售", "support": "客户支持", "project-management": "项目管理",
    "company": "高管", "strategy": "战略", "finance": "财务", "legal": "法务", "hr": "人力资源",
    "security": "安全", "academic": "学术研究", "game-development": "游戏开发", "spatial-computing": "空间计算",
    "gis": "地理信息", "supply-chain": "供应链", "specialized": "专项",
}

# 能把写代码交给编码 agent 的角色（软件开发类）
CAN_CODE = re.compile(r"(developer|builder|prototyper|scripter|programmer|shopping-cart|ai-engineer|data-engineer|"
                      r"devops|minimal-change|backend|frontend|senior-developer|software-architect|pc-host|embedded|"
                      r"orgscript|voice-ai|email-intelligence|multi-agent|security-engineer|sre$|database-optimizer|"
                      r"ai-data-remediation|unity-architect|addon-engineer|multiplayer-engineer|shader)")

# 其他部门里写代码、跑代码的技术角色（安全测试、GIS 开发、XR 开发、技术美术……）
CAN_CODE_EXTRA = {
    "engineering-autonomous-optimization-architect", "engineering-code-reviewer", "engineering-codebase-onboarding-engineer",
    "engineering-filament-optimization-specialist", "engineering-fpga-digital-design-engineer",
    "engineering-git-workflow-master", "engineering-solidity-smart-contract-engineer", "engineering-threat-detection-engineer",
    "testing-api-tester", "testing-embedded-qa-engineer", "testing-accessibility-auditor", "testing-performance-benchmarker",
    "testing-evidence-collector", "security-appsec-engineer", "security-penetration-tester",
    "security-blockchain-security-auditor", "security-threat-detection-engineer", "technical-artist",
    "unreal-systems-engineer", "unreal-multiplayer-architect", "unreal-technical-artist", "game-audio-engineer",
    "macos-spatial-metal-engineer", "terminal-integration-specialist", "visionos-spatial-engineer", "xr-immersive-developer",
    "xr-cockpit-interaction-specialist", "gis-3d-scene-developer", "gis-geoai-ml-engineer", "gis-spatial-data-engineer",
    "gis-spatial-data-scientist", "gis-web-gis-developer", "gis-geoprocessing-specialist", "gis-qa-engineer",
    "lsp-index-engineer", "specialized-mcp-builder", "data-consolidation-agent", "specialized-model-qa",
    "specialized-document-generator", "specialized-salesforce-architect",
}

# 交付物模板章节：按提示词分层原则拆出去，跟着任务走
TEMPLATE_HEAD = re.compile(r"(交付物|交付模板|模板|Deliverable|Template)", re.I)


def parse(text: str) -> tuple[dict, str]:
    """返回 (头信息, 正文)。"""
    m = re.match(r"---\n(.*?)\n---\n", text, re.S)
    meta = {}
    for line in m.group(1).splitlines():
        if ":" in line and not line.startswith(" "):
            k, v = line.split(":", 1)
            meta[k.strip()] = v.strip().strip('"')
    return meta, text[m.end():].strip()


def split_sections(body: str) -> list[tuple[str, str]]:
    """按二级标题切开（代码块里的不算）：[(标题行, 整节文字)]，第一项是二级标题之前的部分（标题行为空）。"""
    out, head, buf, fence = [], "", [], False
    for line in body.splitlines():
        if line.startswith("```"):
            fence = not fence
        if not fence and line.startswith("## "):
            out.append((head, "\n".join(buf)))
            head, buf = line, [line]
        else:
            buf.append(line)
    out.append((head, "\n".join(buf)))
    return out


def convert(path: Path, dept: str) -> dict | None:
    text = path.read_text(encoding="utf-8")
    if not text.startswith("---") or "emoji:" not in text.split("---")[1]:
        return None
    meta, body = parse(text)
    keep, templates = [], []
    for head, section in split_sections(body):
        (templates if head and TEMPLATE_HEAD.search(head) else keep).append(section)
    role_id = path.stem
    out_dir = OUT / dept
    out_dir.mkdir(parents=True, exist_ok=True)
    complete = re.sub(r"\n{3,}", "\n\n", "\n\n".join(s.strip() for s in keep if s.strip())).strip()
    (out_dir / f"{role_id}.complete.md").write_text(complete + "\n", encoding="utf-8")
    if templates:
        TEMPLATES.mkdir(parents=True, exist_ok=True)
        (TEMPLATES / f"{role_id}.md").write_text("\n\n".join(s.strip() for s in templates).strip() + "\n", encoding="utf-8")
    short = re.sub(rf"^({re.escape(dept)}|{dept.split('-')[0]})-", "", role_id)
    return {"id": role_id, "name": meta.get("name", role_id), "icon": meta.get("emoji", "🧩"),
            "tagline": meta.get("description", ""), "department": dept, "department_name": DEPARTMENTS.get(dept, dept),
            "can_code": role_id in CAN_CODE_EXTRA or (dept in ("engineering", "game-development")
                                                      and bool(CAN_CODE.search(role_id))),
            "default_file": f"docs/{short}.md", "template": bool(templates)}


def main() -> None:
    src = Path(sys.argv[1] if len(sys.argv) > 1 else r"D:\agency-agents-zh")
    keep_concise = {p.name: p.read_text(encoding="utf-8") for p in OUT.rglob("*.concise.md")} if OUT.exists() else {}
    if OUT.exists():
        shutil.rmtree(OUT)
    if TEMPLATES.exists():
        shutil.rmtree(TEMPLATES)
    index = []
    for path in sorted(src.glob("*/**/*.md")):
        rel = path.relative_to(src)
        if rel.parts[0] in SKIP_DIRS or rel.parts[0] not in DEPARTMENTS:
            continue
        item = convert(path, rel.parts[0])
        if item:
            item["source"] = rel.as_posix()
            index.append(item)
            name = f"{item['id']}.concise.md"
            if name in keep_concise:  # 重新导入时保留已经改写好的简洁版
                (OUT / item["department"] / name).write_text(keep_concise[name], encoding="utf-8")
    order = list(DEPARTMENTS)
    index.sort(key=lambda r: (order.index(r["department"]), r["id"]))
    (OUT / "index.json").write_text(json.dumps(index, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"已导入 {len(index)} 个角色到 {OUT}，其中 {sum(r['template'] for r in index)} 个拆出了交付物模板")


if __name__ == "__main__":
    main()
