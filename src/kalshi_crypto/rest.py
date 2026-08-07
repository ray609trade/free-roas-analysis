"""Kalshi REST client (trade-api v2).

Market-data reads do not require auth on REST; a :class:`Signer` is only needed
for account and order endpoints. The client attaches auth headers whenever a
signer is present, which is harmless on public routes.

Rate limits are tiered and token-based -- call :meth:`KalshiREST.api_limits` and
respect what it returns. REST latency runs 50-200ms, so this is an event-driven
medium-frequency client, not an HFT path.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx

from .auth import Signer, signing_path
from .config import Settings, load_settings

__all__ = ["KalshiREST", "RateLimitError", "KalshiAPIError"]


class KalshiAPIError(RuntimeError):
    def __init__(self, status: int, body: str, path: str) -> None:
        super().__init__(f"{status} from {path}: {body[:500]}")
        self.status = status
        self.body = body
        self.path = path


class RateLimitError(KalshiAPIError):
    """429 -- back off. Kalshi's limits are token-based, so bursts matter."""


@dataclass
class KalshiREST:
    settings: Settings
    signer: Signer | None = None
    timeout: float = 10.0
    _client: httpx.AsyncClient | None = None

    @classmethod
    def from_env(cls, settings: Settings | None = None) -> KalshiREST:
        settings = settings or load_settings()
        signer = None
        if settings.api_key_id and settings.private_key_pem:
            signer = Signer.from_pem(settings.api_key_id, settings.private_key_pem)
        return cls(settings=settings, signer=signer)

    async def __aenter__(self) -> KalshiREST:
        self._client = httpx.AsyncClient(base_url=self.settings.rest_base, timeout=self.timeout)
        return self

    async def __aexit__(self, *exc: object) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    def _headers(self, method: str, path: str) -> dict[str, str]:
        if self.signer is None:
            return {}
        # Sign the full path including the /trade-api/v2 prefix, without query.
        from .config import REST_PREFIX

        return self.signer.sign(method, signing_path(REST_PREFIX + path))

    async def _request(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        if self._client is None:
            raise RuntimeError("use KalshiREST as an async context manager")
        resp = await self._client.request(
            method, path, headers=self._headers(method, path), **kwargs
        )
        if resp.status_code == 429:
            raise RateLimitError(resp.status_code, resp.text, path)
        if resp.status_code >= 400:
            raise KalshiAPIError(resp.status_code, resp.text, path)
        return resp.json()

    # ---- public market data -------------------------------------------------

    async def exchange_status(self) -> dict[str, Any]:
        return await self._request("GET", "/exchange/status")

    async def markets(self, **params: Any) -> dict[str, Any]:
        """List markets. Useful filters: ``series_ticker``, ``status``, ``limit``."""
        return await self._request("GET", "/markets", params=params)

    async def market(self, ticker: str) -> dict[str, Any]:
        return await self._request("GET", f"/markets/{ticker}")

    async def orderbook(self, ticker: str, depth: int = 10) -> dict[str, Any]:
        return await self._request("GET", f"/markets/{ticker}/orderbook", params={"depth": depth})

    async def trades(self, ticker: str, limit: int = 100) -> dict[str, Any]:
        return await self._request("GET", "/markets/trades", params={"ticker": ticker, "limit": limit})

    async def open_15m_markets(self, asset: str) -> list[dict[str, Any]]:
        """Convenience: currently-open 15-minute markets for one asset."""
        payload = await self.markets(series_ticker=f"KX{asset.upper()}15M", status="open")
        return payload.get("markets", [])

    # ---- account (auth required) -------------------------------------------

    async def api_limits(self) -> dict[str, Any]:
        return await self._request("GET", "/account/api-limits")

    async def balance(self) -> dict[str, Any]:
        return await self._request("GET", "/portfolio/balance")

    async def positions(self, **params: Any) -> dict[str, Any]:
        return await self._request("GET", "/portfolio/positions", params=params)

    async def create_order(self, **order: Any) -> dict[str, Any]:
        """Place an order. Blocked against prod unless live trading is enabled."""
        self.settings.require_order_permission()
        return await self._request("POST", "/portfolio/orders", json=order)

    async def cancel_order(self, order_id: str) -> dict[str, Any]:
        self.settings.require_order_permission()
        return await self._request("DELETE", f"/portfolio/orders/{order_id}")
