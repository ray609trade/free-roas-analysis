"""Kalshi WebSocket client.

The one thing that trips everyone up on day one: **the WebSocket requires
API-key auth even for public channels**, and it signs ``GET /trade-api/ws/v2``
-- the WebSocket path, not the REST prefix, and with no query string. Getting
this wrong produces a handshake rejection that looks like a network problem.

Channels used here:

``ticker_v2``         -- last/bid/ask updates per market
``orderbook_delta``   -- incremental book updates; the snapshot arrives first
``trade``             -- prints, with aggressor side

``orderbook_delta`` is not just plumbing. Informed flow in the contract itself
is a signal about the underlying (spec section 4.3), so it is recorded, not
merely consumed.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field
from typing import Any

import websockets

from .auth import Signer
from .config import WS_PATH, Settings, load_settings

__all__ = ["KalshiWebSocket", "WSMessage"]

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class WSMessage:
    channel: str
    type: str
    seq: int | None
    payload: dict[str, Any]
    received_at: float


@dataclass
class KalshiWebSocket:
    """Auto-reconnecting subscriber.

    Sequence numbers are tracked per channel; a gap means the local book is
    stale, so the client resubscribes rather than silently continuing from a
    corrupted state.
    """

    settings: Settings
    signer: Signer
    channels: tuple[str, ...] = ("ticker_v2", "orderbook_delta", "trade")
    market_tickers: tuple[str, ...] = ()
    ping_interval: float = 10.0
    max_backoff: float = 30.0
    on_gap: Callable[[str, int, int], None] | None = None
    _cmd_id: int = field(default=0, init=False)
    _seq: dict[str, int] = field(default_factory=dict, init=False)

    @classmethod
    def from_env(cls, settings: Settings | None = None, **kwargs: Any) -> KalshiWebSocket:
        settings = settings or load_settings()
        key_id, pem = settings.require_credentials()
        return cls(settings=settings, signer=Signer.from_pem(key_id, pem), **kwargs)

    def auth_headers(self) -> dict[str, str]:
        """Headers for the upgrade request. Signs ``GET`` + the *WebSocket* path."""
        return self.signer.sign("GET", WS_PATH)

    def _next_id(self) -> int:
        self._cmd_id += 1
        return self._cmd_id

    def subscribe_command(self) -> dict[str, Any]:
        params: dict[str, Any] = {"channels": list(self.channels)}
        if self.market_tickers:
            params["market_tickers"] = list(self.market_tickers)
        return {"id": self._next_id(), "cmd": "subscribe", "params": params}

    def _check_sequence(self, channel: str, seq: int | None) -> bool:
        """Return True if the stream is contiguous; False if a gap was detected."""
        if seq is None:
            return True
        previous = self._seq.get(channel)
        self._seq[channel] = seq
        if previous is not None and seq != previous + 1:
            if self.on_gap is not None:
                self.on_gap(channel, previous, seq)
            log.warning("sequence gap on %s: %s -> %s", channel, previous, seq)
            return False
        return True

    async def stream(self) -> AsyncIterator[WSMessage]:
        """Yield messages forever, reconnecting with exponential backoff."""
        backoff = 1.0
        while True:
            try:
                async with websockets.connect(
                    self.settings.ws_url,
                    additional_headers=self.auth_headers(),
                    ping_interval=self.ping_interval,
                ) as conn:
                    backoff = 1.0
                    self._seq.clear()
                    await conn.send(json.dumps(self.subscribe_command()))
                    async for raw in conn:
                        msg = self._parse(raw)
                        if msg is None:
                            continue
                        if not self._check_sequence(msg.channel, msg.seq):
                            # Local book is unreliable; force a fresh snapshot.
                            await conn.send(json.dumps(self.subscribe_command()))
                        yield msg
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - reconnect on anything
                log.warning("websocket dropped (%s); reconnecting in %.1fs", exc, backoff)
                with contextlib.suppress(asyncio.CancelledError):
                    await asyncio.sleep(backoff)
                backoff = min(backoff * 2, self.max_backoff)

    @staticmethod
    def _parse(raw: str | bytes) -> WSMessage | None:
        import time

        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            log.warning("undecodable frame dropped")
            return None
        msg_type = data.get("type", "")
        if msg_type in {"subscribed", "unsubscribed", "ok", "error", "pong"}:
            if msg_type == "error":
                log.error("websocket error frame: %s", data)
            return None
        return WSMessage(
            channel=data.get("msg", {}).get("channel") or msg_type,
            type=msg_type,
            seq=data.get("seq"),
            payload=data.get("msg", {}),
            received_at=time.time(),
        )
