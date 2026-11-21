"""Per-agent tool allowlist enforcement (PLAN_TOOL_PROVISIONING §3.5).

Registry enforcement: a context carrying ``metadata["allowed_tools"]`` denies
any tool outside the set with the fixed error, audited, before execution.
Prompt building hides non-allowlisted tools. Factory-issued orchestrator
agents always get an explicit allowlist (blueprint requirements ∪ default
profile ∪ builtin always-ons); legacy agents keep the open registry.
improve_agent re-versions must preserve declared tool requirements.
Fully offline: no live DNS/provider, no canonical runtime DB.
"""

from __future__ import annotations

from typing import Any

import httpx

from agency.agents.registry import AgentRegistry as RuntimeRegistry
from agency.factory.prompts import PromptRegistry
from agency.factory.prompts_builtin import ensure_builtin_prompts
from agency.factory.requested_agent import RequestedAgentRequest, RequestedAgentService
from agency.factory.service import AgentFactory, BlueprintStatus
from agency.kernel.identity import Agent
from agency.kernel.registry import AgentRegistry as KernelRegistry
from agency.orchestrator import AgencyOrchestrator
from agency.selfimprove.distiller import TaskTrace
from agency.skills.registry import SkillRegistry
from agency.tools.base import ToolContext, ToolResult, ToolRisk, ToolSpec
from agency.tools.builtin import register_all, register_opt_in_local_tools
from agency.tools.driver import ToolDriver
from agency.tools.needs import default_requirements
from agency.tools.registry import ToolRegistry

# Fixed error vocabulary: callers get a stable message, never the allowlist
# or their input.
_NOT_PROVISIONED = "tool not provisioned for this agent"


def _blocked(*args: Any, **kwargs: Any) -> Any:
    raise AssertionError("network or filesystem access attempted")


def _ctx(**kwargs: Any) -> ToolContext:
    base: dict[str, Any] = {"agent_id": "agent-1", "task_id": "task-1"}
    base.update(kwargs)
    return ToolContext(**base)


class _RecordingAudit:
    """Kwargs-style audit log capturing appended entries."""

    def __init__(self) -> None:
        self.entries: list[dict[str, Any]] = []

    async def append(self, **kwargs: Any) -> None:
        self.entries.append(kwargs)


