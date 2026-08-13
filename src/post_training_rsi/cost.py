from __future__ import annotations

from dataclasses import dataclass, field


class BudgetExceeded(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class CostEntry:
    stage: str
    amount_usd: float
    iteration: int
    description: str = ""


@dataclass(slots=True)
class BudgetLedger:
    total_limit_usd: float
    per_trial_limit_usd: float
    entries: list[CostEntry] = field(default_factory=list)

    @property
    def total_spent_usd(self) -> float:
        return round(sum(entry.amount_usd for entry in self.entries), 8)

    def trial_spent_usd(self, iteration: int) -> float:
        return round(
            sum(entry.amount_usd for entry in self.entries if entry.iteration == iteration), 8
        )

    def charge(
        self,
        *,
        stage: str,
        amount_usd: float,
        iteration: int,
        description: str = "",
    ) -> CostEntry:
        if amount_usd < 0:
            raise ValueError("amount_usd must not be negative")
        projected_total = self.total_spent_usd + amount_usd
        projected_trial = self.trial_spent_usd(iteration) + amount_usd
        if projected_total > self.total_limit_usd + 1e-9:
            raise BudgetExceeded(
                f"total budget exceeded: ${projected_total:.4f} > ${self.total_limit_usd:.4f}"
            )
        if projected_trial > self.per_trial_limit_usd + 1e-9:
            raise BudgetExceeded(
                f"trial {iteration} budget exceeded: "
                f"${projected_trial:.4f} > ${self.per_trial_limit_usd:.4f}"
            )
        entry = CostEntry(stage, round(amount_usd, 8), iteration, description)
        self.entries.append(entry)
        return entry

    def to_dict(self) -> dict[str, object]:
        return {
            "total_limit_usd": self.total_limit_usd,
            "per_trial_limit_usd": self.per_trial_limit_usd,
            "total_spent_usd": self.total_spent_usd,
            "entries": [
                {
                    "stage": entry.stage,
                    "amount_usd": entry.amount_usd,
                    "iteration": entry.iteration,
                    "description": entry.description,
                }
                for entry in self.entries
            ],
        }
