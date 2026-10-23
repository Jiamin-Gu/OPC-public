# -*- coding: utf-8 -*-
"""轻记 CLI 真机验证 harness（临时脚本，验证完成、verify.log 生成后删除）。

只做三件事：按用户给定的顺序真机执行命令、原样收集 stdout/stderr/退出码、
把结果写成 code/verify.log。不修改 code/ 下任何被测文件。
"""

import hashlib
import json
import os
import shutil
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.abspath(__file__))          # 工作区根
CODE = os.path.join(ROOT, "code")                          # 运行目录
TMP = "/tmp/lt"                                            # 测试数据目录
TOOLS = "/tmp/lt_tools"                                    # 临时脚本目录
PY = "python"

LOG = []
CODES = []


def out(text=""):
    LOG.append(text)


def banner(text):
    out()
    out("#" * 78)
    out("# " + text)
    out("#" * 78)


def _sha256(path):
    if not os.path.exists(path):
        return None
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _dump_block(title, raw_bytes):
    out("-------- %s --------" % title)
    if not raw_bytes:
        out("（空）")
        out("[末尾换行：无]")
        return
    text = raw_bytes.decode("utf-8", errors="replace")
    has_newline = text.endswith("\n")
    out(text[:-1] if has_newline else text)
    out("[末尾换行：%s]" % ("有" if has_newline else "无"))


def run(label, argv, cwd=CODE, shell=False, display=None, watch=(), env=None,
        extra=(), timeout=180):
    """真机执行一条命令并原样记录。argv 为 list 时 shell=False。"""
    before = {path: _sha256(path) for path in watch}
    shown = display or (argv if shell else " ".join(
        ('"%s"' % item if " " in item else item) for item in argv))
    try:
        proc = subprocess.run(argv, cwd=cwd, shell=shell, env=env,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                              timeout=timeout)
        stdout, stderr, code = proc.stdout, proc.stderr, proc.returncode
    except subprocess.TimeoutExpired:
        stdout, stderr, code = b"", ("超时未结束（> %s 秒）" % timeout).encode("utf-8"), -1

    out()
    out("-" * 78)
    out("[%s]" % label)
    out("工作目录：%s" % cwd)
    out("命令：%s" % shown)
    _dump_block("stdout", stdout)
    _dump_block("stderr", stderr)
    out("-------- 退出码 --------")
    out(str(code))
    for path in watch:
        after = _sha256(path)
        if before[path] is None and after is None:
            out("-------- 数据文件校验 --------")
            out("%s：运行前/后均不存在（未被创建）" % path)
        else:
            short_b = (before[path] or "-")[:12]
            short_a = (after or "-")[:12]
            out("-------- 数据文件校验 --------")
            out("%s sha256 前 12 位：运行前 %s → 运行后 %s → %s"
                % (path, short_b, short_a,
                   "内容未改变 ✓" if before[path] == after else "内容已改变"))
    for line in extra:
        out(line)
    CODES.append((label, code))
    sys.stdout.write("[done] %s -> %s\n" % (label, code))
    sys.stdout.flush()
    return code, stdout, stderr


def check(label, argv, cwd=CODE, shell=False, display=None, env=None,
          watch=(), timeout=180, extra=()):
    return run(label, argv, cwd=cwd, shell=shell, display=display, env=env,
               watch=watch, timeout=timeout, extra=extra)


# ======================================================================
# 环境
# ======================================================================
def write_header():
    out("=" * 78)
    out("轻记 CLI 真机验证记录（verify.log）")
    out("=" * 78)
    out("生成时间（本机）：%s" % time.strftime("%Y-%m-%d %H:%M:%S"))
    out("工作区根目录：%s" % ROOT)
    out("被验证文件：code/ledger.py、code/cli.py")
    out("运行目录（cwd）：%s" % CODE)
    out("每次运行使用的解释器名：%s" % PY)
    out("说明：每条用例分别标注 命令 / stdout / stderr / 退出码；"
        "方括号内为笔者注记（如是否以换行结尾），"
        "所有中文与原始换行均按原样保留。")


