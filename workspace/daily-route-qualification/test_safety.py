"""Synthetic harness safety regressions, never product qualification receipts."""

import ast
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

HARNESS = Path(__file__).resolve().parent
SPEC = importlib.util.spec_from_file_location(
    "safety_qualify_routes", HARNESS / "qualify_routes.py"
)
q = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(q)


class SafetyRegression(unittest.TestCase):
    def setUp(self):
        scratch = Path(
            os.environ.get(
                "AGENCY_HARNESS_SCRATCH",
                "C:/Users/alphi/AppData/Local/hermes/cache/scratch/daily-harness-worker-20261020/checks",
            )
        )
        scratch.mkdir(parents=True, exist_ok=True)
        self.directory = tempfile.TemporaryDirectory(prefix="route-safety-", dir=scratch)
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.source = self.root / "source"
        self.package = self.source / "src/agency"
        self.package.mkdir(parents=True)
        (self.package / "__init__.py").write_text(
            "import builtins\n"
            "builtins._safety_events.append('module-import')\n"
            "def create_app():\n"
            "    builtins._safety_events.append('constructor')\n"
            "create_app()\n"
            "raise RuntimeError('synthetic module reached')\n",
            encoding="utf-8",
        )

    def run_mismatch(self, *, stop_unexpected_import=False):
        code = textwrap.dedent("""
            import builtins, json, pathlib, sys
            import qualify_routes as q
            root = pathlib.Path(sys.argv[1])
            q.SCRATCH = root / 'runs'
            q.EVIDENCE = root / 'evidence'
            q.git_metadata = lambda source: {'root': str(source), 'head': 'fixture', 'branch': 'fixture', 'status_porcelain': ''}
            builtins._safety_events = []
            sensitive_opens = []
            def observe(event, args):
                if event == 'open' and isinstance(args[0], (str, bytes, q.os.PathLike)):
                    path = pathlib.Path(q.os.fsdecode(args[0])).absolute()
                    if path.is_relative_to(root / 'source') and q.sensitive_name(path.name):
                        sensitive_opens.append(path.name)
            sys.addaudithook(observe)
            if sys.argv[2] == 'stop':
                class StopCandidateImport:
                    def find_spec(self, fullname, path=None, target=None):
                        if fullname == 'agency' or fullname.startswith('agency.'):
                            builtins._safety_events.append('unexpected-agency-import')
                            raise RuntimeError('candidate import forbidden by regression')
                sys.meta_path.insert(0, StopCandidateImport())
            sys.argv = ['qualify_routes.py', '--source-root', str(root / 'source')]
            try:
                code = q.main()
                error = None
            except Exception as exc:
                code, error = None, type(exc).__name__
            receipts = [json.loads(p.read_text()) for p in q.EVIDENCE.glob('*/receipts.json')]
            print('SAFETY_RESULT=' + json.dumps({'exit': code, 'error': error, 'events': builtins._safety_events, 'sensitive_opens': sensitive_opens, 'receipts': receipts}))
        """)
        env = {
            k: v
            for k, v in os.environ.items()
            if k.upper() in {"SYSTEMROOT", "WINDIR", "PATH", "PATHEXT", "SYSTEMDRIVE", "COMSPEC"}
        }
        env.update(
            PYTHONPATH=os.pathsep.join(
                [str(HARNESS), *[p for p in sys.path if p.endswith("site-packages")]]
            ),
            PYTHON_DOTENV_DISABLED="1",
            PYTHONDONTWRITEBYTECODE="1",
        )
        child = subprocess.run(
            [
                sys.executable,
                "-B",
                "-c",
                code,
                str(self.root),
                "stop" if stop_unexpected_import else "sentinel",
            ],
            cwd=self.root,
            env=env,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        self.assertEqual(child.returncode, 0, child.stderr)
        rows = [
            line.removeprefix("SAFETY_RESULT=")
            for line in child.stdout.splitlines()
            if line.startswith("SAFETY_RESULT=")
        ]
        self.assertEqual(len(rows), 1, child.stdout)
        result = json.loads(rows[0])
        self.assertEqual(result["events"], [], "unreviewed module and constructor executed")
        self.assertEqual(
            result["sensitive_opens"], [], "main opened sensitive source before preflight"
        )
        self.assertEqual(result["exit"], 2, result)
        self.assertIsNone(result["error"], result)
        self.assertEqual(len(result["receipts"]), 1, result)
        receipt = result["receipts"][0]
        self.assertFalse(receipt["daily_driver_qualified"])
        self.assertFalse(receipt["execution"]["runtime_ready"])
        self.assertFalse(receipt["safety_preflight"]["approved"])
        self.assertEqual(receipt["cases"], [])
        return receipt

    def test_mismatch_stops_module_import_and_module_level_constructor(self):
        self.run_mismatch()

    def copy_reviewed_source(self):
        # Explicit baseline code/metadata paths, never recursively copy runtime data.
        import shutil

        checkout = HARNESS.parents[1]
        for relative in q.REVIEWED_SNAPSHOT["files"]:
            path = checkout / relative
            q.no_links(path)
            target = self.source / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(path, target)

    def test_sensitive_fingerprint_paths_have_zero_open_events(self):
        names = [
            ".env",
            ".env.local",
            ".envrc",
            ".env.py",
            "private.db",
            "private.sqlite3",
            "private.sqlite3-wal",
            "private.sqlite3-shm",
            "private.sqlite3-journal",
            "runtime.json",
        ]
        seen = []
        watching = False

        def observe(event, args):
            if watching and event == "open" and isinstance(args[0], (str, bytes, os.PathLike)):
                path = Path(os.fsdecode(args[0])).absolute()
                if path.is_relative_to(self.source):
                    seen.append(path.name)

        sys.addaudithook(observe)
        # Test each type alone: rejecting .env must not conceal a database read.
        for name in names:
            with self.subTest(name=name):
                fixture = self.package / name
                fixture.write_bytes(b"synthetic-only")
                seen.clear()
                watching = True
                try:
                    with self.assertRaises(ValueError):
                        q.source_snapshot(self.source)
                finally:
                    watching = False
                    fixture.unlink()
                self.assertEqual(
                    seen,
                    [],
                    "fingerprinting opened source before classifying sensitive/runtime files",
                )
        (self.package / "private.sqlite3").write_bytes(b"synthetic-only")
        self.run_mismatch()

    def test_pilot_gaps_force_exit_two_even_with_all_boundary_cases_passing(self):
        import inspect

        tree = ast.parse(inspect.getsource(q.qualify))
        final_return = tree.body[0].body[-1]
        self.assertIsInstance(final_return, ast.Return)
        for present in (False, True):
            with self.subTest(pilot_present=present):
                surfaces = (
                    [
                        {
                            "id": q.surface_id("prototype", method, path),
                            "app": "prototype",
                            "method": method,
                            "path": path,
                        }
                        for method, path in [*q.PLANNED_API, ("GET", "/pilot")]
                    ]
                    if present
                    else []
                )
                cases = [
                    {
                        "surface_ids": [s["id"]],
                        "actual_status": 503,
                        "passed": True,
                        "scope": "boundary-only",
                    }
                    for s in surfaces
                ]
                coverage = q.coverage_report(surfaces, cases)
                self.assertEqual(coverage["failed_cases"], 0)
                self.assertEqual(coverage["unexercised"], [])
                self.assertEqual(coverage["safe_handler_surface_count"], 0)
                planned = q.planned_coverage(surfaces)
                ns = dict(
                    vars(q),
                    result={"bounded_cases_passed": True},
                    inventory={"planned_routes": planned},
                    coverage=coverage,
                    skipped=[],
                    missing=[p for p in planned if p["status"] == "MISSING"],
                )
                expression = ast.Expression(body=final_return.value)
                code = eval(
                    compile(ast.fix_missing_locations(expression), "<actual-qualify-exit>", "eval"),
                    ns,
                )
                self.assertEqual(
                    code, 2, "present-unqualified pilot routes falsely allowed exit zero"
                )

    def test_reviewed_baseline_manifest_matches_without_importing_agency(self):
        self.copy_reviewed_source()
        self.assertEqual(q.source_snapshot(self.source), q.REVIEWED_SNAPSHOT)
        self.assertTrue(q.safety_preflight(self.source)["approved"])
        self.assertNotIn("agency", sys.modules)

    def test_directory_links_are_rejected_without_reading_targets(self):
        outside = self.root / "outside"
        outside.mkdir()
        (outside / "sentinel.py").write_text(
            "raise AssertionError('not executed')", encoding="utf-8"
        )
        link = self.package / "linked"
        try:
            link.symlink_to(outside, target_is_directory=True)
        except OSError:
            if os.name != "nt":
                raise
            created = subprocess.run(
                ["cmd.exe", "/c", "mklink", "/J", str(link), str(outside)],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(created.returncode, 0, created.stderr)
        try:
            with self.assertRaises(ValueError):
                q.source_snapshot(self.source)
        finally:
            if link.is_symlink():
                link.unlink()
            else:
                link.rmdir()

    def test_unchanged_factory_changed_helper_requires_review(self):
        self.copy_reviewed_source()
        app = self.package / "prototype/app.py"
        tree = ast.parse(app.read_text(encoding="utf-8"))
        helper = next(
            n
            for n in tree.body
            if isinstance(n, ast.AsyncFunctionDef) and n.name == "_collect_agents"
        )
        helper.body.insert(0, ast.parse("helper_sentinel = 'not-reviewed'").body[0])
        app.write_text(ast.unparse(tree), encoding="utf-8")
        self.assertTrue(
            q.reviewed_factory(self.source, *q.FACTORIES["prototype"])["reviewed_baseline_match"]
        )
        review = q.safety_preflight(self.source)
        self.assertFalse(review["approved"], "same factory AST hid a changed helper")
        self.assertEqual(review["changed"], ["src/agency/prototype/app.py"])
        self.run_mismatch(stop_unexpected_import=True)

    def test_unchanged_factory_changed_handler_constructor_requires_review(self):
        self.copy_reviewed_source()
        handler = self.package / "telegram/handler.py"
        tree = ast.parse(handler.read_text(encoding="utf-8"))
        cls = next(
            n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "TelegramHandler"
        )
        constructor = next(
            n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name == "__init__"
        )
        constructor.body.insert(
            0, ast.parse("raise AssertionError('unreviewed constructor executed')").body[0]
        )
        handler.write_text(ast.unparse(tree), encoding="utf-8")
        self.assertTrue(
            q.reviewed_factory(self.source, *q.FACTORIES["telegram"])["reviewed_baseline_match"]
        )
        review = q.safety_preflight(self.source)
        self.assertFalse(review["approved"], "same factory AST hid a changed handler dependency")
        self.assertEqual(review["changed"], ["src/agency/telegram/handler.py"])
        self.run_mismatch(stop_unexpected_import=True)


if __name__ == "__main__":
    unittest.main()
