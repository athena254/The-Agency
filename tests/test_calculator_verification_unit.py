"""Supplemental parser/receipt boundary coverage, not original test-first evidence."""

from __future__ import annotations

import ast
import hashlib
from pathlib import Path

import pytest
from pydantic import ValidationError

from agency.admission import calculator_verification as checker
from agency.admission.calculator_verification import (
    CalculatorReceipt,
    CalculatorTaskRequest,
    canonical_message,
    evaluate_expression,
    make_receipt,
    requested_expression,
)


@pytest.mark.parametrize(
    ("expression", "expected"),
    [
        ("0", 0),
        ("2+3*4", 14),
        ("(2+3)*4", 20),
        ("10-3-2", 5),
        (" -(-3) * +2\t", 6),
        ("1--2", 3),
        ("1000000000000000", 10**15),
        ("-1000000000000000", -(10**15)),
        ("(" * 12 + "1" + ")" * 12, 1),
        ("-" * 12 + "1", 1),
        ("+" + "+".join(["1"] * 32), 32),
        (" " * 119 + "1", 1),
    ],
)
def test_supported_expression_and_exact_marker(expression, expected):
    assert evaluate_expression(expression) == expected
    marker = canonical_message(expression)
    assert requested_expression(marker) == expression
    assert requested_expression(marker + "\n") is None
    assert requested_expression("calculate " + expression) is None


@pytest.mark.parametrize(
    "expression",
    [
        "",
        " ",
        "1 2",
        "1+",
        "()",
        "(1",
        "1)",
        "1(2)",
        "01",
        "00",
        "1_0",
        "1#x",
        "2//1",
        "2**1",
        "3/1",
        "0x10",
        "1e1",
        "1.0",
        "a",
        "\n1",
        "1\r",
        "١",
        "１",
        "1\v+2",
        "1;2",
        "1\x00",
        "9" * 120,
        "(" * 13 + "1" + ")" * 13,
        "-" * 13 + "1",
        "-(" * 7 + "1" + ")" * 7,
        "+" * 2 + "+".join(["1"] * 32),
        " " * 120 + "1",
        "1000000000000001",
        "1000000000000000+1-1",
        "1000000000000000*2*0",
        "-1000000000000000-1+1",
        True,
        2,
        None,
    ],
)
def test_rejects_unsupported_or_unbounded_expression(expression):
    with pytest.raises(ValueError):
        evaluate_expression(expression)
    if type(expression) is str:
        marker = (
            "[agency:calculator.v1]\nexpression="
            + expression
            + "\nReturn only the base-10 integer result, without prose or markup."
        )
        assert requested_expression(marker) is None


@pytest.mark.parametrize(
    ("response", "status", "observed"),
    [
        ("5", "passed", "5"),
        (" +005\n", "passed", "5"),
        ("4", "failed", "4"),
        ("-0", "failed", "0"),
        ("-1000000000000000", "failed", "-1000000000000000"),
        ("1000000000000001", "unable_to_verify", None),
        ("0" * 18, "unable_to_verify", None),
        ("5.0", "unable_to_verify", None),
        ("٥", "unable_to_verify", None),
        ("5 and also 6", "unable_to_verify", None),
        ("```5```", "unable_to_verify", None),
        ("", "unable_to_verify", None),
        ("NaN", "unable_to_verify", None),
    ],
)
def test_entire_answer_eligibility_and_exact_output_digest(response, status, observed):
    receipt = make_receipt(
        task_id="task",
        request_id="request",
        thread_id="thread",
        expression="2+3",
        response=response,
    )
    assert receipt.status == status and receipt.observed == observed
    assert receipt.output_sha256 == hashlib.sha256(response.encode()).hexdigest()


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("schema_version", "other"),
        ("method", "model"),
        ("task_id", True),
        ("request_id", 1),
        ("thread_id", "wrong space"),
        ("expression", "3+3"),
        ("expected", "6"),
        ("expected", 5),
        ("expected", True),
        ("expected", "05"),
        ("expected", "-0"),
        ("expected", "1000000000000001"),
        ("observed", "05"),
        ("observed", None),
        ("status", "failed"),
        ("reason", "mismatch"),
        ("criteria_sha256", "0" * 64),
        ("output_sha256", "A" * 64),
        ("extra", "field"),
    ],
)
def test_receipt_rejects_contradictory_noncanonical_or_extra_fields(field, value):
    data = make_receipt(
        task_id="task", request_id="request", thread_id="thread", expression="2+3", response="5"
    ).model_dump(mode="json")
    data[field] = value
    with pytest.raises(ValidationError):
        CalculatorReceipt.model_validate(data)


def test_checker_has_no_tool_or_dynamic_execution_dependency():
    tree = ast.parse(Path(checker.__file__).read_text())
    assert not any(
        isinstance(node, ast.Name) and node.id in {"eval", "exec", "CalculatorTool"}
        for node in ast.walk(tree)
    )
    assert not any(
        isinstance(node, ast.Attribute) and node.attr == "_compute" for node in ast.walk(tree)
    )


def test_dto_freezes_expression_bytes_without_allowing_client_evidence():
    data = {"request_id": "request", "thread_id": "thread", "expression": " 2 +\t3 "}
    payload = CalculatorTaskRequest.model_validate(data).as_owner_task()
    assert payload.message == canonical_message(data["expression"])
    with pytest.raises(ValidationError):
        CalculatorTaskRequest.model_validate({**data, "verification": {"status": "passed"}})
