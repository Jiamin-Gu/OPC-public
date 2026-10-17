"""对比三个版本的产出：成本、审查情况、文档长度、标注的假设、疑似编造证据的说法。"""
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).parent
FABRICATION = re.compile(r"(调研显示|访谈(中|发现|结果)|问卷(显示|结果)|用户反馈显示|n\s*=\s*\d+|\d+(\.\d+)?%\s*的(用户|大学生|学生))")
ASSUME = re.compile(r"假设")


def doc_stats(files: Path) -> dict:
    out = {}
    for p in sorted(files.rglob("*")):
        if p.is_file() and p.suffix in (".md", ".txt"):
            t = p.read_text(encoding="utf-8", errors="replace")
            out[p.relative_to(files).as_posix()] = {
                "chars": len(t), "sections": len(re.findall(r"^#{2,3} ", t, re.M)),
                "assumptions": len(ASSUME.findall(t)),
                "suspect": sorted({m.group(0) for m in FABRICATION.finditer(t)})[:6],
                "tables": len(re.findall(r"^\|.*\|$", t, re.M)),
            }
    return out


for level in sys.argv[1:] or ("v1", "v2"):
    d = ROOT / level
    if not (d / "summary.json").exists():
        print(f"== {level}: 还没有结果")
        continue
    s = json.loads((d / "summary.json").read_text(encoding="utf-8"))
    t = s["total"]
    print(f"\n== {level}：{s['seconds']} 秒，调用 {t['calls']} 次，输入 {t['prompt']/1e3:.0f}k（缓存 {t['cached']/max(t['prompt'],1):.0%}），"
          f"输出 {t['completion']/1e3:.0f}k，上报 CEO {s['escalations']} 次")
    for task in s["tasks"]:
        rv = "；".join(f"第{r['round']}轮 {r['verdict']}（{len(r['issues'])} 条）" for r in task["reviews"]) or "无审查"
        print(f"  任务《{task['title'][:24]}》{task['assignee']}→审查 {task['reviewer']}：{task['status']}，{rv}")
    for name, st in doc_stats(d / "files").items():
        print(f"  {name:32} {st['chars']:6} 字  {st['sections']:3} 节  {st['tables']:3} 表格行  假设 {st['assumptions']:2}  "
              f"疑似编造 {st['suspect'] or '-'}")
