"""轻记：命令行记账工具入口。

用法：
    python cli.py add --type expense --amount 35.80 --category 餐饮
    python cli.py summary --month 2026-10
    python cli.py stats --month 2026-10 --type expense
"""

import argparse
import os
import sys
from datetime import date, datetime

from ledger import (
    TYPE_LABEL,
    DataFileError,
    Record,
    ValidationError,
    append_record,
    category_stats,
    format_cents,
    load,
    monthly_summary,
    parse_amount_to_cents,
    render_stats_table,
    render_summary,
    validate_category,
    validate_date,
    validate_month,
)

DEFAULT_DATA = "./data/ledger.json"


class _Parser(argparse.ArgumentParser):
    """解析失败时输出用法与「错误：...」，并以退出码 2 结束。"""

    def error(self, message):
        self.print_usage(sys.stderr)
        sys.stderr.write("错误：" + message + "\n")
        raise SystemExit(2)


def build_parser() -> _Parser:
    parser = _Parser(prog="cli.py", description="轻记：命令行记账工具")
    subparsers = parser.add_subparsers(dest="command", required=True)

    add_parser = subparsers.add_parser("add", help="记一笔")
    add_parser.add_argument("--type", required=True, choices=["income", "expense"])
    add_parser.add_argument("--amount", required=True)
    add_parser.add_argument("--category", required=True)
    add_parser.add_argument("--date", default=None)
    add_parser.add_argument("--note", default="")
    add_parser.add_argument("--data", default=DEFAULT_DATA)

    summary_parser = subparsers.add_parser("summary", help="月度汇总")
    summary_parser.add_argument("--month", required=True)
    summary_parser.add_argument("--data", default=DEFAULT_DATA)

    stats_parser = subparsers.add_parser("stats", help="分类统计")
    stats_parser.add_argument("--month", required=True)
    stats_parser.add_argument("--type", choices=["expense", "income"], default="expense")
    stats_parser.add_argument("--data", default=DEFAULT_DATA)

    return parser


def _write_error(message) -> None:
    text = str(message).replace("\r", " ").replace("\n", " ")
    sys.stderr.write("错误：" + text + "\n")


def cmd_add(args) -> int:
    # 先全量校验，任何失败都不触碰数据文件。
    amount_cents = parse_amount_to_cents(args.amount)
    category = validate_category(args.category)
    if args.date is None:
        record_date = date.today().isoformat()
    else:
        record_date = validate_date(args.date)
    note = args.note if args.note is not None else ""

    ledger = load(args.data, missing_ok=True)
    record = Record(
        id=ledger.next_id(),
        type=args.type,
        amount_cents=amount_cents,
        category=category,
        date=record_date,
        note=note,
        created_at=datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
    )
    existed = os.path.exists(args.data)
    append_record(args.data, record)
    if not existed:
        absolute = os.path.normpath(os.path.abspath(args.data))
        sys.stderr.write(f"（已新建数据文件：{absolute}）\n")

    line = (
        f"已记录 #{record.id}：{TYPE_LABEL[args.type]} "
        f"{format_cents(amount_cents)} 元 {category} {record_date}"
    )
    if note:
        line += f"（{note}）"
    sys.stdout.write(line + "\n")
    return 0


def cmd_summary(args) -> int:
    month = validate_month(args.month)
    ledger = load(args.data, missing_ok=True)
    summary = monthly_summary(ledger, month)
    if summary.income_count == 0 and summary.expense_count == 0:
        sys.stdout.write(
            "收入：0 笔 0.00 元\n"
            "支出：0 笔 0.00 元\n"
            "结余：0.00 元\n"
        )
        return 0
    sys.stdout.write(render_summary(month, summary) + "\n")
    return 0


def cmd_stats(args) -> int:
    month = validate_month(args.month)
    type_ = args.type
    ledger = load(args.data, missing_ok=True)
    stats = category_stats(ledger, month, type_)
    if not stats:
        sys.stdout.write(f"该月没有{TYPE_LABEL[type_]}记录\n")
        return 0
    summary = monthly_summary(ledger, month)
    sys.stdout.write(render_stats_table(month, type_, stats, summary) + "\n")
    return 0


def main(argv=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "add":
            return cmd_add(args)
        if args.command == "summary":
            return cmd_summary(args)
        if args.command == "stats":
            return cmd_stats(args)
        parser.error("请指定子命令")
        return 2
    except ValidationError as exc:
        _write_error(exc)
        return 2
    except (DataFileError, IOError) as exc:
        _write_error(exc)
        return 3
    except Exception as exc:  # 兜底，避免抛出栈信息
        _write_error(exc)
        return 1


if __name__ == "__main__":
    sys.exit(main())
