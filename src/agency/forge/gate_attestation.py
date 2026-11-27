"""Human-gate attestation: prove required gates are named before a merge.

The module is a deep seam. Callers pass changed paths and one or more
approval-record-like objects (a mapping, a Pydantic model, or any object with
``approver``/``decision``/``caveats``/``note`` fields). It recomputes the
mandatory gates with ``human_gates.required_human_gates`` and returns a single
immutable attestation. It never approves, merges, or makes network calls, and
fails closed when a required gate is not attested by a named human.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from typing import cast

from pydantic import BaseModel, ConfigDict

from agency.forge.human_gates import required_human_gates

_NON_HUMAN = frozenset({"agent", "forge", "auto", "system", "cli", "bot", "none"})
_APPROVED = frozenset({"approve", "approved"})
_GATE_CAVEAT_PREFIX = "gate_reviewed:"
_HUMAN_GATES_NOTE = re.compile(r"human_gates=([^;\s]+)")

GATE_ATTESTATION_CAVEATS: tuple[str, ...] = (
    "not_a_human_gate",
    "does_not_authorize_merge",
    "no_network_calls",
    "caller_supplied_approval_records",
    "fail_closed",
    "best_effort_gate_name_matching",
)


class GateAttestation(BaseModel):
    """Immutable result of checking required gates against approval records."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    allowed: bool
    required_gates: tuple[str, ...] = ()
    attested_gates: tuple[str, ...] = ()
    missing_gates: tuple[str, ...] = ()
    reasons: tuple[str, ...] = ()
    caveats: tuple[str, ...] = GATE_ATTESTATION_CAVEATS
    note: str = (
        "Gate-name attestation only; it does not perform a human review, "
        "authorize a merge, or verify the approval's authenticity."
    )


def _field(record: object, name: str, default: object) -> object:
    """Read a field from a mapping-like or attribute-like record, fail-soft."""
    if isinstance(record, Mapping):
        return cast(Mapping[str, object], record).get(name, default)
    return cast(object, getattr(record, name, default))


def _as_text(value: object) -> str:
    return value if isinstance(value, str) else ""


def _named_human(value: object) -> bool:
    approver = _as_text(value).strip()
    return bool(approver) and approver.lower() not in _NON_HUMAN


def _is_approve(value: object) -> bool:
    return _as_text(value).strip().lower() in _APPROVED


def _gates_from_record(record: object) -> frozenset[str]:
    """Collect gate names a record attests, from caveats and note text."""
    gates: set[str] = set()

    caveats = _field(record, "caveats", ())
    if isinstance(caveats, (list, tuple)):
        for item in caveats:
            text = _as_text(item)
            if text.startswith(_GATE_CAVEAT_PREFIX):
                gate = text[len(_GATE_CAVEAT_PREFIX) :].strip().lower()
                if gate:
                    gates.add(gate)

    note = _as_text(_field(record, "note", ""))
    for match in _HUMAN_GATES_NOTE.finditer(note):
        for token in match.group(1).split(","):
            gate = token.strip().lower()
            if gate and gate != "none":
                gates.add(gate)

    return frozenset(gates)


def attest_human_gates(
    paths: Iterable[str],
    approvals: object,
) -> GateAttestation:
    """Attest that every required human gate is named by a valid approval.

    ``approvals`` may be a single record or a sequence of records; gates from
    valid records are unioned. A record counts only when it carries a named
    human approver and an approve decision. Missing required gates yield
    ``allowed=False``.
    """
    required = required_human_gates(paths)

    records: Sequence[object]
    if approvals is None:
        records = ()
    elif isinstance(approvals, (list, tuple)):
        records = approvals
    else:
        records = (approvals,)

    reasons: list[str] = []
    if not required:
        reasons.append("no_required_gates")
    if not records:
        reasons.append("no_approval_record")

    attested: set[str] = set()
    for record in records:
        if not _named_human(_field(record, "approver", "")):
            reasons.append("invalid_approver_ignored")
            continue
        if not _is_approve(_field(record, "decision", "")):
            reasons.append("decision_not_approve_ignored")
            continue
        attested.update(_gates_from_record(record))

    missing = tuple(gate for gate in required if gate not in attested)
    if missing:
        reasons.append("missing_gate_attestation")

    return GateAttestation(
        allowed=not missing,
        required_gates=required,
        attested_gates=tuple(sorted(attested)),
        missing_gates=missing,
        reasons=tuple(dict.fromkeys(reasons)),
    )


__all__ = [
    "GATE_ATTESTATION_CAVEATS",
    "GateAttestation",
    "attest_human_gates",
]
