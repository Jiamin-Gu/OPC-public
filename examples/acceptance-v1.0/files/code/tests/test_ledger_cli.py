# -*- coding: utf-8 -*-
"""轻记 CLI 记账工具 —— 测试用例。

依据：docs/PRD.md（v1.1）第 4 章验收标准 + docs/技术方案.md（v1.2）第 3/4/5 章。
口径依据：公司资料《记账规范（2026 版）》——金额以「分」整数存储与计算、禁止浮点；
         日期 YYYY-MM-DD、月份 YYYY-MM；分类固定 8 类；仅人民币 CNY。

运行方式（在 code/ 目录下）：
    python -m unittest discover -s tests -v
    python -m unittest discover -s tests -t . -v      # 等价写法

范围说明：本期不测导出 CSV（CEO 指示：需求/方案/代码/测试均去掉该功能）。
本文件只断言 `export` 子命令不存在以锁定本期范围；测试一律用 --data 指向临时文件，
不触碰 code/data/ 与真实账本。测试只读取被测代码，不修改 code/ 下任何实现文件。
"""

import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
from datetime import date

CODE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if CODE_DIR not in sys.path:
    sys.path.insert(0, CODE_DIR)

import ledger  # noqa: E402  （被测模块，位于 code/ledger.py）


def run_cli(*args):
    """在 code/ 目录下执行 `python cli.py ...`，返回 (退出码, stdout, stderr)。"""
    proc = subprocess.run(
        [sys.executable, "cli.py", *args],
        cwd=CODE_DIR,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
    )
    return proc.returncode, proc.stdout, proc.stderr


def sha256_of(path):
    with open(path, "rb") as handle:
        return hashlib.sha256(handle.read()).hexdigest()


