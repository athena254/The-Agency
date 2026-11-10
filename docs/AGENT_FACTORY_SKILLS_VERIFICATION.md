# Agent Factory and skills verification

## Markdown imports: review-only

The Markdown importer lives in `agency.factory.imports.MarkdownImportService`.
`import_markdown(Path(...))` accepts a standalone `.md` file; a string argument
is Markdown content, with optional `source_label`. GitHub repository imports
accept HTTPS `github.com/{owner}/{repo}[.git]` and an explicit branch/tag ref.
They enumerate only Markdown files plus GitHub's license metadata; tree,
individual file, and total repository byte/count limits apply. GitHub refs are
not a substitute for an immutable commit pin; callers should supply a commit
SHA as `ref` for reproducible imports.

Every imported body is inert and is registered as a `DRAFT` prompt in
`PromptRegistry` with `import-quarantine:` provenance containing the source,
path/ref, content digest, and observed SPDX license (or `UNKNOWN`). YAML
frontmatter is parsed with `safe_load`; all fields remain metadata, not
permissions or executable configuration. Code blocks and links are never run
or followed. A missing repository license is reported as `UNKNOWN`, not
assumed permissive.

`find_similar` returns review suggestions only. `propose_merge` requires an
explicit selection of 2-10 source IDs and creates a new immutable `DRAFT`
prompt with `reviewed-import:` provenance. It does not alter sources, grant
permissions, publish, approve, attach the draft to an agent, or activate an
identity. Imported skills currently enter the PromptRegistry as inert skill
instruction content; promotion into the executable skill registry and any
agent/skill typed composition requires separate review and governance.

Format/license research is recorded in
`docs/research-evidence/markdown-agent-skill-import-research.md`.

# Agent Factory and skills system — verification report

**Checked:** November 10, 2026
**Result:** Bounded library components work; guarded ordinary-task skill execution, draft learning/evolution, and authorized factory recovery are implemented. This is not autonomous skill publication or production deployment.

This report records a source-and-test review of the Agent Factory, skill registry/executor, and the related workflow tests. “Working” here means the inspected component behavior is implemented and the focused tests pass; it does not imply production readiness, deployment, or full agent integration.

## Findings

### Agent Factory: bounded lifecycle works

`src/agency/factory/service.py` implements a SQLite-backed factory for versioned blueprints. Its module contract explicitly limits the factory to metadata and deterministic validation: it does not generate code, select models, evaluate agents, sandbox them, or deploy them. Approval uses an injected authorizer; pinned skill and workflow references are checked by injected callbacks, and requested references fail closed when the callback is absent or rejects them. Activation creates an identity at `TrustLevel.UNKNOWN` with L0-only claims and does not grant permissions. See the module contract at [`src/agency/factory/service.py`](../src/agency/factory/service.py#L1-L18), the v1 design contract in [`docs/SPEC_AGENT_FACTORY_V1.md`](SPEC_AGENT_FACTORY_V1.md), and the implementation’s approval/activation/revocation methods in that service.

**Restart recovery:** Startup still does not auto-resurrect identities. `AgentFactory.recover_active(...)` is an explicit recovery path requiring trusted `agent.recover` authorization and nonempty evidence; it revalidates pinned references, records an ACTIVE-to-ACTIVE audit event, and restores the recorded identity ID with UNKNOWN trust, L0 claims, and no permission grant. Conflicts fail closed; newly registered identities are rolled back if recovery audit fails. See `src/agency/factory/service.py` and tests in `tests/test_agent_factory.py`.

Autonomous capability development, evaluation, permission grants, and deployment remain outside this implementation.

### Skills: registry and guarded execution seam work

`src/agency/skills/registry.py` stores versioned skill metadata and lifecycle state in SQLite; registry entries are data, not executable source. `src/agency/skills/executor.py` is a separate execution seam: it requires `PUBLISHED` status, checks the permissions supplied to the executor, resolves the skill’s logical implementation name only through a registered operation, and returns failure for unpublished skills, denied permissions, or unknown operations. It does not import or evaluate arbitrary skill code. See [`src/agency/skills/registry.py`](../src/agency/skills/registry.py) and [`src/agency/skills/executor.py`](../src/agency/skills/executor.py#L1-L6), [`#L27-L55`](../src/agency/skills/executor.py#L27-L55), and [`#L56-L110`](../src/agency/skills/executor.py#L56-L110).

**Runtime integration:** `agency_skill` is now registered with the ordinary non-beta tool loop. It accepts an exact skill ID/version and inputs, but delegates to `AgencyOrchestrator.execute_skill(...)`; only factory-issued agents with that exact pinned reference can invoke it. The orchestrator re-reads the published version and resolves only host-registered operations. Permissioned skills require the configured trusted `skill_authorizer`; absent, false, or failing authorization denies execution. The model may request a tool call, but cannot bypass these checks or create executable code. See `src/agency/skills/tool.py`, `src/agency/orchestrator.py`, and `tests/test_orchestrator_skills.py`.

**Learning and evolution:** successful completed work continues to create draft proposals. When a task successfully invokes a pinned skill, its task evidence can also produce a new immutable successor draft for that skill, preserving the operation and permission requirements. The original published version is unchanged. Drafts are never selectable for execution and require the normal review/approval/publication path. New versions are not silently attached to existing agents; use a separately approved blueprint version to pin one.

### Workflows are a bounded adjacent component

`src/agency/workflows/executor.py` executes only pre-registered operations in deterministic order and documents that it does not promise exactly-once behavior or automatic recovery. The broader workflow spec states that arbitrary imports, shell commands, and remote skill execution are not part of v1: [`docs/SPEC_WORKFLOW_V1.md`](SPEC_WORKFLOW_V1.md#L7-L17).

## Verification performed

Focused tests were run from the repository root using the existing virtual environment:

```powershell
.venv\Scripts\python.exe -m pytest tests\test_agent_factory.py tests\test_factory_prompt_compose.py tests\test_factory_prompt_refs.py tests\test_skill_registry.py tests\test_skill_executor.py tests\test_workflow_registry.py -q
```

**Post-change focused result:** `123 passed in 11.12s` across factory, prompt, skills, orchestrator, runtime prompt, and skill integration tests.

Post-change lint passed for the touched source and test files. Targeted mypy passed for `src/agency/orchestrator.py` and `src/agency/factory/service.py` with `--follow-imports=silent`.

The full repository suite was attempted but did not complete in this environment; its background process stalled during collection/execution and was stopped. This is focused test evidence, not a full-repository test run, CI result, live-service check, or security certification.

## Bottom line

- **Agent Factory lifecycle:** implemented and passing focused tests within its explicit v1 boundary.
- **Skill metadata registry and allowlisted executor:** implemented and passing focused tests as explicit library seams.
- **Ordinary agent skill use:** implemented through the guarded `agency_skill` tool for exact pinned, published versions, allowlisted operations, and trusted permission checks. The tool is only added to the legacy non-beta tool registry; beta/owner strict registries are not modified.
- **On-the-fly learning/evolution:** completed successful work proposes draft metadata; successful pinned skill use can propose immutable successor drafts from task evidence. No learned proposal is auto-approved, published, executable, or attached to a running agent.
- **Factory recovery:** implemented only as explicit authorized recovery with evidence; automatic startup rehydration remains disabled.
- **Autonomous/production factory:** not implemented.

For the repository’s maintained high-level status, see [`docs/CURRENT_STATE.md`](CURRENT_STATE.md#L82-L91) and [`docs/ARCHITECTURE.md`](ARCHITECTURE.md#L110-L113).
