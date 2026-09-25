"""The safe calculator must compute correctly and refuse everything else."""

from __future__ import annotations

import math

import pytest

from app.agents.tools.calculator import CalculatorError, evaluate, try_evaluate


@pytest.mark.parametrize(
    ("expression", "expected"),
    [
        ("2 + 3 * 4", 14),
        ("(2 + 3) * 4", 20),
        ("10 / 4", 2.5),
        ("2 ** 10", 1024),
        ("17 % 5", 2),
        ("sqrt(144)", 12.0),
        ("abs(-7)", 7),
        ("round(3.14159, 2)", 3.14),
        ("max(3, 9, 4)", 9),
        ("factorial(5)", 120),
        ("-5 + 3", -2),
    ],
)
def test_evaluates_arithmetic(expression: str, expected: float) -> None:
    assert evaluate(expression).value == pytest.approx(expected)


def test_constants_are_available() -> None:
    assert evaluate("pi").value == pytest.approx(math.pi)


@pytest.mark.parametrize(
    "expression",
    [
        "__import__('os').system('ls')",
        "().__class__",
        "(1).__class__.__bases__",
        "open('/etc/passwd').read()",
        "eval('1+1')",
        "exec('x=1')",
        "[x for x in range(10)]",
        "lambda: 1",
        "'a' * 100",
        "os.system('ls')",
        "globals()",
        "1 if True else 2",
        "x = 5",
        "import os",
    ],
)
def test_rejects_unsafe_expressions(expression: str) -> None:
    with pytest.raises((CalculatorError, SyntaxError)):
        evaluate(expression)


def test_rejects_attribute_access() -> None:
    with pytest.raises(CalculatorError):
        evaluate("math.pi")


def test_rejects_dunder_text_before_parsing() -> None:
    with pytest.raises(CalculatorError, match="Dunder"):
        evaluate("1 + __name__")


def test_rejects_memory_bomb_exponent() -> None:
    with pytest.raises(CalculatorError, match="exponent"):
        evaluate("9 ** 99999")


def test_rejects_division_by_zero() -> None:
    with pytest.raises(CalculatorError, match="zero"):
        evaluate("1 / 0")


def test_rejects_unknown_function() -> None:
    with pytest.raises(CalculatorError, match="Unknown function"):
        evaluate("frobnicate(2)")


def test_rejects_overlong_expression() -> None:
    with pytest.raises(CalculatorError, match="too long"):
        evaluate("1+" * 300 + "1")


def test_try_evaluate_never_raises() -> None:
    assert try_evaluate("2+2") == "4"
    assert try_evaluate("open('x')").startswith("Could not calculate")
