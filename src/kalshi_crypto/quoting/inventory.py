"""Inventory management for the maker engine.

Two jobs: skew quotes so the book leans toward flattening, and refuse to add
risk past a hard limit. Both are pure functions of state so they can be tested
without any exchange connection.
"""

from __future__ import annotations

from dataclasses import dataclass, field

__all__ = ["InventoryState", "InventoryPolicy"]


@dataclass
class InventoryState:
    position: int = 0
    fills_buy: int = 0
    fills_sell: int = 0
    # P&L on fills only, used to detect adverse selection.
    fill_marks: list[tuple[int, float, float]] = field(default_factory=list)

    def on_fill(self, *, is_buy: bool, size: int, price: float, fair_at_fill: float) -> None:
        self.position += size if is_buy else -size
        if is_buy:
            self.fills_buy += size
        else:
            self.fills_sell += size
        # Positive edge means we bought below fair or sold above it.
        edge = (fair_at_fill - price) if is_buy else (price - fair_at_fill)
        self.fill_marks.append((size, price, edge))

    @property
    def mean_fill_edge(self) -> float | None:
        """Average edge at the moment of fill, size-weighted.

        This is the adverse-selection number from spec section 4.2. If it is
        negative, fills are arriving when the market has already moved against
        the quote -- the strategy is losing even when the spread math looks
        fine. Track it separately from unconditional P&L.
        """
        total_size = sum(size for size, _, _ in self.fill_marks)
        if total_size == 0:
            return None
        return sum(size * edge for size, _, edge in self.fill_marks) / total_size


@dataclass(frozen=True)
class InventoryPolicy:
    max_position: int = 500
    flatten_threshold: int = 400
    skew_cents_per_max: float = 4.0

    def skew_cents(self, position: int) -> float:
        """Price shift in cents to apply against the current inventory.

        Long inventory shifts quotes down (making us more eager to sell).
        """
        if self.max_position <= 0:
            return 0.0
        return -self.skew_cents_per_max * position / self.max_position

    def may_buy(self, position: int, size: int) -> bool:
        return position + size <= self.max_position

    def may_sell(self, position: int, size: int) -> bool:
        return position - size >= -self.max_position

    def must_flatten(self, position: int) -> bool:
        return abs(position) >= self.flatten_threshold
