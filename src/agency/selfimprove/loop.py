"""High-level self-improvement loop wrapping the SkillRegistry."""

from __future__ import annotations

from agency.selfimprove.distiller import TaskTrace, distill_skill
from agency.skills.registry import SkillRegistry, SkillSpec, SkillStatus


class SelfImprovementLoop:
    """Distill experience into skills and retrieve relevant learned skills."""

    def __init__(self, registry: SkillRegistry) -> None:
        self._registry = registry

    @property
    def registry(self) -> SkillRegistry:
        return self._registry

    def distill(self, trace: TaskTrace) -> SkillSpec | None:
        """Attempt to distill one task trace into a DRAFT skill."""
        return distill_skill(trace, self._registry)

    def relevant_skills(self, description: str, *, limit: int = 5) -> list[SkillSpec]:
        """Return published skills whose name/description overlap the query."""
        if not description.strip():
            return []
        tokens = {t for t in re_split(description) if len(t) >= 3}
        if not tokens:
            return []
        matches: list[tuple[int, SkillSpec]] = []
        # Collect all skill ids by scanning list_versions is not possible without
        # an index; iterate known ids via a best-effort scan of the DB.
        for skill_id in self._list_skill_ids():
            for spec in self._registry.list_versions(skill_id):
                if spec.status is not SkillStatus.PUBLISHED:
                    continue
                haystack = f"{spec.name} {spec.description}".lower()
                score = sum(1 for t in tokens if t in haystack)
                if score > 0:
                    matches.append((score, spec))
        matches.sort(key=lambda pair: (-pair[0], pair[1].skill_id, pair[1].version))
        # Dedupe by skill_id keeping highest version.
        seen: set[str] = set()
        result: list[SkillSpec] = []
        for _score, spec in matches:
            if spec.skill_id in seen:
                continue
            seen.add(spec.skill_id)
            result.append(spec)
            if len(result) >= limit:
                break
        return result

    def _list_skill_ids(self) -> list[str]:
        try:
            rows = self._registry._conn.execute("SELECT DISTINCT skill_id FROM skills").fetchall()
        except Exception:  # noqa: BLE001 - best effort
            return []
        return [str(r[0]) for r in rows]


def re_split(text: str) -> list[str]:
    import re

    return re.findall(r"[a-z0-9]+", text.lower())
