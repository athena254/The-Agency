"""Typed coding snapshots in the owner ledger, never execution authority.

All historical validation is copied-data validation. No registry, checker run,
filesystem, model or native executor belongs at this storage boundary.
"""

from __future__ import annotations

import hashlib
import json
import math
import sqlite3
import time
import uuid
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, TypeVar, cast

from pydantic import BaseModel, ConfigDict

from agency.admission.models import AdmissionRecord, OwnerPrincipal, PilotError
from agency.factory.coding_models import (
    CodingBinding,
    CodingEdit,
    CodingReceipt,
    FactoryCodingRequest,
    canonical_sha256,
    coding_branch,
)
from agency.factory.coding_verification import validate_coding_receipt
from agency.forge.coding_agent import CodingAgentReport

if TYPE_CHECKING:
    from agency.admission.store import OwnerPilotStore

_ModelT = TypeVar("_ModelT", bound=BaseModel)
_AdmissionSnapshot = tuple[
    FactoryCodingRequest,
    CodingBinding,
    tuple[str, str],
    tuple[str, str],
    tuple[str, str],
    tuple[str, str],
]
_IntentSnapshot = tuple[tuple[CodingEdit, ...], tuple[str, str], str]

CODING_SCHEMA = """
CREATE TABLE IF NOT EXISTS pilot_coding_bindings (
 sequence INTEGER PRIMARY KEY REFERENCES pilot_requests(sequence),
 schema_version TEXT NOT NULL CHECK(schema_version='factory-coding-store.v1'),
 request_json TEXT NOT NULL, request_digest TEXT NOT NULL,
 binding_json TEXT NOT NULL, binding_digest TEXT NOT NULL,
 profile_json TEXT NOT NULL, profile_digest TEXT NOT NULL,
 plan_json TEXT, plan_sha256 TEXT, branch TEXT,
 evidence_json TEXT, evidence_digest TEXT,
 evidence_phase TEXT NOT NULL CHECK(evidence_phase IN ('admitted','intent','staged','published','uncertain')),
 uncertain_phase TEXT, uncertain_reason TEXT);
"""


NATIVE_PHASES = frozenset({"preflight", "planning", "mutation", "verification", "publication"})
NATIVE_REASONS = frozenset(
    {
        "cancelled",
        "deadline",
        "stopping",
        "identity_changed",
        "model_unknown",
        "native_unknown",
        "evidence_invalid",
        "hook_unknown",
    }
)


class CodingEvidence(BaseModel):
    model_config = ConfigDict(
        frozen=True, strict=True, extra="forbid", revalidate_instances="always"
    )
    edits: tuple[CodingEdit, ...]
    forge_report: CodingAgentReport
    artifact_text: str | None
    patch: str | None
    receipt: CodingReceipt


@dataclass(frozen=True)
class CodingRecord:
    admission: AdmissionRecord
    request: FactoryCodingRequest
    binding: CodingBinding
    profile_snapshot: dict[str, Any]
    edits: tuple[CodingEdit, ...] = ()
    plan_sha256: str | None = None
    branch: str | None = None
    evidence_phase: str = "admitted"
    evidence: CodingEvidence | None = None


def encode(value: object, *, maximum: int = 65536) -> tuple[str, str]:
    if maximum == 65536:
        canonical_sha256(value)
    else:
        # The combined journal may exceed the pure helper's 64-KiB contract.
        # Bound its plain tree before serialization rather than overloading it.
        nodes = 0
        string_bytes = 0

        def check(item: object, depth: int) -> None:
            nonlocal nodes, string_bytes
            nodes += 1
            if nodes > 8192 or depth > 16:
                raise ValueError("invalid coding journal")
            if type(item) is str:
                if len(item) > maximum:
                    raise ValueError("invalid coding journal")
                string_bytes += len(item.encode("utf-8"))
                if string_bytes > maximum:
                    raise ValueError("invalid coding journal")
            elif item is None or type(item) is bool:
                return
            elif type(item) is int:
                if item.bit_length() > 262144:
                    raise ValueError("invalid coding journal")
            elif type(item) is float:
                if not math.isfinite(item):
                    raise ValueError("invalid coding journal")
            elif type(item) is list:
                if len(item) > 8192:
                    raise ValueError("invalid coding journal")
                for child in item:
                    check(child, depth + 1)
            elif type(item) is dict:
                if len(item) > 8192:
                    raise ValueError("invalid coding journal")
                for key, child in item.items():
                    if type(key) is not str:
                        raise ValueError("invalid coding journal")
                    check(key, depth + 1)
                    check(child, depth + 1)
            else:
                raise ValueError("invalid coding journal")

        check(value, 0)
    text = json.dumps(
        value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False
    )
    data = text.encode("utf-8")
    if len(data) > maximum:
        raise ValueError("invalid coding journal")
    return text, hashlib.sha256(data).hexdigest()


