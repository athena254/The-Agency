"""Golden E2E: prompt -> scaffold -> prove -> run -> ship (dry-run).

Builds 3 real apps via Forge, verifies each runs (smoke + tests).
"""

from __future__ import annotations

from pathlib import Path

from agency.forge.generate import generate_app
from agency.forge.pipeline import run_pipeline
from agency.forge.smoke import smoke_app


def test_golden_app_1_minimal_fastapi(tmp_path: Path):
    result = generate_app("Hello API", tmp_path)
    assert result.gate == "PASS", result.detail
    smoke = smoke_app(tmp_path)
    assert smoke.gate == "PASS", smoke


def test_golden_app_2_calculator_service(tmp_path: Path):
    spec = {
        "files": {
            "app.py": "from fastapi import FastAPI\napp = FastAPI()\n@app.get('/health')\ndef health():\n    return {'ok': True}\n@app.get('/add')\ndef add(a: int, b: int):\n    return {'result': a + b}\n",
            "test_app.py": "from fastapi.testclient import TestClient\nfrom app import app\ndef test_add():\n    c = TestClient(app)\n    assert c.get('/add', params={'a': 2, 'b': 3}).json() == {'result': 5}\ndef test_health():\n    c = TestClient(app)\n    assert c.get('/health').json() == {'ok': True}\n",
            "pyproject.toml": "[project]\nname='calc'\nversion='0.1.0'\n",
            "README.md": "# Calc\n",
        }
    }
    result = generate_app(spec, tmp_path)
    assert result.gate == "PASS", result


def test_golden_app_3_static_site(tmp_path: Path):
    (tmp_path / "index.html").write_text(
        "<html><body><h1>Forge Site</h1></body></html>\n", encoding="utf-8"
    )
    (tmp_path / "pyproject.toml").write_text(
        "[project]\nname='x'\nversion='0.1.0'\n", encoding="utf-8"
    )
    (tmp_path / "test_site.py").write_text(
        "def test_placeholder():\n    assert True\n", encoding="utf-8"
    )
    (tmp_path / "README.md").write_text("# Site\n", encoding="utf-8")
    report = run_pipeline(tmp_path, ["index.html", "test_site.py"], run_tests_flag=True)
    assert report.gate == "PASS", (report.inspection, report.security)
    smoke = smoke_app(tmp_path)
    assert smoke.gate == "PASS", smoke
