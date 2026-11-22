---
type: SKILL_SKILLS_REGISTRY
schema_version: 1
---

# Skill — Skills, workflows and patterns

**Three distinct asset kinds — never conflate them.**

| Kind | Definition | Tools? |
|---|---|---|
| Pattern | Reusable reasoning recipe (Fabric-style): extract wisdom, critique, summarize | No |
| Skill | Registered operational capability with inputs/outputs; may reference scripts/tools | Checked by kernel at runtime |
| Workflow | Versioned multi-step orchestration with conditions and error behavior | Via its steps |

**Registries.** Skills and workflows live in versioned registries
(`src/agency/skills/`, workflow registry) with validation and lifecycle
contracts. The user-level skill folders (e.g. `C:\Users\PC\.agents\skills`)
are editor-side helpers, not Agency registry entries.

**Importing external skills/agents.**

- `uv run python scripts/import_github_skills.py --discover-new` prints repos
  whose tree SHA is new/changed.
- `--emit-plan data/repo_imports.txt` writes a newline-delimited plan;
  `agency loop run repos:data/repo_imports.txt` imports each repo through the
  governed pipeline (SkillSpector-gated, results in `data/promotion_results/`).
- Import with `--promote` only after reviewing the catalog; **never execute
  upstream code** as part of review.
- Division-grouped promotion uses `scripts/promote_division.py` with
  SkillSpector gates.

**Rules to state to users.**

- Imported content is untrusted input until reviewed and registered.
- A SKILL.md describes behavior; the kernel decides actual tool access.
- SkillSpector risk scores gate promotion; debris is flagged, not silently
  imported.
- Registration ≠ permission: tools are granted by the kernel, always.

**Guidance.** To add a capability: prefer an existing registered skill →
else draft one → SkillSpector scan → owner review → register → verify with
a harmless task. For reusable reasoning (no tools), propose a pattern
document instead of a skill.
