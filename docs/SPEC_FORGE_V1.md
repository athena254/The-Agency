# Forge v1 — read-only patch inspection gate

Status: bounded design, not the full Forge in `SOURCE_CONSOLIDATED_BRIEF.md` §§ 11–12. Reuse `agency.workflows` for versioned orchestration later; v1 is a deterministic, non-LLM inspector. The existing `addons/dark_factory` remains a legacy generator, not Forge.

## Contract

- Trusted local caller passes an absolute repository path and a list of relative changed paths. Do not accept an HTTP request as authority. Reject absolute paths, `..`, paths escaping root via symlink, directories, oversized files, more than 64 files, and nonexistent files. Do not follow symlinks. No network, shell commands, subprocesses, generated code execution, installation, Git writes, or release.
- For each accepted regular file, store relative path, SHA-256, byte count, and Python syntax verdict if `.py`; parse via `ast.parse` only. No raw source content in the report. Reject parse errors as failed findings, not as an unhandled crash. Unsupported file types may be hashed but must not be described as security-reviewed.
- An immutable report includes explicit gate state (`PASS` only for bounded inventory/syntax, `FAIL` for invalid syntax or rejected paths), caveats (`tests_not_run`, `security_review_not_run`, `release_not_authorized`), timestamp, and evidence of every inspected path. Persist the report in SQLite keyed by unique report ID so it survives restart. No report alone may authorize merge/deploy.
- This is an inspection gate, not a sandbox or end-to-end SDLC. Add tests for symlink/path traversal, missing file, cap, binary content, syntax failure, content hash, persistence, and no process execution.

## Boundary

Forge must be Agency-native and independent of ATHENA. Do not rename an unrelated external factory or rewrite code as Athena-native. The coding agent, actual test execution in isolation, static/security scanning, adversarial QA, review approvals, and release remain follow-on work.