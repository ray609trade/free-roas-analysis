"""Paper broker -- simulated positions against live prices, no real money.

Every order in this system goes through here. There is no code path from the
live engine to a real exchange order: :class:`PaperBroker` does not hold API
credentials and has no network client at all. That is the design, not a setting
you have to remember to leave switched on.

Fills are charged the real fee schedule. A paper account that ignores fees will
show you a profitable strategy that loses money live -- on this product the fee
is a bigger lever than the model, so simulating without it is worse than not
simulating.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal

from ..fees import DEFAULT_SCHEDULE, FeeSchedule

__all__ = ["PaperFill", "PaperPosition", "PaperBroker"]


@dataclass(frozen=True)
class PaperFill:
    timestamp: datetime
    instrument: str
    side: str          # "up" | "down"
    contracts: int
    price: float       # probability in dollars, 0..1
    fee: Decimal
    is_taker: bool
    window_label: str


@dataclass
class PaperPosition:
    instrument: str
    window_label: str
    contracts: int = 0     # positive = long UP, negative = long DOWN
    cash: Decimal = Decimal("0")

    @property
    def is_flat(self) -> bool:
        return self.contracts == 0


@dataclass
class PaperBroker:
    """Simulated account. Holds no credentials and opens no sockets."""

    starting_bankroll: float = 1_000.0
    schedule: FeeSchedule = field(default_factory=lambda: DEFAULT_SCHEDULE)
    max_contracts_per_window: int = 100
    fills: list[PaperFill] = field(default_factory=list)
    positions: dict[str, PaperPosition] = field(default_factory=dict)
    realised_pnl: Decimal = field(default=Decimal("0"), init=False)
    total_fees: Decimal = field(default=Decimal("0"), init=False)
    settled_windows: int = field(default=0, init=False)
    wins: int = field(default=0, init=False)

    def _key(self, instrument: str, window_label: str) -> str:
        return f"{instrument}|{window_label}"

    def position(self, instrument: str, window_label: str) -> PaperPosition:
        key = self._key(instrument, window_label)
        if key not in self.positions:
            self.positions[key] = PaperPosition(instrument, window_label)
        return self.positions[key]

    def buy(
        self,
        *,
        instrument: str,
        window_label: str,
        side: str,
        contracts: int,
        price: float,
        timestamp: datetime,
        is_taker: bool = True,
    ) -> PaperFill | None:
        """Open or add to a position. ``price`` is the cost of that side, 0..1.

        Returns ``None`` if the trade would breach the per-window size cap --
        silently truncating instead would flatter every result.
        """
        if side not in ("up", "down"):
            raise ValueError("side must be 'up' or 'down'")
        if contracts <= 0 or not 0.0 < price < 1.0:
            return None

        pos = self.position(instrument, window_label)
        signed = contracts if side == "up" else -contracts
        if abs(pos.contracts + signed) > self.max_contracts_per_window:
            return None

        fee = self.schedule.fee(price, contracts, is_taker=is_taker)
        pos.contracts += signed
        pos.cash -= Decimal(str(price)) * Decimal(contracts) + fee
        self.total_fees += fee

        fill = PaperFill(
            timestamp=timestamp, instrument=instrument, side=side,
            contracts=contracts, price=price, fee=fee, is_taker=is_taker,
            window_label=window_label,
        )
        self.fills.append(fill)
        return fill

    def settle(self, instrument: str, window_label: str, outcome_up: bool) -> Decimal:
        """Settle a window. Each UP contract pays $1 if up, else $0. No fee."""
        key = self._key(instrument, window_label)
        pos = self.positions.pop(key, None)
        if pos is None:
            return Decimal("0")

        if pos.contracts > 0:      # long UP
            payout = Decimal(pos.contracts) if outcome_up else Decimal(0)
        else:                      # long DOWN
            payout = Decimal(-pos.contracts) if not outcome_up else Decimal(0)

        pnl = pos.cash + payout
        self.realised_pnl += pnl
        self.settled_windows += 1
        if pnl > 0:
            self.wins += 1
        return pnl

    @property
    def bankroll(self) -> float:
        return self.starting_bankroll + float(self.realised_pnl)

    @property
    def open_positions(self) -> list[PaperPosition]:
        return [p for p in self.positions.values() if not p.is_flat]

    def summary(self) -> dict[str, float]:
        contracts = sum(f.contracts for f in self.fills)
        return {
            "bankroll": self.bankroll,
            "realised_pnl": float(self.realised_pnl),
            "total_fees": float(self.total_fees),
            "fills": float(len(self.fills)),
            "contracts": float(contracts),
            "settled_windows": float(self.settled_windows),
            "win_rate": self.wins / self.settled_windows if self.settled_windows else 0.0,
            "pnl_per_contract": float(self.realised_pnl) / contracts if contracts else 0.0,
        }

    def format_summary(self) -> str:
        s = self.summary()
        return (
            f"PAPER ACCOUNT (no real funds)\n"
            f"  bankroll        : ${s['bankroll']:,.2f} "
            f"(started ${self.starting_bankroll:,.2f})\n"
            f"  realised P&L    : ${s['realised_pnl']:+,.2f}\n"
            f"  fees paid       : ${s['total_fees']:,.2f}\n"
            f"  windows settled : {int(s['settled_windows'])}  "
            f"win rate {s['win_rate']:.1%}\n"
            f"  contracts       : {int(s['contracts'])}  "
            f"P&L/contract ${s['pnl_per_contract']:+.4f}"
        )
