"""Offline smoke test of Agency threads, skills, and deterministic workflows."""
from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory

from agency.butler.threads import ThreadStore
from agency.skills.registry import SkillRegistry, SkillSpec, SkillStatus
from agency.workflows.executor import WorkflowExecutor
from agency.workflows.registry import WorkflowDefinition, WorkflowRegistry, WorkflowStep


def main() -> None:
    with TemporaryDirectory() as directory:
        root = Path(directory)
        threads = ThreadStore(str(root / "threads.db"))
        workspace = threads.create_workspace("smoke-user", "Smoke project")
        thread = threads.create_thread("smoke-user", workspace.id, "Research")
        threads.append_message("smoke-user", thread.id, "user", "verify this")
        threads.close()
        reopened = ThreadStore(str(root / "threads.db"))
        assert reopened.list_messages("smoke-user", thread.id)[0].content == "verify this"
        try:
            reopened.list_messages("other-user", thread.id)
        except KeyError:
            pass
        else:
            raise AssertionError("thread access was not owner-scoped")
        reopened.close()

        skills = SkillRegistry(str(root / "skills.db"), allowed_permissions=frozenset({"analyze"}))
        skills.register(SkillSpec(
            skill_id="count-text", version="1.0.0", name="Count text", description="Character count",
            inputs={"text": "string"}, outputs={"count": "integer"},
            permissions=("analyze",), implementation="text.count", provenance="smoke",
        ))
        skills.transition("count-text", "1.0.0", SkillStatus.TESTING)
        skills.transition("count-text", "1.0.0", SkillStatus.APPROVED, "unit-tested")
        skill = skills.publish("count-text", "1.0.0", "human-approved")
        assert skills.list_events("count-text", "1.0.0")[-1]["evidence"] == "human-approved"

        workflows = WorkflowRegistry(str(root / "workflows.db"))
        workflows.register(WorkflowDefinition(
            workflow_id="count", version="1.0.0", steps=(WorkflowStep(
                step_id="measure", operation=skill.implementation, permissions=skill.permissions,
            ),),
        ))
        executor = WorkflowExecutor(
            workflows,
            operations={"text.count": lambda inputs, _: {"count": len(inputs["text"])}},
            authorizer=lambda actor, permission: actor == "smoke-user" and permission == "analyze",
        )
        run = executor.run("count", "1.0.0", {"text": "verify this"}, "smoke-user")
        assert run.status == "COMPLETED"
        assert run.step_results["measure"]["output"] == {"count": 11}
        denied = executor.run("count", "1.0.0", {"text": "secret"}, "other-user")
        assert denied.status == "FAILED"
        workflows.close()
        reopened_workflows = WorkflowRegistry(str(root / "workflows.db"))
        assert reopened_workflows.get_run(run.run_id).status == "COMPLETED"
        reopened_workflows.close()
        skills.close()
        print("AGENCY_CORE_SMOKE_OK thread_isolation=ok skill_lifecycle=ok workflow=ok deny=ok restart=ok")


if __name__ == "__main__":
    main()
