"""Contracts for the bounded literal-assignment fixture."""

from __future__ import annotations

import difflib
import hashlib
import json
import math
import re
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationInfo, field_validator, model_validator


class _Frozen(BaseModel):
    model_config = ConfigDict(
        frozen=True,
        strict=True,
        extra="forbid",
        revalidate_instances="always",
        validate_default=True,
    )

    @field_validator("*", mode="before")
    @classmethod
    def exact_wire_types(cls, value: object, info: ValidationInfo) -> object:
        if info.field_name == "binding":
            return value
        if info.field_name in ("release_authorized", "tests_run"):
            if type(value) is not bool:
                raise ValueError("invalid coding flag")
        elif value is None and info.field_name in ("artifact_sha256", "patch_sha256"):
            return value
        elif type(value) is not str:
            raise ValueError("invalid coding string")
        else:
            _text(value, maximum=65536)
        return value


_Id = Annotated[str, Field(min_length=1, max_length=128)]
_Hash = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$", min_length=64, max_length=64)]


def _text(value: str, *, maximum: int) -> str:
    if type(value) is not str or len(value) > maximum or "\x00" in value:
        raise ValueError("invalid coding text")
    try:
        data = value.encode("utf-8")
    except UnicodeError:
        raise ValueError("invalid coding text") from None
    if len(data) > maximum:
        raise ValueError("invalid coding text")
    return value


class FactoryCodingRequest(_Frozen):
    request_id: _Id
    thread_id: _Id
    profile_id: Literal["literal-assignment-v1"]
    task: Annotated[str, Field(min_length=1, max_length=8000)]

    @field_validator("request_id", "thread_id")
    @classmethod
    def request_identifier(cls, value: str) -> str:
        if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}", value) is None:
            raise ValueError("invalid coding identifier")
        return value

    @field_validator("task")
    @classmethod
    def task_text(cls, value: str) -> str:
        _text(value, maximum=32000)
        if not value.strip():
            raise ValueError("invalid coding task")
        return value


class CodingBinding(_Frozen):
    schema_version: Literal["factory-coding-binding.v1"] = "factory-coding-binding.v1"
    owner_id: _Id
    workspace_id: _Id
    request_id: _Id
    task_id: _Id
    thread_id: _Id
    profile_id: Literal["literal-assignment-v1"]
    profile_sha256: _Hash
    blueprint_id: _Id
    blueprint_version: _Id
    runtime_agent_id: _Id
    base_commit: Annotated[str, Field(pattern=r"^[0-9a-fA-F]{40}$", min_length=40, max_length=40)]
    checker_id: Literal["literal-assignment"] = "literal-assignment"
    checker_version: Literal["1"] = "1"
    checker_sha256: _Hash
    criteria_sha256: _Hash

    @field_validator(
        "owner_id",
        "workspace_id",
        "blueprint_id",
        "blueprint_version",
        "runtime_agent_id",
        "request_id",
        "task_id",
        "thread_id",
    )
    @classmethod
    def identity(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("invalid coding identifier")
        return value

    @field_validator("request_id", "task_id", "thread_id")
    @classmethod
    def request_identifier(cls, value: str) -> str:
        return FactoryCodingRequest.request_identifier(value)


class CodingEdit(_Frozen):
    path: Literal["m.py"]
    old: Annotated[str, Field(min_length=1)]
    new: Annotated[str, Field(min_length=1)]

    @field_validator("old", "new")
    @classmethod
    def edit_text(cls, value: str) -> str:
        return _text(value, maximum=8192)


class CodingReceipt(_Frozen):
    schema_version: Literal["factory-coding.v1"] = "factory-coding.v1"
    binding: CodingBinding
    plan_sha256: _Hash
    forge_report_id: _Id
    forge_report_sha256: _Hash
    artifact_sha256: _Hash | None
    patch_sha256: _Hash | None
    objective_status: Literal["passed", "failed", "unable_to_verify"]
    reason: Literal[
        "matched",
        "criterion_mismatch",
        "inventory_mismatch",
        "artifact_unavailable",
        "unsafe_representation",
        "forge_failed",
        "binding_mismatch",
    ]
    release_authorized: Literal[False] = False
    tests_run: Literal[False] = False

    @field_validator("forge_report_id")
    @classmethod
    def report_identifier(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("invalid coding identifier")
        return value

    @model_validator(mode="after")
    def consistent_outcome(self) -> CodingReceipt:
        reasons = {
            "passed": ("matched",),
            "failed": (
                "criterion_mismatch",
                "inventory_mismatch",
                "forge_failed",
                "binding_mismatch",
            ),
            "unable_to_verify": ("artifact_unavailable", "unsafe_representation"),
        }
        if self.reason not in reasons[self.objective_status]:
            raise ValueError("inconsistent coding receipt")
        if self.objective_status == "passed" and (
            self.artifact_sha256 is None or self.patch_sha256 is None
        ):
            raise ValueError("inconsistent coding receipt")
        return self


def canonical_sha256(value: object) -> str:
    """Hash plain, finite JSON: <=64 KiB UTF-8, depth <=16, <=2048 nodes.

    Root depth is zero. Object keys count as JSON string nodes. No normalization,
    object adapters, tuple conversion or arbitrary serializers are supported.
    """
    nodes = 0
    string_bytes = 0

    def check(item: object, depth: int) -> None:
        nonlocal nodes, string_bytes
        nodes += 1
        if nodes > 2048 or depth > 16:
            raise ValueError("invalid coding JSON")
        if type(item) is str:
            if len(item) > 65536:
                raise ValueError("invalid coding JSON")
            string_bytes += len(item.encode("utf-8"))
            if string_bytes > 65536:
                raise ValueError("invalid coding JSON")
        elif item is None or type(item) is bool:
            return
        elif type(item) is int:
            if item.bit_length() > 262144:
                raise ValueError("invalid coding JSON")
        elif type(item) is float:
            if not math.isfinite(item):
                raise ValueError("invalid coding JSON")
        elif type(item) is list:
            if len(item) > 2048:
                raise ValueError("invalid coding JSON")
            for child in item:
                check(child, depth + 1)
        elif type(item) is dict:
            if len(item) > 2048:
                raise ValueError("invalid coding JSON")
            for key, child in item.items():
                if type(key) is not str:
                    raise ValueError("invalid coding JSON")
                check(key, depth + 1)
                check(child, depth + 1)
        else:
            raise ValueError("invalid coding JSON")

    try:
        check(value, 0)
        data = json.dumps(
            value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
        ).encode("utf-8")
    except (TypeError, ValueError, UnicodeError, RecursionError):
        raise ValueError("invalid coding JSON") from None
    if len(data) > 65536:
        raise ValueError("invalid coding JSON")
    return hashlib.sha256(data).hexdigest()


def coding_branch(binding: CodingBinding) -> str:
    """The server-derived branch, not a caller-selected branch."""
    binding = CodingBinding.model_validate(binding)
    return (
        "factory-"
        + canonical_sha256({"workspace_id": binding.workspace_id, "task_id": binding.task_id})[:24]
    )


def literal_patch(artifact_text: str) -> str:
    """Exact plain diff from the fixed six-byte baseline; never executable."""
    _text(artifact_text, maximum=8192)
    return "".join(
        difflib.unified_diff(
            ["x = 1\n"], artifact_text.splitlines(keepends=True), fromfile="a/m.py", tofile="b/m.py"
        )
    )
