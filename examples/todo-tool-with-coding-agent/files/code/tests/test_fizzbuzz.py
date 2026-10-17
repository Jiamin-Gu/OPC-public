"""Tests for the standalone :mod:`fizzbuzz` module.

运行时 cwd 为 ``code/``，依赖 ``pyproject.toml`` 里已有的
``pythonpath = ["."]``，因此可以直接 ``from fizzbuzz import fizzbuzz``。
"""

import pytest

from fizzbuzz import fizzbuzz

# 显式列出期望值，避免在测试里复用被测逻辑。
EXPECTED_1 = [1]
EXPECTED_5 = [1, 2, "Fizz", 4, "Buzz"]
EXPECTED_15 = [
    1, 2, "Fizz", 4, "Buzz",
    "Fizz", 7, 8, "Fizz", "Buzz",
    11, "Fizz", 13, 14, "FizzBuzz",
]

# (位置 i, 期望值)，覆盖 1..15 的每一个位置。
EXPECTED_BY_POSITION = list(enumerate(EXPECTED_15, start=1))


def test_zero_returns_empty_list():
    assert fizzbuzz(0) == []


@pytest.mark.parametrize(
    "n, expected",
    [(1, EXPECTED_1), (5, EXPECTED_5), (15, EXPECTED_15)],
)
def test_exact_results(n, expected):
    assert fizzbuzz(n) == expected, "fizzbuzz(%d) 结果与期望不符" % n


@pytest.mark.parametrize("i, expected", EXPECTED_BY_POSITION, ids=["i=%d" % i for i, _ in EXPECTED_BY_POSITION])
def test_each_position_within_15(i, expected):
    result = fizzbuzz(15)
    assert result[i - 1] == expected, "位置 %d 期望 %r，实际 %r" % (i, expected, result[i - 1])


@pytest.mark.parametrize("n", [15, 30, 45])
def test_multiples_of_15_are_fizzbuzz(n):
    result = fizzbuzz(n)
    assert result[n - 1] == "FizzBuzz", "第 %d 项应为 'FizzBuzz'，实际 %r" % (n, result[n - 1])


@pytest.mark.parametrize("n, value", [(1, 1), (7, 7), (44, 44)])
def test_non_multiples_keep_int_type_and_value(n, value):
    item = fizzbuzz(n)[n - 1]
    assert item == value, "第 %d 项期望 %r，实际 %r" % (n, value, item)
    assert type(item) is int, "第 %d 项应为 int，实际为 %s" % (n, type(item).__name__)


@pytest.mark.parametrize("n", [0, 1, 5, 15, 100])
def test_returns_list_with_length_equal_to_n(n):
    result = fizzbuzz(n)
    assert isinstance(result, list), "fizzbuzz(%d) 应返回 list" % n
    assert len(result) == n, "fizzbuzz(%d) 长度应为 %d，实际 %d" % (n, n, len(result))
    if n:
        assert result[0] == 1, "fizzbuzz(%d) 的首项应为 1，实际 %r" % (n, result[0])


@pytest.mark.parametrize(
    "bad, message",
    [
        (-1, "n must be non-negative"),
        (-100, "n must be non-negative"),
        (3.5, "n must be an integer"),
        ("3", "n must be an integer"),
        (None, "n must be an integer"),
        (True, "n must be an integer"),
    ],
)
def test_invalid_input_raises_value_error_with_message(bad, message):
    with pytest.raises(ValueError) as excinfo:
        fizzbuzz(bad)
    assert str(excinfo.value) == message, (
        "输入 %r 的异常消息应为 %r，实际 %r" % (bad, message, str(excinfo.value))
    )
