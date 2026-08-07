"""Calibration tracking -- the accountability layer.

A probability model is only worth anything if, across all the times it said
"70%", the thing happened about 70% of the time. This module is what turns the
model from an opinion into something you can audit.

Read the numbers this way:

* **Brier score** -- mean squared error of the probability. Always compare it to
  the always-say-50% baseline of 0.25. Above 0.25 means you are worse than a
  coin, and that happens more often than people admit.
* **Brier skill score** -- ``1 - brier/0.25``. Positive means better than always
  saying 50%. On a 15-minute horizon, a genuinely good model scores a few
  percent. If you see 0.30, look for a bug before celebrating.
* **Reliability table** -- the honest picture. Bucket predictions and compare
  predicted to actual. A model can have a decent Brier score and still be badly
  miscalibrated at the extremes, which is exactly where you size up.

Predictions must be recorded **before** the outcome is known. That is the whole
point, and it is why the live engine writes them as they are made.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from pathlib import Path

__all__ = ["Prediction", "CalibrationTracker"]


@dataclass(frozen=True)
class Prediction:
    asset: str
    venue: str
    window_label: str
    prob_up: float
    outcome_up: bool | None = None
    market_prob: float | None = None

    @property
    def is_resolved(self) -> bool:
        return self.outcome_up is not None


@dataclass
class CalibrationTracker:
    """Accumulates predictions and scores them once outcomes land."""

    predictions: list[Prediction] = field(default_factory=list)
    n_bins: int = 10

    def record(
        self, asset: str, venue: str, window_label: str, prob_up: float,
        market_prob: float | None = None,
    ) -> None:
        self.predictions.append(
            Prediction(asset, venue, window_label, prob_up, None, market_prob)
        )

    def resolve(self, window_label: str, outcome_up: bool, asset: str | None = None) -> int:
        """Attach an outcome to every matching open prediction. Returns count."""
        resolved = 0
        for i, p in enumerate(self.predictions):
            if p.is_resolved or p.window_label != window_label:
                continue
            if asset is not None and p.asset != asset:
                continue
            self.predictions[i] = Prediction(
                p.asset, p.venue, p.window_label, p.prob_up, outcome_up, p.market_prob
            )
            resolved += 1
        return resolved

    @property
    def resolved(self) -> list[Prediction]:
        return [p for p in self.predictions if p.is_resolved]

    def brier(self) -> float | None:
        rows = self.resolved
        if not rows:
            return None
        return sum((p.prob_up - (1.0 if p.outcome_up else 0.0)) ** 2 for p in rows) / len(rows)

    def brier_skill(self) -> float | None:
        """Versus always saying 50%. Positive is better than a coin flip."""
        brier = self.brier()
        return None if brier is None else 1.0 - brier / 0.25

    def log_loss(self) -> float | None:
        rows = self.resolved
        if not rows:
            return None
        total = 0.0
        for p in rows:
            q = min(max(p.prob_up, 1e-6), 1 - 1e-6)
            total += -(math.log(q) if p.outcome_up else math.log(1 - q))
        return total / len(rows)

    def accuracy(self) -> float | None:
        """Directional hit rate, counting only calls off the fence."""
        rows = [p for p in self.resolved if p.prob_up != 0.5]
        if not rows:
            return None
        hits = sum(1 for p in rows if (p.prob_up > 0.5) == p.outcome_up)
        return hits / len(rows)

    def beats_market(self) -> float | None:
        """Brier improvement over the market price, where one was recorded.

        This is the only comparison that matters. Beating 50% is easy; beating
        the market's own price is the actual bar, and most models do not.
        """
        rows = [p for p in self.resolved if p.market_prob is not None]
        if not rows:
            return None
        ours = sum((p.prob_up - (1.0 if p.outcome_up else 0.0)) ** 2 for p in rows) / len(rows)
        theirs = sum(
            (p.market_prob - (1.0 if p.outcome_up else 0.0)) ** 2 for p in rows
        ) / len(rows)
        return theirs - ours  # positive means we are better

    def reliability(self) -> list[dict[str, float]]:
        """Predicted-vs-actual by probability bucket."""
        rows = self.resolved
        buckets: list[list[Prediction]] = [[] for _ in range(self.n_bins)]
        for p in rows:
            idx = min(int(p.prob_up * self.n_bins), self.n_bins - 1)
            buckets[idx].append(p)
        table = []
        for i, bucket in enumerate(buckets):
            if not bucket:
                continue
            table.append({
                "bin_low": i / self.n_bins,
                "bin_high": (i + 1) / self.n_bins,
                "n": float(len(bucket)),
                "predicted": sum(p.prob_up for p in bucket) / len(bucket),
                "actual": sum(1.0 for p in bucket if p.outcome_up) / len(bucket),
            })
        return table

    def report(self) -> dict[str, object]:
        return {
            "n_predictions": len(self.predictions),
            "n_resolved": len(self.resolved),
            "brier": self.brier(),
            "brier_skill_vs_coinflip": self.brier_skill(),
            "log_loss": self.log_loss(),
            "directional_accuracy": self.accuracy(),
            "brier_gain_vs_market": self.beats_market(),
            "reliability": self.reliability(),
        }

    def format_report(self) -> str:
        r = self.report()
        if not r["n_resolved"]:
            return f"{r['n_predictions']} predictions recorded, none resolved yet."
        lines = [
            f"resolved            : {r['n_resolved']}",
            f"brier               : {r['brier']:.4f}  (0.25 = always saying 50%)",
            f"brier skill         : {r['brier_skill_vs_coinflip']:+.4f}",
            f"log loss            : {r['log_loss']:.4f}",
            f"directional accuracy: {r['directional_accuracy']:.1%}"
            if r["directional_accuracy"] is not None else "directional accuracy: n/a",
        ]
        gain = r["brier_gain_vs_market"]
        if gain is not None:
            verdict = "BETTER than the market" if gain > 0 else "worse than the market"
            lines.append(f"vs market price     : {gain:+.4f} brier -- {verdict}")
        else:
            lines.append("vs market price     : no market prices recorded")
        if r["reliability"]:
            lines.append("")
            lines.append("  bucket      n   predicted   actual")
            for row in r["reliability"]:
                lines.append(
                    f"  {row['bin_low']:.1f}-{row['bin_high']:.1f} {int(row['n']):6d}"
                    f"   {row['predicted']:8.1%} {row['actual']:8.1%}"
                )
        return "\n".join(lines)

    def save(self, path: str | Path) -> None:
        Path(path).write_text(json.dumps([
            {
                "asset": p.asset, "venue": p.venue, "window": p.window_label,
                "prob_up": p.prob_up, "outcome_up": p.outcome_up,
                "market_prob": p.market_prob,
            }
            for p in self.predictions
        ], indent=2))

    @classmethod
    def load(cls, path: str | Path) -> CalibrationTracker:
        tracker = cls()
        for row in json.loads(Path(path).read_text()):
            tracker.predictions.append(Prediction(
                row["asset"], row["venue"], row["window"], row["prob_up"],
                row.get("outcome_up"), row.get("market_prob"),
            ))
        return tracker