# ======================================================================
# 准备工作：清空 /tmp/lt
# ======================================================================
def prepare():
    banner("准备：清空测试数据目录 /tmp/lt（不污染 code/data/）")
    run("准备-1 清空并重建 /tmp/lt",
        "rm -rf /tmp/lt && mkdir -p /tmp/lt && ls -la /tmp/lt",
        cwd=ROOT, shell=True)
    if os.path.isdir(TOOLS):
        shutil.rmtree(TOOLS, ignore_errors=True)
    os.makedirs(TOOLS, exist_ok=True)
    out("（临时辅助脚本目录：%s，仅本 harness 使用，验证完可删）" % TOOLS)


# ======================================================================
# 步骤 1：环境与语法
# ======================================================================
def step1():
    banner("步骤 1：环境与语法")
    run("1-1 解释器版本", [PY, "--version"])
    run("1-1b 解释器绝对路径与版本（供核对实际用的是哪个 python）",
        [PY, "-c", "import sys;print(sys.executable);print(sys.version)"])
    run("1-2 语法编译检查", [PY, "-m", "py_compile", "ledger.py", "cli.py"])
    run("1-3 帮助信息", [PY, "cli.py", "--help"])


# ======================================================================
# 步骤 2：主流程
# ======================================================================
T_JSON = "/tmp/lt/t.json"


def step2():
    banner("步骤 2：主流程（全部使用 --data /tmp/lt/t.json）")
    adds = [
        ("2-1 add 支出 35.80（首次写入，预期新建文件）",
         ["add", "--type", "expense", "--amount", "35.80",
          "--category", "餐饮", "--date", "2026-10-23"]),
        ("2-2 add 收入 1200 + 备注（预期 id=2、金额显示 1200.00）",
         ["add", "--type", "income", "--amount", "1200", "--category", "其他",
          "--date", "2026-10-01", "--note", "项目款"]),
        ("2-3 add 支出 0.10（精度用例，第一笔）",
         ["add", "--type", "expense", "--amount", "0.10",
          "--category", "交通", "--date", "2026-10-24"]),
        ("2-4 add 支出 0.20（精度用例，第二笔）",
         ["add", "--type", "expense", "--amount", "0.20",
          "--category", "交通", "--date", "2026-10-24"]),
        ("2-5 add 支出 35.8（验证与 35.80 等价，日期跨月 2026-09）",
         ["add", "--type", "expense", "--amount", "35.8",
          "--category", "餐饮", "--date", "2026-09-28"]),
    ]
    for label, args in adds:
        argv = [PY, "cli.py"] + args + ["--data", T_JSON]
        display = " ".join([PY, "cli.py"] + args + ["--data", T_JSON])
        if "--note" in args:
            display = display.replace("--note 项目款", '--note "项目款"')
        run(label, argv, display=display)

    run("2-6 summary --month 2026-10",
        [PY, "cli.py", "summary", "--month", "2026-10", "--data", T_JSON])
    run("2-7 summary --month 2026-09",
        [PY, "cli.py", "summary", "--month", "2026-09", "--data", T_JSON])
    run("2-8 stats --month 2026-10 --type expense",
        [PY, "cli.py", "stats", "--month", "2026-10", "--type", "expense",
         "--data", T_JSON])
    run("2-9 stats --month 2026-10 --type income",
        [PY, "cli.py", "stats", "--month", "2026-10", "--type", "income",
         "--data", T_JSON])


