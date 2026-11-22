"""Tests for the bundled prompt library seed data (segments + REAP packs)."""

from __future__ import annotations

from agency.factory.prompts import (
    MAX_PROMPT_BODY_BYTES,
    PromptKind,
    PromptRegistry,
    PromptStatus,
)
from agency.factory.prompts_builtin import (
    DATA_DIR,
    MANIFEST_PATH,
    builtin_specs,
    ensure_builtin_prompts,
)

REAP_PACK_IDS = {
    "reap-00-install-and-verify",
    "reap-01-getting-started",
    "reap-02-feature-tracing-any-program",
    "reap-03-native-binary-exploration",
    "reap-04-software-and-feature-cloning",
    "reap-05-games",
    "reap-06-game-portability-to-wasm",
    "reap-07-firmware-and-embedded",
    "reap-08-android",
    "reap-09-kernel-analysis",
    "reap-10-vulnerability-research",
    "reap-11-electron-and-javascript",
    "reap-12-anti-cheat-and-protection-research",
    "reap-13-dma-and-hardware-assisted-cheat-research-defensive",
    "reap-14-software-cracking-research",
    "reap-15-version-comparison-and-crash-work",
    "reap-16-long-running-investigations",
    "reap-17-malware-analysis",
    "reap-18-advanced-binary-exploration-and-deobfuscation",
    "reap-19-kernel-and-driver-exploitation-research",
    "reap-20-advanced-software-cloning-and-feature-reconstruction",
    "reap-21-protector-and-obfuscator-research",
    "reap-22-go-and-rust-binary-source-recovery",
    "reap-wildcard",
    "reap-prompt-writing-tips",
    "reap-prompts",
}

SEGMENT_IDS = {
    "agency-base-identity",
    "agency-evidence-discipline",
    "agency-forge-orchestrator",
    "agency-l0-scope",
    "agency-tool-availability",
    "apprentice-researcher",
    "reap-core-methodology",
    "reap-safety-scope",
    "reap-tool-availability",
}

DEFAULT_AGENT_PROMPT_IDS = {
    "default-agent-personal",
    "default-agent-work",
    "default-agent-business",
    "default-agent-security",
    "default-agent-research",
    "default-agent-general",
}

DEFAULT_AGENT_DOMAINS = {
    "default-agent-personal": "personal",
    "default-agent-work": "work",
    "default-agent-business": "business",
    "default-agent-security": "security",
    "default-agent-research": "research",
    "default-agent-general": "general",
}

ALL_BUILTIN_IDS = REAP_PACK_IDS | SEGMENT_IDS | DEFAULT_AGENT_PROMPT_IDS


def test_manifest_and_license_present() -> None:
    assert MANIFEST_PATH.exists()
    assert (DATA_DIR / "THIRD_PARTY_LICENSES.md").exists()
    license_text = (DATA_DIR / "THIRD_PARTY_LICENSES.md").read_text(encoding="utf-8")
    assert "MIT License" in license_text
    assert "Joas Antonio dos Santos" in license_text
    assert "ccb02b89721dd7d466beb17004a89d6bc25f5b02" in license_text


def test_builtin_specs_cover_segments_and_all_reap_packs() -> None:
    specs = builtin_specs()
    ids = {spec.prompt_id for spec in specs}
    assert ids == ALL_BUILTIN_IDS
    assert len(specs) == len(ALL_BUILTIN_IDS)
    by_id = {spec.prompt_id: spec for spec in specs}
    for pack_id in REAP_PACK_IDS:
        assert by_id[pack_id].kind is PromptKind.PACK
        assert by_id[pack_id].provenance.startswith("builtin:")
        assert "JoasASantos/REA-Prompts@" in by_id[pack_id].provenance
    for segment_id in SEGMENT_IDS:
        assert by_id[segment_id].kind is PromptKind.SEGMENT
        assert by_id[segment_id].status is PromptStatus.PUBLISHED
    assert by_id["reap-safety-scope"].kind is PromptKind.SEGMENT
    for prompt_id in DEFAULT_AGENT_PROMPT_IDS:
        assert by_id[prompt_id].kind is PromptKind.BASE
        assert by_id[prompt_id].status is PromptStatus.PUBLISHED
        assert by_id[prompt_id].domain == DEFAULT_AGENT_DOMAINS[prompt_id]
        assert by_id[prompt_id].version == "1.0.0"
        assert by_id[prompt_id].provenance.startswith("builtin:agency-factory/default-agents/")


def test_every_builtin_body_is_nonblank_and_bounded() -> None:
    for spec in builtin_specs():
        assert spec.body.strip()
        assert len(spec.body.encode("utf-8")) <= MAX_PROMPT_BODY_BYTES


def test_reap_packs_preserve_upstream_evidence_discipline() -> None:
    by_id = {spec.prompt_id: spec for spec in builtin_specs()}
    for pack_id in ("reap-02-feature-tracing-any-program", "reap-10-vulnerability-research"):
        body = by_id[pack_id].body.lower()
        assert "evidence" in body
        assert "unknown" in body or "hypothes" in body


def test_ensure_builtin_prompts_is_idempotent(tmp_path) -> None:
    reg = PromptRegistry(str(tmp_path / "prompts.db"))
    try:
        assert ensure_builtin_prompts(reg) == len(ALL_BUILTIN_IDS)
        assert ensure_builtin_prompts(reg) == 0
        assert reg.is_published("reap-safety-scope", "1.0.0") is True
        assert (
            reg.is_published("reap-13-dma-and-hardware-assisted-cheat-research-defensive", "1.0.0")
            is True
        )
        events = reg.list_events("reap-safety-scope", "1.0.0")
        assert len(events) == 1
        assert events[0]["to_status"] == "PUBLISHED"
        assert "builtin:" in events[0]["evidence"]
    finally:
        reg.close()
