"""Forge v2 static security scan (stdlib-only, no subprocess).

Bounded, deterministic scanner over caller-supplied relative paths under an
absolute repository root. Reuses the inspection gate's path-safety discipline
but adds AST-based and regex-based security findings. Read-only: never
executes, imports, shells out, or mutates the repository.

This is a *best-effort* heuristic scanner, not a certified security review.
A ``PASS`` means no HIGH/CRITICAL findings were raised; it never authorizes
merge or deploy on its own.
"""

from __future__ import annotations

import ast
import os
import re
import stat
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal
from uuid import uuid4

import structlog
from pydantic import BaseModel, ConfigDict, Field

from agency.forge.inspection import (
    MAX_FILE_BYTES,
    MAX_FILES,
    MAX_PATH_CHARS,
    _has_parent_traversal,
    _is_absolute_input,
    _resolve_root,
    _symlink_or_escape,
)

logger = structlog.get_logger(__name__)

Severity = Literal["low", "medium", "high", "critical"]
ScanGate = Literal["PASS", "FAIL"]

# Dangerous call names (direct, unqualified).
_DANGEROUS_CALLS: dict[str, tuple[Severity, str]] = {
    "eval": ("critical", "SEC001 eval() executes arbitrary code"),
    "exec": ("critical", "SEC002 exec() executes arbitrary code"),
    "compile": ("high", "SEC003 compile() can feed exec/eval"),
    "__import__": ("high", "SEC004 dynamic import"),
    "os.system": ("critical", "SEC005 shell command execution"),
    "os.popen": ("critical", "SEC006 shell pipe execution"),
    "subprocess.run": ("high", "SEC007 subprocess execution"),
    "subprocess.call": ("high", "SEC008 subprocess execution"),
    "subprocess.check_output": ("high", "SEC009 subprocess execution"),
    "subprocess.Popen": ("high", "SEC010 subprocess execution"),
    "pickle.loads": ("high", "SEC011 unsafe deserialization"),
    "pickle.load": ("high", "SEC012 unsafe deserialization"),
    "yaml.load": ("medium", "SEC013 yaml.load without SafeLoader"),
    "marshal.loads": ("high", "SEC014 unsafe deserialization"),
}

_SECRET_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (
        re.compile(r"(?i)(api[_-]?key|secret|token|password|passwd)\s*=\s*['\"][^'\"]{8,}['\"]"),
        "SEC100 possible hardcoded secret",
    ),
    (re.compile(r"-----BEGIN (RSA |EC |OPENSSH )?PRIVATE KEY-----"), "SEC101 private key material"),
    (re.compile(r"AKIA[0-9A-Z]{16}"), "SEC102 AWS access key id"),
    (re.compile(r"ghp_[A-Za-z0-9]{36}"), "SEC103 GitHub personal access token"),
]


