# Markdown agent and skill import research

**Checked:** November 10, 2026

## Upstream repository formats

### `msitarzewski/agency-agents`

- Agents are individual Markdown files grouped by functional directories such as `engineering/`; see [the repository](https://github.com/msitarzewski/agency-agents) and [engineering agents](https://github.com/msitarzewski/agency-agents/tree/main/engineering).
- The [Senior Developer agent](https://raw.githubusercontent.com/msitarzewski/agency-agents/main/engineering/engineering-senior-developer.md) begins with YAML frontmatter (`name`, `description`, `color`, `emoji`, `vibe`) followed by long-form Markdown agent instructions.
- The root [README](https://raw.githubusercontent.com/msitarzewski/agency-agents/main/README.md) and [LICENSE](https://raw.githubusercontent.com/msitarzewski/agency-agents/main/LICENSE) indicate MIT licensing.

### `affaan-m/ECC`

- The [current repository README](https://raw.githubusercontent.com/affaan-m/ECC/main/README.md) describes a source tree with component collections and platform-specific packaging. Current `skills/` is a directory of named skills, not `skills/coding/` or `skills/departments/`; see [skills tree](https://github.com/affaan-m/ECC/tree/main/skills). A coding-related example is [coding-standards/SKILL.md](https://raw.githubusercontent.com/affaan-m/ECC/main/skills/coding-standards/SKILL.md).
- Skill files use YAML frontmatter including `name`, `description`, and sometimes nested metadata, followed by Markdown instructions.
- The [README](https://raw.githubusercontent.com/affaan-m/ECC/main/README.md) and [LICENSE](https://raw.githubusercontent.com/affaan-m/ECC/main/LICENSE) indicate MIT licensing.

### `mattpocock/skills`

- Skills are grouped under category and skill directories, e.g. [engineering/tdd](https://github.com/mattpocock/skills/tree/main/skills/engineering/tdd), with a `SKILL.md` entry point and optional supporting Markdown files.
- The [TDD skill](https://raw.githubusercontent.com/mattpocock/skills/main/skills/engineering/tdd/SKILL.md) uses YAML `name` and `description` fields and Markdown body content.
- The [README](https://raw.githubusercontent.com/mattpocock/skills/main/README.md) and [LICENSE](https://raw.githubusercontent.com/mattpocock/skills/main/LICENSE) indicate MIT licensing.

## Import implications

1. There is no single shared Markdown schema. Support plain standalone Markdown, frontmatter-based agent files, and `SKILL.md` directory layouts; preserve unknown frontmatter as inert source metadata.
2. Directories do not reliably indicate whether a Markdown file is an agent or skill. Classification should be explicit or reported as an uncertain import finding, not inferred as authority.
3. Record source repository, pinned commit/ref, source path, content digest, and observed license. Repository-level MIT declarations are useful provenance, but the importer must not treat a missing/ambiguous license as permission to redistribute.
4. Imported text is untrusted instruction/data. Never execute frontmatter, code fences, scripts, or links; do not follow transitive URLs. Quarantine content and create drafts only.
5. Near-duplicate detection should return evidence and candidate pairs. It must not silently combine content: a human/authorized caller chooses a base and creates a new immutable draft, preserving both source provenances.
6. Reuse Agency's existing immutable capability contracts, attestations, resolver, Factory draft creation, and explicit approval flow. Imported agents remain prompt/content proposals and do not gain permissions or runtime identity by import.

## Primary source links

- [Agency Agents](https://github.com/msitarzewski/agency-agents)
- [ECC](https://github.com/affaan-m/ECC)
- [Matt Pocock skills](https://github.com/mattpocock/skills)
- [Agency Factory v2 untrusted import requirements](../SPEC_AGENT_FACTORY_V2.md#123-untrusted-imports)