# ======================================================================
# 步骤 3：数据文件全文
# ======================================================================
def step3():
    banner("步骤 3：打印 /tmp/lt/t.json 全文")
    run("3-1 文件大小与校验和", "ls -l /tmp/lt/t.json && sha256sum /tmp/lt/t.json",
        cwd=ROOT, shell=True)
    run("3-2 打印全文（cat）", "cat /tmp/lt/t.json", cwd=ROOT, shell=True,
        display="cat /tmp/lt/t.json")

    data = json.load(open(T_JSON, encoding="utf-8"))
    notes = [r["note"] for r in data["records"]]
    ids = [r["id"] for r in data["records"]]
    cents = [r["amount_cents"] for r in data["records"]]
    extra = [
        "-------- 逐项核对（由 harness 用 json 读取 t.json 后判定）--------",
        "顶层字段：%s" % list(data.keys()),
        "version = %r（预期 1）" % data["version"],
        "records 条数 = %d" % len(data["records"]),
        "id 序列 = %s（预期 1..N 严格自增）" % ids,
        "amount_cents 全部为 int（非 bool、非 float、非字符串）= %s，值 = %s"
        % (all(isinstance(v, int) and not isinstance(v, bool) for v in cents), cents),
        "note 字段 = %s（预期未传备注的是空字符串 \"\"，不是 null）" % notes,
    ]
    run("3-3 对 t.json 的字段核对", [PY, "-c",
        "import json;d=json.load(open('%s',encoding='utf-8'));"
        "print('version=',repr(d['version']));"
        "print('ids=',[r['id'] for r in d['records']]);"
        "print('amount_cents=',[r['amount_cents'] for r in d['records']]);"
        "print('amount_cents types=',[type(r['amount_cents']).__name__ for r in d['records']]);"
        "print('notes=',[r['note'] for r in d['records']])" % T_JSON],
        extra=extra)


# ======================================================================
# 步骤 4：金额精度卡口
# ======================================================================
def step4():
    banner("步骤 4：金额精度卡口（35.80+0.10+0.20 必须恰好 36.10）")
    run("4-1 按分整数复算 2026-10 支出合计",
        [PY, "-c",
         "import json;d=json.load(open('%s',encoding='utf-8'));"
         "rows=[r for r in d['records'] if r['date'][:7]=='2026-10' and r['type']=='expense'];"
         "print('明细金额(分)=',[r['amount_cents'] for r in rows]);"
         "print('整数分求和 =',sum(r['amount_cents'] for r in rows));"
         "print('展示为元 =',sum(r['amount_cents'] for r in rows)//100,"
         "'.',sum(r['amount_cents'] for r in rows)%%100)" % T_JSON])
    run("4-2 对照：如果用浮点相加会得到什么（仅作对照，不参与实现）",
        [PY, "-c", "print(35.80+0.10+0.20)"])
    run("4-3 2026-10 交通分类金额（分）", [PY, "-c",
        "import json;d=json.load(open('%s',encoding='utf-8'));"
        "rows=[r for r in d['records'] if r['date'][:7]=='2026-10' and r['type']=='expense'"
        " and r['category']=='交通'];"
        "print('交通笔数=',len(rows),' 金额(分)=',sum(r['amount_cents'] for r in rows))"
        % T_JSON])


# ======================================================================
# 步骤 5：12 笔样例
# ======================================================================
BIG = "/tmp/lt/big.json"
GEN_BIG = """
import os, sys
CODE = %r
sys.path.insert(0, CODE)
from ledger import Record, append_record, load, parse_amount_to_cents

PATH = %r
if os.path.exists(PATH):
    os.remove(PATH)

# 12 笔支出：餐饮 5 笔共 1200.00、交通 4 笔共 900.00、购物 3 笔共 1356.78
plan = [("餐饮", "240.00")] * 5 + [("交通", "225.00")] * 4 + [("购物", "452.26")] * 3
assert len(plan) == 12

ledger = load(PATH, missing_ok=True)
for index, (category, amount) in enumerate(plan, start=1):
    day = index
    record = Record(
        id=ledger.next_id(),
        type="expense",
        amount_cents=parse_amount_to_cents(amount),
        category=category,
        date="2026-10-%%02d" %% day,
        note="",
        created_at="2026-10-%%02dT09:00:00" %% day,
    )
    append_record(PATH, record)          # 复用被测代码的公开写入函数
    ledger.records.append(record)        # 本地视图同步，用于 next_id()

# 复算校验
data = load(PATH, missing_ok=True)
counts, cents = {}, {}
for record in data.records:
    assert record.type == "expense" and record.date[:7] == "2026-10"
    counts[record.category] = counts.get(record.category, 0) + 1
    cents[record.category] = cents.get(record.category, 0) + record.amount_cents
print("已写入 %%d 条记录到 %%s（由 ledger.Record + ledger.append_record 逐条写入）"
      %% (len(data.records), PATH))
for category in ("餐饮", "交通", "购物"):
    print("  %%s：%%d 笔，合计 %%d 分" %% (category, counts[category], cents[category]))
total = sum(cents.values())
print("  全部：%%d 笔，合计 %%d 分（= %%d.%%02d 元）"
      %% (len(data.records), total, total // 100, total %% 100))
assert counts == {"餐饮": 5, "交通": 4, "购物": 3}
assert cents == {"餐饮": 120000, "交通": 90000, "购物": 135678}
assert total == 345678
print("自校验通过：12 笔 / 3456.78 元 / 餐饮 1200.00 / 交通 900.00 / 购物 1356.78")
""" % (CODE, BIG)