class SecurityFinding(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    path: str
    line: int = Field(ge=1)
    rule_id: str
    severity: Severity
    message: str


class SecurityReport(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    report_id: str = Field(min_length=1)
    created_at: datetime
    root: str
    gate: ScanGate
    findings: tuple[SecurityFinding, ...] = ()
    scanned_files: int = Field(ge=0)
    skipped_files: int = Field(ge=0)
    note: str = Field(default="Heuristic static scan; not a certified security review.")


class _ScanResult:
    __slots__ = ("findings", "scanned", "skipped")

    def __init__(self) -> None:
        self.findings: list[SecurityFinding] = []
        self.scanned = 0
        self.skipped = 0


def _call_name(node: ast.Call) -> str:
    if isinstance(node.func, ast.Name):
        return node.func.id
    if isinstance(node.func, ast.Attribute):
        parts: list[str] = []
        cur: ast.expr = node.func
        while isinstance(cur, ast.Attribute):
            parts.append(cur.attr)
            cur = cur.value
        if isinstance(cur, ast.Name):
            parts.append(cur.id)
            parts.reverse()
            return ".".join(parts)
    return ""


def _scan_python(path: str, source: str) -> list[SecurityFinding]:
    findings: list[SecurityFinding] = []
    try:
        tree = ast.parse(source, filename=path)
    except SyntaxError:
        # Syntax errors are handled by the inspection gate; skip AST rules here.
        return findings
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            name = _call_name(node)
            if name in _DANGEROUS_CALLS:
                sev, msg = _DANGEROUS_CALLS[name]
                findings.append(
                    SecurityFinding(
                        path=path,
                        line=getattr(node, "lineno", 1) or 1,
                        rule_id=msg.split()[0],
                        severity=sev,
                        message=msg,
                    )
                )
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name in ("pickle", "marshal"):
                    findings.append(
                        SecurityFinding(
                            path=path,
                            line=getattr(node, "lineno", 1) or 1,
                            rule_id="SEC020",
                            severity="medium",
                            message=f"SEC020 import of {alias.name} (review usage)",
                        )
                    )
    # secret patterns (line-based)
    for lineno, line in enumerate(source.splitlines(), start=1):
        for pat, msg in _SECRET_PATTERNS:
            if pat.search(line):
                findings.append(
                    SecurityFinding(
                        path=path,
                        line=lineno,
                        rule_id=msg.split()[0],
                        severity="high",
                        message=msg,
                    )
                )
    return findings


def scan_changes(root: str | Path, changed_paths: list[str]) -> SecurityReport:
    real_root = _resolve_root(root)
    if not isinstance(changed_paths, list) or any(not isinstance(p, str) for p in changed_paths):
        raise ValueError("changed_paths must be a list of strings")

    report_id = str(uuid4())
    created_at = datetime.now(UTC)
    result = _ScanResult()

    if len(changed_paths) > MAX_FILES:
        # mirror inspection gate: bounded failure
        return SecurityReport(
            report_id=report_id,
            created_at=created_at,
            root=str(real_root),
            gate="FAIL",
            findings=(),
            scanned_files=0,
            skipped_files=len(changed_paths),
            note=f"file count exceeds limit {MAX_FILES}",
        )

    if not changed_paths:
        return SecurityReport(
            report_id=report_id,
            created_at=created_at,
            root=str(real_root),
            gate="FAIL",
            findings=(),
            scanned_files=0,
            skipped_files=0,
            note="empty change set",
        )

    seen: set[str] = set()
    for raw in changed_paths:
        reason = None
        if len(raw) > MAX_PATH_CHARS or not raw.strip():
            reason = "invalid_path"
        elif _is_absolute_input(raw):
            reason = "absolute_path_not_allowed"
        elif _has_parent_traversal(raw):
            reason = "path_traversal_not_allowed"
        normalized = raw.replace("\\", "/")
        parts = [p for p in normalized.split("/") if p not in ("", ".")]
        if reason is None and (not parts or any(p in (".", "..") for p in parts)):
            reason = "invalid_relative_path"

        key = os.path.normcase("/".join(parts)) if os.name == "nt" else "/".join(parts)
        if key in seen:
            result.skipped += 1
            continue
        if parts:
            seen.add(key)

        if reason is not None:
            result.skipped += 1
            continue

        candidate = real_root.joinpath(*parts)
        escape = _symlink_or_escape(real_root, candidate)
        if escape is not None:
            result.skipped += 1
            continue
        try:
            st = os.lstat(candidate)
        except OSError:
            result.skipped += 1
            continue
        if not stat.S_ISREG(st.st_mode) or st.st_size > MAX_FILE_BYTES:
            result.skipped += 1
            continue

        # read bounded
        try:
            with open(candidate, "rb") as fh:
                data = fh.read(MAX_FILE_BYTES + 1)
        except OSError:
            result.skipped += 1
            continue
        if len(data) > MAX_FILE_BYTES:
            result.skipped += 1
            continue

        result.scanned += 1
        display = "/".join(parts)
        if candidate.suffix.lower() == ".py":
            try:
                text = data.decode("utf-8")
            except UnicodeDecodeError:
                result.findings.append(
                    SecurityFinding(
                        path=display,
                        line=1,
                        rule_id="SEC030",
                        severity="medium",
                        message="SEC030 non-UTF8 python source (cannot scan)",
                    )
                )
                continue
            result.findings.extend(_scan_python(display, text))
        else:
            # secret patterns only for non-python text-ish files (best effort)
            try:
                text = data.decode("utf-8", errors="replace")
            except Exception:  # noqa: BLE001
                text = ""
            for lineno, line in enumerate(text.splitlines(), start=1):
                for pat, msg in _SECRET_PATTERNS:
                    if pat.search(line):
                        result.findings.append(
                            SecurityFinding(
                                path=display,
                                line=lineno,
                                rule_id=msg.split()[0],
                                severity="high",
                                message=msg,
                            )
                        )

    has_high = any(f.severity in ("high", "critical") for f in result.findings)
    gate: ScanGate = "FAIL" if has_high else "PASS"
    logger.info(
        "forge.scanned",
        root=str(real_root),
        scanned=result.scanned,
        findings=len(result.findings),
        gate=gate,
    )
    return SecurityReport(
        report_id=report_id,
        created_at=created_at,
        root=str(real_root),
        gate=gate,
        findings=tuple(result.findings),
        scanned_files=result.scanned,
        skipped_files=result.skipped,
    )
