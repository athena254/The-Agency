"""Cold-import regressions: pytest collection order must not hide cycles."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest


@pytest.mark.parametrize(
    "module",
    ["agency.tools.driver", "agency.llm.metered", "agency.telegram.handler", "telegram_bot"],
)
def test_entrypoint_cold_import_in_fresh_interpreter(module):
    root = Path(__file__).resolve().parents[1]
    environment = dict(os.environ)
    environment.update(
        PYTHONPATH=str(root / "src"),
        AGENCY_LLM_PROVIDER="echo",
        AGENCY_LLM_MODEL="echo",
        PYTHON_DOTENV_DISABLED="1",
    )
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import importlib,sys; importlib.import_module(sys.argv[1])",
            module,
        ],
        cwd=root,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
        timeout=20,
    )
    assert result.returncode == 0, result.stderr
