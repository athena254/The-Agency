"""Bounded offline ledger validation, durability, and atomic admission tests."""

import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal

import pytest
from pydantic import ValidationError

from agency.admission.spending import SpendingConfig, SpendingError, SpendingLedger


def config(**changes):
    return SpendingConfig(
        **dict(
            {
                "limit_usd": "1",
                "input_usd_per_million": "1",
                "output_usd_per_million": "2",
                "pricing_source": "offline synthetic rates",
            },
            **changes,
        )
    )


@pytest.mark.parametrize(
    "field,value",
    [
        ("limit_usd", True),
        ("limit_usd", 1),
        ("limit_usd", "-1"),
        ("limit_usd", "NaN"),
        ("limit_usd", "Infinity"),
        ("limit_usd", "1e3"),
        ("limit_usd", " 1"),
        ("input_usd_per_million", "0"),
        ("output_usd_per_million", "-1"),
        ("pricing_source", ""),
        ("pricing_source", "  "),
    ],
)
def test_strict_config_rejects_non_decimal_or_nonpositive_rates(field, value):
    with pytest.raises(ValidationError):
        config(**{field: value})


def test_atomic_cap_across_independent_connections(tmp_path):
    cfg = config(limit_usd="0.000003")
    ledgers = [SpendingLedger(tmp_path / "spend.db", cfg, identity="owner") for _ in range(8)]

    def reserve(pair):
        n, ledger = pair
        try:
            ledger.reserve(str(n), 1, 1)
            return True
        except SpendingError:
            return False

    with ThreadPoolExecutor(max_workers=4) as pool:
        assert sum(pool.map(reserve, enumerate(ledgers))) == 1
    snap = ledgers[0].snapshot()
    assert snap["unresolved_calls"] == 1
    assert Decimal(snap["reserved_usd"]) == Decimal("0.000003")


def test_restart_refuses_changed_ceiling_rates_provenance_or_identity(tmp_path):
    path = tmp_path / "spend.db"
    ledger = SpendingLedger(path, config(), identity="owner")
    ledger.reserve("call", 10, 4)
    before = ledger.snapshot()
    for changes in [
        {"limit_usd": "2"},
        {"input_usd_per_million": "2"},
        {"output_usd_per_million": "3"},
        {"pricing_source": "other"},
    ]:
        with pytest.raises(SpendingError):
            SpendingLedger(path, config(**changes), identity="owner")
    with pytest.raises(SpendingError):
        SpendingLedger(path, config(), identity="other")
    assert SpendingLedger(path, config(), identity="owner").snapshot() == before


@pytest.mark.parametrize(
    "usage",
    [
        None,
        {},
        {"prompt_tokens": True, "completion_tokens": 1},
        {"prompt_tokens": -1, "completion_tokens": 1},
        {"prompt_tokens": 1.0, "completion_tokens": 1},
        {"prompt_tokens": 1, "completion_tokens": False},
        {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": True},
        {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 3},
        {"prompt_tokens": 11, "completion_tokens": 1},
        {"prompt_tokens": 1, "completion_tokens": 5},
    ],
)
def test_unknown_or_malformed_usage_never_refunds(tmp_path, usage):
    ledger = SpendingLedger(tmp_path / "spend.db", config(), identity="owner")
    ledger.reserve("call", 10, 4)
    before = ledger.snapshot()
    with pytest.raises(SpendingError):
        ledger.settle("call", usage)
    assert ledger.snapshot() == before


def test_settlement_is_exact_once_and_survives_new_process(tmp_path):
    path = tmp_path / "spend.db"
    ledger = SpendingLedger(path, config(), identity="owner")
    ledger.reserve("call", 100, 10)
    usage = {"prompt_tokens": 3, "completion_tokens": 2, "total_tokens": 5}
    ledger.settle("call", usage)
    ledger.settle("call", usage)
    with pytest.raises(SpendingError):
        ledger.settle("call", {"prompt_tokens": 4, "completion_tokens": 2})
    before = ledger.snapshot()
    assert Decimal(before["reported_cost_usd"]) == Decimal("0.000007")
    script = (
        "from agency.admission.spending import SpendingConfig,SpendingLedger; import json,sys; "
        'c=SpendingConfig(limit_usd="1",input_usd_per_million="1",output_usd_per_million="2",pricing_source="offline synthetic rates"); '
        'print(json.dumps(SpendingLedger(sys.argv[1],c,identity="owner").snapshot()))'
    )
    result = subprocess.run(
        [sys.executable, "-c", script, str(path)],
        check=False,
        env=os.environ.copy(),
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    import json

    assert json.loads(result.stdout) == before


def test_launcher_accepts_explicit_spending_config():
    from pydantic import SecretStr

    from agency.prototype.pilot_launcher import _Config

    value = _Config(
        owner_id="owner",
        subject_id="subject",
        provider="openai",
        model="synthetic",
        base_url="https://provider.invalid/v1",
        api_key=SecretStr("SYNTHETIC"),
        spending=config().model_dump(),
    )
    assert value.spending == config()


def test_launcher_cannot_disable_preexisting_spend_history(tmp_path, monkeypatch):
    import json

    from agency.prototype import pilot_launcher as launcher

    monkeypatch.setattr(launcher, "_paths", lambda *a, **kw: None)
    path = tmp_path / "owner.json"
    path.write_text(
        json.dumps(
            {
                "owner_id": "owner",
                "subject_id": "subject",
                "provider": "openai",
                "model": "synthetic",
                "base_url": "https://provider.invalid/v1",
                "api_key": "SYNTHETIC",
            }
        )
    )
    SpendingLedger(tmp_path / "spend.db", config(), identity="owner")
    with pytest.raises(launcher.LauncherError):
        launcher.validate(path, tmp_path)
