"""Pinned 4-validator genesis membership loader."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from agency.peers.membership import Membership, derive_genesis_membership


def load_genesis(path: str | Path) -> Membership:
    raw = Path(path).read_text(encoding="utf-8")
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid genesis json: {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError("genesis must be an object")  # noqa: TRY004
    policy_version = data.get("policy_version")
    if not isinstance(policy_version, str) or not policy_version:
        raise ValueError("missing policy_version")
    members = data.get("members")
    if not isinstance(members, list) or len(members) != 4:
        raise ValueError("genesis must contain exactly 4 members")
    seen: set[str] = set()
    normalized: list[dict[str, Any]] = []
    for idx, m in enumerate(members):
        if not isinstance(m, dict):
            raise ValueError(f"member {idx} must be an object")  # noqa: TRY004
        peer_id = m.get("peer_id")
        public_key = m.get("public_key")
        roles = m.get("roles")
        joined_epoch = m.get("joined_epoch")
        if not isinstance(peer_id, str) or not peer_id:
            raise ValueError(f"member {idx} missing peer_id")
        if peer_id in seen:
            raise ValueError(f"duplicate peer_id {peer_id}")
        seen.add(peer_id)
        if not isinstance(public_key, str) or not public_key:
            raise ValueError(f"member {idx} missing public_key")
        if not isinstance(roles, list) or "validator" not in roles:
            raise ValueError(f"member {idx} must have validator role")
        if not isinstance(joined_epoch, int) or joined_epoch < 1:
            raise ValueError(f"member {idx} invalid joined_epoch")
        normalized.append(
            {
                "peer_id": peer_id,
                "public_key": public_key,
                "roles": list(roles),
                "joined_epoch": joined_epoch,
            }
        )
    return derive_genesis_membership(normalized, policy_version=policy_version)
