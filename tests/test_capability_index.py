from __future__ import annotations

import dataclasses

import pytest

from agency.capability.contract import (
    CapabilityContract,
    CapabilityNotFoundError,
    CapabilityRef,
    CapabilityType,
    CorruptCapabilityError,
    ProvenanceRef,
    compute_content_digest,
)
from agency.capability.index import (
    CapabilityIndex,
    CapabilityQuery,
    CapabilitySearchResult,
    IndexBuildReport,
)


def _prov() -> ProvenanceRef:
    return ProvenanceRef("agency_native", source_id="src-1")


def _mk(
    ctype: CapabilityType,
    cid: str,
    version: str,
    *,
    name: str,
    description: str,
    lifecycle: str,
    native: str | None = None,
    tags: tuple[str, ...] = (),
    perms: tuple[str, ...] = ("read",),
) -> CapabilityContract:
    base = CapabilityContract(
        1,
        CapabilityRef(ctype, cid, version),
        name,
        description,
        lifecycle,
        native if native is not None else lifecycle,
        None,
        None,
        perms,
        None,
        (),
        tags,
        _prov(),
        "sha256:" + "0" * 64,
        None,
    )
    return dataclasses.replace(base, content_digest=compute_content_digest(base))


class FakeAdapter:
    def __init__(
        self,
        ctype: CapabilityType,
        contracts: list[CapabilityContract],
        *,
        corrupt: bool = False,
    ) -> None:
        self.capability_type = ctype
        self._contracts = {(c.ref.capability_id, c.ref.version): c for c in contracts}
        self._corrupt = corrupt

    def list_refs(self) -> list[CapabilityRef]:
        return [c.ref for c in self._contracts.values()]

    def get(self, capability_id: str, version: str) -> CapabilityContract:
        if self._corrupt:
            raise CorruptCapabilityError("corrupt record")
        try:
            return self._contracts[(capability_id, version)]
        except KeyError:
            raise CapabilityNotFoundError(f"unknown {capability_id}@{version}") from None


def _contracts() -> dict[str, CapabilityContract]:
    return {
        "summarize": _mk(
            CapabilityType.SKILL,
            "summarize-docs",
            "1.0.0",
            name="Summarize Docs",
            description="Summarize a document deterministically",
            lifecycle="PUBLISHED",
            tags=("docs", "summarize"),
        ),
        "draft": _mk(
            CapabilityType.SKILL,
            "draft-skill",
            "1.0.0",
            name="Draft Helper",
            description="Helper draft notes",
            lifecycle="DRAFT",
            tags=("docs", "helper"),
        ),
        "prompt": _mk(
            CapabilityType.PROMPT,
            "tone-guide",
            "1.0.0",
            name="Tone Guide",
            description="Style and tone guide",
            lifecycle="PUBLISHED",
            tags=("writing", "tone"),
        ),
        "workflow": _mk(
            CapabilityType.WORKFLOW,
            "daily-sync",
            "1.0.0",
            name="Daily Sync",
            description="Run daily sync steps",
            lifecycle="REGISTERED_UNVERIFIED",
            tags=("sync",),
        ),
        "alpha1": _mk(
            CapabilityType.SKILL,
            "alpha",
            "1.0.0",
            name="Alpha",
            description="alpha tool",
            lifecycle="PUBLISHED",
            tags=("tools",),
        ),
        "alpha2": _mk(
            CapabilityType.SKILL,
            "alpha",
            "2.0.0",
            name="Alpha",
            description="alpha tool v2",
            lifecycle="PUBLISHED",
            tags=("tools",),
        ),
        "unattested": _mk(
            CapabilityType.SKILL,
            "old-skill",
            "1.0.0",
            name="Old Skill",
            description="old unattested skill",
            lifecycle="PUBLISHED_UNATTESTED",
            native="PUBLISHED",
            tags=("docs",),
        ),
        "testing": _mk(
            CapabilityType.PROMPT,
            "test-prompt",
            "1.0.0",
            name="Test Prompt",
            description="testing prompt",
            lifecycle="TESTING",
            tags=("writing",),
        ),
    }


def _index() -> tuple[CapabilityIndex, dict[str, CapabilityContract]]:
    cs = _contracts()
    skills = FakeAdapter(
        CapabilityType.SKILL,
        [cs["summarize"], cs["draft"], cs["alpha1"], cs["alpha2"], cs["unattested"]],
    )
    prompts = FakeAdapter(CapabilityType.PROMPT, [cs["prompt"], cs["testing"]])
    workflows = FakeAdapter(CapabilityType.WORKFLOW, [cs["workflow"]])
    return CapabilityIndex([skills, prompts, workflows]), cs


def test_empty_before_rebuild() -> None:
    idx, cs = _index()
    assert len(idx) == 0
    assert idx.search(CapabilityQuery(text="docs")) == []
    assert idx.get(cs["summarize"].ref) is None
    assert idx.is_stale(cs["summarize"]) is True


def test_rebuild_count_and_types() -> None:
    idx, _ = _index()
    report = idx.rebuild()
    assert isinstance(report, IndexBuildReport)
    assert report.count == 8
    assert report.types == ("prompt", "skill", "workflow")
    assert len(idx) == 8
    # Rebuild is deterministic.
    assert idx.rebuild() == report