def step5():
    banner("步骤 5：12 笔样例（/tmp/lt/big.json）")
    gen_path = os.path.join(TOOLS, "gen_big.py")
    with open(gen_path, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(GEN_BIG)
    out()
    out("（说明：big.json 用一段 Python 脚本、经被测代码的公开 API "
        "ledger.Record + ledger.append_record 逐条写入；脚本源码如下，"
        "落盘于 %s）" % gen_path)
    out("")
    out(GEN_BIG.strip())
    run("5-1 生成 big.json", [PY, gen_path])
    run("5-2 文件大小", "ls -l /tmp/lt/big.json", cwd=ROOT, shell=True)
    run("5-3 stats --month 2026-10 --type expense（预期行序：购物→餐饮→交通）",
        [PY, "cli.py", "stats", "--month", "2026-10", "--type", "expense",
         "--data", BIG])
    run("5-4 占比独立复算（整数半进位 tenths=(cents*2000+total)//(2*total)）",
        [PY, "-c",
         "total=345678;parts=[('购物',135678),('餐饮',120000),('交通',90000)];"
         "print([(name,(cents*2000+total)//(2*total)) for name,cents in parts])"])
    out()
    out("-------- 人工核对（步骤 5 期望值）--------")
    out("期望行序：购物 39.2% → 餐饮 34.7% → 交通 26.0%，合计行 100.0%")


# ======================================================================
# 步骤 6：非法输入
# ======================================================================
def step6():
    banner("步骤 6：非法输入（每条都记录 stdout/stderr/退出码，"
           "并核对 t.json 字节内容未被改动）")
    run("6-0 非法用例前的 t.json 指纹（作为基准）",
        "ls -l /tmp/lt/t.json && sha256sum /tmp/lt/t.json", cwd=ROOT, shell=True)

    cases = [
        ("6-1 amount=1.005（小数超两位）", ["--amount", "1.005"]),
        ("6-2 amount=0", ["--amount", "0"]),
        ("6-3 amount=-5", ["--amount", "-5"]),
        ("6-4 amount=abc", ["--amount", "abc"]),
        ("6-5 amount=1,200（千分位逗号）", ["--amount", "1,200"]),
        ("6-6 category=旅行（不在 8 类）", ["--amount", "10", "--category", "旅行"]),
        ("6-7 date=2026-02-30（不存在的日期）",
         ["--amount", "10", "--category", "餐饮", "--date", "2026-02-30"]),
        ("6-8 date=2026-2-3（未补零）",
         ["--amount", "10", "--category", "餐饮", "--date", "2026-2-3"]),
        ("6-9 date=2026/10/23（分隔符错）",
         ["--amount", "10", "--category", "餐饮", "--date", "2026/10/23"]),
    ]
    for label, tail in cases:
        args = ["add", "--type", "expense"] + tail
        argv = [PY, "cli.py"] + args + ["--data", T_JSON]
        check(label, argv, watch=(T_JSON,))

    run("6-10 summary --month 2026-13（月份 13）",
        [PY, "cli.py", "summary", "--month", "2026-13", "--data", T_JSON],
        watch=(T_JSON,))
    run("6-11 summary --month 2026-1（月份未补零）",
        [PY, "cli.py", "summary", "--month", "2026-1", "--data", T_JSON],
        watch=(T_JSON,))
    run("6-12 summary --month 202610（缺分隔符）",
        [PY, "cli.py", "summary", "--month", "202610", "--data", T_JSON],
        watch=(T_JSON,))
    run("6-13 stats --month 2026-10 --type transfer（类型不在枚举内）",
        [PY, "cli.py", "stats", "--month", "2026-10", "--type", "transfer",
         "--data", T_JSON], watch=(T_JSON,))
    run("6-14 add --type transfer（类型不在枚举内）",
        [PY, "cli.py", "add", "--type", "transfer", "--amount", "10",
         "--category", "餐饮", "--data", T_JSON], watch=(T_JSON,))

    # 闰年用例：必须成功，会真的写入一条（id=6，日期 2024-02-29）
    run("6-15 add date=2024-02-29（闰年，必须成功 → 会新增一条 id=6；"
        "此条预期改变文件内容）",
        [PY, "cli.py", "add", "--type", "expense", "--amount", "10",
         "--category", "餐饮", "--date", "2024-02-29", "--data", T_JSON],
        watch=(T_JSON,))
    run("6-16 闰年写入后的 t.json（确认 id=6 与日期 2024-02-29）",
        "cat /tmp/lt/t.json", cwd=ROOT, shell=True)
    run("6-17 闰年记录不会计入 2026-10 / 2026-09 汇总（只读，不改文件）",
        [PY, "cli.py", "summary", "--month", "2026-10", "--data", T_JSON],
        watch=(T_JSON,))


# ======================================================================
# 步骤 7：空数据 / 只读无副作用
# ======================================================================
NONE = "/tmp/lt/none.json"


def step7():
    banner("步骤 7：空数据 / 只读命令不得创建文件")
    if os.path.exists(NONE):
        os.remove(NONE)
    run("7-0 前置确认：none.json 不存在（确认不存在时退出码 0）",
        'if [ -e /tmp/lt/none.json ]; then echo "存在（异常）"; else echo "确认不存在 ✓"; fi',
        cwd=ROOT, shell=True, watch=(NONE,))
    run("7-1 summary --month 2026-11 --data /tmp/lt/none.json（空数据）",
        [PY, "cli.py", "summary", "--month", "2026-11", "--data", NONE],
        watch=(NONE,))
    run("7-2 stats --month 2026-11 --data /tmp/lt/none.json（空数据）",
        [PY, "cli.py", "stats", "--month", "2026-11", "--data", NONE],
        watch=(NONE,))
    run("7-3 事后确认：none.json 仍不存在",
        'if [ -e /tmp/lt/none.json ]; then echo "存在（异常，只读命令不应创建文件）"; '
        'else echo "确认仍不存在 ✓（只读命令无副作用）"; fi',
        cwd=ROOT, shell=True, watch=(NONE,))


# ======================================================================
# 步骤 8：数据文件损坏
# ======================================================================
BAD = "/tmp/lt/bad.json"


def step8():
    banner("步骤 8：数据文件损坏（非法 JSON）")
    run("8-1 写入非法 JSON（只有一个左花括号）",
        "printf '{' > /tmp/lt/bad.json && ls -l /tmp/lt/bad.json && "
        "sha256sum /tmp/lt/bad.json", cwd=ROOT, shell=True)
    run("8-2 原始字节确认",
        [PY, "-c", "print(open('/tmp/lt/bad.json','rb').read())"])
    run("8-3 summary --month 2026-10 --data /tmp/lt/bad.json（预期退出码 3）",
        [PY, "cli.py", "summary", "--month", "2026-10", "--data", BAD],
        watch=(BAD,))
    run("8-4 stats --month 2026-10 --data /tmp/lt/bad.json（附加：同样预期 3）",
        [PY, "cli.py", "stats", "--month", "2026-10", "--data", BAD],
        watch=(BAD,))
    run("8-5 add --data /tmp/lt/bad.json（附加：add 遇到损坏文件也不得改写）",
        [PY, "cli.py", "add", "--type", "expense", "--amount", "1.00",
         "--category", "餐饮", "--date", "2026-10-23", "--data", BAD],
        watch=(BAD,))


# ======================================================================
# 步骤 9：零浮点卡口
# ======================================================================
def step9():
    banner("步骤 9：零浮点卡口（技术方案 2.3；范围 code/ledger.py code/cli.py）")
    cmd1 = 'grep -rnE "float\\(|:[[:space:]]*float" code/ledger.py code/cli.py'
    cmd2 = 'grep -rnE "(^|[^/])/[[:space:]]*100([^0-9]|$)" code/ledger.py code/cli.py'
    notes = []
    for index, cmd in enumerate((cmd1, cmd2), start=1):
        _, stdout, _ = run("9-%d %s" % (index, cmd), cmd, cwd=ROOT, shell=True)
        hits = [line for line in stdout.decode("utf-8", "replace").splitlines()
                if line.strip()]
        notes.append((index, hits))
    out()
    out("-------- 步骤 9 结论 --------")
    for index, hits in notes:
        out("正则 9-%d：命中 %d 行%s" % (
            index, len(hits),
            ("：" + "；".join(hits)) if hits else "（零命中 ✓）"))
    out()
    out("-------- 说明 --------")
    out("grep 无命中时退出码为 1（这是 grep 的约定，不是被测程序的错误），"
        "有命中时为 0。上面两条均以退出码与命中行数双重给出结论。")


# ======================================================================
# 步骤 10：性能
# ======================================================================
TENK = "/tmp/lt/tenk.json"
MAKE_TENK = """
import json, os, random, sys
CODE = %r
sys.path.insert(0, CODE)
from ledger import CATEGORIES, Record

TENK = %r
rnd = random.Random(20261023)
months = ["%%04d-%%02d" %% (year, month) for year in (2025, 2026) for month in range(1, 13)]
records = []
for number in range(1, 10001):
    month = months[rnd.randrange(len(months))]
    day = rnd.randint(1, 28)
    record = Record(
        id=number,
        type="expense" if rnd.random() < 0.7 else "income",
        amount_cents=rnd.randint(1, 500000),
        category=CATEGORIES[rnd.randrange(len(CATEGORIES))],
        date="%%s-%%02d" %% (month, day),
        note="",
        created_at="%%s-%%02dT12:00:00" %% (month, day),
    )
    records.append(record.to_dict())

with open(TENK, "w", encoding="utf-8", newline="\\n") as handle:
    json.dump({"version": 1, "records": records}, handle, ensure_ascii=False, indent=2)

size = os.path.getsize(TENK)
october = [r for r in records if r["date"][:7] == "2026-10"]
october_expense = [r for r in october if r["type"] == "expense"]
print("已写入 %%d 条记录到 %%s（直接写 JSON 文件：1 万条若逐条 append_record 会做 1 万次全量重写，O(n^2)，故直接构造）" %% (len(records), TENK))
print("文件大小 %%d 字节（%%.2f MB）" %% (size, size / 1048576))
print("2026-10 共 %%d 条，其中支出 %%d 条 %%d 分" %% (len(october), len(october_expense), sum(r["amount_cents"] for r in october_expense)))
""" % (CODE, TENK)

BENCH = """
import os, shutil, subprocess, sys, time
CODE = %r
sys.path.insert(0, CODE)
import ledger

TENK = %r
ADD_COPY = %r
size = os.path.getsize(TENK)
print("数据文件：%%s（%%d 条记录，%%.2f MB）" %% (TENK, 10000, size / 1048576))
print("计时方式：subprocess 端到端（含解释器启动与文件读写），每条跑 3 次")
print()

def wall(argv, repeat=3):
    times = []
    for _ in range(repeat):
        start = time.perf_counter()
        proc = subprocess.run(["python", "cli.py"] + argv, cwd=CODE,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        elapsed = time.perf_counter() - start
        assert proc.returncode == 0, proc.stderr.decode("utf-8", "replace")
        times.append(elapsed)
    return times

for name, argv in (
    ("summary --month 2026-10", ["summary", "--month", "2026-10", "--data", TENK]),
    ("stats --month 2026-10 --type expense", ["stats", "--month", "2026-10", "--type", "expense", "--data", TENK]),
):
    times = wall(argv)
    print("%%-40s 三次：%%s 秒；最快 %%.3f 秒" %% (name, ["%%.3f" %% t for t in times], min(times)))

# add 为 O(n) 全量重写：每次先用 1 万条的副本覆盖，保证是真实的 1 万条追加
times = []
for _ in range(3):
    shutil.copyfile(TENK, ADD_COPY)
    start = time.perf_counter()
    proc = subprocess.run(["python", "cli.py", "add", "--type", "expense",
                           "--amount", "1.00", "--category", "餐饮",
                           "--date", "2026-10-15", "--data", ADD_COPY],
                          cwd=CODE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    elapsed = time.perf_counter() - start
    assert proc.returncode == 0, proc.stderr.decode("utf-8", "replace")
    times.append(elapsed)
print("%%-40s 三次：%%s 秒；最快 %%.3f 秒（附加项）" %% ("add（O(n) 全量重写，附加）", ["%%.3f" %% t for t in times], min(times)))
print()

# 进程内拆分：文件读取 vs 计算
start = time.perf_counter(); data = ledger.load(TENK); t_load = time.perf_counter() - start
start = time.perf_counter(); summary = ledger.monthly_summary(data, "2026-10"); t_sum = time.perf_counter() - start
start = time.perf_counter(); stats = ledger.category_stats(data, "2026-10", "expense"); t_stat = time.perf_counter() - start
print("进程内（%%d 条）：load() %%.3f 秒；monthly_summary() %%.3f 秒；category_stats() %%.3f 秒"
      %% (len(data.records), t_load, t_sum, t_stat))
print("2026-10：收入 %%d 笔 / %%d 分；支出 %%d 笔 / %%d 分；分类数 %%d"
      %% (summary.income_count, summary.income_cents, summary.expense_count,
         summary.expense_cents, len(stats)))
print("对照技术方案 2.4：单条命令 < 1 秒；文件 %%.2f MB" %% (size / 1048576))
""" % (CODE, TENK, "/tmp/lt/tenk_add.json")


def step10():
    banner("步骤 10：性能（1 万条，估算用，非硬指标）")
    make_path = os.path.join(TOOLS, "make_tenk.py")
    bench_path = os.path.join(TOOLS, "bench.py")
    with open(make_path, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(MAKE_TENK)
    with open(bench_path, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(BENCH)
    out()
    out("（说明：big.json 只有 12 条，其 3456.78 元样例需要保持可复现，"
        "故另建 /tmp/lt/tenk.json 存放 1 万条，不就地扩展 big.json；"
        "辅助脚本：%s、%s）" % (make_path, bench_path))
    run("10-1 生成 1 万条数据 /tmp/lt/tenk.json", [PY, make_path])
    run("10-2 实测 summary / stats / add 耗时", [PY, bench_path], timeout=600)


# ======================================================================
# 收尾
# ======================================================================
def finish():
    banner("退出码一览（按执行顺序）")
    for label, code in CODES:
        out("%-4s | %s" % (code, label))
    banner("收尾：/tmp/lt 目录内容")
    out("（全部测试数据都在 /tmp/lt 下，code/data/ 未被创建或改动）")
    run("收尾-1 ls -la /tmp/lt", "ls -la /tmp/lt", cwd=ROOT, shell=True)
    run("收尾-2 code/ 目录内容（确认没有新增业务文件、没有 data/）",
        "ls -la code", cwd=ROOT, shell=True)

    target = os.path.join(CODE, "verify.log")
    with open(target, "w", encoding="utf-8", newline="\n") as handle:
        handle.write("\n".join(LOG) + "\n")
    print("已写出 %s（%d 行，%d 字节）" % (target, len(LOG), os.path.getsize(target)))


def main():
    write_header()
    prepare()
    step1()
    step2()
    step3()
    step4()
    step5()
    step6()
    step7()
    step8()
    step9()
    step10()
    finish()


if __name__ == "__main__":
    main()
