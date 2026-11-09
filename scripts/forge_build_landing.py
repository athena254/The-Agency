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

PROMPT = """Write a complete single-file landing page (full HTML with inline <style>, dark modern theme, no external assets, no JS frameworks) introducing The Agency: governed multi-agent execution system, decentralised signed quorum (3-of-4), hash-chained proof trace, autonomous Forge factory that builds tools/apps itself, roadmap, community. Return ONLY the HTML code, starting with <!DOCTYPE html>."""

out_root = Path(__file__).resolve().parents[1] / "agency-landing"
proof = ProofBundle()

# 1. Agency LLM generates the HTML
from agency.llm.adapter import LLMAdapter

adapter = LLMAdapter(provider=os.environ.get("AGENCY_LLM_PROVIDER", "inception"), model=os.environ.get("AGENCY_LLM_MODEL", "mercury-3"))
html = asyncio.run(adapter.generate(PROMPT))
proof.append("agency_llm", "generate_html", {"length": len(html)})
print(f"Agency LLM wrote {len(html)} bytes of HTML")

if "<html" not in html.lower():
    raise SystemExit("agency did not return HTML; fail-closed")

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