def test_search_by_text_casefold() -> None:
    idx, _ = _index()
    idx.rebuild()
    hits = idx.search(CapabilityQuery(text="SUMMAR", include_unverified=True))
    assert [h.contract.ref.capability_id for h in hits] == ["summarize-docs"]
    # Matches description and id as well.
    assert [
        h.contract.ref.capability_id for h in idx.search(CapabilityQuery(text="tone guide"))
    ] == ["tone-guide"]
    assert [
        h.contract.ref.capability_id for h in idx.search(CapabilityQuery(text="DAILY"))
    ] == []  # unverified excluded
    hits = idx.search(CapabilityQuery(text="alpha", include_unverified=True))
    assert {(h.contract.ref.capability_id, h.contract.ref.version) for h in hits} == {
        ("alpha", "1.0.0"),
        ("alpha", "2.0.0"),
    }


def test_search_by_type_and_tag() -> None:
    idx, _ = _index()
    idx.rebuild()
    by_type = idx.search(CapabilityQuery(capability_type=CapabilityType.PROMPT))
    assert [(h.contract.ref.capability_type, h.contract.ref.capability_id) for h in by_type] == [
        (CapabilityType.PROMPT, "tone-guide")
    ]
    # All requested tags must be present (exact).
    both = idx.search(CapabilityQuery(tags=("docs", "summarize")))
    assert [h.contract.ref.capability_id for h in both] == ["summarize-docs"]
    docs = idx.search(CapabilityQuery(tags=("docs",), include_unverified=True))
    assert [h.contract.ref.capability_id for h in docs] == [
        "draft-skill",
        "old-skill",
        "summarize-docs",
    ]
    assert idx.search(CapabilityQuery(tags=("missing",), include_unverified=True)) == []


def test_unverified_excluded_by_default_included_with_flag() -> None:
    idx, _ = _index()
    idx.rebuild()
    default_ids = {h.contract.ref.capability_id for h in idx.search(CapabilityQuery())}
    assert "draft-skill" not in default_ids
    assert "old-skill" not in default_ids
    assert "daily-sync" not in default_ids
    assert "test-prompt" not in default_ids
    assert default_ids == {"summarize-docs", "tone-guide", "alpha"}
    all_ids = {
        h.contract.ref.capability_id for h in idx.search(CapabilityQuery(include_unverified=True))
    }
    assert {"draft-skill", "old-skill", "daily-sync", "test-prompt"} <= all_ids


def test_limit_and_deterministic_order() -> None:
    idx, _ = _index()
    idx.rebuild()
    hits = idx.search(CapabilityQuery(include_unverified=True, limit=3))
    assert len(hits) == 3
    keys = [
        (h.contract.ref.capability_type.value, h.contract.ref.capability_id, h.contract.ref.version)
        for h in hits
    ]
    assert keys == sorted(keys)
    assert keys[0] == ("prompt", "test-prompt", "1.0.0")
    full = idx.search(CapabilityQuery(include_unverified=True))
    full_keys = [
        (h.contract.ref.capability_type.value, h.contract.ref.capability_id, h.contract.ref.version)
        for h in full
    ]
    assert full_keys == sorted(full_keys)
    assert full_keys[:2] == [("prompt", "test-prompt", "1.0.0"), ("prompt", "tone-guide", "1.0.0")]


def test_get_and_is_stale() -> None:
    idx, cs = _index()
    idx.rebuild()
    got = idx.get(cs["summarize"].ref)
    assert got is not None and got.ref == cs["summarize"].ref
    assert idx.get(CapabilityRef(CapabilityType.SKILL, "nope", "1.0.0")) is None
    assert idx.is_stale(cs["summarize"]) is False
    tampered = dataclasses.replace(cs["summarize"], content_digest="sha256:" + "f" * 64)
    assert idx.is_stale(tampered) is True
    unknown = _mk(
        CapabilityType.SKILL, "ghost", "1.0.0", name="Ghost", description="x", lifecycle="PUBLISHED"
    )
    assert idx.is_stale(unknown) is True


def test_corrupt_and_notfound_propagate_on_rebuild() -> None:
    cs = _contracts()
    corrupt = FakeAdapter(CapabilityType.SKILL, [cs["summarize"]], corrupt=True)
    idx = CapabilityIndex([corrupt])
    with pytest.raises(CorruptCapabilityError):
        idx.rebuild()

    class MissingAdapter(FakeAdapter):
        def get(self, capability_id: str, version: str) -> CapabilityContract:
            raise CapabilityNotFoundError("missing")

    idx2 = CapabilityIndex([MissingAdapter(CapabilityType.SKILL, [cs["summarize"]])])
    with pytest.raises(CapabilityNotFoundError):
        idx2.rebuild()


def test_query_validation() -> None:
    CapabilityQuery(limit=1)
    CapabilityQuery(limit=500)
    CapabilityQuery(text="x" * 500)
    with pytest.raises(ValueError):
        CapabilityQuery(limit=0)
    with pytest.raises(ValueError):
        CapabilityQuery(limit=501)
    with pytest.raises(ValueError):
        CapabilityQuery(text="x" * 501)


def test_result_to_dict_and_result_wrapper() -> None:
    idx, cs = _index()
    idx.rebuild()
    result = idx.search(CapabilityQuery())[0]
    assert isinstance(result, CapabilitySearchResult)
    d = result.to_dict()
    assert d["name"] == result.contract.name
    assert d["lifecycle_state"] == result.contract.lifecycle_state
    assert d["native_state"] == result.contract.native_state
    assert d["content_digest"] == result.contract.content_digest
    assert d["requested_permissions"] == list(result.contract.requested_permissions)
    assert d["tags"] == list(result.contract.tags)
    assert d["ref"] == result.contract.ref.to_dict()
    assert cs["summarize"].ref.to_dict()["capability_id"] == "summarize-docs"
