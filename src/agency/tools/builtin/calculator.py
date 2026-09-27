"""Bounded, network-free arithmetic tool; never execute caller-supplied code."""

from __future__ import annotations

import ast
import math
import re
from typing import Any

from agency.tools.base import ToolContext, ToolResult, ToolRisk, ToolSpec

_MAX_CHARS = 120
_MAX_NODES = 64
_MAX_DEPTH = 12
_MAX_MAGNITUDE = 1e15


class _InvalidExpression(Exception):
    def __init__(self, reason: str) -> None:
        self.reason = reason


class CalculatorTool:
    """Perform finite arithmetic on a tightly constrained expression AST."""

    spec = ToolSpec(
        name="calculator",
        description="Calculate bounded arithmetic (+, -, *, /, parentheses).",
        parameters={
            "type": "object",
            "properties": {"expression": {"type": "string"}},
            "required": ["expression"],
            "additionalProperties": False,
        },
        risk=ToolRisk.READ_ONLY,
    )

    @staticmethod
    def _compute(node: ast.AST, depth: int = 0) -> int | float:
        if depth > _MAX_DEPTH:
            raise _InvalidExpression("expression too complex")
        if isinstance(node, ast.Expression):
            return CalculatorTool._compute(node.body, depth + 1)
        if (
            isinstance(node, ast.Constant)
            and isinstance(node.value, (int, float))
            and not isinstance(node.value, bool)
        ):
            value = node.value
            if not math.isfinite(value) or abs(value) > _MAX_MAGNITUDE:
                raise _InvalidExpression("operand out of range")
            return value
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
            operand = CalculatorTool._compute(node.operand, depth + 1)
            result = operand if isinstance(node.op, ast.UAdd) else -operand
        elif isinstance(node, ast.BinOp) and isinstance(
            node.op, (ast.Add, ast.Sub, ast.Mult, ast.Div)
        ):
            left = CalculatorTool._compute(node.left, depth + 1)
            right = CalculatorTool._compute(node.right, depth + 1)
            if isinstance(node.op, ast.Add):
                result = left + right
            elif isinstance(node.op, ast.Sub):
                result = left - right
            elif isinstance(node.op, ast.Mult):
                result = left * right
            else:
                if right == 0:
                    raise _InvalidExpression("division by zero")
                result = left / right
        else:
            raise _InvalidExpression("unsupported syntax")
        if not math.isfinite(result):
            raise _InvalidExpression("result is not finite")
        if abs(result) > _MAX_MAGNITUDE:
            raise _InvalidExpression("result out of range")
        return result

    async def run(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        """Reject unsupported or expensive syntax before computing anything."""
        _ = ctx
        if not isinstance(args, dict) or set(args) != {"expression"}:
            return ToolResult(tool=self.spec.name, ok=False, error="calculator: invalid request")
        expression = args["expression"]
        if not isinstance(expression, str) or not expression.strip():
            return ToolResult(tool=self.spec.name, ok=False, error="calculator: invalid request")
        if len(expression) > _MAX_CHARS:
            return ToolResult(
                tool=self.spec.name, ok=False, error="calculator: expression too long"
            )
        if re.fullmatch(r"[0-9.eE+*/()\s-]+", expression) is None:
            return ToolResult(tool=self.spec.name, ok=False, error="calculator: unsupported syntax")
        nesting = 0
        for ch in expression:
            if ch == "(":
                nesting += 1
                if nesting > 11:
                    return ToolResult(
                        tool=self.spec.name, ok=False, error="calculator: expression too complex"
                    )
            elif ch == ")":
                nesting -= 1
        try:
            parsed = ast.parse(expression.strip(), mode="eval")
            if sum(1 for _ in ast.walk(parsed)) > _MAX_NODES:
                raise _InvalidExpression("expression too complex")
            result = self._compute(parsed)
        except (SyntaxError, ValueError, OverflowError, RecursionError):
            return ToolResult(tool=self.spec.name, ok=False, error="calculator: unsupported syntax")
        except _InvalidExpression as exc:
            return ToolResult(tool=self.spec.name, ok=False, error=f"calculator: {exc.reason}")
        return ToolResult(
            tool=self.spec.name,
            ok=True,
            output={"expression": expression, "result": result},
            evidence={"expression_chars": len(expression)},
        )


__all__ = ["CalculatorTool"]
