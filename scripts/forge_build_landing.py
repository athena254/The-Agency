"""Agency-built landing page via Forge (no hand-written HTML).

The Agency (LLM) writes the HTML; Forge generates + proves + smokes.
Usage:
  AGENCY_LLM_PROVIDER=inception AGENCY_LLM_MODEL=mercury-3 \\
  .venv/Scripts/python.exe scripts/forge_build_landing.py
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

from agency.autonomy.proof import ProofBundle
from agency.forge.generate import generate_app
from agency.forge.pipeline import run_pipeline
from agency.forge.smoke import smoke_app

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

PROMPT = """Write a very short HTML landing page (under 350 words) for The Agency: hero, 3 bullets (signed 3-of-4 quorum, hash-chain proof, Forge factory), footer. Dark theme, one <style>. Return ONLY raw HTML from <!DOCTYPE html> to </html>."""

out_root = Path(__file__).resolve().parents[1] / "agency-landing"
proof = ProofBundle()

# 1. Agency LLM generates the HTML (debug-repair loop)
from agency.llm.adapter import LLMAdapter

adapter = LLMAdapter(provider=os.environ.get("AGENCY_LLM_PROVIDER", "inception"), model=os.environ.get("AGENCY_LLM_MODEL", "mercury-3"))


def _strip_fences(text: str) -> str:
    t = text.strip()
    if t.startswith("```"):
        t = t.split("\n", 1)[1] if "\n" in t else ""
        if t.rstrip().endswith("```"):
            t = t.rstrip()[:-3]
    elif t.rstrip().endswith("```"):
        t = t.rstrip()[:-3]
    return t.strip()


def _agency_generate(adapter: LLMAdapter, proof: ProofBundle) -> str:
    """Agency debug loop: request, strip fences, repair truncation, retry."""
    prompt = PROMPT
    for attempt in range(1, 4):
        try:
            raw = asyncio.run(adapter.generate(prompt))
        except Exception as exc:
            proof.append("agency_llm", "generate_error", {"attempt": attempt, "error": str(exc)[:120]})
            print(f"Agency LLM attempt {attempt} failed: {exc}; retrying shorter...")
            prompt = "Write a complete HTML landing page for The Agency (dark theme, hero + 3 features + roadmap). Return ONLY raw HTML from <!DOCTYPE html> to </html>, compact CSS, under 800 words."
            continue
        html = _strip_fences(raw)
        proof.append("agency_llm", "generate_html", {"attempt": attempt, "length": len(html)})
        print(f"Agency LLM attempt {attempt}: wrote {len(html)} bytes")
        if "<html" not in html.lower():
            prompt = PROMPT + "\n\nRETRY: return only raw HTML, start with <!DOCTYPE html>, end with </html>."
            continue
        if "</html>" not in html.lower():
            print(f"  truncated (missing </html>), retrying full compact...")
            prompt = PROMPT
            continue
        if "</html>" in html.lower():
            return html
        prompt = PROMPT
    return html


html = _agency_generate(adapter, proof)

# 2. Forge generates (writes files) + proves (inspect/scan/test/review)
spec = {"files": {"index.html": html, "README.md": "# The Agency Landing\n\nBuilt by the Agency via Forge.\n", "pyproject.toml": "[project]\nname='agency-landing'\nversion='0.1.0'\n"}}
report = generate_app(spec, out_root)
proof.append("forge", "generate", {"gate": report.gate, "files": len(report.files_written)})
print(f"Forge gate: {report.gate} files={report.files_written}")

# 3. Run/smoke
report2 = run_pipeline(out_root, list(report.files_written), run_tests_flag=False)
proof.append("forge", "pipeline", {"gate": report2.gate})
print(f"Pipeline gate: {report2.gate}")

smoke = smoke_app(out_root)
proof.append("forge", "smoke", {"gate": smoke.gate, "status": smoke.status_code})
print(f"Smoke: {smoke.gate} status={smoke.status_code}")

# 4. Proof
print(f"Proof events: {len(proof.events)} chain head={proof.head[:16]}...")
from agency.autonomy.proof import verify_chain
print(f"Chain verifies: {verify_chain(proof.events)}")
print(f"Output: {out_root / 'index.html'}")
if report.gate == "PASS" and smoke.gate == "PASS":
    print("LANDING PAGE BUILT BY AGENCY VIA FORGE: PASS (production-ready, no human code)")
else:
    print("FORGE GATE FAILED: page not shipped (fail-closed)")