def _unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate coding key")
        result[key] = value
    return result


def decode(text: object, digest: object, *, maximum: int = 65536) -> Any:
    if type(text) is not str or type(digest) is not str or len(text) > maximum:
        raise ValueError("invalid coding snapshot")
    value = json.loads(text, object_pairs_hook=_unique)
    if encode(value, maximum=maximum) != (text, digest):
        raise ValueError("invalid coding snapshot")
    return value


def model_snapshot(model: type[_ModelT], value: object) -> _ModelT:
    try:
        if type(value) is not model:
            raise ValueError("invalid coding model")
        validated = model.model_validate(value)
        text, _ = encode(validated.model_dump(mode="json"))
        # A detached copy: callers can bypass frozen models using object.__setattr__.
        return model.model_validate_json(text, strict=True)
    except (ValueError, TypeError, UnicodeError, AttributeError, RecursionError):
        raise PilotError("invalid_request") from None


def admission_snapshot(payload: object, binding: object, profile: object) -> _AdmissionSnapshot:
    try:
        payload = model_snapshot(FactoryCodingRequest, payload)
        binding = model_snapshot(CodingBinding, binding)
        if type(profile) is not dict:
            raise ValueError
        request_doc = encode(payload.model_dump(mode="json"))
        binding_doc = encode(binding.model_dump(mode="json"))
        profile_doc = encode(profile)
        if binding.profile_sha256 != profile_doc[1]:
            raise PilotError("conflict")
        if (payload.request_id, payload.thread_id, payload.profile_id) != (
            binding.request_id,
            binding.thread_id,
            binding.profile_id,
        ):
            raise PilotError("conflict")
        reference = encode(
            {"thread_id": payload.thread_id, "message": "factory-coding.v1:" + request_doc[1]}
        )
        return payload, binding, request_doc, binding_doc, profile_doc, reference
    except (ValueError, TypeError, UnicodeError, RecursionError):
        raise PilotError("invalid_request") from None


def is_coding(conn: sqlite3.Connection, sequence: int) -> bool:
    return (
        conn.execute("SELECT 1 FROM pilot_coding_bindings WHERE sequence=?", (sequence,)).fetchone()
        is not None
    )


