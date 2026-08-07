"""Kalshi API-key request signing (RSA-PSS over SHA-256).

The signed message is::

    str(timestamp_ms) + HTTP_METHOD + PATH

and travels in three headers: ``KALSHI-ACCESS-KEY``, ``KALSHI-ACCESS-SIGNATURE``
(base64), ``KALSHI-ACCESS-TIMESTAMP`` (ms since epoch).

Two path rules that cause almost every day-one failure:

1. **Query strings are excluded.** Sign ``/trade-api/v2/markets``, never
   ``/trade-api/v2/markets?limit=100``.
2. **The WebSocket signs its own path**, ``GET /trade-api/ws/v2`` -- not the
   REST prefix. Public channels still require this; there is no anonymous
   WebSocket access even for market data that REST serves unauthenticated.

:func:`signing_path` enforces rule 1 so callers cannot pass a URL with a query
string by accident.
"""

from __future__ import annotations

import base64
import time
from dataclasses import dataclass
from urllib.parse import urlsplit

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa

__all__ = ["Signer", "signing_path", "load_private_key"]


def load_private_key(pem: bytes, password: bytes | None = None) -> rsa.RSAPrivateKey:
    key = serialization.load_pem_private_key(pem, password=password)
    if not isinstance(key, rsa.RSAPrivateKey):
        raise TypeError("Kalshi API keys are RSA; got a different key type")
    return key


def signing_path(path_or_url: str) -> str:
    """Strip scheme, host, query and fragment; return the bare path to sign."""
    parts = urlsplit(path_or_url)
    path = parts.path or "/"
    if not path.startswith("/"):
        path = "/" + path
    return path


@dataclass
class Signer:
    """Signs requests for one API key."""

    key_id: str
    private_key: rsa.RSAPrivateKey

    @classmethod
    def from_pem(cls, key_id: str, pem: bytes, password: bytes | None = None) -> Signer:
        return cls(key_id=key_id, private_key=load_private_key(pem, password))

    def sign(self, method: str, path_or_url: str, timestamp_ms: int | None = None) -> dict[str, str]:
        """Return the three auth headers for one request.

        ``timestamp_ms`` is injectable so tests are deterministic; production
        callers should let it default to now.
        """
        ts = int(time.time() * 1000) if timestamp_ms is None else timestamp_ms
        path = signing_path(path_or_url)
        message = f"{ts}{method.upper()}{path}".encode()
        signature = self.private_key.sign(
            message,
            padding.PSS(
                mgf=padding.MGF1(hashes.SHA256()),
                salt_length=padding.PSS.DIGEST_LENGTH,
            ),
            hashes.SHA256(),
        )
        return {
            "KALSHI-ACCESS-KEY": self.key_id,
            "KALSHI-ACCESS-SIGNATURE": base64.b64encode(signature).decode(),
            "KALSHI-ACCESS-TIMESTAMP": str(ts),
        }
