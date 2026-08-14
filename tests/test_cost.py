from __future__ import annotations

import pytest

from post_training_rsi.config import BudgetConfig
from post_training_rsi.cost import APICircuitOpen, BudgetExceeded, CostLedger


def test_cost_ledger_enforces_iteration_limit() -> None:
    ledger = CostLedger(BudgetConfig(total_limit_usd=10.0, per_iteration_limit_usd=2.0))
    ledger.charge(1.5, iteration=1, category="generation")
    with pytest.raises(BudgetExceeded):
        ledger.charge(0.6, iteration=1, category="evaluation")


def test_api_failures_open_the_circuit() -> None:
    ledger = CostLedger(
        BudgetConfig(
            total_limit_usd=10.0,
            per_iteration_limit_usd=2.0,
            max_consecutive_api_failures=2,
        )
    )
    ledger.record_api_failure("fixture", "first")
    with pytest.raises(APICircuitOpen):
        ledger.record_api_failure("fixture", "second")
