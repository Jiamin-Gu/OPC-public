"""用模型把角色库的完整版改写成简洁版（开发时运行一次；中断了再运行会跳过已经改写好的）。

    python scripts/rewrite_concise.py [--only 角色id,角色id] [--jobs 6]

写法照 6 个手工改写的预设角色（opc/role_prompts/*.concise.md）：一段定位 + 关键规则 + 工作流程 + 和同事协作 +
你做不到的事，按 OPC 里 AI 同事的实际能力改写，不含任何交付物模板和输出格式（格式跟着任务走）。
改写结果先过机械检查（必须有的章节、长度、不能有代码块和模板），不合格的重试一次，还不合格就记下来人工处理。

模型接口用环境变量 OPC_BASE_URL / OPC_API_KEY / OPC_MODEL（和 OPC 本身一样）。
"""
import argparse
import json
import os
import re
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from openai import OpenAI

ROOT = Path(__file__).resolve().parent.parent / "opc"
LIB = ROOT / "role_library"
EXAMPLE = (ROOT / "role_prompts" / "pm.concise.md").read_text(encoding="utf-8").strip()

SYSTEM = f"""你负责把一个 AI 角色的完整提示词，改写成给"一人公司"里 AI 同事用的简洁版。

这家公司里的 AI 同事能做的事：读写共享文件区里的文件；公司允许时联网搜索、阅读网页；和同事协作（上报上游的问题、
向同事提问）。软件开发类的角色还有一个工具"编码 agent"，可以用它写代码、运行测试；编码 agent 是工具不是同事，
其他角色没有这个工具，需要实现的部分只能写清楚需求，交给负责开发的同事。做不到的事：发邮件、打电话、开会、登录后台或
第三方平台、接触真实用户和公司内部数据。

改写要求：
1. 结构和下面的范本一样：开头一段话说明这个角色是谁、关注什么（1～2 句）；然后依次是"## 关键规则"（5～8 条，
   每条"**一句话原则。** 一两句解释"）、"## 工作流程"（4～7 步，每步"**步骤名**：做什么"）、"## 和同事协作"
   （2～4 条，说明它的产出给谁用、依赖谁；和别的角色没关系就写它产出的东西怎样方便别人使用）、"## 你做不到的事"
   （一段话：结合这个角色说明哪些事 AI 做不到，改为用公开资料（注明来源）或明确标注的假设代替，绝不编造数据）
2. 保留原文里最有价值的专业判断标准和方法（具体的框架名、数字标准、检查要点），去掉空话、人设故事、
   "身份与记忆""成功指标""沟通风格""学习与记忆"这类内容
3. 不写任何交付物模板、输出格式、示例文档、代码块、表格：产出的格式由每次的任务说明规定，不放在角色里
4. 原文里需要真人才能做的工作方式（访谈用户、看后台数据、线上监控、联系外部的人），改写成 AI 能做的版本
   （写出方案、清单，说明需要谁来完成）
5. 全部用中文，总长 700～1600 字。只输出改写后的提示词本身，不要任何解释

范本（产品经理的简洁版）：
{EXAMPLE}"""

REQUIRED = ("## 关键规则", "## 工作流程", "## 你做不到的事")


def problems(text: str, can_code: bool = True) -> list[str]:
    out = [f"缺少章节 {h}" for h in REQUIRED if h not in text]
    if not can_code and "编码 agent" in text.split("## 你做不到的事")[0]:
        out.append("这个角色没有编码 agent 这个工具，不能写把工作交给编码 agent；需要实现的部分交给负责开发的同事")
    n = len(text)
    if not 500 <= n <= 2400:
        out.append(f"长度 {n} 字不在 500～2400 之间")
    if "```" in text:
        out.append("有代码块")
    if re.search(r"^#+.*模板", text, re.M):
        out.append("有模板章节")
    if re.search(r"^\s*\|.*\|\s*$", text, re.M):
        out.append("有表格")
    if len(re.findall(r"[一-鿿]", text)) < n * 0.4:
        out.append("中文太少")
    return out


def rewrite(client: OpenAI, model: str, role: dict, complete: str) -> tuple[str, list[str]]:
    user = (f"角色：{role['name']}（{role['department_name']}部门）\n一句话介绍：{role['tagline']}\n"
            f"{'这个角色有编码 agent 这个工具，可以用它写代码、运行测试。' if role['can_code'] else '这个角色没有编码 agent，不写代码。'}\n\n"
            f"完整提示词：\n{complete}")
    messages = [{"role": "system", "content": SYSTEM}, {"role": "user", "content": user}]
    text, bad = "", ["没有调用"]
    for _ in range(2):  # 不合格的把问题告诉它重写一次
        r = client.chat.completions.create(model=model, messages=messages, max_tokens=4000)
        text = (r.choices[0].message.content or "").strip()
        text = re.sub(r"^```(?:markdown)?\n|\n```$", "", text).strip()
        bad = problems(text, role["can_code"])
        if not bad:
            break
        messages += [{"role": "assistant", "content": text},
                     {"role": "user", "content": "不符合要求：" + "；".join(bad) + "。请按要求重新输出完整的简洁版。"}]
    return text, bad


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", default="")
    ap.add_argument("--jobs", type=int, default=6)
    ap.add_argument("--redo", action="store_true", help="已经有简洁版的也重新改写（配合 --only）")
    args = ap.parse_args()
    client = OpenAI(base_url=os.environ["OPC_BASE_URL"], api_key=os.environ["OPC_API_KEY"], timeout=300)
    model = os.environ.get("OPC_MODEL", "deepseek-v4-flash")
    index = json.loads((LIB / "index.json").read_text(encoding="utf-8"))
    only = {x for x in args.only.split(",") if x}
    todo = [r for r in index if (not only or r["id"] in only)
            and (args.redo or not (LIB / r["department"] / f"{r['id']}.concise.md").exists())]
    print(f"要改写 {len(todo)} 个角色", flush=True)
    lock, failed = threading.Lock(), {}

    def work(role: dict) -> None:
        folder = LIB / role["department"]
        complete = (folder / f"{role['id']}.complete.md").read_text(encoding="utf-8")
        try:
            text, bad = rewrite(client, model, role, complete)
        except Exception as e:  # noqa: BLE001  一个失败不影响别的，下次运行会重试
            with lock:
                failed[role["id"]] = [f"{type(e).__name__}: {e}"]
                print(f"失败 {role['id']}：{type(e).__name__}", flush=True)
            return
        if bad:
            with lock:
                failed[role["id"]] = bad
                print(f"不合格 {role['id']}：{'；'.join(bad)}", flush=True)
            return
        (folder / f"{role['id']}.concise.md").write_text(text + "\n", encoding="utf-8")
        with lock:
            print(f"完成 {role['id']}（{len(text)} 字）", flush=True)

    with ThreadPoolExecutor(max_workers=args.jobs) as pool:
        list(pool.map(work, todo))
    (LIB / "concise_failed.json").write_text(json.dumps(failed, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"结束：失败或不合格 {len(failed)} 个（见 concise_failed.json）", flush=True)
    sys.exit(0)


if __name__ == "__main__":
    main()
