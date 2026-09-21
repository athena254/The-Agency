"""SDLC workflow — requirements → design → build → test → deploy → maintain.

Each phase produces a typed :class:`SDLCArtifact` carrying both the produced
content and machine-checkable evidence (checks run, hashes, timestamps) so
downstream phases and auditors can verify provenance without re-running work.
"""

from __future__ import annotations

import ast
import hashlib
from datetime import UTC, datetime
from enum import Enum
from typing import Any
from uuid import uuid4

import structlog
from pydantic import BaseModel, ConfigDict, Field

logger = structlog.get_logger(__name__)


class SDLCPhase(str, Enum):
    """Ordered phases of the Dark Factory lifecycle."""

    REQUIREMENTS = "requirements"
    DESIGN = "design"
    BUILD = "build"
    TEST = "test"
    DEPLOY = "deploy"
    MAINTAIN = "maintain"


class SDLCArtifact(BaseModel):
    """Output of a single SDLC phase with attached evidence."""

    model_config = ConfigDict(extra="forbid")

    artifact_id: str = Field(default_factory=lambda: str(uuid4()))
    phase: SDLCPhase
    title: str = Field(default="")
    content: dict[str, Any] = Field(default_factory=dict)
    evidence: dict[str, Any] = Field(default_factory=dict)
    parent_id: str | None = Field(default=None)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    def content_hash(self) -> str:
        """Stable SHA-256 over the canonical content payload."""
        digest = hashlib.sha256(repr(sorted(self.content.items())).encode("utf-8"))
        return digest.hexdigest()


class RequirementSpec(BaseModel):
    """Structured requirements produced by :meth:`SDLCWorkflow.analyze_requirements`."""

    model_config = ConfigDict(extra="forbid")

    goals: list[str] = Field(default_factory=list)
    functional: list[str] = Field(default_factory=list)
    non_functional: list[str] = Field(default_factory=list)
    constraints: list[str] = Field(default_factory=list)
    acceptance_criteria: list[str] = Field(default_factory=list)
    out_of_scope: list[str] = Field(default_factory=list)


class ArchitectureDesign(BaseModel):
    """Architecture produced by :meth:`SDLCWorkflow.design_architecture`."""

    model_config = ConfigDict(extra="forbid")

    components: list[str] = Field(default_factory=list)
    modules: list[dict[str, Any]] = Field(default_factory=list)
    interfaces: list[dict[str, Any]] = Field(default_factory=list)
    data_flow: list[str] = Field(default_factory=list)
    risks: list[str] = Field(default_factory=list)


