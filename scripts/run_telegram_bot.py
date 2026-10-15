"""Run the Telegram bot from this canonical checkout (main).

This is the single supported start command for the bot. It replaces the previous
pattern of several throwaway launchers in %TEMP% that pinned separate deployment
checkouts which no longer exist.

Guarantees before starting:

* the ``src`` and ``tests`` git tree ids match the verified free-program revision,
  so documentation-only commits on main cannot invalidate the pin while any real
  source change refuses to start;
* the working tree has no local edits under ``src`` or ``tests``;
* the private ``.env`` supplies ``TELEGRAM_BOT_TOKEN``;
* non-beta runtime settings are preserved exactly as the previous deployment used
  them: ``AGENCY_BETA_MODE=0`` and no ``TELEGRAM_WEBHOOK_URL``;
* the process working directory is this checkout, so the same runtime databases
  are used.

Use ``--check`` to run the preflight and exit without starting the bot.
"""

from __future__ import annotations

import os
import runpy
import subprocess
import sys
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]

# Verified trees at 59e9bc9: 1297 Python tests, 14 UI tests, Ruff and mypy.
# See docs/research-evidence/late-review-verification.json for scope/warnings.
VERIFIED_SRC_TREE = "6a87df6a15a876e5b2443fd3b1eda6d975e5c5b3"
VERIFIED_TESTS_TREE = "3342f6e32fa7372dcacc7c8b7a473b0551f529bc"


def git(*args: str) -> str:
    """Return stripped stdout of ``git -C ROOT <args>``."""
    return subprocess.check_output(["git", "-C", str(ROOT), *args], text=True).strip()


def verify() -> None:
    """Refuse to start unless this checkout is the verified revision and is clean."""
    src_tree = git("rev-parse", "HEAD:src")
    tests_tree = git("rev-parse", "HEAD:tests")

    if src_tree != VERIFIED_SRC_TREE:
        raise RuntimeError(f"src tree {src_tree} does not match the verified revision")
    if tests_tree != VERIFIED_TESTS_TREE:
        raise RuntimeError(f"tests tree {tests_tree} does not match the verified revision")
    if git("status", "--porcelain", "--", "src", "tests"):
        raise RuntimeError("checkout has local edits under src/ or tests/; refusing to start")


def main() -> None:
    """Run the preflight, then execute the bot from this checkout."""
    verify()
    load_dotenv(ROOT / ".env", override=True)
    if not os.environ.get("TELEGRAM_BOT_TOKEN"):
        raise RuntimeError("TELEGRAM_BOT_TOKEN is not available; refusing to start")

    revision = git("rev-parse", "HEAD")
    if "--check" in sys.argv:
        print(
            f"preflight ok: revision={revision} src_tree_verified=True "
            f"tests_tree_verified=True local_src_edits=False"
        )
        return

    os.environ["AGENCY_BETA_MODE"] = "0"
    os.environ.pop("TELEGRAM_WEBHOOK_URL", None)
    os.chdir(ROOT)
    print(f"agency_deployment_revision={revision}", flush=True)
    print(f"agency_deployment_source={ROOT / 'src' / 'telegram_bot.py'}", flush=True)
    runpy.run_path(str(ROOT / "src" / "telegram_bot.py"), run_name="__main__")


if __name__ == "__main__":
    main()
