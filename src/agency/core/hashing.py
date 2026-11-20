"""Neutral hashing primitive shared by factory and capability.

Avoids the capability <-> factory import cycle (gaps.md #1).
"""

from __future__ import annotations

import hashlib
import json
import math
from typing import Any


def canonical_sha256(value: Any) -> str:
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