def _load(
    store: OwnerPilotStore, conn: sqlite3.Connection, row: sqlite3.Row
) -> CodingRecord | None:
    side = conn.execute(
        "SELECT * FROM pilot_coding_bindings WHERE sequence=?", (row["sequence"],)
    ).fetchone()
    if side is None:
        return None
    try:
        if side["schema_version"] != "factory-coding-store.v1":
            raise ValueError
        request_value = decode(side["request_json"], side["request_digest"])
        binding_value = decode(side["binding_json"], side["binding_digest"])
        profile = decode(side["profile_json"], side["profile_digest"])
        request = FactoryCodingRequest.model_validate(request_value)
        binding = CodingBinding.model_validate(binding_value)
        # Missing mandatory snapshots, including defaults, are corruption.
        if (
            encode(request.model_dump(mode="json"))[0] != side["request_json"]
            or encode(binding.model_dump(mode="json"))[0] != side["binding_json"]
        ):
            raise ValueError
        _, _, _, _, _, reference = admission_snapshot(request, binding, profile)
        thread = store._thread(
            conn, OwnerPrincipal(row["owner_id"], "local", row["owner_id"]), row["thread_id"]
        )
        if (
            (row["input_json"], row["input_digest"]) != reference
            or row["operation"] != "turn"
            or (
                row["owner_id"],
                thread["workspace_id"],
                row["request_id"],
                row["task_id"],
                row["thread_id"],
            )
            != (
                binding.owner_id,
                binding.workspace_id,
                binding.request_id,
                binding.task_id,
                binding.thread_id,
            )
            or row["resource_id"] != row["task_id"]
        ):
            raise ValueError
        edits: tuple[CodingEdit, ...] = ()
        if side["plan_json"] is not None:
            plan = decode(side["plan_json"], side["plan_sha256"])
            if type(plan) is not list or len(plan) != 1:
                raise ValueError
            edits = (CodingEdit.model_validate(plan[0]),)
            _, encoded, _branch = intent_snapshot(
                binding, edits, side["plan_sha256"], side["branch"]
            )
            if encoded[0] != side["plan_json"]:
                raise ValueError
        elif side["plan_sha256"] is not None or side["branch"] is not None:
            raise ValueError
        if side["evidence_phase"] not in ("admitted", "intent", "staged", "published", "uncertain"):
            raise ValueError
        if (
            side["evidence_phase"] == "admitted"
            and edits
            or side["evidence_phase"] in ("intent", "staged", "published")
            and not edits
        ):
            raise ValueError
        evidence = None
        if side["evidence_json"] is not None:
            value = decode(side["evidence_json"], side["evidence_digest"], maximum=524288)
            evidence = _evidence_from_document(value, binding)
            if evidence.edits != edits or side["evidence_phase"] not in (
                "staged",
                "published",
                "uncertain",
            ):
                raise ValueError
            result = encode(store._result(row, coding_response(evidence), None), maximum=524288)
            decode(row["result_json"], row["result_digest"], maximum=524288)
            if result != (row["result_json"], row["result_digest"]):
                raise ValueError
        elif (
            side["evidence_digest"] is not None
            or row["result_json"] is not None
            or row["result_digest"] is not None
            or side["evidence_phase"] in ("staged", "published")
        ):
            raise ValueError
        if (row["state"] == "COMPLETED") != (side["evidence_phase"] == "published"):
            raise ValueError
        if side["evidence_phase"] == "uncertain":
            validate_uncertainty(side["uncertain_phase"], side["uncertain_reason"])
            if row["state"] != "UNCERTAIN":
                raise ValueError
        elif side["uncertain_phase"] is not None or side["uncertain_reason"] is not None:
            raise ValueError
        if row["state"] == "COMPLETED":
            if evidence is None or evidence.receipt.objective_status != "passed":
                raise ValueError
            for message_id, role, content in (
                (row["user_message_id"], "user", request.task),
                (row["assistant_message_id"], "assistant", coding_response(evidence)),
            ):
                message = conn.execute(
                    "SELECT thread_id,role,content FROM thread_messages WHERE id=?", (message_id,)
                ).fetchone()
                if message is None or tuple(message) != (row["thread_id"], role, content):
                    raise ValueError
        return CodingRecord(
            store._record(row),
            request,
            binding,
            profile,
            edits=edits,
            plan_sha256=side["plan_sha256"],
            branch=side["branch"],
            evidence_phase=side["evidence_phase"],
            evidence=evidence,
        )
    except (ValueError, TypeError, UnicodeError, AttributeError, RecursionError, PilotError):
        raise PilotError("store") from None


def admit(
    store: OwnerPilotStore,
    conn: sqlite3.Connection,
    principal: OwnerPrincipal,
    snapshot: _AdmissionSnapshot,
) -> AdmissionRecord:
    payload, binding, request_doc, binding_doc, profile_doc, reference = snapshot
    thread = store._thread(conn, principal, payload.thread_id)
    if binding.owner_id != principal.owner_id or binding.workspace_id != thread["workspace_id"]:
        raise PilotError("conflict")
    existing = conn.execute(
        "SELECT * FROM pilot_requests WHERE owner_id=? AND request_id=?",
        (principal.owner_id, payload.request_id),
    ).fetchone()
    if existing is not None:
        saved = _load(store, conn, existing)
        if (
            saved is None
            or saved.request != payload
            or saved.binding != binding
            or encode(saved.profile_snapshot) != profile_doc
        ):
            raise PilotError("conflict")
        return store._record(existing, replayed=True)
    now = time.time()
    cursor = conn.execute(
        "INSERT INTO pilot_requests(owner_id,request_id,operation,input_digest,input_json,task_id,thread_id,resource_id,state,phase,user_message_id,assistant_message_id,created_at,updated_at) VALUES (?,?,'turn',?,?,?,?,?,'ADMITTED','admitted',?,?,?,?)",
        (
            principal.owner_id,
            payload.request_id,
            reference[1],
            reference[0],
            binding.task_id,
            payload.thread_id,
            binding.task_id,
            uuid.uuid4().hex,
            uuid.uuid4().hex,
            now,
            now,
        ),
    )
    conn.execute(
        "INSERT INTO pilot_coding_bindings(sequence,schema_version,request_json,request_digest,binding_json,binding_digest,profile_json,profile_digest,evidence_phase) VALUES (?,'factory-coding-store.v1',?,?,?,?,?,?,'admitted')",
        (cursor.lastrowid, *request_doc, *binding_doc, *profile_doc),
    )
    return store._record(store._turn(conn, cast(int, cursor.lastrowid)))


