"""轻记：命令行记账工具的核心逻辑。

文件内按小节组织：金额 / 校验 / 模型 / 存储 / 报表。
所有金额一律以「分」（int）为单位存储与计算，全程不使用小数类型。
"""

import json
import os
import re
import tempfile
import unicodedata
from dataclasses import dataclass, field
from datetime import date as _date

# ======================================================================
# 常量
# ======================================================================

CATEGORIES = ("餐饮", "交通", "购物", "居住", "娱乐", "医疗", "教育", "其他")
TYPE_LABEL = {"income": "收入", "expense": "支出"}
CURRENCY = "CNY"

_AMOUNT_PATTERN = re.compile(r"^(\d{1,12})(?:\.(\d{1,2}))?$")
_DATE_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_MONTH_PATTERN = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")

_AMOUNT_MESSAGE = "金额必须是正数（单位：元），最多两位小数，例如 35.80"
_DATE_MESSAGE = "日期不存在或格式应为 YYYY-MM-DD（如 2026-10-23）"
_MONTH_MESSAGE = "月份格式应为 YYYY-MM（如 2026-10）"
_CATEGORY_MESSAGE = "分类必须是以下 8 类之一：" + "、".join(CATEGORIES)
_TYPE_MESSAGE = "类型必须是 income 或 expense 之一"


class ValidationError(Exception):
    """输入校验失败。"""


class DataFileError(Exception):
    """数据文件损坏、version 未知或字段不合法。"""


# ======================================================================
# 金额
# ======================================================================

def parse_amount_to_cents(text: str) -> int:
    """把「元」字符串解析为「分」整数；非法输入抛 ValidationError。"""
    if not isinstance(text, str):
        raise ValidationError(_AMOUNT_MESSAGE)
    matched = _AMOUNT_PATTERN.match(text)
    if matched is None:
        raise ValidationError(_AMOUNT_MESSAGE)
    whole = int(matched.group(1))
    fraction = matched.group(2)
    if fraction is None:
        cents = whole * 100
    else:
        cents = whole * 100 + int(fraction.ljust(2, "0"))
    if cents <= 0:
        raise ValidationError(_AMOUNT_MESSAGE)
    return cents


def format_cents(cents: int) -> str:
    """把「分」整数格式化为「元」字符串；负数带前导负号。"""
    if cents < 0:
        return "-" + format_cents(-cents)
    return f"{cents//100}.{cents%100:02d}"


# ======================================================================
# 校验
# ======================================================================

def validate_date(text) -> str:
    """校验并返回严格 YYYY-MM-DD 且真实存在的日期字符串。"""
    if not isinstance(text, str) or _DATE_PATTERN.match(text) is None:
        raise ValidationError(_DATE_MESSAGE)
    try:
        _date.fromisoformat(text)
    except ValueError:
        raise ValidationError(_DATE_MESSAGE)
    return text


def validate_month(text) -> str:
    """校验并返回 YYYY-MM 月份字符串。"""
    if not isinstance(text, str) or _MONTH_PATTERN.match(text) is None:
        raise ValidationError(_MONTH_MESSAGE)
    return text


def validate_category(text) -> str:
    """校验并返回合法分类。"""
    if text not in CATEGORIES:
        raise ValidationError(_CATEGORY_MESSAGE)
    return text


def validate_type(text) -> str:
    """校验并返回 income / expense。"""
    if text not in ("income", "expense"):
        raise ValidationError(_TYPE_MESSAGE)
    return text


# ======================================================================
# 模型
# ======================================================================

@dataclass
class Record:
    id: int
    type: str
    amount_cents: int
    category: str
    date: str
    note: str
    created_at: str

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "type": self.type,
            "amount_cents": self.amount_cents,
            "category": self.category,
            "date": self.date,
            "note": self.note,
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, data) -> "Record":
        reason = _record_problem(data)
        if reason is not None:
            raise DataFileError(reason)
        note = data.get("note", "")
        created_at = data.get("created_at", "")
        return cls(
            id=data["id"],
            type=data["type"],
            amount_cents=data["amount_cents"],
            category=data["category"],
            date=data["date"],
            note=note,
            created_at=created_at,
        )


@dataclass
class Ledger:
    version: int = 1
    records: list = field(default_factory=list)

    def next_id(self) -> int:
        if not self.records:
            return 1
        return max(record.id for record in self.records) + 1


