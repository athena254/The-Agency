"""Job routing: spend the right amount of worker on the right job."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

JobClass = str  # trivial | standard | risky


@dataclass(frozen=True)
class JobRoute:
    job_class: JobClass
    role: str
    model: str
    tool_budget: int
    require_quorum: bool
    gates: tuple[str, ...]


_ROUTES = {
    "trivial": JobRoute(
        "trivial", "apprentice", "tencent/hy4-flash:free", 12, False, ("inspection", "tests")
    ),
    "standard": JobRoute(
        "standard",
        "striker",
        "tencent/hy4-flash:free",
        40,
        False,
        ("inspection", "security", "tests", "review"),
    ),
    "risky": JobRoute(
        "risky",
        "striker",
        "deepseek/deepseek-v4.1-flash-1017-free",
        60,
        True,
        ("inspection", "security", "tests", "review", "runtime"),
    ),
}

_RISKY = ("security", "auth", "payment", "permission", "migration", "deploy", "secret")
_TRIVIAL = ("docs", "typo", "readme", "rename", "comment", "formatting")

# Worker CLIs whose flag for pinned models is known. A route's model is pinned
# automatically ONLY for these command shapes; every other command must opt in
# with a ``{model}`` placeholder in its template. We never guess a flag for an
# unknown executable — that would be an unsafe change to an arbitrary command.
_MODEL_FLAG_COMMANDS: tuple[tuple[tuple[str, ...], str], ...] = (
    (("hermes", "chat"), "--model"),
    (("opencode",), "--model"),
)


def route_job(title: str, task: str = "", risk_hint: str = "") -> JobRoute:
    """Classify a job deterministically from words in its title and task."""
    text = f"{title} {task} {risk_hint}".lower()
    if any(word in text for word in _RISKY):
        return _ROUTES["risky"]
    if any(word in text for word in _TRIVIAL):
        return _ROUTES["trivial"]
    return _ROUTES["standard"]


def resolve_route(route: str, task: str = "") -> JobRoute:
    """Resolve the effective route from an explicit name, then fall back to text.

    An explicit, known route name (``trivial``/``standard``/``risky``) is
    authoritative; anything else is classified from the task text so callers
    that only supply prose still get a sensible route.
    """
    key = str(route or "").strip().lower()
    return _ROUTES[key] if key in _ROUTES else route_job("", task)


def apply_route_model(command: list[str], model: str) -> tuple[list[str], str]:
    """Pin ``model`` into a worker command and report how it was applied.

    Precedence:
      1. A ``{model}`` placeholder in any token is substituted in place
         (mechanism ``"placeholder"``).
      2. A known worker command (``hermes chat`` or ``opencode``) gets
         ``--model <model>`` appended (mechanism ``"flag"``), unless the
         command already pins a model (``"already-set"``).
      3. Any other command is left untouched (``"unsupported"``), and an empty
         model produces ``"none"``.

    The input list is never mutated. The mechanism label is safe to record as
    evidence — it describes *how* the model was applied, never a credential.
    """
    out = [str(part) for part in command]
    if not model:
        return out, "none"
    if any("{model}" in part for part in out):
        return [part.replace("{model}", model) for part in out], "placeholder"
    # Never clobber an explicit model already in the command (e.g. the
    # orchestrator's default ``opencode run ... --model X``).
    if any(part in ("--model", "-m") or part.startswith("--model=") for part in out):
        return out, "already-set"
    if not out:
        return out, "unsupported"
    # ``command[0]`` may already be a PATH-resolved absolute path with a .cmd
    # shim suffix; the stem keeps detection working on Windows and POSIX alike.
    executable = Path(out[0]).stem.lower()
    second = out[1].lower() if len(out) > 1 else ""
    for prefix, flag in _MODEL_FLAG_COMMANDS:
        if len(prefix) == 1 and executable == prefix[0]:
            return [*out, flag, model], "flag"
        if len(prefix) == 2 and executable == prefix[0] and second == prefix[1]:
            return [*out, flag, model], "flag"
    return out, "unsupported"
