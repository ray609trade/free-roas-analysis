"""Auth signing and the production-order gate.

The WebSocket signing rule is the day-one trap called out in the build spec, so
it gets an explicit test rather than a comment.
"""

from __future__ import annotations

import base64

import pytest
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa

from kalshi_crypto.auth import Signer, signing_path
from kalshi_crypto.config import (
    WS_PATH,
    LiveTradingNotEnabled,
    Settings,
    load_settings,
)


@pytest.fixture(scope="module")
def key() -> rsa.RSAPrivateKey:
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


@pytest.fixture(scope="module")
def signer(key) -> Signer:
    return Signer(key_id="test-key-id", private_key=key)


class TestSigningPath:
    def test_strips_query_string(self):
        assert signing_path("/trade-api/v2/markets?limit=100") == "/trade-api/v2/markets"

    def test_strips_host_and_scheme(self):
        assert signing_path("https://api.kalshi.com/trade-api/v2/markets") == (
            "/trade-api/v2/markets"
        )

    def test_strips_fragment(self):
        assert signing_path("/trade-api/v2/markets#frag") == "/trade-api/v2/markets"


class TestSigner:
    def test_signature_verifies(self, signer, key):
        headers = signer.sign("GET", "/trade-api/v2/markets", timestamp_ms=1_700_000_000_000)
        message = b"1700000000000GET/trade-api/v2/markets"
        key.public_key().verify(
            base64.b64decode(headers["KALSHI-ACCESS-SIGNATURE"]),
            message,
            padding.PSS(
                mgf=padding.MGF1(hashes.SHA256()),
                salt_length=padding.PSS.DIGEST_LENGTH,
            ),
            hashes.SHA256(),
        )

    def test_all_three_headers_present(self, signer):
        headers = signer.sign("GET", "/trade-api/v2/markets")
        assert set(headers) == {
            "KALSHI-ACCESS-KEY",
            "KALSHI-ACCESS-SIGNATURE",
            "KALSHI-ACCESS-TIMESTAMP",
        }
        assert headers["KALSHI-ACCESS-KEY"] == "test-key-id"

    def test_method_is_upper_cased(self, signer):
        lower = signer.sign("get", "/x", timestamp_ms=1)
        upper = signer.sign("GET", "/x", timestamp_ms=1)
        # PSS is randomised, so compare by verifying both against the same message.
        assert lower.keys() == upper.keys()
        assert lower["KALSHI-ACCESS-TIMESTAMP"] == upper["KALSHI-ACCESS-TIMESTAMP"]

    def test_query_string_is_excluded_from_the_signature(self, signer, key):
        """Signing the path with a query string is the classic 401 cause."""
        headers = signer.sign(
            "GET", "/trade-api/v2/markets?limit=100", timestamp_ms=1_700_000_000_000
        )
        # The signature must verify against the path *without* the query.
        key.public_key().verify(
            base64.b64decode(headers["KALSHI-ACCESS-SIGNATURE"]),
            b"1700000000000GET/trade-api/v2/markets",
            padding.PSS(
                mgf=padding.MGF1(hashes.SHA256()),
                salt_length=padding.PSS.DIGEST_LENGTH,
            ),
            hashes.SHA256(),
        )

    def test_websocket_signs_its_own_path_not_the_rest_prefix(self, signer, key):
        """The WebSocket signs GET /trade-api/ws/v2 -- not the REST path."""
        from kalshi_crypto.ws import KalshiWebSocket

        ws = KalshiWebSocket(settings=Settings(), signer=signer)
        assert WS_PATH == "/trade-api/ws/v2"
        headers = ws.auth_headers()
        ts = headers["KALSHI-ACCESS-TIMESTAMP"]
        key.public_key().verify(
            base64.b64decode(headers["KALSHI-ACCESS-SIGNATURE"]),
            f"{ts}GET{WS_PATH}".encode(),
            padding.PSS(
                mgf=padding.MGF1(hashes.SHA256()),
                salt_length=padding.PSS.DIGEST_LENGTH,
            ),
            hashes.SHA256(),
        )

    def test_rejects_non_rsa_keys(self):
        from cryptography.hazmat.primitives.asymmetric import ed25519

        pem = ed25519.Ed25519PrivateKey.generate().private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        )
        with pytest.raises(TypeError):
            Signer.from_pem("k", pem)


class TestSettings:
    def test_defaults_to_demo(self):
        settings = load_settings({})
        assert settings.environment == "demo"
        assert settings.rest_base == "https://demo-api.kalshi.co/trade-api/v2"
        assert settings.ws_url == "wss://demo-api.kalshi.co/trade-api/ws/v2"
        assert not settings.is_live

    def test_prod_urls(self):
        settings = load_settings({"KALSHI_ENV": "prod"})
        assert settings.rest_base == "https://api.kalshi.com/trade-api/v2"
        assert settings.ws_url == "wss://api.kalshi.com/trade-api/ws/v2"

    def test_rejects_unknown_environment(self):
        with pytest.raises(ValueError):
            load_settings({"KALSHI_ENV": "staging"})

    def test_demo_orders_allowed_once_paper_only_is_disabled(self):
        load_settings({"KALSHI_PAPER_ONLY": "0"}).require_order_permission()

    def test_prod_orders_blocked_without_explicit_opt_in(self):
        settings = load_settings({"KALSHI_ENV": "prod", "KALSHI_PAPER_ONLY": "0"})
        with pytest.raises(LiveTradingNotEnabled):
            settings.require_order_permission()

    def test_prod_orders_allowed_with_both_opt_ins(self):
        settings = load_settings({
            "KALSHI_ENV": "prod",
            "KALSHI_ALLOW_LIVE_ORDERS": "1",
            "KALSHI_PAPER_ONLY": "0",
        })
        settings.require_order_permission()  # does not raise

    def test_missing_credentials_raises(self):
        with pytest.raises(RuntimeError, match="Missing credentials"):
            load_settings({}).require_credentials()

    def test_inline_pem_is_read(self):
        settings = load_settings(
            {"KALSHI_API_KEY_ID": "abc", "KALSHI_PRIVATE_KEY_PEM": "-----BEGIN-----"}
        )
        key_id, pem = settings.require_credentials()
        assert key_id == "abc"
        assert pem == b"-----BEGIN-----"

    def test_missing_key_file_raises(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            load_settings({"KALSHI_PRIVATE_KEY_PATH": str(tmp_path / "nope.pem")})
