"""Requested-agent onboarding and self-improvement.

Turns a free-text request into a governed Agent Factory identity: discovers
relevant published skills, writes a versioned purpose prompt, creates/approves/
activates a blueprint.  During work, ``improve_agent`` refines skills and the
prompt and re-versions the blueprint.  Every write goes through the native
registries so lifecycle transitions and audit events are preserved.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from agency.capability.adapters import SkillCapabilityAdapter
from agency.capability.attestation import AttestationError
from agency.capability.contract import CapabilityRef, CapabilityType
from agency.core.hashing import canonical_sha256
from agency.factory.prompts import PromptKind, PromptRegistry, PromptSpec, PromptStatus
from agency.factory.prompts_builtin import ensure_builtin_prompts
from agency.factory.service import AgentFactory, BlueprintStatus
from agency.selfimprove.distiller import TaskTrace, _description_from_trace, _slugify
from agency.selfimprove.loop import SelfImprovementLoop
from agency.skills.executor import DOCUMENTATION_ONLY_IMPLEMENTATION
from agency.skills.registry import SkillRegistry, SkillSpec, SkillStatus

if TYPE_CHECKING:
    from agency.capability.attestation import AttestationStore

MAX_PURPOSE_CHARS = 8000
MAX_NAME_CHARS = 200
MAX_DOMAIN_CHARS = 128
MAX_BLUEPRINT_ID_LEN = 128
MAX_GAPS = 16
MAX_GAP_CHARS = 512
MAX_NEW_SKILLS = 8
MAX_LIFETIME_LEARNED_SKILLS = 32
SELF_IMPROVE_PERMISSION = "agent.self_improve"
MAX_ID_CHARS = 128

BASE_PROMPT_REFS: tuple[tuple[str, str], ...] = (
    ("agency-base-identity", "1.0.0"),
    ("agency-evidence-discipline", "1.0.0"),
    ("agency-l0-scope", "1.0.0"),
)

_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")


@dataclass(frozen=True)
class RequestedAgentRequest:
    blueprint_id: str
    name: str
    purpose: str
    creator: str
    domain: str = "general"
    capabilities: tuple[str, ...] = ()
    approver: str = ""
    tool_requirements: Sequence[str] | None = None
    use_default_profile: bool = False


@dataclass(frozen=True)
class RequestedAgentResult:
    blueprint_id: str
    version: str
    agent_id: str
    attached_skills: tuple[tuple[str, str], ...]
    prompt_refs: tuple[tuple[str, str], ...]
    note: str


@dataclass(frozen=True)
class ImprovementReport:
    agent_id: str
    blueprint_id: str
    blueprint_version: str
    evolved_skills: tuple[tuple[str, str], ...]
    distilled_skills: tuple[tuple[str, str], ...]
    new_skills: tuple[tuple[str, str], ...]
    prompt_refined: bool
    prompt_version: str
    pending_promotions: tuple[tuple[str, str], ...]
    note: str


def _next_version(versions: list[str]) -> str:
    if not versions:
        return "1.0.0"
    parts = versions[-1].split(".")
    if len(parts) != 3:
        raise ValueError(f"invalid version: {versions[-1]!r}")
    return f"{parts[0]}.{parts[1]}.{int(parts[2]) + 1}"


def _purpose_prompt_id(blueprint_id: str) -> str:
    return f"purpose-{blueprint_id}"


def _nonblank(value: object, maximum: int) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        raise ValueError(f"invalid nonblank string (max {maximum})")
    return value.strip()


def _normalize_gaps(prompt_gaps: Sequence[str] | None) -> tuple[str, ...]:
    if not prompt_gaps:
        return ()
    gaps: list[str] = []
    for raw in prompt_gaps:
        if not isinstance(raw, str):
            continue
        text = raw.strip()[:MAX_GAP_CHARS]
        if text and text not in gaps:
            gaps.append(text)
        if len(gaps) >= MAX_GAPS:
            break
    return tuple(gaps)


def _failed_skill_steps(trace: TaskTrace) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    for step in trace.tool_steps:
        if not isinstance(step, dict):
            continue
        if step.get("ok") is not False:
            continue
        sid = step.get("skill_id")
        if not isinstance(sid, str) or not sid.strip():
            continue
        raw_ver = step.get("version")
        ver = raw_ver.strip() if isinstance(raw_ver, str) and raw_ver.strip() else ""
        out.append((sid.strip(), ver))
    return out


def _invoked_skills(trace: TaskTrace) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    for step in trace.tool_steps:
        if not isinstance(step, dict):
            continue
        sid = step.get("skill_id")
        if not isinstance(sid, str) or not sid.strip():
            continue
        raw_ver = step.get("version")
        ver = raw_ver.strip() if isinstance(raw_ver, str) and raw_ver.strip() else ""
        out.append((sid.strip(), ver))
    return out


def _derived_gaps(trace: TaskTrace) -> tuple[str, ...]:
    gaps: list[str] = []
    for sid, ver in _failed_skill_steps(trace):
        suffix = f" v{ver}" if ver else ""
        gaps.append(
            f"Skill {sid}{suffix} failed during task {trace.task_id}. "
            "Improve or create a replacement skill."[:MAX_GAP_CHARS]
        )
        if len(gaps) >= MAX_GAPS:
            break
    return tuple(gaps)


def _normalize_description(text: str) -> str:
    """Lowercase, collapse whitespace, and blank digit runs.

    Volatile identifiers (task ids, skill versions) inside generated gap
    descriptions must not defeat cross-cycle dedup (gaps.md #8).
    """
    return re.sub(r"\d+", "#", " ".join(text.lower().split()))


def _description_key(text: str) -> str:
    """Neutral hash of the normalized description (gaps.md #8)."""
    return canonical_sha256(_normalize_description(text))


def _new_gap_skill(
    registry: SkillRegistry, agent_id: str, title: str, gap: str
) -> SkillSpec | None:
    description = gap[:500]
    slug = re.sub(r"[^a-z0-9]+", "-", _normalize_description(description)).strip("-")[:48]
    slug = slug or "gap-skill"
    skill_id = f"gap-{slug}"[:64]
    # Cross-cycle dedup: the same normalized description already registered
    # under this id means the gap was recorded in an earlier cycle (gaps.md #8).
    key = _description_key(description)
    for existing in registry.list_versions(skill_id):
        if _description_key(existing.description) == key:
            return None
    version = _next_version([s.version for s in registry.list_versions(skill_id)])
    spec = SkillSpec(
        skill_id=skill_id,
        version=version,
        name=f"Gap: {title}"[:120],
        description=description,
        inputs={"task": {"type": "string"}},
        outputs={"result": {"type": "string"}},
        permissions=(),
        # Documentation-only metadata (gaps.md #4): no callable is registered
        # for learned gap skills, so never name a nonexistent operation here.
        implementation=DOCUMENTATION_ONLY_IMPLEMENTATION,
        provenance=f"agent-gap:{agent_id}",
        status=SkillStatus.DRAFT,
    )
    try:
        return registry.register(spec)
    except (ValueError, KeyError):
        return None


def _learned_skill_duplicate(registry: SkillRegistry, trace: TaskTrace) -> bool:
    """True when the record ``distill_skill`` would create already exists.

    Mirrors the distiller's id and description derivation so the dedup key
    matches the record that would be registered (gaps.md #8).
    """
    skill_id = f"learned-{_slugify(trace.title)}"[:64]
    key = _description_key(_description_from_trace(trace))
    return any(
        _description_key(item.description) == key for item in registry.list_versions(skill_id)
    )


class RequestedAgentService:
    """Compose governed agents from a request; refine them during work."""

    def __init__(
        self,
        *,
        factory: AgentFactory,
        prompt_registry: PromptRegistry,
        skill_registry: SkillRegistry,
        kernel_registry: Any | None = None,
        attestation_store: AttestationStore | None = None,
        approver: str = "factory-improvement",
        auto_promote: bool = True,
        self_improve_authorizer: Callable[[str, str], bool] | None = None,
    ) -> None:
        self._factory = factory
        self._prompt_registry = prompt_registry
        self._skill_registry = skill_registry
        self._kernel = kernel_registry if kernel_registry is not None else factory.kernel_registry
        self._attestation_store = attestation_store
        self._approver = approver
        self._auto_promote = bool(auto_promote)
        # Human gate for self-modification (gaps.md #2): prompt refinement and
        # blueprint re-versioning are denied unless a distinct actor is
        # accepted by this callback for ``agent.self_improve`` (fail closed).
        self._self_improve_authorizer = self_improve_authorizer
        # Lifetime count of learned/gap skills registered by this service.
        self._learned_skill_count = 0
        self._loop = SelfImprovementLoop(skill_registry)

    def _validate_request(self, request: RequestedAgentRequest) -> None:
        if not isinstance(request, RequestedAgentRequest):
            raise TypeError("request must be a RequestedAgentRequest")
        bid = _nonblank(request.blueprint_id, MAX_BLUEPRINT_ID_LEN)
        if _ID_RE.fullmatch(bid) is None:
            raise ValueError(f"invalid blueprint_id: {request.blueprint_id!r}")
        _nonblank(request.name, MAX_NAME_CHARS)
        _nonblank(request.purpose, MAX_PURPOSE_CHARS)
        _nonblank(request.domain, MAX_DOMAIN_CHARS)
        _nonblank(request.creator, MAX_ID_CHARS)
        if request.approver:
            _nonblank(request.approver, MAX_ID_CHARS)

    def _discover_published_skills(
        self, purpose: str, *, limit: int = 8
    ) -> tuple[tuple[str, str], ...]:
        try:
            found = self._loop.relevant_skills(purpose, limit=limit)
        except Exception:  # noqa: BLE001 - discovery is best-effort
            return ()
        out: list[tuple[str, str]] = []
        for spec in found:
            if spec.status is not SkillStatus.PUBLISHED:
                continue
            key = (spec.skill_id, spec.version)
            if key not in out:
                out.append(key)
            if len(out) >= limit:
                break
        return tuple(out)

    def _purpose_body(
        self, request: RequestedAgentRequest, attached: tuple[tuple[str, str], ...]
    ) -> str:
        lines = [
            f"# {request.name}",
            "",
            "## Role",
            "You are an agent manufactured by The Agency's governed Agent Factory.",
            f"- Domain: {request.domain}",
            f"- Purpose: {request.purpose}",
        ]
        if attached:
            lines.append("")
            lines.append(
                "## Pinned Skills (Agency-native, versioned; invoke via `agency_skill` "
                "with the exact pinned ID, version, and inputs)"
            )
            for skill_id, version in attached:
                try:
                    description = self._skill_registry.get(skill_id, version).description
                except Exception:  # noqa: BLE001
                    description = ""
                lines.append(f"- {skill_id} v{version}: {description}")
        lines.extend(
            [
                "",
                "## Behavior",
                (
                    "- Operate only within the capabilities recorded on your identity; "
                    "never assume authority you were not issued."
                ),
                (
                    "- Stay inside the domain and scope of this blueprint; escalate or stop "
                    "when a request leaves it."
                ),
                (
                    "- State the evidence for every claim; never fabricate tool results, "
                    "outputs or findings."
                ),
                (
                    "- When you notice a missing or insufficient skill, or a prompt rule "
                    "that would improve the work, record it in the self-improvement report "
                    "and recommend a fix."
                ),
                "",
            ]
        )
        return "\n".join(lines)

    def request_agent(self, request: RequestedAgentRequest) -> RequestedAgentResult:
        """Create, approve, and activate a governed agent for ``request``."""
        self._validate_request(request)
        ensure_builtin_prompts(self._prompt_registry)
        tool_requirements = request.tool_requirements
        if tool_requirements is None and request.use_default_profile:
            # Lazy import: agency.tools must never be a module-level factory
            # dependency (gaps.md #1 import-cycle rule).
            from agency.tools.needs import default_requirements

            tool_requirements = default_requirements()

        versions = [bp.version for bp in self._factory.list_versions(request.blueprint_id)]
        version = _next_version(versions)
        attached = self._discover_published_skills(request.purpose, limit=8)

        pid = _purpose_prompt_id(request.blueprint_id)
        prompt_version = _next_version(
            [s.version for s in self._prompt_registry.list_versions(pid)]
        )
        body = self._purpose_body(request, attached)
        self._prompt_registry.register(
            PromptSpec(
                prompt_id=pid,
                version=prompt_version,
                name=f"Purpose: {request.name}"[:200],
                description=(request.purpose or request.name)[:2000],
                domain=request.domain,
                kind=PromptKind.BASE,
                body=body,
                provenance=f"requested-agent:{request.blueprint_id}",
                status=PromptStatus.DRAFT,
            )
        )
        self._prompt_registry.transition(
            pid, prompt_version, PromptStatus.TESTING, evidence="Purpose prompt written"
        )
        self._prompt_registry.transition(
            pid,
            prompt_version,
            PromptStatus.APPROVED,
            evidence="Validated for blueprint pinning",
        )
        self._prompt_registry.publish(
            pid,
            prompt_version,
            evidence="Published for blueprint approval",
            approver=self._approver,
        )

        prompt_refs = list(BASE_PROMPT_REFS) + [(pid, prompt_version)]
        self._factory.create(
            request.blueprint_id,
            version,
            request.name,
            domain=request.domain,
            capabilities=list(request.capabilities),
            tool_requirements=list(tool_requirements or ()),
            skill_refs=list(attached),
            prompt_refs=prompt_refs,
            creator=request.creator,
        )
        approver = request.approver or self._approver
        if approver == request.creator:
            raise ValueError("approver must differ from creator")
        self._factory.approve(
            request.blueprint_id,
            version,
            approver=approver,
            evidence=f"Requested agent {request.name!r} approved",
        )
        agent = self._factory.activate(request.blueprint_id, version)
        note = (
            f"Activated agent {agent.id}; pinned skills: {attached or 'none'}; "
            f"purpose prompt {pid} v{prompt_version}"
        )
        return RequestedAgentResult(
            blueprint_id=request.blueprint_id,
            version=version,
            agent_id=agent.id,
            attached_skills=attached,
            prompt_refs=tuple(prompt_refs),
            note=note,
        )

    def is_factory_issued(self, agent_id: str) -> bool:
        agent = self._kernel.get(agent_id)
        return bool(agent is not None and agent.metadata.get("factory") == "agent-factory-v1")

    def _require_self_improve_approval(self, approver: str, agent_id: str) -> str:
        """Human gate before self-modification (gaps.md #2); fails closed.

        The requester — the agent being improved and this service's automated
        identity — can never authorize its own re-versioning. A distinct actor
        must be accepted by ``self_improve_authorizer`` for
        ``agent.self_improve``, mirroring ``AgentFactory.approve``.
        """
        if not isinstance(approver, str) or not approver.strip():
            raise ValueError("self-improvement approver must be a nonblank string.")
        approver = approver.strip()
        if approver == agent_id or approver == self._approver:
            raise ValueError(
                "self-improvement approver must differ from the requester (no self-approval)."
            )
        if self._self_improve_authorizer is None:
            raise PermissionError("no authorizer configured (fail closed).")
        try:
            allowed = self._self_improve_authorizer(approver, SELF_IMPROVE_PERMISSION)
        except PermissionError:
            raise
        except Exception as exc:
            raise PermissionError(f"authorizer failed closed: {exc}") from exc
        if allowed is not True:
            raise PermissionError(f"approver {approver!r} denied for {SELF_IMPROVE_PERMISSION}.")
        return approver

    def improve_agent(
        self,
        agent_id: str,
        trace: TaskTrace,
        *,
        prompt_gaps: Sequence[str] | None = None,
        max_new_skills: int = MAX_NEW_SKILLS,
        approver: str = "",
    ) -> ImprovementReport:
        """Refine skills/prompt for a factory-issued agent, re-version its blueprint.

        Prompt refinement and blueprint re-versioning are human-gated
        (gaps.md #2): ``approver`` must be a distinct actor authorized for
        ``agent.self_improve`` by the configured ``self_improve_authorizer``.
        """
        agent = self._kernel.get_required(agent_id)
        if agent.metadata.get("factory") != "agent-factory-v1":
            raise ValueError(f"agent {agent_id!r} is not factory-issued")
        blueprint_id = str(agent.metadata["blueprint_id"])
        old_version = str(agent.metadata["blueprint_version"])
        bp = self._factory.get(blueprint_id, old_version)
        if bp.status is not BlueprintStatus.ACTIVE:
            raise ValueError(f"blueprint {(blueprint_id, old_version)!r} is not ACTIVE")

        gaps = _normalize_gaps(prompt_gaps) if prompt_gaps is not None else _derived_gaps(trace)
        if gaps and self._auto_promote:
            approver = self._require_self_improve_approval(approver, agent_id)

        targets = list(_failed_skill_steps(trace))
        if gaps:
            for sid, ver in _invoked_skills(trace):
                if (sid, ver) not in targets:
                    targets.append((sid, ver))
        evolved: list[tuple[str, str]] = []
        seen: set[tuple[str, str]] = set()
        for sid, ver in targets:
            if (sid, ver) in seen:
                continue
            seen.add((sid, ver))
            spec = self._loop.evolve(sid, trace)
            if spec is not None:
                evolved.append((spec.skill_id, spec.version))

        new_skills: list[tuple[str, str]] = []
        for gap in gaps[:max_new_skills]:
            if self._learned_skill_count >= MAX_LIFETIME_LEARNED_SKILLS:
                break
            spec = _new_gap_skill(self._skill_registry, agent_id, trace.title, gap)
            if spec is not None:
                self._learned_skill_count += 1
                key = (spec.skill_id, spec.version)
                if key not in new_skills:
                    new_skills.append(key)

        distilled: list[tuple[str, str]] = []
        if (
            trace.status.strip().lower() == "completed"
            and self._learned_skill_count < MAX_LIFETIME_LEARNED_SKILLS
            and not _learned_skill_duplicate(self._skill_registry, trace)
        ):
            spec = self._loop.distill(trace)
            if spec is not None:
                self._learned_skill_count += 1
                key = (spec.skill_id, spec.version)
                if key not in new_skills and key not in distilled:
                    distilled.append(key)

        prompt_refined = False
        prompt_version = ""
        new_bp_version = ""
        new_agent_id = agent_id
        if gaps and self._auto_promote:
            pid = _purpose_prompt_id(blueprint_id)
            versions = [s.version for s in self._prompt_registry.list_versions(pid)]
            new_prompt_version = _next_version(versions)
            prev = self._prompt_registry.get(pid, versions[-1]) if versions else None
            gaps_text = "\n".join(f"- {gap}" for gap in gaps)
            body = (
                f"{prev.body if prev is not None else ''}"
                f"\n\n## Refinements (requested agent improvement)\n{gaps_text}\n"
            )
            self._prompt_registry.register(
                PromptSpec(
                    prompt_id=pid,
                    version=new_prompt_version,
                    name=f"Purpose: {bp.name}"[:200],
                    description=(prev.description if prev is not None else bp.name)[:2000],
                    domain=bp.domain,
                    kind=PromptKind.BASE,
                    body=body,
                    provenance=f"requested-agent:refined:{blueprint_id}",
                    status=PromptStatus.DRAFT,
                )
            )
            self._prompt_registry.transition(
                pid,
                new_prompt_version,
                PromptStatus.TESTING,
                evidence="Refined by self-improvement loop",
            )
            self._prompt_registry.transition(
                pid,
                new_prompt_version,
                PromptStatus.APPROVED,
                evidence="Validated by self-improvement loop",
            )
            self._prompt_registry.publish(
                pid,
                new_prompt_version,
                evidence="Published for blueprint re-activation",
                approver=approver,
            )
            prompt_refined = True
            prompt_version = new_prompt_version

            new_bp_version = _next_version([bp.version])
            new_refs = [ref for ref in bp.prompt_refs if ref[0] != pid] + [
                (pid, new_prompt_version)
            ]
            self._factory.create(
                blueprint_id,
                new_bp_version,
                bp.name,
                domain=bp.domain,
                capabilities=list(bp.capabilities),
                tool_requirements=list(bp.tool_requirements),
                skill_refs=list(bp.skill_refs),
                prompt_refs=new_refs,
                prompt_params=dict(bp.prompt_params),
                prompt_override=bp.prompt_override,
                creator=bp.creator,
            )
            self._factory.approve(
                blueprint_id,
                new_bp_version,
                approver=approver,
                evidence=f"Blueprint re-version for self-improvement: {gaps[0][:200]}",
            )
            # One SQLite transaction: a crash can never leave two ACTIVE
            # blueprints or strand the superseded identity (gaps.md #3).
            new_agent = self._factory.reversion(
                blueprint_id,
                new_bp_version,
                old_version,
                evidence=f"Superseded by {new_bp_version}",
            )
            new_agent_id = new_agent.id

        pending = tuple(evolved + distilled + new_skills)
        note = (
            f"Skills evolved={len(evolved)} distilled={len(distilled)} "
            f"new={len(new_skills)} prompt_refined={prompt_refined}"
        )
        return ImprovementReport(
            agent_id=new_agent_id,
            blueprint_id=blueprint_id,
            blueprint_version=new_bp_version or old_version,
            evolved_skills=tuple(evolved),
            distilled_skills=tuple(distilled),
            new_skills=tuple(new_skills),
            prompt_refined=prompt_refined,
            prompt_version=prompt_version,
            pending_promotions=pending,
            note=note,
        )

    def promote_skill(self, skill_id: str, version: str, *, evidence: str = "") -> None:
        spec = self._skill_registry.get(skill_id, version)
        if spec.status is SkillStatus.DRAFT:
            self._skill_registry.transition(
                skill_id,
                version,
                SkillStatus.TESTING,
                evidence=evidence or "Promoted by requested-agent service",
            )
        elif spec.status is not SkillStatus.TESTING:
            raise ValueError(f"cannot promote {(skill_id, version)!r} from {spec.status.value}")

    def publish_skill(
        self, skill_id: str, version: str, *, approver: str, evidence: str = ""
    ) -> SkillSpec:
        spec = self._skill_registry.get(skill_id, version)
        if spec.status is SkillStatus.PUBLISHED:
            raise ValueError("skill is already published")
        # Mandatory publication gate (gaps.md #5): an unattested skill can
        # never be promoted DRAFT -> PUBLISHED by this service.
        if self._attestation_store is None:
            raise AttestationError(
                f"publication of {(skill_id, version)!r} requires a configured "
                "attestation store (fail closed)."
            )
        digest = SkillCapabilityAdapter(self._skill_registry).get(skill_id, version).content_digest
        ref = CapabilityRef(CapabilityType.SKILL, skill_id, version)
        if self._attestation_store.get(ref, digest) is None:
            raise AttestationError(f"publication of {(skill_id, version)!r} requires attestation")
        current = self._skill_registry.get(skill_id, version)
        if current.status is SkillStatus.DRAFT:
            self._skill_registry.transition(
                skill_id,
                version,
                SkillStatus.TESTING,
                evidence=evidence or "Promoted to TESTING before publish",
            )
            current = self._skill_registry.get(skill_id, version)
        if current.status is SkillStatus.TESTING:
            self._skill_registry.transition(
                skill_id,
                version,
                SkillStatus.APPROVED,
                evidence=evidence or "Approved before publish",
            )
        return self._skill_registry.publish(
            skill_id,
            version,
            evidence=evidence or "Published by requested-agent service",
            approver=approver,
        )


__all__ = [
    "BASE_PROMPT_REFS",
    "MAX_BLUEPRINT_ID_LEN",
    "MAX_DOMAIN_CHARS",
    "MAX_GAPS",
    "MAX_GAP_CHARS",
    "MAX_LIFETIME_LEARNED_SKILLS",
    "MAX_NAME_CHARS",
    "MAX_NEW_SKILLS",
    "MAX_PURPOSE_CHARS",
    "SELF_IMPROVE_PERMISSION",
    "ImprovementReport",
    "RequestedAgentRequest",
    "RequestedAgentResult",
    "RequestedAgentService",
]