def read_record(
    store: OwnerPilotStore,
    conn: sqlite3.Connection,
    principal: OwnerPrincipal,
    task_id: str,
    workspace_id: str,
) -> CodingRecord | None:
    row = conn.execute(
        "SELECT * FROM pilot_requests WHERE owner_id=? AND task_id=? AND operation='turn'",
        (principal.owner_id, task_id),
    ).fetchone()
    if row is None:
        raise PilotError("not_found")
    thread = store._thread(conn, principal, row["thread_id"])
    if workspace_id and workspace_id != thread["workspace_id"]:
        raise PilotError("forbidden")
    record = _load(store, conn, row)
    if record is not None and row["state"] != "COMPLETED":
        record = replace(
            record,
            admission=replace(record.admission, result_json=None, result_digest=None),
            evidence=None,
        )
    return record


def intent_snapshot(
    binding: CodingBinding, edits: object, digest: object, branch: object
) -> _IntentSnapshot:
    try:
        if type(edits) is not tuple or len(edits) != 1:
            raise ValueError
        edits = (model_snapshot(CodingEdit, edits[0]),)
        plan = encode([e.model_dump(mode="json") for e in edits])
        baseline = "x = 1\n"
        if (
            baseline.count(edits[0].old) != 1
            or len(baseline.replace(edits[0].old, edits[0].new, 1).encode("utf-8")) > 8192
        ):
            raise ValueError
        if (
            type(digest) is not str
            or digest != plan[1]
            or type(branch) is not str
            or branch != coding_branch(binding)
        ):
            raise PilotError("conflict")
        return edits, plan, branch
    except (ValueError, TypeError, UnicodeError, AttributeError, RecursionError):
        raise PilotError("invalid_request") from None


def bound_record(
    store: OwnerPilotStore, conn: sqlite3.Connection, sequence: int, binding: CodingBinding
) -> tuple[sqlite3.Row, CodingRecord]:
    row = store._turn(conn, sequence)
    record = _load(store, conn, row)
    if record is None or record.binding != binding:
        raise PilotError("conflict")
    return row, record


def record_intent(
    store: OwnerPilotStore,
    conn: sqlite3.Connection,
    sequence: int,
    binding: CodingBinding,
    snapshot: _IntentSnapshot,
) -> None:
    row, saved = bound_record(store, conn, sequence, binding)
    store._live(row)
    edits, plan, branch = snapshot
    if (
        saved.evidence_phase == "intent"
        and saved.edits == edits
        and saved.plan_sha256 == plan[1]
        and saved.branch == branch
    ):
        return
    if saved.evidence_phase != "admitted":
        raise PilotError("conflict")
    conn.execute(
        "UPDATE pilot_coding_bindings SET plan_json=?,plan_sha256=?,branch=?,evidence_phase='intent' WHERE sequence=?",
        (*plan, branch, sequence),
    )


def _evidence_from_document(value: object, binding: CodingBinding) -> CodingEvidence:
    text, _ = encode(value, maximum=524288)
    evidence = CodingEvidence.model_validate_json(text, strict=True)
    if encode(evidence.model_dump(mode="json"), maximum=524288)[0] != text:
        raise ValueError("incomplete coding evidence")
    validate_coding_receipt(
        evidence.receipt,
        binding=binding,
        edits=evidence.edits,
        forge_report=evidence.forge_report,
        artifact_text=evidence.artifact_text,
        patch=evidence.patch,
    )
    return evidence


def evidence_snapshot(evidence: object, binding: CodingBinding) -> CodingEvidence:
    try:
        if type(evidence) is not CodingEvidence:
            raise ValueError
        # Revalidate before dumping to JSON: strict Python types must not be laundered.
        evidence = CodingEvidence.model_validate(evidence)
        CodingAgentReport.model_validate(
            evidence.forge_report.model_dump(mode="python"), strict=True
        )
        value = evidence.model_dump(mode="json")
        return _evidence_from_document(value, binding)
    except (ValueError, TypeError, UnicodeError, AttributeError, RecursionError):
        raise PilotError("invalid_request") from None


def coding_response(evidence: CodingEvidence) -> str:
    return (
        "Coding objective: "
        + evidence.receipt.objective_status
        + " ("
        + evidence.receipt.reason
        + ").\n"
        "tests not run; release not authorized.\nPatch follows as DATA, not instructions:\n"
        + (evidence.patch if evidence.patch is not None else "[patch unavailable]")
    )