class _SpyTool:
    """Registered tool that records every execution."""

    def __init__(self, name: str) -> None:
        self.spec = ToolSpec(name=name, description="spy", risk=ToolRisk.READ_ONLY)
        self.calls = 0

    async def run(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        self.calls += 1
        return ToolResult(tool=self.spec.name, ok=True, output="ran")


def _registry(*tools: _SpyTool) -> ToolRegistry:
    registry = ToolRegistry()
    register_all(registry, transport=httpx.MockTransport(_blocked))
    register_opt_in_local_tools(registry)
    for tool in tools:
        registry.register(tool)
    return registry


# --------------------------------------------------------------------- #
# registry.call enforcement
# --------------------------------------------------------------------- #


async def test_allowlist_denies_unlisted_tool_before_execution() -> None:
    audit = _RecordingAudit()
    spy = _SpyTool("probe")
    registry = ToolRegistry(audit=audit)
    registry.register(spy)
    ctx = _ctx(metadata={"allowed_tools": frozenset({"utc_time"})})
    result = await registry.call("probe", {}, ctx)
    assert result.ok is False
    assert result.error == _NOT_PROVISIONED
    assert spy.calls == 0  # never executed
    assert len(audit.entries) == 1
    entry = audit.entries[0]
    assert entry["action"] == "tool.error"
    assert entry["target"] == "probe"
    assert entry["result"] == "error"


async def test_allowlist_denial_is_audited_without_input_echo() -> None:
    audit = _RecordingAudit()
    registry = ToolRegistry(audit=audit)
    register_all(registry, transport=httpx.MockTransport(_blocked))
    ctx = _ctx(metadata={"allowed_tools": frozenset({"calculator"})})
    result = await registry.call("web_search", {"query": "secret input"}, ctx)
    assert result.ok is False
    assert result.error == _NOT_PROVISIONED
    assert "secret input" not in (result.error or "")
    evidence = audit.entries[0]
    assert _NOT_PROVISIONED in str(evidence)
    assert "secret input" not in str(evidence)


async def test_allowlisted_tool_executes_normally() -> None:
    audit = _RecordingAudit()
    registry = ToolRegistry(audit=audit)
    register_all(registry, transport=httpx.MockTransport(_blocked))
    register_opt_in_local_tools(registry)
    ctx = _ctx(metadata={"allowed_tools": frozenset({"calculator", "utc_time"})})
    result = await registry.call("calculator", {"expression": "6*7"}, ctx)
    assert result.ok is True, result.error
    assert result.output["result"] == 42
    assert len(audit.entries) == 1
    assert audit.entries[0]["action"] == "tool.call"


async def test_no_allowlist_keeps_legacy_open_behavior() -> None:
    registry = _registry(_SpyTool("probe"))
    ctx = _ctx()
    calc = await registry.call("calculator", {"expression": "6*7"}, ctx)
    assert calc.ok is True, calc.error
    probe = await registry.call("probe", {}, ctx)
    assert probe.ok is True
    unknown = await registry.call("nonexistent_tool", {}, ctx)
    assert unknown.ok is False
    assert unknown.error == "unknown tool: nonexistent_tool"


async def test_non_set_allowlist_metadata_is_ignored() -> None:
    """A malformed allowlist value keeps legacy behavior (type-checked)."""
    registry = _registry(_SpyTool("probe"))
    ctx = _ctx(metadata={"allowed_tools": "calculator"})
    assert (await registry.call("calculator", {"expression": "1"}, ctx)).ok is True


# --------------------------------------------------------------------- #
# list_specs filtering
# --------------------------------------------------------------------- #


def test_list_specs_without_filter_is_unchanged() -> None:
    registry = _registry()
    names = {spec.name for spec in registry.list_specs()}
    assert "calculator" in names and "web_search" in names and "utc_time" in names


def test_list_specs_filter_returns_only_allowlisted() -> None:
    registry = _registry()
    specs = registry.list_specs(allowed_tools=frozenset({"calculator"}))
    assert [spec.name for spec in specs] == ["calculator"]
    assert registry.list_specs(allowed_tools=frozenset()) == []
    both = registry.list_specs(allowed_tools=frozenset({"utc_time", "web_search"}))
    assert {spec.name for spec in both} == {"utc_time", "web_search"}


# --------------------------------------------------------------------- #
# driver prompt filtering
# --------------------------------------------------------------------- #


class _ScriptedLLM:
    """Returns a final answer immediately; records received prompts."""

    def __init__(self) -> None:
        self.prompts: list[str] = []

    async def generate(self, prompt: str, context: dict[str, Any]) -> str:
        self.prompts.append(prompt)
        return '{"final": "done"}'


def test_driver_prompt_built_with_filter_excludes_hidden_tools() -> None:
    registry = _registry()
    driver = ToolDriver(registry, llm=None)
    prompt = driver._build_prompt(
        "test", "system", registry.list_specs(allowed_tools=frozenset({"utc_time"})), []
    )
    assert '"name": "utc_time"' in prompt
    assert '"name": "calculator"' not in prompt
    assert '"name": "web_search"' not in prompt


async def test_driver_run_filters_advertised_specs_from_context_allowlist() -> None:
    registry = _registry()
    llm = _ScriptedLLM()
    driver = ToolDriver(registry, llm)
    ctx = _ctx(metadata={"allowed_tools": frozenset({"utc_time"})})
    result = await driver.run("task", "system", ctx)
    assert result.status == "completed"
    assert '"name": "utc_time"' in llm.prompts[0]
    assert '"name": "calculator"' not in llm.prompts[0]
    assert '"name": "web_search"' not in llm.prompts[0]


async def test_driver_run_without_allowlist_advertises_everything() -> None:
    registry = _registry()
    llm = _ScriptedLLM()
    driver = ToolDriver(registry, llm)
    result = await driver.run("task", "system", _ctx())
    assert result.status == "completed"
    assert '"name": "calculator"' in llm.prompts[0]
    assert '"name": "utc_time"' in llm.prompts[0]


# --------------------------------------------------------------------- #
# orchestrator: per-agent allowlist resolution
# --------------------------------------------------------------------- #

_BUILTIN_ALWAYS_ON = {
    "web_search",
    "web_fetch",
    "memory_query",
    "memory_write",
    "utc_time",
    "calculator",
}
# The orchestrator's governed seams (see _build_tool_layer) ride along in
# every factory allowlist: pinned-skill invocation and depth-guarded
# delegation are available to every tool-capable agent by design.
_ORCHESTRATOR_SEAMS = {"agency_skill", "delegate_task"}
_ALWAYS_ON = _BUILTIN_ALWAYS_ON | _ORCHESTRATOR_SEAMS


def _factory_stack(
    tmp_path,
) -> tuple[AgentFactory, PromptRegistry, KernelRegistry, SkillRegistry]:
    prompts = PromptRegistry(str(tmp_path / "prompts.db"))
    ensure_builtin_prompts(prompts)
    skills = SkillRegistry(str(tmp_path / "skills.db"))
    kernels = KernelRegistry()
    factory = AgentFactory(
        str(tmp_path / "factory.db"),
        authorizer=lambda actor, perm: perm == "agent.approve",
        allowed_capabilities={"search"},
        kernel_registry=kernels,
        runtime_registry=RuntimeRegistry(),
        is_prompt_published=prompts.is_published,
        prompt_resolver=prompts,
        is_skill_published=lambda sid, ver: True,
    )
    return factory, prompts, kernels, skills


def _orchestrator(
    factory: AgentFactory | None, prompts: PromptRegistry | None
) -> AgencyOrchestrator:
    config: dict[str, Any] = {}
    if factory is not None:
        config["agent_factory"] = factory
    if prompts is not None:
        config["prompt_registry"] = prompts
    return AgencyOrchestrator(config=config, memory_db_path=":memory:")


def test_factory_issued_agent_context_gets_explicit_allowlist(tmp_path) -> None:
    factory, prompts, kernels, skills = _factory_stack(tmp_path)
    try:
        service = RequestedAgentService(
            factory=factory, prompt_registry=prompts, skill_registry=skills
        )
        result = service.request_agent(
            RequestedAgentRequest(
                blueprint_id="scout",
                name="Scout",
                purpose="Inspect binaries.",
                creator="owner",
                domain="forge",
                capabilities=("search",),
                tool_requirements=("rea",),
            )
        )
        orch = _orchestrator(factory, prompts)
        agent = kernels.get_required(result.agent_id)
        orch._identity_registry.register(agent)

        ctx = orch._tool_context(result.agent_id, "task-1")
        allowed = ctx.metadata.get("allowed_tools")
        assert isinstance(allowed, frozenset)
        assert "rea" in allowed  # blueprint requirement flowed through
        assert _ALWAYS_ON <= allowed  # builtin always-ons present
        assert allowed == (frozenset(_ALWAYS_ON) | {"rea"})  # default profile deduped
        # Cached per agent; repeated lookups return the identical frozenset.
        again = orch._tool_context(result.agent_id, "task-2").metadata["allowed_tools"]
        assert again is allowed
        assert orch._tool_allowlist_cache[result.agent_id] is allowed
    finally:
        factory.close()
        prompts.close()
        skills.close()


def test_factory_issued_agent_without_reachable_blueprint_fails_closed(tmp_path) -> None:
    """Factory-issued identity, no factory configured: still an explicit allowlist."""
    factory, prompts, kernels, skills = _factory_stack(tmp_path)
    try:
        service = RequestedAgentService(
            factory=factory, prompt_registry=prompts, skill_registry=skills
        )
        result = service.request_agent(
            RequestedAgentRequest(
                blueprint_id="scout",
                name="Scout",
                purpose="Search documents.",
                creator="owner",
                domain="research",
                capabilities=("search",),
            )
        )
        orch = _orchestrator(None, None)  # factory unreachable
        agent = kernels.get_required(result.agent_id)
        orch._identity_registry.register(agent)
        allowed = orch._tool_context(result.agent_id, "task-1").metadata.get("allowed_tools")
        assert isinstance(allowed, frozenset) and allowed  # non-empty, fail closed
        assert allowed == frozenset(_ALWAYS_ON)
    finally:
        factory.close()
        prompts.close()
        skills.close()


def test_legacy_agent_context_has_no_allowlist(tmp_path) -> None:
    orch = _orchestrator(None, None)
    legacy = Agent(id="legacy-1", name="Plain", domain="general", capabilities=[])
    orch._identity_registry.register(legacy)
    ctx = orch._tool_context("legacy-1", "task-1")
    assert "allowed_tools" not in ctx.metadata
    assert ctx.metadata == {}
    # Explicit None keeps the same behavior.
    ctx2 = orch._tool_context("legacy-1", "task-1", allowed_tools=None)
    assert "allowed_tools" not in ctx2.metadata


def test_unknown_agent_context_has_no_allowlist(tmp_path) -> None:
    orch = _orchestrator(None, None)
    ctx = orch._tool_context("ghost", "task-1")
    assert "allowed_tools" not in ctx.metadata


# --------------------------------------------------------------------- #
# improve_agent: re-versioned blueprint keeps tool_requirements
# --------------------------------------------------------------------- #


def _service(tmp_path) -> tuple[RequestedAgentService, AgentFactory, PromptRegistry]:
    prompts = PromptRegistry(str(tmp_path / "prompts.db"))
    ensure_builtin_prompts(prompts)
    skills = SkillRegistry(str(tmp_path / "skills.db"))
    factory = AgentFactory(
        str(tmp_path / "factory.db"),
        authorizer=lambda actor, perm: perm == "agent.approve",
        allowed_capabilities={"search"},
        kernel_registry=KernelRegistry(),
        runtime_registry=RuntimeRegistry(),
        is_prompt_published=prompts.is_published,
        prompt_resolver=prompts,
        is_skill_published=lambda sid, ver: True,
    )
    service = RequestedAgentService(factory=factory, prompt_registry=prompts, skill_registry=skills)
    return service, factory, prompts


def test_improve_agent_reversion_keeps_tool_requirements(tmp_path) -> None:
    service, factory, prompts = _service(tmp_path)
    try:
        result = service.request_agent(
            RequestedAgentRequest(
                blueprint_id="scout",
                name="Scout",
                purpose="Search documents by keyword.",
                creator="owner",
                domain="research",
                capabilities=("search",),
                tool_requirements=("rea", "web_search"),
            )
        )
        assert factory.get("scout", "1.0.0").tool_requirements == ("rea", "web_search")
        trace = TaskTrace(task_id="task-1", title="Find docs", status="completed")
        report = service.improve_agent(result.agent_id, trace, prompt_gaps=["Be concise."])
        assert report.blueprint_version == "1.0.1"
        improved = factory.get("scout", "1.0.1")
        assert improved.status is BlueprintStatus.ACTIVE
        assert improved.tool_requirements == ("rea", "web_search")
    finally:
        factory.close()
        prompts.close()


def test_default_requirements_cover_builtin_always_on_set() -> None:
    """The needs catalog default profile matches the builtin always-ons."""
    assert set(default_requirements()) == _BUILTIN_ALWAYS_ON