def _is_positive_int(value) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def _record_problem(data) -> "str | None":
    """检查一条记录字典，合法返回 None，否则返回原因文本。"""
    if not isinstance(data, dict):
        return "记录必须是 JSON 对象"
    if not _is_positive_int(data.get("id")):
        return "id 必须是正整数"
    if data.get("type") not in ("income", "expense"):
        return "type 必须是 income 或 expense"
    if not _is_positive_int(data.get("amount_cents")):
        return "amount_cents 必须是正整数"
    if data.get("category") not in CATEGORIES:
        return "category 取值不合法"
    value = data.get("date")
    if not isinstance(value, str) or _DATE_PATTERN.match(value) is None:
        return "date 格式应为 YYYY-MM-DD"
    try:
        _date.fromisoformat(value)
    except ValueError:
        return "date 不是真实存在的日期"
    if not isinstance(data.get("note", ""), str):
        return "note 必须是字符串"
    if not isinstance(data.get("created_at", ""), str):
        return "created_at 必须是字符串"
    return None


# ======================================================================
# 存储
# ======================================================================

def _format_error(path: str, reason: str) -> str:
    return f"数据文件格式不正确（{path}）：{reason}"


def _ledger_to_data(ledger: Ledger) -> dict:
    return {
        "version": ledger.version,
        "records": [record.to_dict() for record in ledger.records],
    }


def load(path: str, missing_ok: bool = True) -> Ledger:
    """读取数据文件；不存在时按 missing_ok 决定返回空账本还是抛 IOError。"""
    if not os.path.exists(path):
        if missing_ok:
            return Ledger()
        raise IOError(f"数据文件不存在：{path}")

    try:
        with open(path, "r", encoding="utf-8") as handle:
            raw = handle.read()
    except UnicodeDecodeError as exc:
        raise DataFileError(_format_error(path, f"文件不是有效的 UTF-8 文本：{exc}"))

    try:
        data = json.loads(raw)
    except ValueError as exc:
        raise DataFileError(_format_error(path, f"JSON 解析失败：{exc}"))

    if not isinstance(data, dict):
        raise DataFileError(_format_error(path, "顶层必须是 JSON 对象"))

    version = data.get("version")
    if not isinstance(version, int) or isinstance(version, bool) or version != 1:
        raise DataFileError(_format_error(path, f"version 必须是 1（实际为 {version!r}）"))

    records = data.get("records")
    if not isinstance(records, list):
        raise DataFileError(_format_error(path, "records 必须是数组"))

    seen = set()
    parsed = []
    for index, item in enumerate(records):
        try:
            record = Record.from_dict(item)
        except DataFileError as exc:
            raise DataFileError(_format_error(path, f"第 {index + 1} 条记录：{exc}"))
        if record.id in seen:
            raise DataFileError(_format_error(path, f"第 {index + 1} 条记录：id {record.id} 重复"))
        seen.add(record.id)
        parsed.append(record)

    return Ledger(version=version, records=parsed)


def _write_atomic(path: str, ledger: Ledger) -> None:
    directory = os.path.dirname(os.path.abspath(path))
    if directory and not os.path.isdir(directory):
        try:
            os.makedirs(directory, exist_ok=True)
        except OSError as exc:
            raise IOError(str(exc))

    payload = json.dumps(_ledger_to_data(ledger), ensure_ascii=False, indent=2)

    try:
        handle_fd, temp_path = tempfile.mkstemp(prefix=".ledger-", suffix=".tmp", dir=directory)
    except OSError as exc:
        raise IOError(str(exc))

    try:
        with os.fdopen(handle_fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, path)
        temp_path = None
    except OSError as exc:
        raise IOError(str(exc))
    finally:
        if temp_path is not None and os.path.exists(temp_path):
            try:
                os.remove(temp_path)
            except OSError:
                pass


def append_record(path: str, record: Record) -> None:
    """读入 → 追加 → 原子替换写。

    调用方若需要知道「本次是否新建了数据文件」，在调用之前用
    ``os.path.exists(path)`` 判定即可（例如 ``cli.py add`` 的路径提示）。
    """
    ledger = load(path, missing_ok=True)
    ledger.records.append(record)
    _write_atomic(path, ledger)


# ======================================================================
# 报表
# ======================================================================

