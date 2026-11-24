"""Platform-neutral absolute path used by Forge tests as an opaque repo id."""

from pathlib import Path

FAKE_REPO = str(Path(__file__).resolve().parent / "fake-repo")
