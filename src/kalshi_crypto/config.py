"""Environment configuration.

Secrets come from the environment or a private-key file on disk. Nothing
sensitive is ever written to the repo -- ``*.pem``/``*.key``/``.env`` are
gitignored.

Environments
------------
``demo``  -- paper money, https://demo-api.kalshi.co. The default, deliberately.
``prod``  -- real capital, https://api.kalshi.com.

Anything that can send an order refuses to run against ``prod`` unless
``KALSHI_ALLOW_LIVE_ORDERS=1`` is also set. Reading market data from prod is
fine; sending orders there needs two explicit opt-ins.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

__all__ = ["Settings", "Environment", "load_settings", "LiveTradingNotEnabled"]

Environment = str  # "demo" | "prod"

_HOSTS = {
    "demo": ("https://demo-api.kalshi.co", "wss://demo-api.kalshi.co"),
    "prod": ("https://api.kalshi.com", "wss://api.kalshi.com"),
}

REST_PREFIX = "/trade-api/v2"
WS_PATH = "/trade-api/ws/v2"


class LiveTradingNotEnabled(RuntimeError):
    """Raised when order-sending code is pointed at prod without opt-in."""


@dataclass(frozen=True)
class Settings:
    environment: Environment = "demo"
    api_key_id: str | None = None
    private_key_pem: bytes | None = None
    allow_live_orders: bool = False
    database_url: str | None = None
    # Master switch. On by default: no order reaches any exchange, demo
    # included. The live engine trades through PaperBroker, which holds no
    # credentials and opens no sockets, so this is belt and braces.
    paper_only: bool = True

    @property
    def rest_base(self) -> str:
        return _HOSTS[self.environment][0] + REST_PREFIX

    @property
    def ws_url(self) -> str:
        return _HOSTS[self.environment][1] + WS_PATH

    @property
    def is_live(self) -> bool:
        return self.environment == "prod"

    def require_order_permission(self) -> None:
        """Gate for any code path that can create or cancel a real order."""
        if self.paper_only:
            raise LiveTradingNotEnabled(
                "PAPER_ONLY is on: this system does not place exchange orders. "
                "Simulated trading runs through PaperBroker. To send real "
                "orders you must deliberately set KALSHI_PAPER_ONLY=0 -- and "
                "you should not do that until the 60-day paper record says so."
            )
        if self.is_live and not self.allow_live_orders:
            raise LiveTradingNotEnabled(
                "Refusing to send orders to the production exchange. "
                "Set KALSHI_ENV=demo to paper trade, or set "
                "KALSHI_ALLOW_LIVE_ORDERS=1 to deliberately risk capital."
            )

    def require_credentials(self) -> tuple[str, bytes]:
        if not self.api_key_id or not self.private_key_pem:
            raise RuntimeError(
                "Missing credentials: set KALSHI_API_KEY_ID and "
                "KALSHI_PRIVATE_KEY_PATH (or KALSHI_PRIVATE_KEY_PEM)."
            )
        return self.api_key_id, self.private_key_pem


def load_settings(env: dict[str, str] | None = None) -> Settings:
    """Build settings from the environment, defaulting to the demo exchange."""
    env = dict(os.environ if env is None else env)
    environment = env.get("KALSHI_ENV", "demo").lower()
    if environment not in _HOSTS:
        raise ValueError(f"KALSHI_ENV must be one of {sorted(_HOSTS)}, got {environment!r}")

    pem: bytes | None = None
    if inline := env.get("KALSHI_PRIVATE_KEY_PEM"):
        pem = inline.encode()
    elif path := env.get("KALSHI_PRIVATE_KEY_PATH"):
        key_path = Path(path).expanduser()
        if not key_path.exists():
            raise FileNotFoundError(f"private key not found at {key_path}")
        pem = key_path.read_bytes()

    return Settings(
        environment=environment,
        api_key_id=env.get("KALSHI_API_KEY_ID"),
        private_key_pem=pem,
        allow_live_orders=env.get("KALSHI_ALLOW_LIVE_ORDERS") == "1",
        database_url=env.get("KALSHI_DATABASE_URL"),
        paper_only=env.get("KALSHI_PAPER_ONLY", "1") != "0",
    )