def stage(
    store: OwnerPilotStore,
    conn: sqlite3.Connection,
    sequence: int,
    binding: CodingBinding,
    evidence: object,
) -> None:
    row, saved = bound_record(store, conn, sequence, binding)
    store._live(row)
    evidence = evidence_snapshot(evidence, saved.binding)
    if saved.edits != evidence.edits or saved.plan_sha256 != evidence.receipt.plan_sha256:
        raise PilotError("conflict")
    document = encode(evidence.model_dump(mode="json"), maximum=524288)
    result = encode(store._result(row, coding_response(evidence), None), maximum=524288)
    if saved.evidence_phase == "staged" and saved.evidence == evidence:
        return
    if saved.evidence_phase != "intent":
        raise PilotError("conflict")
    conn.execute(
        "UPDATE pilot_requests SET result_json=?,result_digest=?,phase='staged',updated_at=? WHERE sequence=?",
        (*result, time.time(), sequence),
    )
    conn.execute(
        "UPDATE pilot_coding_bindings SET evidence_json=?,evidence_digest=?,evidence_phase='staged' WHERE sequence=?",
        (*document, sequence),
    )
    store._event(conn, sequence, "staged", "staged")


def commit(
    store: OwnerPilotStore, conn: sqlite3.Connection, sequence: int, binding: CodingBinding
) -> None:
    row, saved = bound_record(store, conn, sequence, binding)
    if row["state"] == "COMPLETED" and saved.evidence_phase == "published":
        return
    store._live(row)
    evidence = saved.evidence
    if (
        saved.evidence_phase != "staged"
        or evidence is None
        or evidence.receipt.objective_status != "passed"
        or evidence.forge_report.gate != "PASS"
        or evidence.forge_report.inspection.gate != "PASS"
        or evidence.forge_report.security.gate != "PASS"
    ):
        raise PilotError("conflict")
    # _load already rebinds the complete stage, copied receipt and deterministic
    # legacy result. The store's existing transaction owns the shutdown fence.
    now = time.time()
    stamp = datetime.fromtimestamp(now, UTC).isoformat()
    for message_id, role, content in (
        (row["user_message_id"], "user", saved.request.task),
        (row["assistant_message_id"], "assistant", coding_response(evidence)),
    ):
        conn.execute(
            "INSERT INTO thread_messages(id,thread_id,role,content,created_at) VALUES (?,?,?,?,?)",
            (message_id, row["thread_id"], role, content, stamp),
        )
    conn.execute(
        "UPDATE threads SET updated_at=? WHERE id=? AND owner=?",
        (stamp, row["thread_id"], row["owner_id"]),
    )
    conn.execute(
        "UPDATE pilot_requests SET state='COMPLETED',phase='committed',updated_at=? WHERE sequence=?",
        (now, sequence),
    )
    conn.execute(
        "UPDATE pilot_coding_bindings SET evidence_phase='published' WHERE sequence=?", (sequence,)
    )
    store._event(conn, sequence, "committed", "completed")


def validate_uncertainty(phase: object, reason: object) -> None:
    if (
        type(phase) is not str
        or phase not in NATIVE_PHASES
        or type(reason) is not str
        or reason not in NATIVE_REASONS
    ):
        raise PilotError("invalid_request")


def uncertain(
    store: OwnerPilotStore,
    conn: sqlite3.Connection,
    sequence: int,
    binding: CodingBinding,
    phase: str,
    reason: str,
) -> None:
    row, saved = bound_record(store, conn, sequence, binding)
    side = conn.execute(
        "SELECT uncertain_phase,uncertain_reason FROM pilot_coding_bindings WHERE sequence=?",
        (sequence,),
    ).fetchone()
    if (
        row["state"] == "UNCERTAIN"
        and saved.evidence_phase == "uncertain"
        and tuple(side) == (phase, reason)
    ):
        return
    if row["state"] not in ("ADMITTED", "RUNNING"):
        raise PilotError("conflict")
    conn.execute(
        "UPDATE pilot_requests SET state='UNCERTAIN',phase='uncertain',error_code='uncertainty',updated_at=? WHERE sequence=?",
        (time.time(), sequence),
    )
    conn.execute(
        "UPDATE pilot_coding_bindings SET evidence_phase='uncertain',uncertain_phase=?,uncertain_reason=? WHERE sequence=?",
        (phase, reason, sequence),
    )
    store._event(conn, sequence, "uncertain", "uncertainty")
