from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .config import BudgetConfig


class BudgetExceeded(RuntimeError):
    """Raised before a charge would cross a hard budget boundary."""


class APICircuitOpen(RuntimeError):
    """Raised after too many consecutive provider failures."""


@dataclass(slots=True)
class CostLedger:
    config: BudgetConfig
    total_charged_usd: float = 0.0
    charges_by_iteration: dict[int, float] = field(default_factory=dict)
    consecutive_api_failures: int = 0
    events: list[dict[str, Any]] = field(default_factory=list)

    def charge(self, amount_usd: float, *, iteration: int, category: str) -> None:
        if amount_usd < 0:
            raise ValueError("amount_usd cannot be negative")
        current_iteration = self.charges_by_iteration.get(iteration, 0.0)
        next_iteration = current_iteration + amount_usd
        next_total = self.total_charged_usd + amount_usd
        if next_iteration > self.config.per_iteration_limit_usd + 1e-12:
            raise BudgetExceeded(
                f"iteration {iteration} would spend ${next_iteration:.4f}; "
                f"limit is ${self.config.per_iteration_limit_usd:.4f}"
            )
        if next_total > self.config.total_limit_usd + 1e-12:
            raise BudgetExceeded(
                f"run would spend ${next_total:.4f}; "
                f"limit is ${self.config.total_limit_usd:.4f}"
            )
        self.charges_by_iteration[iteration] = next_iteration
        self.total_charged_usd = next_total
        self.events.append(
            {
                "iteration": iteration,
                "category": category,
                "amount_usd": round(amount_usd, 8),
                "iteration_total_usd": round(next_iteration, 8),
                "run_total_usd": round(next_total, 8),
            }
        )

    def record_api_failure(self, provider: str, error: str) -> None:
        self.consecutive_api_failures += 1
        self.events.append(
            {
                "category": "api_failure",
                "provider": provider,
                "error": error,
                "consecutive_failures": self.consecutive_api_failures,
            }
        )
        if self.consecutive_api_failures >= self.config.max_consecutive_api_failures:
            raise APICircuitOpen(
                f"provider circuit opened after {self.consecutive_api_failures} failures"
            )

    def record_api_success(self) -> None:
        self.consecutive_api_failures = 0

    def iteration_total(self, iteration: int) -> float:
        return self.charges_by_iteration.get(iteration, 0.0)

    def snapshot(self) -> dict[str, Any]:
        return {
            "total_charged_usd": round(self.total_charged_usd, 8),
            "charges_by_iteration": {
                str(key): round(value, 8) for key, value in self.charges_by_iteration.items()
            },
            "consecutive_api_failures": self.consecutive_api_failures,
            "events": list(self.events),
        }
