"""Live validation of the LLM-backed decomposition strategy (gaps #21f).

Decomposes a few sample tasks through the same strategy the AgentPlanner
wires for a live provider, prints the LLM plan next to the deterministic
regex plan, and records evidence JSON (no API keys are ever printed or
stored). Requires a live provider configured via the ambient environment /
untracked ``.env``; refuses to run against the echo provider.

Not part of the test suite — tests stay hermetic (echo/stub only).

Usage (from a worktree with a live ``.env``):
    uv run python scripts/decompose_live_demo.py
    uv run python scripts/decompose_live_demo.py --evidence-path <file.json>
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

from agency.agents.planner import (
    _LLM_PLAN_MAX_TOKENS,
    _LLM_PLAN_TEMPERATURE,
    LLM_DECOMPOSE_MAX_SUBTASKS,
    AgentPlanner,
    SubtaskSpec,
    _default_strategy,
    make_llm_strategy,
)
from agency.llm.adapter import LLMAdapter
from agency.llm.config import ProviderKind, load_config

SAMPLE_TASKS = [
    "Research the pricing of three competitor products and write a one-page comparison",
    "Fix the failing import-order test in module X and add a regression test",
    (
        "Review the pull request for security issues, summarise the findings, "
        "and draft an owner decision memo for the top risk"
    ),
]

DEFAULT_EVIDENCE_PATH = Path("D:/agency-merge-backups/decomp-evidence.json")


def _spec_record(spec: SubtaskSpec) -> dict[str, object]:
    return {
        "title": spec.title,
        "description": spec.description,
        "required_capabilities": spec.required_capabilities,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--evidence-path",
        type=Path,
        default=DEFAULT_EVIDENCE_PATH,
        help="Where to write the evidence JSON (default: %(default)s)",
    )
    args = parser.parse_args(argv)

    # load_config loads the untracked .env (cwd first) into the ambient env;
    # the AgentPlanner default-strategy resolver then sees the same provider.
    config = load_config()
    if config.provider is ProviderKind.ECHO:
        print(
            "No live provider configured (provider=echo). "
            "Set AGENCY_LLM_* in the environment or an untracked .env.",
            file=sys.stderr,
        )
        return 2

    bounded = config.model_copy(
        update={"max_tokens": _LLM_PLAN_MAX_TOKENS, "temperature": _LLM_PLAN_TEMPERATURE}
    )
    adapter = LLMAdapter(config=bounded)

    # The production resolver wires _default_strategy as the floor; this demo
    # wraps the same default with a recorder so evidence can state honestly
    # whether each task used the LLM or fell back to the regex strategy.
    fallback_tasks: list[str] = []

    def recording_fallback(task: str) -> list[SubtaskSpec]:
        fallback_tasks.append(task)
        return _default_strategy(task)

    strategy = make_llm_strategy(adapter, fallback=recording_fallback)

    # Offline check (no LLM call): the planner must select the LLM strategy
    # for the now-configured live provider.
    from agency.agents.registry import AgentRegistry

    planner_selection_uses_llm = AgentPlanner(AgentRegistry())._strategy is not _default_strategy

    print(f"provider={config.provider} model={config.model}")
    print(f"planner default selection uses LLM strategy: {planner_selection_uses_llm}")
    print("=" * 72)

    records: list[dict[str, object]] = []
    for task in SAMPLE_TASKS:
        fallback_tasks.clear()
        start = time.monotonic()
        llm_specs = strategy(task)
        duration_s = round(time.monotonic() - start, 2)
        fallback_used = task in fallback_tasks
        regex_specs = _default_strategy(task)

        records.append(
            {
                "task": task,
                "llm_plan_used": not fallback_used,
                "fallback_to_regex": fallback_used,
                "duration_s": duration_s,
                "llm_subtasks": (
                    [_spec_record(s) for s in llm_specs] if not fallback_used else None
                ),
                "regex_subtasks": [_spec_record(s) for s in regex_specs],
            }
        )

        print(f"\nTask: {task}")
        if fallback_used:
            print("  LLM decomposition FAILED -> regex fallback (recorded honestly)")
        else:
            print(f"  LLM plan ({len(llm_specs)} subtasks):")
            for i, spec in enumerate(llm_specs, 1):
                caps = ",".join(spec.required_capabilities) or "-"
                print(f"    {i}. {spec.title}  [{caps}]")
                print(f"       {spec.description[:160]}")
        print(f"  Regex plan ({len(regex_specs)} subtasks):")
        for i, spec in enumerate(regex_specs, 1):
            print(f"    {i}. {spec.title}")

    evidence = {
        "generated_at": datetime.now(UTC).isoformat(),
        "item": "gaps.md #21 (f) — LLM-backed task decomposition",
        "provider": str(config.provider),
        "model": config.model,
        "base_url": config.base_url,
        "max_subtasks_cap": LLM_DECOMPOSE_MAX_SUBTASKS,
        "output_budget_max_tokens": _LLM_PLAN_MAX_TOKENS,
        "planner_default_selection_uses_llm_strategy": planner_selection_uses_llm,
        "tasks": records,
        "notes": (
            "Regex _default_strategy remains the offline floor (echo) and the "
            "guaranteed fallback on any LLM failure (malformed JSON, over-cap "
            "plan, timeout, provider error). No API keys are stored here."
        ),
    }
    args.evidence_path.parent.mkdir(parents=True, exist_ok=True)
    args.evidence_path.write_text(
        json.dumps(evidence, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    print("\n" + "=" * 72)
    used = sum(1 for r in records if r["llm_plan_used"])
    print(f"LLM plans used: {used}/{len(records)} (rest fell back to regex)")
    print(f"Evidence written to {args.evidence_path}")
    return 0 if used else 1


if __name__ == "__main__":
    raise SystemExit(main())
