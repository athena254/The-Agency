"""Harness-only regressions; synthetic routes are NOT product receipts."""

import importlib.util
import unittest
from pathlib import Path


class InventoryRegression(unittest.TestCase):
    def test_lazy_hidden_docs_and_method_union(self):
        path = Path(__file__).with_name("qualify_routes.py")
        self.assertTrue(path.is_file(), "reusable inventory harness has not been implemented")
        spec = importlib.util.spec_from_file_location("qualify_routes", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        from fastapi import APIRouter, FastAPI

        app = FastAPI()
        inner = APIRouter()

        @inner.get("/item")
        @inner.post("/item")
        async def item():
            return {"fixture": True}

        app.include_router(inner, prefix="/nested")

        @app.get("/hidden", include_in_schema=False)
        async def hidden():
            return {"fixture": True}

        inventory = module.inventory_app("fixture", app)
        keys = {(r["method"], r["path"]) for r in inventory["surfaces"]}
        self.assertTrue(
            {
                ("GET", "/nested/item"),
                ("POST", "/nested/item"),
                ("GET", "/hidden"),
                ("GET", "/docs"),
                ("HEAD", "/docs"),
                ("GET", "/openapi.json"),
            }
            <= keys
        )
        self.assertEqual(len(keys), len(inventory["surfaces"]))
        self.assertEqual(inventory["openapi_only"], [])
        self.assertEqual(inventory["route_only_schema_expected"], [])

    def test_head_denial_has_no_json_body(self):
        path = Path(__file__).with_name("qualify_routes.py")
        spec = importlib.util.spec_from_file_location("qualify_routes", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        import httpx

        response = httpx.Response(503, request=httpx.Request("HEAD", "http://127.0.0.1/docs"))
        self.assertTrue(module.peer_denial_matches(response))
        response = httpx.Response(
            503,
            json={"detail": "Peer authorization is unavailable."},
            request=httpx.Request("GET", "http://127.0.0.1/docs"),
        )
        self.assertTrue(module.peer_denial_matches(response))

    def test_missing_pilot_is_not_a_passing_404(self):
        path = Path(__file__).with_name("qualify_routes.py")
        spec = importlib.util.spec_from_file_location("qualify_routes", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        planned = module.planned_coverage([])
        self.assertEqual(len(planned), 9)
        self.assertTrue(all(p["status"] == "MISSING" for p in planned))
        surfaces = [
            {
                "id": "prototype GET /api/pilot/tasks",
                "app": "prototype",
                "method": "GET",
                "path": "/api/pilot/tasks",
            }
        ]
        planned = module.planned_coverage(surfaces)
        self.assertEqual(sum(p["status"] == "MISSING" for p in planned), 8)
        coverage = module.coverage_report(surfaces, [])
        self.assertEqual(coverage["passed_cases"], 0)
        self.assertEqual(coverage["unexercised"], [surfaces[0]["id"]])
        failed = [
            {
                "surface_ids": [surfaces[0]["id"]],
                "actual_status": 503,
                "passed": False,
                "scope": "boundary-only",
            }
        ]
        coverage = module.coverage_report(surfaces, failed)
        self.assertEqual(coverage["executed_surface_count"], 1)
        self.assertEqual(coverage["passed_case_surface_count"], 0)
        self.assertEqual(coverage["safe_handler_surface_count"], 0)

    def test_static_inventory_expands_exact_files_and_mount(self):
        path = Path(__file__).with_name("qualify_routes.py")
        spec = importlib.util.spec_from_file_location("qualify_routes", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        import tempfile

        from fastapi import FastAPI
        from starlette.staticfiles import StaticFiles

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "fixture.js").write_bytes(b"// synthetic fixture")
            app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
            app.mount("/assets", StaticFiles(directory=root), name="assets")
            inventory = module.inventory_app("fixture", app)
        surfaces = inventory["surfaces"]
        self.assertEqual(
            {(r["method"], r["path"]) for r in surfaces},
            {
                ("*", "/assets/{path:path}"),
                ("GET", "/assets/fixture.js"),
                ("HEAD", "/assets/fixture.js"),
            },
        )
        self.assertEqual(len(surfaces), 3)
        mount = next(r for r in surfaces if r["method"] == "*")
        self.assertEqual(mount["handler_methods"], ["GET", "HEAD"])

    def test_guard_denies_egress_process_and_nonrun_io(self):
        import subprocess
        import sys
        import textwrap

        code = textwrap.dedent("""
            import asyncio, json, pathlib, socket, sqlite3, subprocess, tempfile
            import qualify_routes as q
            run = pathlib.Path(tempfile.mkdtemp(prefix="guard-", dir=q.SCRATCH))
            output = run / "evidence"
            output.mkdir()
            q.configure_environment(run)
            async def check():
                denied = q.install_guard(run, output)
                sock = socket.socket()
                operations = [
                    lambda: sock.bind(("127.0.0.1", 0)),
                    lambda: sock.connect(("127.0.0.1", 9)),
                    lambda: socket.getaddrinfo("synthetic.invalid", 443),
                    lambda: subprocess.Popen(["must-not-execute-qualification-sentinel"]),
                    lambda: (run / ".env").read_bytes(),
                    lambda: sqlite3.connect(str(q.SCRATCH / "outside-guard-sentinel.db")),
                    lambda: (q.SCRATCH / "outside-guard-sentinel.txt").write_text("forbidden"),
                ]
                for operation in operations:
                    try:
                        operation()
                    except PermissionError:
                        continue
                    raise AssertionError("guard did not reject forbidden operation")
                sock.close()
                with tempfile.NamedTemporaryFile() as handle:
                    assert pathlib.Path(handle.name).is_relative_to(run / "temp")
                assert len(denied) == len(operations)
                print(json.dumps({"denied_operations": len(denied), "temp_in_run": True}))
            asyncio.run(check())
        """)
        import os

        env = dict(os.environ)
        env["PYTHONPATH"] = str(Path(__file__).resolve().parent)
        result = subprocess.run(
            [sys.executable, "-B", "-c", code],
            env=env,
            text=True,
            capture_output=True,
            timeout=30,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('"denied_operations": 7', result.stdout)
        self.assertIn('"temp_in_run": true', result.stdout)


if __name__ == "__main__":
    # Keep even harness-only fixtures and tempfile allocations in approved scratch.
    import os
    import tempfile

    root = Path(
        "C:/Users/alphi/AppData/Local/hermes/cache/scratch/agency-build-20261019/daily-routes/selfcheck"
    )
    root.mkdir(parents=True, exist_ok=True)
    os.chdir(root)
    for key in ("TEMP", "TMP", "TMPDIR"):
        os.environ[key] = str(root)
    tempfile.tempdir = str(root)
    unittest.main()
