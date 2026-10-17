"""一个独立、零依赖的 FizzBuzz 模块。

对外只暴露 :func:`fizzbuzz`，并支持 ``python fizzbuzz.py N`` 直接运行。
"""

import argparse
import sys


def fizzbuzz(n):
    # type: (int) -> list
    """返回 ``1..n`` 的 FizzBuzz 结果列表。

    规则:
        对于 ``1..n`` 中的每个整数 ``i``：

        * ``i`` 同时是 3 和 5 的倍数 -> 字符串 ``"FizzBuzz"``
        * ``i`` 只是 3 的倍数        -> 字符串 ``"Fizz"``
        * ``i`` 只是 5 的倍数        -> 字符串 ``"Buzz"``
        * 其他情况                  -> 该整数本身（``int``，不做 ``str()`` 转换）

    参数:
        n (int): 上界（含）。必须是整数（``bool`` 视为非法），且 ``n >= 0``。

    返回:
        list: 长度为 ``n`` 的列表，按 ``1, 2, ..., n`` 的顺序排列；
        ``n == 0`` 时返回 ``[]``。

    异常:
        ValueError: ``n`` 不是合法整数时消息为 ``"n must be an integer"``；
            ``n < 0`` 时消息为 ``"n must be non-negative"``。

    示例:
        >>> fizzbuzz(0)
        []
        >>> fizzbuzz(1)
        [1]
        >>> fizzbuzz(5)
        [1, 2, 'Fizz', 4, 'Buzz']
        >>> fizzbuzz(3)
        [1, 2, 'Fizz']
        >>> fizzbuzz(15)[-1]
        'FizzBuzz'
    """
    if isinstance(n, bool) or not isinstance(n, int):
        raise ValueError("n must be an integer")
    if n < 0:
        raise ValueError("n must be non-negative")

    result = []
    for i in range(1, n + 1):
        if i % 15 == 0:
            result.append("FizzBuzz")
        elif i % 3 == 0:
            result.append("Fizz")
        elif i % 5 == 0:
            result.append("Buzz")
        else:
            result.append(i)
    return result


def _system_exit_code(exc):
    # type: (SystemExit) -> int
    """把 argparse 抛出的 ``SystemExit`` 映射为进程退出码。"""
    code = exc.code
    if code is None:
        return 0
    if isinstance(code, int):
        return code
    return 2


def main(argv=None):
    # type: (object) -> int
    """命令行入口：打印 ``1..n`` 的 FizzBuzz 结果，每行一个。

    正常时打印到 stdout 并返回 ``0``；参数缺失或非法时把错误写到 stderr
    并返回 ``2``。该函数总是返回 ``int``，不会直接抛出 ``SystemExit``。
    """
    parser = argparse.ArgumentParser(
        prog="fizzbuzz",
        description="Print the FizzBuzz sequence from 1 to N, one item per line.",
    )
    parser.add_argument(
        "n",
        metavar="N",
        type=int,
        help="Upper bound (inclusive), must be an integer >= 0.",
    )
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        return _system_exit_code(exc)

    try:
        values = fizzbuzz(args.n)
    except ValueError as exc:
        print("error: %s" % exc, file=sys.stderr)
        return 2

    for value in values:
        print(value)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
