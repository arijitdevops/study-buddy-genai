"""A safe arithmetic evaluator.

``eval`` is never used. The expression is parsed with :mod:`ast` and walked
against an allowlist of node types, operators and function names, so
``__import__('os').system('rm -rf /')`` and friends fail at parse-validation
time rather than at run time.
"""

from __future__ import annotations

import ast
import logging
import math
import operator
from dataclasses import dataclass
from typing import Any, Callable, Union

logger = logging.getLogger(__name__)

Number = Union[int, float]


class CalculatorError(ValueError):
    """The expression was rejected or could not be evaluated."""


_BIN_OPS: dict[type[ast.operator], Callable[[Any, Any], Any]] = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
}

_UNARY_OPS: dict[type[ast.unaryop], Callable[[Any], Any]] = {
    ast.UAdd: operator.pos,
    ast.USub: operator.neg,
}

#: Whitelisted callables. Everything else -- including attribute access of any
#: kind -- is rejected.
_FUNCTIONS: dict[str, Callable[..., Number]] = {
    "abs": abs,
    "round": round,
    "min": min,
    "max": max,
    "sqrt": math.sqrt,
    "pow": math.pow,
    "exp": math.exp,
    "log": math.log,
    "log2": math.log2,
    "log10": math.log10,
    "sin": math.sin,
    "cos": math.cos,
    "tan": math.tan,
    "asin": math.asin,
    "acos": math.acos,
    "atan": math.atan,
    "atan2": math.atan2,
    "degrees": math.degrees,
    "radians": math.radians,
    "floor": math.floor,
    "ceil": math.ceil,
    "factorial": math.factorial,
    "gcd": math.gcd,
    "hypot": math.hypot,
}

_CONSTANTS: dict[str, Number] = {"pi": math.pi, "e": math.e, "tau": math.tau}

#: Guards against `9**9**9` style memory bombs.
_MAX_EXPONENT = 1024
_MAX_EXPRESSION_CHARS = 500


@dataclass(slots=True)
class CalculationResult:
    """The outcome of evaluating one expression."""

    expression: str
    value: Number
    formatted: str


class _SafeEvaluator(ast.NodeVisitor):
    """AST visitor that computes the value of an allowlisted expression."""

    def visit(self, node: ast.AST) -> Any:
        """Dispatch to a ``visit_*`` method, rejecting anything unhandled."""
        method = getattr(self, f"visit_{type(node).__name__}", None)
        if method is None:
            raise CalculatorError(
                f"'{type(node).__name__}' is not allowed in a calculation."
            )
        return method(node)

    def visit_Expression(self, node: ast.Expression) -> Any:
        """Evaluate the wrapped expression body."""
        return self.visit(node.body)

    def visit_Constant(self, node: ast.Constant) -> Number:
        """Return a numeric literal; strings, bytes and None are rejected."""
        if isinstance(node.value, bool) or not isinstance(node.value, (int, float)):
            raise CalculatorError("Only numbers are allowed as literals.")
        return node.value

    def visit_BinOp(self, node: ast.BinOp) -> Number:
        """Apply a binary operator from the allowlist."""
        op = _BIN_OPS.get(type(node.op))
        if op is None:
            raise CalculatorError(f"Operator '{type(node.op).__name__}' is not allowed.")
        left = self.visit(node.left)
        right = self.visit(node.right)
        if isinstance(node.op, ast.Pow) and isinstance(right, (int, float)):
            if abs(right) > _MAX_EXPONENT:
                raise CalculatorError("That exponent is too large to compute safely.")
        try:
            return op(left, right)
        except ZeroDivisionError as exc:
            raise CalculatorError("Division by zero.") from exc
        except (OverflowError, ValueError) as exc:
            raise CalculatorError(f"Could not compute that: {exc}") from exc

    def visit_UnaryOp(self, node: ast.UnaryOp) -> Number:
        """Apply a unary +/- operator."""
        op = _UNARY_OPS.get(type(node.op))
        if op is None:
            raise CalculatorError(f"Operator '{type(node.op).__name__}' is not allowed.")
        return op(self.visit(node.operand))

    def visit_Call(self, node: ast.Call) -> Number:
        """Call an allowlisted maths function by bare name only."""
        if not isinstance(node.func, ast.Name):
            raise CalculatorError("Only plain function names may be called.")
        func = _FUNCTIONS.get(node.func.id)
        if func is None:
            raise CalculatorError(f"Unknown function '{node.func.id}'.")
        if node.keywords:
            raise CalculatorError("Keyword arguments are not allowed.")
        args = [self.visit(arg) for arg in node.args]
        try:
            return func(*args)
        except (ValueError, TypeError, OverflowError) as exc:
            raise CalculatorError(f"{node.func.id}() could not be evaluated: {exc}") from exc

    def visit_Name(self, node: ast.Name) -> Number:
        """Resolve an allowlisted constant such as ``pi``."""
        if node.id in _CONSTANTS:
            return _CONSTANTS[node.id]
        raise CalculatorError(f"Unknown name '{node.id}'.")

    def visit_Tuple(self, node: ast.Tuple) -> Any:
        """Allow tuples only as argument groups (e.g. ``max((1, 2))``)."""
        return tuple(self.visit(element) for element in node.elts)


def evaluate(expression: str) -> CalculationResult:
    """Safely evaluate an arithmetic expression.

    Args:
        expression: Something like ``"2 * (3 + 4) ** 2"`` or ``"sqrt(144)"``.

    Returns:
        A :class:`CalculationResult`.

    Raises:
        CalculatorError: The expression used a disallowed construct, referenced
            an unknown name, or could not be computed.

    Example:
        >>> evaluate("sqrt(16) + 2").value
        6.0
    """
    if not expression or not expression.strip():
        raise CalculatorError("Nothing to calculate.")
    if len(expression) > _MAX_EXPRESSION_CHARS:
        raise CalculatorError("That expression is too long to evaluate.")
    if "__" in expression:
        raise CalculatorError("Dunder names are not allowed.")

    try:
        tree = ast.parse(expression, mode="eval")
    except SyntaxError as exc:
        raise CalculatorError(f"That isn't a valid expression: {exc.msg}") from exc

    value = _SafeEvaluator().visit(tree)
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise CalculatorError("The expression did not produce a number.")
    if isinstance(value, float) and (math.isnan(value) or math.isinf(value)):
        raise CalculatorError("The result is not a finite number.")

    formatted = f"{value:g}" if isinstance(value, float) else str(value)
    logger.debug("Calculator evaluated %r -> %s", expression, formatted)
    return CalculationResult(expression=expression.strip(), value=value, formatted=formatted)


def try_evaluate(expression: str) -> str:
    """Evaluate ``expression`` and return either the result or a friendly error."""
    try:
        return evaluate(expression).formatted
    except CalculatorError as exc:
        return f"Could not calculate: {exc}"
