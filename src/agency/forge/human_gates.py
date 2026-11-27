"""Path-based mandatory human gates, enforced through approval records.

A named human is always required before a consequential change is accepted.
This module only names which gates apply; it never approves anything.
"""

from __future__ import annotations

from collections.abc import Iterable

_GATE_MARKERS = {
    "auth": ("auth", "login", "session", "token", "credential"),
    "crypto": ("crypto", "cipher", "keyring", "signature"),
    "payments": ("payment", "billing", "invoice", "checkout"),
    "permissions": ("permission", "acl", "authorization", "role"),
    "vendor_spend": ("vendor", "billing", "invoice", "spend"),
    "legal_safety": ("legal", "compliance", "safety"),
    "factory_guardrails": ("gates/", "guardrail", "approval", "policy", ".github/workflows"),
}

MAX_PATHS = 256


def required_human_gates(paths: Iterable[str]) -> tuple[str, ...]:
    """Return the sorted gate names touched by *paths*, fail-closed on bad input."""
    gates: set[str] = set()
    for raw in list(paths)[:MAX_PATHS]:
        path = str(raw).replace("\\", "/").lower()
        for gate, markers in _GATE_MARKERS.items():
            if any(marker in path for marker in markers):
                gates.add(gate)
    return tuple(sorted(gates))