class CliTestCase(unittest.TestCase):
    """公共夹具：每个用例一个临时目录，--data 指向其中的 ledger.json。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="lt-test-")
        self.addCleanup(self._tmp.cleanup)
        self.data = os.path.join(self._tmp.name, "ledger.json")

    # ---- 便捷方法 ---------------------------------------------------
    def cli(self, *args):
        return run_cli(*args)

    def add(self, amount, category, type_="expense", **kwargs):
        args = ["add", "--type", type_, "--amount", str(amount), "--category", category]
        for key, value in kwargs.items():
            args.extend(["--" + key, value])
        args.extend(["--data", self.data])
        return run_cli(*args)

    def load_data(self):
        with open(self.data, "r", encoding="utf-8") as handle:
            return json.load(handle)

    def records(self):
        return self.load_data()["records"]

    def seed_october(self):
        """PRD 故事 2/3 的样例数据：2026-10 有 1 笔收入 + 3 笔支出，另有 2026-09 一笔。"""
        self.add("1200", "餐饮", date="2026-10-05")
        self.add("900", "交通", date="2026-10-06")
        self.add("1356.78", "购物", date="2026-10-07")
        self.add("1200", "其他", type_="income", date="2026-10-01")
        self.add("50", "娱乐", date="2026-09-30")


# ======================================================================
# 一、正常流程
# ======================================================================

class TestNormalFlow(CliTestCase):
    """记一笔支出、记一笔收入、按月汇总、按分类统计、数据文件结构。"""

    def test_add_expense_ok_writes_integer_cents(self):
        """记一笔支出：退出码 0、确认行正确、落盘 amount_cents 为整数 3580。"""
        rc, out, err = self.add("35.80", "餐饮", date="2026-10-23")
        self.assertEqual(0, rc, "stderr=%r" % err)
        self.assertEqual("已记录 #1：支出 35.80 元 餐饮 2026-10-23\n", out)
        record = self.records()[0]
        self.assertEqual(3580, record["amount_cents"])
        self.assertIsInstance(record["amount_cents"], int)
        self.assertNotIsInstance(record["amount_cents"], bool)
        self.assertEqual("expense", record["type"])
        self.assertEqual("餐饮", record["category"])
        self.assertEqual("", record["note"], "未传 --note 必须存空字符串，不是 null")

    def test_add_amount_forms_are_equivalent(self):
        """`35.8` 与 `35.80` 落盘一致（PRD：整数 35 与 35.80 等价）。"""
        self.add("35.8", "餐饮")
        self.add("35", "交通")
        self.add("0.5", "购物")
        amounts = [r["amount_cents"] for r in self.records()]
        self.assertEqual([3580, 3500, 50], amounts)

    def test_add_income_and_id_increment_keeps_old_records(self):
        """已有 3 条记录后再记一笔收入：id=4 严格自增，原记录不被改写。"""
        self.add("10", "餐饮", date="2026-10-01")
        self.add("20", "交通", date="2026-10-02")
        self.add("30", "购物", date="2026-10-03")
        before = self.records()

        rc, out, _ = self.add("1200", "其他", type_="income", date="2026-10-04")
        self.assertEqual(0, rc)
        self.assertEqual("已记录 #4：收入 1200.00 元 其他 2026-10-04\n", out)

        after = self.records()
        self.assertEqual([1, 2, 3, 4], [r["id"] for r in after])
        self.assertEqual(before, after[:3], "原有记录不得被改写")
        self.assertEqual(120000, after[3]["amount_cents"])
        self.assertEqual("income", after[3]["type"])

    def test_note_saved_verbatim_and_echoed(self):
        """--note 保存原文（含逗号/引号），并在确认行以全角括号回显。"""
        note = '午餐,外带 "加辣"'
        rc, out, _ = self.add("35.80", "餐饮", date="2026-10-23", note=note)
        self.assertEqual(0, rc)
        self.assertEqual("已记录 #1：支出 35.80 元 餐饮 2026-10-23（%s）\n" % note, out)
        self.assertEqual(note, self.records()[0]["note"])

    def test_note_empty_string_via_cli(self):
        """显式传 --note "" 时不回显括号、存空字符串。"""
        rc, out, _ = self.add("35.80", "餐饮", date="2026-10-23", note="")
        self.assertEqual(0, rc)
        self.assertNotIn("（", out)
        self.assertEqual("", self.records()[0]["note"])

    def test_summary_monthly_totals_and_month_filter(self):
        """按月汇总：收入/支出/结余与笔数正确，其他月份记录不计入。"""
        self.seed_october()
        rc, out, err = self.cli("summary", "--month", "2026-10", "--data", self.data)
        self.assertEqual(0, rc, "stderr=%r" % err)
        self.assertEqual(
            "2026-10 汇总（人民币 CNY）\n"
            "收入：1 笔   1200.00 元\n"
            "支出：3 笔   3456.78 元\n"
            "结余：-2256.78 元\n",
            out,
        )

    def test_summary_excludes_other_months(self):
        """2026-09 只有一笔 50 元支出，汇总里不得出现 10 月数据。"""
        self.seed_october()
        rc, out, _ = self.cli("summary", "--month", "2026-09", "--data", self.data)
        self.assertEqual(0, rc)
        self.assertIn("2026-09 汇总（人民币 CNY）", out)
        self.assertIn("支出：1 笔   50.00 元", out)
        self.assertIn("结余：-50.00 元", out)

    def test_summary_only_expense_negative_balance(self):
        """该月只有支出：收入显示 0 笔 0.00 元，结余为负并带 - 号。"""
        self.add("35.80", "餐饮", date="2026-10-23")
        rc, out, _ = self.cli("summary", "--month", "2026-10", "--data", self.data)
        self.assertEqual(0, rc)
        self.assertIn("收入：0 笔   0.00 元", out)
        self.assertIn("支出：1 笔   35.80 元", out)
        self.assertIn("结余：-35.80 元", out)

    def test_summary_balance_zero_no_minus_sign(self):
        """收支相等时结余显示 0.00 元，不得出现 -0.00。"""
        self.add("100", "餐饮", type_="income", date="2026-10-01")
        self.add("100", "餐饮", date="2026-10-02")
        rc, out, _ = self.cli("summary", "--month", "2026-10", "--data", self.data)
        self.assertEqual(0, rc)
        self.assertIn("结余：0.00 元", out)
        self.assertNotIn("-0.00", out)

    def test_stats_by_category_order_and_ratio(self):
        """按分类统计：金额降序、占比 1 位小数、合计行 100.0%（不补差）。"""
        self.seed_october()
        rc, out, err = self.cli("stats", "--month", "2026-10", "--data", self.data)
        self.assertEqual(0, rc, "stderr=%r" % err)
        lines = out.rstrip("\n").split("\n")
        self.assertEqual("2026-10 支出分类统计（共 3456.78 元 / 3 笔）", lines[0])
        # 列宽是排版细节：空白弹性匹配，数值与行序是硬契约（技术方案 4.5）
        self.assertRegex(lines[1], r"^分类\s+笔数\s+金额\(元\)\s+占比$")
        self.assertRegex(lines[2], r"^购物\s+1\s+1356\.78\s+39\.2%$")
        self.assertRegex(lines[3], r"^餐饮\s+1\s+1200\.00\s+34\.7%$")
        self.assertRegex(lines[4], r"^交通\s+1\s+900\.00\s+26\.0%$")
        self.assertRegex(lines[5], r"^合计\s+3\s+3456\.78\s+100\.0%$")
        self.assertEqual(6, len(lines), "只列出有记录的分类 + 表头 + 合计行")
        # 各分类占比之和 99.9% 与合计行 100.0% 不等，属 PRD 允许的正常差异
        self.assertAlmostEqual(99.9, 39.2 + 34.7 + 26.0, places=6)

    def test_stats_amount_tie_uses_fixed_category_order(self):
        """金额并列时按固定 8 类顺序（餐饮、交通、购物…）排列，输出可重复。"""
        self.add("500", "购物", date="2026-10-01")
        self.add("500", "餐饮", date="2026-10-02")
        self.add("500", "交通", date="2026-10-03")
        rc, out, _ = self.cli("stats", "--month", "2026-10", "--data", self.data)
        self.assertEqual(0, rc)
        order = [line.split()[0] for line in out.rstrip("\n").split("\n")[2:5]]
        self.assertEqual(["餐饮", "交通", "购物"], order)

    def test_stats_type_income_switch(self):
        """--type income 只统计收入一侧。"""
        self.seed_october()
        rc, out, _ = self.cli("stats", "--month", "2026-10", "--type", "income",
                              "--data", self.data)
        self.assertEqual(0, rc)
        self.assertEqual("2026-10 收入分类统计（共 1200.00 元 / 1 笔）", out.split("\n")[0])
        self.assertRegex(out.split("\n")[2], r"^其他\s+1\s+1200\.00\s+100\.0%$")
        self.assertNotIn("餐饮", out, "收入统计不得混入支出分类")

    def test_stats_default_type_is_expense(self):
        """不传 --type 时默认统计支出。"""
        self.seed_october()
        rc, out, _ = self.cli("stats", "--month", "2026-10", "--data", self.data)
        self.assertEqual(0, rc)
        self.assertIn("支出分类统计", out)

    def test_data_file_structure_and_encoding(self):
        """数据文件为 UTF-8 无 BOM 的 JSON：version=1、records 数组、id 升序、2 空格缩进。"""
        self.seed_october()
        with open(self.data, "rb") as handle:
            raw = handle.read()
        self.assertFalse(raw.startswith(b"\xef\xbb\xbf"), "不得写 BOM")
        text = raw.decode("utf-8")
        self.assertIn('\n  "version": 1,', text, "应为 2 空格缩进的 JSON")
        data = json.loads(text)
        self.assertEqual(1, data["version"])
        self.assertIsInstance(data["records"], list)
        self.assertEqual([1, 2, 3, 4, 5], [r["id"] for r in data["records"]])
        for record in data["records"]:
            self.assertEqual({"id", "type", "amount_cents", "category", "date",
                              "note", "created_at"}, set(record))
            self.assertIsInstance(record["amount_cents"], int)
            self.assertTrue(record["amount_cents"] > 0)

    def test_add_creates_missing_directory(self):
        """--data 指向不存在的子目录时，add 自动创建目录并写入。"""
        target = os.path.join(self._tmp.name, "sub", "dir", "ledger.json")
        rc, out, err = run_cli("add", "--type", "expense", "--amount", "1",
                               "--category", "餐饮", "--date", "2026-10-23",
                               "--data", target)
        self.assertEqual(0, rc, "stderr=%r" % err)
        self.assertTrue(os.path.exists(target))
        self.assertIn("已记录 #1：支出 1.00 元 餐饮 2026-10-23", out)


# ======================================================================
# 二、边界情况
# ======================================================================

class TestBoundary(CliTestCase):
    """空数据、无记录月份、金额 0 / 0.01 / 大额、跨月多笔。"""

    def test_summary_without_data_file_all_zero_without_creating_file(self):
        """数据文件不存在：三行全 0，退出码 0，且不创建文件。"""
        self.assertFalse(os.path.exists(self.data))
        rc, out, err = self.cli("summary", "--month", "2026-10", "--data", self.data)
        self.assertEqual(0, rc, "stderr=%r" % err)
        self.assertEqual("收入：0 笔 0.00 元\n支出：0 笔 0.00 元\n结余：0.00 元\n", out)
        self.assertFalse(os.path.exists(self.data), "只读命令不得创建数据文件")

    def test_stats_without_data_file_prompt_without_creating_file(self):
        """数据文件不存在：stats 只输出一行提示，退出码 0，不创建文件。"""
        rc, out, err = self.cli("stats", "--month", "2026-10", "--data", self.data)
        self.assertEqual(0, rc, "stderr=%r" % err)
        self.assertEqual("该月没有支出记录\n", out)
        self.assertFalse(os.path.exists(self.data))

    def test_summary_month_without_records_all_zero(self):
        """文件里有 10 月记录，查 11 月：按全 0 输出，退出码 0，不报「无数据」。"""
        self.add("35.80", "餐饮", date="2026-10-23")
        rc, out, _ = self.cli("summary", "--month", "2026-11", "--data", self.data)
        self.assertEqual(0, rc)
        self.assertEqual("收入：0 笔 0.00 元\n支出：0 笔 0.00 元\n结余：0.00 元\n", out)

    def test_stats_month_without_records_no_table(self):
        """该月无该类型记录：只输出提示、无表格、退出码 0。"""
        self.add("35.80", "餐饮", date="2026-10-23")
        rc, out, _ = self.cli("stats", "--month", "2026-09", "--data", self.data)
        self.assertEqual(0, rc)
        self.assertEqual("该月没有支出记录\n", out)
        rc, out, _ = self.cli("stats", "--month", "2026-10", "--type", "income",
                              "--data", self.data)
        self.assertEqual(0, rc)
        self.assertEqual("该月没有收入记录\n", out)

    def test_amount_zero_rejected_without_writing(self):
        """金额 0 / 0.00 非法，退出码 2，不写文件。"""
        for value in ("0", "0.00", "00", "0.0"):
            with self.subTest(amount=value):
                rc, out, err = self.add(value, "餐饮", date="2026-10-23")
                self.assertEqual(2, rc)
                self.assertEqual("", out)
                self.assertTrue(err.startswith("错误："), err)
                self.assertFalse(os.path.exists(self.data))

    def test_amount_smallest_positive(self):
        """最小正金额 0.01 元 → 1 分，且 0.01 累加不出现浮点误差。"""
        self.add("0.01", "餐饮", date="2026-10-01")
        self.add("0.02", "餐饮", date="2026-10-02")
        self.assertEqual([1, 2], [r["amount_cents"] for r in self.records()])
        rc, out, _ = self.cli("summary", "--month", "2026-10", "--data", self.data)
        self.assertEqual(0, rc)
        self.assertIn("支出：2 笔   0.03 元", out)

    def test_amount_max_supported_value(self):
        """上限 999999999999.99 元可写入，落盘 99999999999999 分，汇总显示正确。"""
        self.add("999999999999.99", "其他", date="2026-10-01")
        self.assertEqual(99999999999999, self.records()[0]["amount_cents"])
        rc, out, _ = self.cli("summary", "--month", "2026-10", "--data", self.data)
        self.assertEqual(0, rc)
        self.assertIn("支出：1 笔   999999999999.99 元", out)

    def test_amount_over_limit_rejected(self):
        """超上限（整数部分 13 位 / 小数 3 位）非法，退出码 2。"""
        for value in ("1000000000000", "999999999999.999", "10000000000000.00"):
            with self.subTest(amount=value):
                rc, out, err = self.add(value, "餐饮", date="2026-10-23")
                self.assertEqual(2, rc)
                self.assertIn("金额必须是正数", err)
                self.assertFalse(os.path.exists(self.data))

    def test_cross_month_multiple_records(self):
        """跨 3 个月多笔记录：各月汇总互不串月，分类统计只看目标月。"""
        self.add("100.00", "餐饮", date="2026-08-31")
        self.add("200.00", "交通", date="2026-08-31")
        self.add("300.50", "购物", date="2026-09-01")
        self.add("0.01", "餐饮", date="2026-09-30")
        self.add("1000", "其他", type_="income", date="2026-10-01")
        self.add("400.40", "居住", date="2026-10-31")

        rc, aug, _ = self.cli("summary", "--month", "2026-08", "--data", self.data)
        self.assertEqual(0, rc)
        self.assertIn("支出：2 笔   300.00 元", aug)
        rc, sep, _ = self.cli("summary", "--month", "2026-09", "--data", self.data)
        self.assertEqual(0, rc)
        self.assertIn("支出：2 笔   300.51 元", sep)
        rc, oct_, _ = self.cli("summary", "--month", "2026-10", "--data", self.data)
        self.assertEqual(0, rc)
        self.assertIn("收入：1 笔   1000.00 元", oct_)
        self.assertIn("支出：1 笔   400.40 元", oct_)
        self.assertIn("结余：599.60 元", oct_)

        rc, out, _ = self.cli("stats", "--month", "2026-09", "--data", self.data)
        self.assertEqual(0, rc)
        self.assertIn("共 300.51 元 / 2 笔", out)
        self.assertNotIn("居住", out)

    def test_float_precision_0_10_plus_0_20(self):
        """PRD 故事 1 硬断言：0.10 + 0.20 必须显示 0.30，不出现浮点尾数。"""
        self.add("0.10", "交通", date="2026-10-01")
        self.add("0.20", "交通", date="2026-10-02")
        self.assertEqual([10, 20], [r["amount_cents"] for r in self.records()])
        rc, out, _ = self.cli("summary", "--month", "2026-10", "--data", self.data)
        self.assertEqual(0, rc)
        self.assertIn("支出：2 笔   0.30 元", out)
        self.assertNotIn("0.3000", out)
        rc, out, _ = self.cli("stats", "--month", "2026-10", "--data", self.data)
        self.assertEqual(0, rc)
        self.assertIn("共 0.30 元 / 2 笔", out)

    def test_leap_year_date_accepted(self):
        """2024-02-29 合法（闰年）；2025-02-29 非法。"""
        rc, _, _ = self.add("10", "餐饮", date="2024-02-29")
        self.assertEqual(0, rc)
        rc, _, err = self.add("10", "餐饮", date="2025-02-29")
        self.assertEqual(2, rc)
        self.assertIn("日期", err)

    def test_default_date_is_local_today(self):
        """不传 --date 时使用本机当天日期，并在输出中回显。"""
        today = date.today().isoformat()
        rc, out, _ = self.add("35.80", "餐饮")
        self.assertEqual(0, rc)
        self.assertEqual("已记录 #1：支出 35.80 元 餐饮 %s\n" % today, out)
        self.assertEqual(today, self.records()[0]["date"])

    def test_summary_zero_branch_has_no_title_line(self):
        """已知输出差异：无记录分支只有 3 行、没有标题行；有记录分支带标题行。

        PRD 故事 2 的零记录验收只要求这三行，故实现符合 PRD；此处把差异固化，
        防止后续改动无声漂移（见 docs/测试报告.md 风险 R1）。
        """
        self.add("35.80", "餐饮", date="2026-10-23")
        _, zero_out, _ = self.cli("summary", "--month", "2026-11", "--data", self.data)
        self.assertEqual(3, len(zero_out.rstrip("\n").split("\n")))
        self.assertNotIn("汇总（人民币 CNY）", zero_out)
        _, normal_out, _ = self.cli("summary", "--month", "2026-10", "--data", self.data)
        self.assertIn("2026-10 汇总（人民币 CNY）", normal_out)


# ======================================================================
# 三、异常情况
# ======================================================================

class TestInvalidInput(CliTestCase):
    """非法分类、非法日期、非法月份、金额格式错误、非法类型、IO 错误。"""

    def _assert_rejected_and_untouched(self, args, expect_in_err, expect_rc=2,
                                       err_prefix=True):
        """执行命令并断言：非 0 退出码、错误写 stderr、数据文件内容不变。

        err_prefix=True 表示错误来自本实现的校验（stderr 首行即以「错误：」开头）；
        argparse 层报错会先打印 usage 再打印「错误：」，故传 False。
        """
        before = sha256_of(self.data) if os.path.exists(self.data) else None
        rc, out, err = run_cli(*args)
        self.assertEqual(expect_rc, rc, "stdout=%r stderr=%r" % (out, err))
        self.assertEqual("", out, "错误场景不得写 stdout")
        if err_prefix:
            self.assertTrue(err.startswith("错误："), err)
        self.assertIn("错误：", err)
        self.assertIn(expect_in_err, err)
        after = sha256_of(self.data) if os.path.exists(self.data) else None
        self.assertEqual(before, after, "校验失败时数据文件必须不变")

    def test_invalid_category(self):
        """分类不在 8 类内或为空：退出码 2，提示合法 8 类。"""
        self.add("10", "餐饮", date="2026-10-01")  # 先建立基线文件
        for bad in ("旅行", "", "餐饮 ", "dining"):
            with self.subTest(category=bad):
                self._assert_rejected_and_untouched(
                    ["add", "--type", "expense", "--amount", "10", "--category", bad,
                     "--date", "2026-10-23", "--data", self.data],
                    "分类必须是以下 8 类之一：餐饮、交通、购物、居住、娱乐、医疗、教育、其他",
                )

    def test_invalid_date_format_or_nonexistent(self):
        """日期格式错误或不存在：退出码 2，不写入。"""
        self.add("10", "餐饮", date="2026-10-01")
        for bad in ("2026-02-30", "2026-2-3", "2026/10/23", "20261023", "", "   ",
                    "2026-13-01"):
            with self.subTest(date=bad):
                self._assert_rejected_and_untouched(
                    ["add", "--type", "expense", "--amount", "10", "--category", "餐饮",
                     "--date", bad, "--data", self.data],
                    "日期不存在或格式应为 YYYY-MM-DD",
                )

    def test_invalid_month_format(self):
        """月份格式非法：退出码 2（summary 与 stats 都要拦）。"""
        self.add("10", "餐饮", date="2026-10-01")
        for bad in ("2026-13", "2026-1", "202610", "", "2026-00", "2026/10"):
            with self.subTest(month=bad):
                self._assert_rejected_and_untouched(
                    ["summary", "--month", bad, "--data", self.data], "月份格式应为 YYYY-MM")
                self._assert_rejected_and_untouched(
                    ["stats", "--month", bad, "--data", self.data], "月份格式应为 YYYY-MM")

    def test_invalid_amount_formats(self):
        """金额格式错误：abc、负数、千分位、货币符号、空格、科学计数法、超 2 位小数。"""
        self.add("10", "餐饮", date="2026-10-01")
        for bad in ("abc", "-5", "-0.01", "+5", "1.005", "1,200", "¥35", "35元",
                    " 35", "35 ", "1e3", "一", "35.8.0"):
            with self.subTest(amount=bad):
                self._assert_rejected_and_untouched(
                    ["add", "--type", "expense", "--amount", bad, "--category", "餐饮",
                     "--date", "2026-10-23", "--data", self.data],
                    "金额必须是正数（单位：元），最多两位小数",
                )

    def test_invalid_type(self):
        """--type 传 transfer 等非法值：退出码 2。"""
        self.add("10", "餐饮", date="2026-10-01")
        for bad in ("transfer", "EXPENSE", "", "支出"):
            with self.subTest(type=bad):
                self._assert_rejected_and_untouched(
                    ["add", "--type", bad, "--amount", "10", "--category", "餐饮",
                     "--data", self.data], "--type", err_prefix=False)

    def test_invalid_stats_type(self):
        """stats 的 --type 传 transfer：退出码 2。"""
        self.add("10", "餐饮", date="2026-10-01")
        self._assert_rejected_and_untouched(
            ["stats", "--month", "2026-10", "--type", "transfer", "--data", self.data],
            "--type", err_prefix=False)

    def test_missing_required_arguments(self):
        """缺少必需参数（add 无 --amount、summary 无 --month、无子命令）退出码 2。"""
        self.add("10", "餐饮", date="2026-10-01")
        self._assert_rejected_and_untouched(
            ["add", "--type", "expense", "--category", "餐饮", "--data", self.data],
            "--amount", err_prefix=False)
        self._assert_rejected_and_untouched(
            ["summary", "--data", self.data], "--month", err_prefix=False)
        self._assert_rejected_and_untouched([], "usage", err_prefix=False)

    def test_export_subcommand_absent(self):
        """本期不做导出 CSV：export 子命令不存在，调用即退出码 2（锁定范围）。"""
        self.add("10", "餐饮", date="2026-10-01")
        self._assert_rejected_and_untouched(
            ["export", "--month", "2026-10", "--data", self.data],
            "invalid choice", err_prefix=False)

    def test_unwritable_data_path(self):
        """数据文件所在路径不可写（父路径是普通文件）：退出码 3，不留下半截文件。"""
        blocker = os.path.join(self._tmp.name, "afile")
        with open(blocker, "w", encoding="utf-8") as handle:
            handle.write("x")
        target = os.path.join(blocker, "ledger.json")
        rc, out, err = run_cli("add", "--type", "expense", "--amount", "5",
                               "--category", "餐饮", "--date", "2026-10-23",
                               "--data", target)
        self.assertEqual(3, rc, "stdout=%r stderr=%r" % (out, err))
        self.assertTrue(err.startswith("错误："), err)
        self.assertEqual("", out)
        self.assertFalse(os.path.exists(target))

    def test_corrupt_data_file_exit_3_and_not_rewritten(self):
        """数据文件损坏 / version 未知：退出码 3，原文件绝不被改写。"""
        cases = [
            ('{"version": 2, "records": []}', "version"),
            ('{"version": 1, "records": ', "JSON 解析失败"),
            ('{"version": 1, "records": [{"id": 1, "type": "expense", '
             '"amount_cents": 35.5, "category": "餐饮", "date": "2026-10-01"}]}',
             "amount_cents"),
            ('{"version": 1, "records": [{"id": 1, "type": "expense", '
             '"amount_cents": 100, "category": "旅行", "date": "2026-10-01"}]}',
             "category"),
        ]
        for payload, keyword in cases:
            with self.subTest(keyword=keyword):
                with open(self.data, "w", encoding="utf-8") as handle:
                    handle.write(payload)
                before = sha256_of(self.data)
                rc, out, err = run_cli("summary", "--month", "2026-10", "--data", self.data)
                self.assertEqual(3, rc, "stdout=%r stderr=%r" % (out, err))
                self.assertIn("数据文件格式不正确", err)
                self.assertIn(keyword, err)
                self.assertEqual(before, sha256_of(self.data), "损坏文件不得被改写")

    def test_add_on_corrupt_file_exit_3_and_not_rewritten(self):
        """数据文件损坏时 add 也不能覆盖它（退出码 3）。"""
        payload = '{"version": 1, "records": [{"id": 1, "type": "expense", "amount_cents": 1}]}'
        with open(self.data, "w", encoding="utf-8") as handle:
            handle.write(payload)
        before = sha256_of(self.data)
        rc, out, err = self.add("10", "餐饮", date="2026-10-23")
        self.assertEqual(3, rc, "stdout=%r stderr=%r" % (out, err))
        self.assertEqual(before, sha256_of(self.data))


# ======================================================================
# 四、模块级单元测试（金额整数化与校验函数）
# ======================================================================

class TestMoneyUnit(unittest.TestCase):
    """依据《记账规范（2026 版）》一、金额：整数分存储、禁止浮点。"""

    def test_parse_amount_to_cents_returns_int(self):
        for text, expected in (("35", 3500), ("35.8", 3580), ("35.80", 3580),
                               ("0.05", 5), ("0.01", 1), ("1200", 120000),
                               ("999999999999.99", 99999999999999)):
            with self.subTest(amount=text):
                cents = ledger.parse_amount_to_cents(text)
                self.assertIsInstance(cents, int)
                self.assertNotIsInstance(cents, bool)
                self.assertEqual(expected, cents)

    def test_parse_amount_to_cents_rejects_invalid(self):
        for text in ("", "0", "0.00", "-5", "+5", "1.005", "1,200", "¥35", " 35",
                     "35 ", "1e3", "abc", "1000000000000", "35.8.0"):
            with self.subTest(amount=text):
                with self.assertRaises(ledger.ValidationError):
                    ledger.parse_amount_to_cents(text)

    def test_format_cents(self):
        self.assertEqual("35.80", ledger.format_cents(3580))
        self.assertEqual("1200.00", ledger.format_cents(120000))
        self.assertEqual("0.01", ledger.format_cents(1))
        self.assertEqual("0.00", ledger.format_cents(0))
        self.assertEqual("-2256.78", ledger.format_cents(-225678))
        self.assertEqual("0.00", ledger.format_cents(-0))
        self.assertEqual("999999999999.99", ledger.format_cents(99999999999999))

    def test_format_cents_roundtrip_for_sample_values(self):
        """分 → 元 → 分 往返一致（字符串解析无精度损失）。"""
        for cents in (1, 5, 99, 100, 3580, 120000, 99999999999999):
            self.assertEqual(cents, ledger.parse_amount_to_cents(ledger.format_cents(cents)))


class TestValidatorsUnit(unittest.TestCase):
    """依据《记账规范（2026 版）》二、日期与三、分类。"""

    def test_validate_date_accepts_valid(self):
        for text in ("2026-10-23", "2024-02-29", "2026-01-01", "2026-12-31"):
            self.assertEqual(text, ledger.validate_date(text))

    def test_validate_date_rejects_invalid(self):
        for text in ("2026-02-30", "2026-2-3", "2026/10/23", "20261023", "", "   ",
                     "2026-13-01", "2026-10-32", None, 20261023):
            with self.subTest(date=text):
                with self.assertRaises(ledger.ValidationError):
                    ledger.validate_date(text)

    def test_validate_month(self):
        self.assertEqual("2026-10", ledger.validate_month("2026-10"))
        for text in ("2026-13", "2026-1", "202610", "", "2026-00", "2026/10", None):
            with self.subTest(month=text):
                with self.assertRaises(ledger.ValidationError):
                    ledger.validate_month(text)

    def test_categories_are_exactly_eight(self):
        self.assertEqual(("餐饮", "交通", "购物", "居住", "娱乐", "医疗", "教育", "其他"),
                         tuple(ledger.CATEGORIES))
        for name in ledger.CATEGORIES:
            self.assertEqual(name, ledger.validate_category(name))
        for bad in ("旅行", "", "dining", "餐饮 "):
            with self.subTest(category=bad):
                with self.assertRaises(ledger.ValidationError):
                    ledger.validate_category(bad)

    def test_validate_type(self):
        self.assertEqual("income", ledger.validate_type("income"))
        self.assertEqual("expense", ledger.validate_type("expense"))
        for bad in ("transfer", "EXPENSE", "", None):
            with self.subTest(type=bad):
                with self.assertRaises(ledger.ValidationError):
                    ledger.validate_type(bad)


class TestNoFloatSourceGate(unittest.TestCase):
    """PRD 指标「代码中 float 出现 0 次」+《记账规范》禁止浮点的可执行卡口。"""

    PATTERNS = (
        (re.compile(r"float\("), "出现 float() 调用"),
        (re.compile(r":\s*float\b"), "出现 float 类型注解"),
        (re.compile(r"[^/]/[ \t]*100(?![0-9])"), "出现 /100 真除法（金额换算必须走 // 与 %）"),
    )

    def test_no_float_usage_in_implementation(self):
        for name in ("ledger.py", "cli.py"):
            path = os.path.join(CODE_DIR, name)
            with open(path, "r", encoding="utf-8") as handle:
                source = handle.read()
            for pattern, message in self.PATTERNS:
                with self.subTest(file=name, pattern=pattern.pattern):
                    hits = [line for line in source.split("\n") if pattern.search(line)]
                    self.assertEqual([], hits, "%s：%s -> %r" % (name, message, hits))


if __name__ == "__main__":
    unittest.main(verbosity=2)
