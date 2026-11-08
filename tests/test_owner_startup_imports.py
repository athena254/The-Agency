"""Owner persistence must not eagerly construct the unrelated server/runtime."""

import json
import os
import subprocess
import sys


def test_store_import_does_not_load_server_or_execution_runtime(tmp_path):
    code = """import json, sys
from agency.admission.store import OwnerPilotStore
print(json.dumps({name: name in sys.modules for name in (
    'agency.butler.server', 'agency.orchestrator', 'agency.agents.executor')}))
"""
    result = subprocess.run(
        [sys.executable, "-c", code],
        check=False,
        cwd=tmp_path,
        env=os.environ.copy(),
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, result.stderr
    modules = json.loads(result.stdout)
    assert not any(modules.values()), modules


def test_lazy_package_exports_preserve_identity():
    from agency import agents, butler
    from agency.agents.executor import AgentExecutor
    from agency.butler.service import ButlerService

    assert agents.AgentExecutor is AgentExecutor
    assert butler.ButlerService is ButlerService
    assert agents.AgentExecutor is agents.AgentExecutor
    assert butler.ButlerService is butler.ButlerService
    for package in (agents, butler):
        assert set(package.__all__).issubset(dir(package))
        for name in package.__all__:
            assert getattr(package, name) is not None
        try:
            _ = package.not_a_public_export
        except AttributeError:
            pass
        else:
            raise AssertionError("Unknown attributes must fail normally")