class Summary:
    """月度汇总结果。"""

    def __init__(self, income_count: int, income_cents: int,
                 expense_count: int, expense_cents: int):
        self.income_count = income_count
        self.income_cents = income_cents
        self.expense_count = expense_count
        self.expense_cents = expense_cents
        self.balance_cents = income_cents - expense_cents


class CategoryStat:
    """单个分类的统计结果。"""

    def __init__(self, category: str, count: int, amount_cents: int, ratio_tenths: int):
        self.category = category
        self.count = count
        self.amount_cents = amount_cents
        self.ratio_tenths = ratio_tenths


def monthly_summary(ledger: Ledger, month: str) -> Summary:
    income_count = 0
    income_cents = 0
    expense_count = 0
    expense_cents = 0
    for record in ledger.records:
        if record.date[:7] != month:
            continue
        if record.type == "income":
            income_count += 1
            income_cents += record.amount_cents
        else:
            expense_count += 1
            expense_cents += record.amount_cents
    return Summary(income_count, income_cents, expense_count, expense_cents)


def category_stats(ledger: Ledger, month: str, type_) -> list:
    totals = {}
    counts = {}
    total_cents = 0
    for record in ledger.records:
        if record.date[:7] != month or record.type != type_:
            continue
        totals[record.category] = totals.get(record.category, 0) + record.amount_cents
        counts[record.category] = counts.get(record.category, 0) + 1
        total_cents += record.amount_cents

    order = {name: index for index, name in enumerate(CATEGORIES)}
    stats = []
    for category, amount_cents in totals.items():
        if total_cents:
            ratio_tenths = (amount_cents * 2000 + total_cents) // (2 * total_cents)
        else:
            ratio_tenths = 0
        stats.append(CategoryStat(category, counts[category], amount_cents, ratio_tenths))
    stats.sort(key=lambda item: (-item.amount_cents, order[item.category]))
    return stats


def _display_width(text: str) -> int:
    width = 0
    for char in text:
        width += 2 if unicodedata.east_asian_width(char) in ("W", "F") else 1
    return width


def _pad(text: str, width: int, align: str) -> str:
    padding = width - _display_width(text)
    if padding < 0:
        padding = 0
    if align == "right":
        return " " * padding + text
    return text + " " * padding


def _format_ratio(ratio_tenths: int) -> str:
    return f"{ratio_tenths//10}.{ratio_tenths%10}%"


def _resolve_total(type_, total):
    """把合计参数归一化为 (笔数, 金额分)。"""
    if hasattr(total, "income_cents") and hasattr(total, "expense_cents"):
        if type_ == "income":
            return total.income_count, total.income_cents
        return total.expense_count, total.expense_cents
    if hasattr(total, "amount_cents"):
        return total.count, total.amount_cents
    if isinstance(total, (tuple, list)) and len(total) == 2:
        return int(total[0]), int(total[1])
    raise TypeError("无法识别的合计对象")


def _table_row(category, count, amount, ratio) -> str:
    return " ".join([
        _pad(category, 6, "left"),
        _pad(count, 6, "right"),
        _pad(amount, 10, "right"),
        _pad(ratio, 8, "right"),
    ])


def _summary_line(label: str, count: int, cents: int) -> str:
    prefix = f"{label}：{count} 笔"
    padding = max(1, 9 - len(prefix))
    return prefix + " " * padding + f"{format_cents(cents)} 元"


def render_summary(month: str, summary: Summary) -> str:
    lines = [
        f"{month} 汇总（人民币 {CURRENCY}）",
        _summary_line("收入", summary.income_count, summary.income_cents),
        _summary_line("支出", summary.expense_count, summary.expense_cents),
        f"结余：{format_cents(summary.balance_cents)} 元",
    ]
    return "\n".join(lines)


def render_stats_table(month: str, type_, stats, total) -> str:
    total_count, total_cents = _resolve_total(type_, total)
    lines = [
        f"{month} {TYPE_LABEL[type_]}分类统计"
        f"（共 {format_cents(total_cents)} 元 / {total_count} 笔）",
        _table_row("分类", "笔数", "金额(元)", "占比"),
    ]
    for item in stats:
        lines.append(_table_row(
            item.category,
            str(item.count),
            format_cents(item.amount_cents),
            _format_ratio(item.ratio_tenths),
        ))
    lines.append(_table_row("合计", str(total_count), format_cents(total_cents), "100.0%"))
    return "\n".join(lines)
