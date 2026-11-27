"""Default runtime verification specs for the products Forge builds.

A :class:`RuntimeSpec` is a small, immutable bundle of a start command and the
loopback endpoints a runtime gate should poll. ``spec_for`` is the whole public
surface: it normalizes the product type and returns a usable spec for the two
real products (node, python), or a fail-closed empty spec for anything else.

Nothing here opens a socket, starts a process, reads the network or touches the
filesystem at import time. The specs are data; ``agency.forge.runtime_verify``
performs the actual gate with these values injected by the orchestrator.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict

MAX_PRODUCT_TYPE_CHARS = 32

_LOOPBACK_NODE = ("http://127.0.0.1:3000/", "http://127.0.0.1:3000/health")
_LOOPBACK_PYTHON = ("http://127.0.0.1:8000/", "http://127.0.0.1:8000/health")

_DEFAULT_CAVEATS: tuple[str, ...] = (
    "default_runtime_spec",
    "loopback_endpoints_only",
    "override_per_project",
)
_UNSUPPORTED_CAVEATS: tuple[str, ...] = (
    "unsupported_product_type",
    "no_verify_command",
    "no_endpoints",
)


class RuntimeSpec(BaseModel):
    """Default command and endpoints for one product type (data only)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    product_type: str
    verify_command: tuple[str, ...] = ()
    endpoints: tuple[str, ...] = ()
    caveats: tuple[str, ...] = ()

    @property
    def usable(self) -> bool:
        """A spec is usable only when it has both a command and endpoints."""
        return bool(self.verify_command and self.endpoints)


NODE_SPEC = RuntimeSpec(
    product_type="node",
    verify_command=("npm", "run", "start"),
    endpoints=_LOOPBACK_NODE,
    caveats=_DEFAULT_CAVEATS,
)

PYTHON_SPEC = RuntimeSpec(
    product_type="python",
    verify_command=("python", "-m", "uvicorn", "app:app", "--host", "127.0.0.1", "--port", "8000"),
    endpoints=_LOOPBACK_PYTHON,
    caveats=_DEFAULT_CAVEATS,
)

_SPECS: dict[str, RuntimeSpec] = {
    NODE_SPEC.product_type: NODE_SPEC,
    PYTHON_SPEC.product_type: PYTHON_SPEC,
}


def spec_for(product_type: str) -> RuntimeSpec:
    """Return the default spec for *product_type*, fail-closed when unknown.

    Normalization and length bounding keep an untrusted product string from
    steering a command. Unknown products get an empty spec, so a caller that
    feeds it to ``verify_runtime`` receives a FAIL gate, never a free pass.
    """
    key = str(product_type or "").strip().lower()[:MAX_PRODUCT_TYPE_CHARS]
    spec = _SPECS.get(key)
    if spec is not None:
        return spec
    return RuntimeSpec(product_type=key, caveats=_UNSUPPORTED_CAVEATS)


__all__ = [
    "NODE_SPEC",
    "PYTHON_SPEC",
    "RuntimeSpec",
    "spec_for",
]
