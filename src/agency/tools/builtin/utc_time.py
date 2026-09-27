"""Local UTC clock tool; no caller-controlled timezone or external I/O."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from agency.tools.base import ToolContext, ToolResult, ToolRisk, ToolSpec


class UtcTimeTool:
    """Return an ISO-8601 UTC timestamp using an injectable clock."""

    spec = ToolSpec(
        name="utc_time",
        description="Return the current UTC timestamp in ISO-8601 format.",
        parameters={"type": "object", "properties": {}, "additionalProperties": False},
        risk=ToolRisk.READ_ONLY,
    )

    def __init__(self, clock: Callable[[], datetime] | None = None) -> None:
        self._clock = clock if clock is not None else lambda: datetime.now(UTC)

    async def run(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        _ = ctx
        if not isinstance(args, dict) or args:
            return ToolResult(tool=self.spec.name, ok=False, error="utc_time: invalid request")
        try:
            moment = self._clock()
            if not isinstance(moment, datetime):
                raise TypeError("clock must return datetime")
            if moment.tzinfo is None or moment.utcoffset() is None:
                raise ValueError("clock must be timezone-aware")
            utc = moment.astimezone(UTC).isoformat()
        except Exception:  # noqa: BLE001 — fixed public error; never leak clock diagnostics
            return ToolResult(tool=self.spec.name, ok=False, error="utc_time: clock unavailable")
        return ToolResult(tool=self.spec.name, ok=True, output={"utc": utc, "tz": "UTC"})


__all__ = ["UtcTimeTool"]
