"""LLM tool-calling driver — JSON protocol loop over a ToolRegistry."""

from __future__ import annotations

import json
from typing import Any

import structlog
from pydantic import BaseModel, Field

from agency.tools.base import ToolContext

logger = structlog.get_logger(__name__)

_INVALID_JSON_OBSERVATION = (
    "Your last response was not valid JSON with 'action' or 'final'. "
    "Respond with exactly one JSON object."
)
_FORCED_FINAL_MESSAGE = 'You have used all tool calls. Answer now with {"final": ...}'
_FALLBACK_MAX_ITERATIONS = "Maximum tool iterations reached without a final answer."
_FALLBACK_PARSE_FAILURE = "Could not produce a final answer."
_LIST_EVIDENCE_KEYS = ("urls", "sources", "queries")


class ToolLoopStep(BaseModel):
    """One executed tool call within the loop."""

    tool: str
    args: dict[str, Any] = Field(default_factory=dict)
    ok: bool
    duration_ms: int = 0
    error: str | None = None


class ToolLoopResult(BaseModel):
    """Outcome of a full tool-calling loop."""

    final_answer: str
    steps: list[ToolLoopStep] = Field(default_factory=list)
    evidence: dict[str, Any] = Field(default_factory=dict)
    llm_calls: int = 0
    status: str = "completed"


def _extract_json(text: str) -> dict[str, Any] | None:
    """Return the first balanced ``{...}`` block parsed as a dict.

    Tolerant of code fences, leading prose, and trailing prose. Uses a
    brace-depth scanner that respects JSON string quoting and escapes
    (not regex-only). Returns None when no valid JSON object is found.
    """
    if not text:
        return None
    start: int | None = None
    depth = 0
    in_string = False
    escaped = False
    for i, ch in enumerate(text):
        if in_string:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
        elif ch == "{":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "}" and depth > 0:
            depth -= 1
            if depth == 0 and start is not None:
                candidate = text[start : i + 1]
                try:
                    parsed = json.loads(candidate)
                except (json.JSONDecodeError, ValueError):
                    start = None
                    continue
                if isinstance(parsed, dict):
                    return parsed
                start = None
    return None


def _truncate(value: str, limit: int = 2000) -> str:
    if len(value) <= limit:
        return value
    return value[:limit]


