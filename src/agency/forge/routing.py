"""Job routing: spend the right amount of worker on the right job."""

from __future__ import annotations

from dataclasses import dataclass

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


def route_job(title: str, task: str = "", risk_hint: str = "") -> JobRoute:
    """Classify a job deterministically from words in its title and task."""
    text = f"{title} {task} {risk_hint}".lower()
    if any(word in text for word in _RISKY):
        return _ROUTES["risky"]
    if any(word in text for word in _TRIVIAL):
        return _ROUTES["trivial"]
    return _ROUTES["standard"]
