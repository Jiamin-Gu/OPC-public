"""长期记忆端到端对比的机械检查（第 2 条指令的产出是否遵守以前的口径和公司资料）。

第一版只查关键词出现没有，人工核对时发现会误判：知识库关闭的那次写了"积分有效期 24 个月"，
却因为别处出现过"6–12 个月"算成通过；"90 天"匹配到的是"上线后 90 天"这个指标窗口。
这一版按"规则说的那件事"去找：积分有效期取出月数，必须是 12；金额不能用 DECIMAL / 浮点；券的有效期取出天数，都不超过 90。
"""
import re

MONEY_FLOAT = re.compile(r"DECIMAL\s*\(\s*\d+\s*,\s*2\s*\)|\bdecimal\b|\bFLOAT\b|\bfloat\b|\bDOUBLE\b|浮点数?存储")
NEGATED = re.compile(r"禁止|不允许|不得|不出现|不能|无\s*`?DECIMAL|不用")
CENT_INT = re.compile(r"以\s*[「“\"]?分[」”\"]?\s*为单位|单位为分|单位[:：]\s*分|（分）|\(分\)|_cents?\b|_fen\b")


def _money_float(text: str) -> bool:
    """金额用了小数类型：某一行出现 DECIMAL(n,2) / FLOAT / DOUBLE，而且这一行不是在说"禁止、不允许、不出现"。"""
    return any(MONEY_FLOAT.search(line) and not NEGATED.search(line) for line in text.splitlines())


def _points_months(text: str) -> list[int]:
    """积分有效期写的是几个月（同一句里"积分 … 有效期 … N 个月"）。"""
    out = []
    for sent in re.split(r"[。\n]", text):
        if "积分" in sent and re.search(r"有效期|有效|过期", sent):
            out += [int(n) for n in re.findall(r"(\d+)\s*个月", sent)]
    return out


def _coupon_days(text: str) -> list[int]:
    """券的有效期写的是几天（"有效期 … N 天"、"领取后 N 天"、"1–90 天"）。"""
    out = []
    for m in re.finditer(r"(?:券[^。\n|]{0,20}有效期|有效期天数|有效期|valid_days|领取后)[^。\n|]{0,20}?(\d+)\s*(?:[–\-~～至到]\s*(\d+)\s*)?天", text):
        out += [int(x) for x in m.groups() if x]
    return out


def check(text: str) -> dict:
    months = _points_months(text)
    days = _coupon_days(text)
    results = {
        "M1": ("第 1 条指令", "积分有效期 12 个月", bool(months) and 12 in months and not any(m not in (12,) for m in months
                                                                                     if m > 12)),
        "M2": ("第 1 条指令", "先到期的积分先扣",
               bool(re.search(r"先到期|先过期|最早到期|到期时间.{0,6}(升序|最早|优先)|FIFO|先进先出", text))),
        "M3": ("第 1 条指令", "积分不能转让",
               bool(re.search(r"(不可|不能|不得|禁止|不支持|不做)[^。\n|]{0,10}(转让|转赠)", text))),
        "L1": ("公司资料", "金额以分为单位的整数", bool(CENT_INT.search(text)) and not _money_float(text)),
        "L2": ("公司资料", "优惠券有效期最长 90 天", bool(days) and max(days) <= 90),
        "L3": ("公司资料", "优惠券不可叠加",
               bool(re.search(r"(不可|不能|不得|禁止|不支持)[^。\n|]{0,6}叠加|(最多|只能)(使用|用)?\s*(一|1)\s*张", text))),
    }
    return {k: {"from": src, "rule": rule, "ok": ok} for k, (src, rule, ok) in results.items()} | {
        "_evidence": {"points_months": months, "coupon_days": days}}


if __name__ == "__main__":
    import sys
    from pathlib import Path
    for name in sys.argv[1:]:
        docs = Path(name) / "files" / "docs"
        text = "\n".join(p.read_text(encoding="utf-8") for p in sorted(docs.glob("coupon*.md")) if "简洁版" not in p.name)
        r = check(text)
        score = sum(v["ok"] for k, v in r.items() if not k.startswith("_"))
        print(name, score, {k: v["ok"] for k, v in r.items() if not k.startswith("_")}, r["_evidence"])