class ToolDriver:
    """Run the JSON tool-calling protocol loop for any text LLM."""

    def __init__(self, registry: Any, llm: Any, max_tool_iterations: int = 6) -> None:
        self._registry = registry
        self._llm = llm
        self._max_tool_iterations = max_tool_iterations
        self._log = structlog.get_logger(__name__)

    def _build_prompt(
        self,
        task: str,
        system_prompt: str,
        tool_specs: list[Any],
        history: list[Any],
    ) -> str:
        """Assemble the unified JSON-protocol prompt."""
        spec_dicts: list[dict[str, Any]] = []
        for spec in tool_specs:
            spec_dicts.append(
                {
                    "name": getattr(spec, "name", ""),
                    "description": getattr(spec, "description", ""),
                    "parameters": getattr(spec, "parameters", {}),
                }
            )
        lines: list[str] = [system_prompt.strip(), "", "AVAILABLE TOOLS:", json.dumps(spec_dicts)]
        if history:
            lines.append("")
            lines.append("Conversation so far:")
            for i, turn in enumerate(history, start=1):
                if isinstance(turn, dict):
                    assistant = str(turn.get("assistant", turn.get("you", "")))
                    observation = str(turn.get("observation", turn.get("tool_result", "")))
                elif isinstance(turn, (tuple, list)) and len(turn) == 2:
                    assistant, observation = str(turn[0]), str(turn[1])
                else:
                    assistant, observation = str(turn), ""
                lines.append(f"{i}. You: {assistant}")
                if observation:
                    lines.append(f"{i}. Tool result: {_truncate(json.dumps(observation))}")
        lines.extend(
            [
                "",
                f"Task: {task}",
                "",
                "Respond with EXACTLY ONE JSON object, nothing else:",
                '- To use a tool: {"action": {"name": "<tool>", "args": {...}}}',
                (
                    '- When you have enough information to answer: {"final": '
                    '"<your complete answer with citations>"}'
                ),
            ]
        )
        return "\n".join(lines)

    def _aggregate_evidence(self, evidence: dict[str, Any], result: Any) -> None:
        result_evidence = getattr(result, "evidence", None) or {}
        if not isinstance(result_evidence, dict):
            return
        for key in _LIST_EVIDENCE_KEYS:
            value = result_evidence.get(key)
            if value is None:
                continue
            items = value if isinstance(value, list) else [value]
            bucket = evidence.setdefault(key, [])
            for item in items:
                if item not in bucket:
                    bucket.append(item)
        engine = result_evidence.get("engine")
        if engine is not None and not isinstance(engine, (list, dict)):
            evidence["engine"] = engine

    def _summarize_result(self, tool_name: str, result: Any) -> str:
        if getattr(result, "ok", False):
            try:
                output = json.dumps(getattr(result, "output", None), default=str)
            except (TypeError, ValueError):
                output = str(getattr(result, "output", None))
            return f"Tool '{tool_name}' succeeded: {_truncate(output)}"
        error = getattr(result, "error", None) or "unknown error"
        return f"Tool '{tool_name}' failed: {error}"

    async def run(self, task: str, system_prompt: str, ctx: ToolContext) -> ToolLoopResult:
        """Run the tool loop until a final answer, limit, or LLM error."""
        steps: list[ToolLoopStep] = []
        evidence: dict[str, Any] = {}
        history: list[dict[str, str]] = []
        llm_calls = 0
        parse_failures = 0
        tool_calls = 0
        task_id = getattr(ctx, "task_id", "")

        specs: list[Any] = []
        try:
            specs = list(self._registry.list_specs())
        except Exception as exc:  # noqa: BLE001 — driver must still run
            self._log.warning("driver.list_specs_failed", error=str(exc))

        while True:
            prompt = self._build_prompt(task, system_prompt, specs, history)
            try:
                raw = await self._llm.generate(prompt, {"task_id": task_id})
            except Exception as exc:  # noqa: BLE001 — surfaced as llm_error
                return ToolLoopResult(
                    final_answer=f"LLM error: {exc}",
                    steps=steps,
                    evidence=evidence,
                    llm_calls=llm_calls,
                    status="llm_error",
                )
            llm_calls += 1
            raw_text = raw if isinstance(raw, str) else str(raw)
            parsed = _extract_json(raw_text)

            final = parsed.get("final") if isinstance(parsed, dict) else None
            if isinstance(final, str) and final.strip():
                return ToolLoopResult(
                    final_answer=final,
                    steps=steps,
                    evidence=evidence,
                    llm_calls=llm_calls,
                    status="completed",
                )

            action = parsed.get("action") if isinstance(parsed, dict) else None
            if (
                isinstance(action, dict)
                and isinstance(action.get("name"), str)
                and action["name"].strip()
                and isinstance(action.get("args", {}), dict)
            ):
                parse_failures = 0
                tool_name: str = action["name"].strip()
                args: dict[str, Any] = action.get("args", {})
                try:
                    result = await self._registry.call(tool_name, args, ctx)
                except Exception as exc:  # noqa: BLE001 — surfaced as a failed step
                    result = _failed_result(tool_name, f"{type(exc).__name__}: {exc}")
                steps.append(
                    ToolLoopStep(
                        tool=tool_name,
                        args=args,
                        ok=bool(getattr(result, "ok", False)),
                        duration_ms=int(getattr(result, "duration_ms", 0) or 0),
                        error=getattr(result, "error", None),
                    )
                )
                self._aggregate_evidence(evidence, result)
                observation = self._summarize_result(tool_name, result)
                history.append({"assistant": raw_text, "observation": observation})
                tool_calls += 1
                if tool_calls >= self._max_tool_iterations:
                    forced_prompt = self._build_prompt(
                        task,
                        system_prompt,
                        specs,
                        [*history, {"assistant": "", "observation": _FORCED_FINAL_MESSAGE}],
                    )
                    try:
                        forced_raw = await self._llm.generate(forced_prompt, {"task_id": task_id})
                    except Exception as exc:  # noqa: BLE001
                        return ToolLoopResult(
                            final_answer=f"LLM error: {exc}",
                            steps=steps,
                            evidence=evidence,
                            llm_calls=llm_calls,
                            status="llm_error",
                        )
                    llm_calls += 1
                    forced_text = forced_raw if isinstance(forced_raw, str) else str(forced_raw)
                    forced_parsed = _extract_json(forced_text)
                    forced_final = (
                        forced_parsed.get("final") if isinstance(forced_parsed, dict) else None
                    )
                    if isinstance(forced_final, str) and forced_final.strip():
                        return ToolLoopResult(
                            final_answer=forced_final,
                            steps=steps,
                            evidence=evidence,
                            llm_calls=llm_calls,
                            status="completed",
                        )
                    return ToolLoopResult(
                        final_answer=forced_text.strip() or _FALLBACK_MAX_ITERATIONS,
                        steps=steps,
                        evidence=evidence,
                        llm_calls=llm_calls,
                        status="max_iterations",
                    )
                continue

            parse_failures += 1
            if parse_failures >= 2:
                return ToolLoopResult(
                    final_answer=raw_text.strip() or _FALLBACK_PARSE_FAILURE,
                    steps=steps,
                    evidence=evidence,
                    llm_calls=llm_calls,
                    status="completed",
                )
            history.append({"assistant": raw_text, "observation": _INVALID_JSON_OBSERVATION})


def _failed_result(tool_name: str, error: str) -> Any:
    from agency.tools.base import ToolResult

    return ToolResult(tool=tool_name, ok=False, error=error)


__all__ = [
    "ToolDriver",
    "ToolLoopResult",
    "ToolLoopStep",
]
