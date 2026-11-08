"""Independent bounded integer checker, not a tool-use or general truth certificate."""

from __future__ import annotations

import hashlib
import json
import re
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from agency.admission.models import OwnerTaskRequest

LIMIT = 10**15
_PREFIX = "[agency:calculator.v1]\nexpression="
_SUFFIX = "\nReturn only the base-10 integer result, without prose or markup."
_Id = Annotated[str, Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$", max_length=128)]
_Hash = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$", max_length=64)]
_Number = Annotated[str, Field(max_length=17)]
_ReceiptStatus = Literal["passed", "failed", "unable_to_verify"]
_ReceiptReason = Literal["matches", "mismatch", "answer_not_integer"]


def _bounded(value: int) -> int:
    if abs(value) > LIMIT:
        raise ValueError("integer magnitude")
    return value


class _Parser:
    """Recursive descent; precedence is explicit and every operation is bounded."""

    def __init__(self, expression: str) -> None:
        if (
            type(expression) is not str
            or not 1 <= len(expression) <= 120
            or re.fullmatch(r"[0-9 \t+*()\-]+", expression) is None
        ):
            raise ValueError("expression grammar")
        self.tokens = re.findall(r"[0-9]+|[+*()\-]", expression)
        if not 1 <= len(self.tokens) <= 64:
            raise ValueError("token limit")
        self.index = 0

    def peek(self) -> str:
        return self.tokens[self.index] if self.index < len(self.tokens) else ""

    def atom(self, depth: int) -> int:
        token = self.peek()
        if token in ("+", "-", "("):
            if depth >= 12:
                raise ValueError("nesting limit")
            self.index += 1
            if token == "(":
                value = self.sum(depth + 1)
                if self.peek() != ")":
                    raise ValueError("unclosed parenthesis")
                self.index += 1
                return value
            value = self.atom(depth + 1)
            return _bounded(-value if token == "-" else value)
        if re.fullmatch(r"0|[1-9][0-9]*", token) is None or len(token) > 16:
            raise ValueError("integer literal")
        self.index += 1
        return _bounded(int(token))

    def product(self, depth: int) -> int:
        value = self.atom(depth)
        while self.peek() == "*":
            self.index += 1
            value = _bounded(value * self.atom(depth))
        return value

    def sum(self, depth: int) -> int:
        value = self.product(depth)
        while self.peek() in ("+", "-"):
            token = self.peek()
            self.index += 1
            other = self.product(depth)
            value = _bounded(value + other if token == "+" else value - other)
        return value


def evaluate_expression(expression: str) -> int:
    parser = _Parser(expression)
    value = parser.sum(0)
    if parser.index != len(parser.tokens):
        raise ValueError("trailing tokens")
    return value


def canonical_message(expression: str) -> str:
    evaluate_expression(expression)
    return _PREFIX + expression + _SUFFIX


def requested_expression(message: str) -> str | None:
    """Only exact, valid marker messages opt in, including via the legacy DTO."""
    if type(message) is not str or not message.startswith(_PREFIX) or not message.endswith(_SUFFIX):
        return None
    expression = message[len(_PREFIX) : -len(_SUFFIX)]
    try:
        return expression if canonical_message(expression) == message else None
    except ValueError:
        return None


def admitted_expression(input_json: str, input_digest: str, thread_id: str) -> str | None:
    """Verify the immutable admission binding before trusting its criteria."""
    if type(input_json) is not str or len(input_json) > 32768:
        raise ValueError("admission encoding")
    if hashlib.sha256(input_json.encode("utf-8")).hexdigest() != input_digest:
        raise ValueError("admission digest")
    payload = json.loads(input_json)
    if type(payload) is not dict or set(payload) != {"thread_id", "message"}:
        raise ValueError("admission shape")
    OwnerTaskRequest.model_validate({"request_id": "validation", **payload})
    if payload["thread_id"] != thread_id:
        raise ValueError("admission thread")
    return requested_expression(payload["message"])


def _observed(response: str) -> str | None:
    value = response.strip()
    if len(value) > 17 or re.fullmatch(r"[+-]?[0-9]+", value) is None:
        return None
    integer = int(value)
    return str(integer) if abs(integer) <= LIMIT else None


class CalculatorReceipt(BaseModel):
    model_config = ConfigDict(
        extra="forbid", strict=True, frozen=True, revalidate_instances="always"
    )
    schema_version: Literal["calculator.v1"]
    method: Literal["bounded_integer_arithmetic/v1"]
    task_id: _Id
    request_id: _Id
    thread_id: _Id
    expression: Annotated[str, Field(min_length=1, max_length=120)]
    criteria_sha256: _Hash
    output_sha256: _Hash
    status: _ReceiptStatus
    reason: _ReceiptReason
    expected: _Number
    observed: _Number | None

    @field_validator("expected", "observed")
    @classmethod
    def canonical_integer(cls, value: str | None) -> str | None:
        if value is not None and (_observed(value) != value):
            raise ValueError("noncanonical integer")
        return value

    @model_validator(mode="after")
    def consistent(self) -> CalculatorReceipt:
        if (
            str(evaluate_expression(self.expression)) != self.expected
            or hashlib.sha256(self.expression.encode("utf-8")).hexdigest() != self.criteria_sha256
        ):
            raise ValueError("criteria binding")
        status, reason = (
            ("unable_to_verify", "answer_not_integer")
            if self.observed is None
            else ("passed", "matches")
            if self.observed == self.expected
            else ("failed", "mismatch")
        )
        if (self.status, self.reason) != (status, reason):
            raise ValueError("contradictory verdict")
        return self


def make_receipt(
    *, task_id: str, request_id: str, thread_id: str, expression: str, response: str
) -> CalculatorReceipt:
    expected = str(evaluate_expression(expression))
    observed = _observed(response)
    status: _ReceiptStatus
    reason: _ReceiptReason
    status, reason = (
        ("unable_to_verify", "answer_not_integer")
        if observed is None
        else ("passed", "matches")
        if observed == expected
        else ("failed", "mismatch")
    )
    return CalculatorReceipt(
        schema_version="calculator.v1",
        method="bounded_integer_arithmetic/v1",
        task_id=task_id,
        request_id=request_id,
        thread_id=thread_id,
        expression=expression,
        criteria_sha256=hashlib.sha256(expression.encode("utf-8")).hexdigest(),
        output_sha256=hashlib.sha256(response.encode("utf-8")).hexdigest(),
        status=status,
        reason=reason,
        expected=expected,
        observed=observed,
    )


def validate_receipt(
    value: Any,
    *,
    task_id: str,
    request_id: str,
    thread_id: str,
    expression: str | None,
    response: str,
) -> dict[str, Any]:
    if expression is None:
        raise ValueError("verification not requested")
    receipt = CalculatorReceipt.model_validate(value)
    bound = make_receipt(
        task_id=task_id,
        request_id=request_id,
        thread_id=thread_id,
        expression=expression,
        response=response,
    )
    if receipt != bound:
        raise ValueError("receipt binding")
    return receipt.model_dump(mode="json")


class CalculatorTaskRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    request_id: _Id
    thread_id: _Id
    expression: Annotated[str, Field(min_length=1, max_length=120)]

    @field_validator("expression")
    @classmethod
    def supported_expression(cls, value: str) -> str:
        evaluate_expression(value)
        return value

    def as_owner_task(self) -> OwnerTaskRequest:
        return OwnerTaskRequest(
            request_id=self.request_id,
            thread_id=self.thread_id,
            message=canonical_message(self.expression),
        )