class SDLCWorkflow:
    """Deterministic, task-agnostic SDLC pipeline.

    The workflow is intentionally dependency-free: each method accepts plain
    data (or the artifact of the previous phase) and returns a new
    :class:`SDLCArtifact`. Callers chain phases via :meth:`run_full_cycle`
    or invoke individual phases for fine-grained control.
    """

    def __init__(self) -> None:
        self._log = logger.bind(component="SDLCWorkflow")
        self._history: list[SDLCArtifact] = []

    # ------------------------------------------------------------------ #
    # Properties
    # ------------------------------------------------------------------ #

    @property
    def history(self) -> list[SDLCArtifact]:
        """Artifacts produced so far, in phase order."""
        return list(self._history)

    # ------------------------------------------------------------------ #
    # Phases
    # ------------------------------------------------------------------ #

    def analyze_requirements(self, spec: str | dict[str, Any]) -> SDLCArtifact:
        """Parse a raw spec into structured requirements + acceptance criteria."""
        raw = spec if isinstance(spec, str) else "\n".join(f"{k}: {v}" for k, v in spec.items())
        lines = [line.strip("-•* \t") for line in raw.splitlines() if line.strip()]
        goals = [
            line
            for line in lines
            if any(k in line.lower() for k in ("goal", "purpose", "objective"))
        ] or lines[:2]
        functional = [line for line in lines if line not in goals] or [
            "Implement requested behaviour."
        ]
        requirements = RequirementSpec(
            goals=goals,
            functional=functional,
            non_functional=[
                "Typed Python with Pydantic v2 models.",
                "Structured logging via structlog.",
            ],
            constraints=["Python >= 3.11.", "No uncontrolled network access at runtime."],
            acceptance_criteria=[f"AC-{i + 1}: {item}" for i, item in enumerate(functional)],
            out_of_scope=[],
        )
        artifact = SDLCArtifact(
            phase=SDLCPhase.REQUIREMENTS,
            title="Requirements analysis",
            content=requirements.model_dump(),
            evidence={
                "source_lines": len(lines),
                "criteria_count": len(requirements.acceptance_criteria),
                "method": "heuristic_parse",
            },
        )
        self._record(artifact)
        return artifact

    def design_architecture(
        self, reqs: SDLCArtifact | RequirementSpec | dict[str, Any]
    ) -> SDLCArtifact:
        """Derive components, modules, and interfaces from requirements."""
        payload = self._coerce(reqs, SDLCPhase.REQUIREMENTS)
        functional: list[str] = list(payload.get("functional", []))
        modules = [
            {"name": self._slug(item), "responsibility": item, "language": "python"}
            for item in functional
        ]
        design = ArchitectureDesign(
            components=[m["name"] for m in modules] or ["core"],
            modules=modules,
            interfaces=[{"module": m["name"], "api": [f"{m['name']}.run"]} for m in modules],
            data_flow=[f"{m['name']} -> core" for m in modules],
            risks=["Scope creep in underspecified requirements."],
        )
        parent_id = reqs.artifact_id if isinstance(reqs, SDLCArtifact) else None
        artifact = SDLCArtifact(
            phase=SDLCPhase.DESIGN,
            title="Architecture design",
            content=design.model_dump(),
            evidence={
                "modules": len(modules),
                "interfaces": len(design.interfaces),
                "parent_id": parent_id,
            },
            parent_id=parent_id,
        )
        self._record(artifact)
        return artifact

    def implement(self, design: SDLCArtifact | ArchitectureDesign | dict[str, Any]) -> SDLCArtifact:
        """Scaffold Python modules from an architecture design."""
        payload = self._coerce(design, SDLCPhase.DESIGN)
        modules: list[dict[str, Any]] = list(payload.get("modules", [])) or [
            {"name": "core", "responsibility": "Core behaviour"}
        ]
        files: dict[str, str] = {}
        for module in modules:
            name = str(module.get("name", "core"))
            responsibility = str(module.get("responsibility", ""))
            files[f"{name}.py"] = self._render_module(name, responsibility)
        artifact = SDLCArtifact(
            phase=SDLCPhase.BUILD,
            title="Implementation",
            content={"files": files, "language": "python"},
            evidence={
                "file_count": len(files),
                "total_lines": sum(v.count("\n") + 1 for v in files.values()),
                "syntax_checked": self._syntax_ok(files),
            },
            parent_id=design.artifact_id if isinstance(design, SDLCArtifact) else None,
        )
        self._record(artifact)
        return artifact

    def test(self, implementation: SDLCArtifact | dict[str, Any]) -> SDLCArtifact:
        """Statically validate an implementation (syntax + import surface)."""
        payload = (
            implementation.content
            if isinstance(implementation, SDLCArtifact)
            else dict(implementation)
        )
        files: dict[str, str] = dict(payload.get("files", {}))
        results: dict[str, dict[str, Any]] = {}
        for name, source in files.items():
            try:
                tree = ast.parse(source)
                functions = [
                    n.name
                    for n in ast.walk(tree)
                    if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
                ]
                classes = [n.name for n in ast.walk(tree) if isinstance(n, ast.ClassDef)]
                results[name] = {"ok": True, "functions": functions, "classes": classes}
            except SyntaxError as exc:
                results[name] = {"ok": False, "error": f"{exc.msg} (line {exc.lineno})"}
        passed = all(r["ok"] for r in results.values()) if results else False
        artifact = SDLCArtifact(
            phase=SDLCPhase.TEST,
            title="Test report",
            content={"results": results, "passed": passed},
            evidence={
                "files_checked": len(results),
                "passed": passed,
                "method": "ast_parse",
            },
            parent_id=implementation.artifact_id
            if isinstance(implementation, SDLCArtifact)
            else None,
        )
        self._record(artifact)
        return artifact

    def deploy(self, artifact: SDLCArtifact | dict[str, Any]) -> SDLCArtifact:
        """Package a tested implementation into a deployable bundle descriptor."""
        payload = artifact.content if isinstance(artifact, SDLCArtifact) else dict(artifact)
        files: dict[str, str] = dict(
            payload.get("files", payload.get("content", {}).get("files", {}))
        )
        bundle_hash = hashlib.sha256(repr(sorted(files.items())).encode("utf-8")).hexdigest()
        deployment = SDLCArtifact(
            phase=SDLCPhase.DEPLOY,
            title="Deployment bundle",
            content={
                "bundle_hash": bundle_hash,
                "files": sorted(files.keys()),
                "entrypoint": (min(files.keys()) if files else None),
                "version": "0.1.0",
            },
            evidence={"bundle_hash": bundle_hash, "file_count": len(files)},
            parent_id=artifact.artifact_id if isinstance(artifact, SDLCArtifact) else None,
        )
        self._record(deployment)
        return deployment

    def maintain(self, artifact: SDLCArtifact | dict[str, Any]) -> SDLCArtifact:
        """Produce a maintenance plan (monitoring, patching, debt) for a bundle."""
        parent_id = artifact.artifact_id if isinstance(artifact, SDLCArtifact) else None
        plan = {
            "monitoring": ["Track error rate and latency of generated modules."],
            "patch_policy": ["Security patches within 24h; minor fixes weekly."],
            "tech_debt": [
                "Re-run test phase after every change.",
                "Regenerate bundle hash on release.",
            ],
            "review_cadence_days": 30,
        }
        maintenance = SDLCArtifact(
            phase=SDLCPhase.MAINTAIN,
            title="Maintenance plan",
            content=plan,
            evidence={"parent_id": parent_id, "review_cadence_days": 30},
            parent_id=parent_id,
        )
        self._record(maintenance)
        return maintenance

    def run_full_cycle(self, spec: str | dict[str, Any]) -> list[SDLCArtifact]:
        """Run requirements → design → build → test → deploy → maintain."""
        reqs = self.analyze_requirements(spec)
        design = self.design_architecture(reqs)
        implementation = self.implement(design)
        test_report = self.test(implementation)
        deployment = self.deploy(implementation)
        maintenance = self.maintain(deployment)
        self._log.info(
            "sdlc.cycle_complete",
            artifacts=len(self._history),
            tests_passed=test_report.content.get("passed", False),
        )
        return [reqs, design, implementation, test_report, deployment, maintenance]

    # ------------------------------------------------------------------ #
    # Internals
    # ------------------------------------------------------------------ #

    def _record(self, artifact: SDLCArtifact) -> None:
        self._history.append(artifact)
        self._log.info(
            "sdlc.phase_complete",
            phase=artifact.phase.value,
            artifact_id=artifact.artifact_id,
            hash=artifact.content_hash(),
        )

    @staticmethod
    def _coerce(
        value: SDLCArtifact | BaseModel | dict[str, Any], expected: SDLCPhase
    ) -> dict[str, Any]:
        if isinstance(value, SDLCArtifact):
            if value.phase is not expected:
                raise ValueError(f"expected {expected.value} artifact, got {value.phase.value}")
            return dict(value.content)
        if isinstance(value, BaseModel):
            return value.model_dump()
        return dict(value)

    @staticmethod
    def _slug(text: str) -> str:
        slug = "".join(ch.lower() if ch.isalnum() else "_" for ch in text[:40]).strip("_")
        while "__" in slug:
            slug = slug.replace("__", "_")
        return slug or "core"

    @staticmethod
    def _render_module(name: str, responsibility: str) -> str:
        class_name = "".join(part.capitalize() for part in name.split("_")) or "Core"
        return (
            f'"""Generated module {name}: {responsibility}."""\n\n'
            "from __future__ import annotations\n\n"
            "import structlog\n"
            "from pydantic import BaseModel, Field\n\n"
            f"logger = structlog.get_logger(__name__)\n\n\n"
            f"class {class_name}Config(BaseModel):\n"
            '    """Configuration for the generated module."""\n\n'
            "    enabled: bool = Field(default=True)\n\n\n"
            f"class {class_name}:\n"
            f'    """{responsibility}."""\n\n'
            "    def __init__(self, config: " + f"{class_name}Config | None = None) -> None:\n"
            "        self.config = config or " + f"{class_name}Config()\n"
            '        self._log = logger.bind(module="' + name + '")\n\n'
            "    def run(self, payload: dict[str, object]) -> dict[str, object]:\n"
            '        """Execute the module behaviour."""\n'
            '        self._log.info("module.run", payload_keys=sorted(payload.keys()))\n'
            '        return {"ok": True, "module": "' + name + '", "payload": payload}\n'
        )

    @staticmethod
    def _syntax_ok(files: dict[str, str]) -> bool:
        try:
            for source in files.values():
                ast.parse(source)
            return True
        except SyntaxError:
            return False


__all__ = [
    "ArchitectureDesign",
    "RequirementSpec",
    "SDLCArtifact",
    "SDLCPhase",
    "SDLCWorkflow",
]
