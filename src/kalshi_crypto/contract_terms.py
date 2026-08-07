"""Fetch and pin Kalshi contract terms (build-order item 2).

Section 3 of the build spec records a genuine, unresolved conflict:

* Two sources say **all** Kalshi crypto contracts settle on a 60-second average
  of the CF Benchmarks Real-Time Index, sampled once per second over the final
  minute.
* One source says the 15-minute family settles against Kalshi's own captured
  ``expiration_value`` on the market record.

These may be the same mechanism seen at two layers -- Kalshi capturing the BRTI
average onto the market record -- but the difference is not cosmetic. Every
number in :mod:`kalshi_crypto.settlement` depends on which is true, because the
whole ``sigma*sqrt(T/3)`` result exists *only* if settlement is an average.
If settlement were a snapshot, the correct factor is ``sigma*sqrt(T)`` and the
model is mispriced by ~42% in the standard deviation.

So this module does not decide. It downloads the primary document, records a
SHA-256 so a silent change to the terms is detectable, and leaves a human to
read it and record the finding in ``docs/SETTLEMENT.md``.

The sandbox this was developed in has no route to Kalshi, so
:func:`fetch_contract_terms` is written to run on a networked machine and fails
loudly rather than silently substituting a default.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path

import httpx

__all__ = ["ContractTermsRecord", "fetch_contract_terms", "CONTRACT_TERMS_URLS"]

CONTRACT_TERMS_URLS = {
    "CRYPTO15M": "https://kalshi-public-docs.s3.amazonaws.com/contract_terms/CRYPTO15M.pdf",
}

# Phrases worth grepping for once the PDF is converted to text. Presence of the
# first group supports the BRTI-average reading; the second supports the
# captured-value reading. Both may appear -- that is the "same thing at two
# layers" case, and it is the answer we most expect.
SETTLEMENT_MARKERS = {
    "brti_average": (
        "real-time index", "brti", "cf benchmarks",
        "sixty", "60 second", "average",
    ),
    "captured_value": (
        "expiration_value", "expiration value", "recorded by the exchange",
    ),
}


@dataclass(frozen=True)
class ContractTermsRecord:
    """Provenance for a downloaded terms document."""

    name: str
    url: str
    sha256: str
    size_bytes: int
    fetched_at: str
    path: str

    def to_json(self) -> str:
        return json.dumps(asdict(self), indent=2, sort_keys=True)


def fetch_contract_terms(
    name: str = "CRYPTO15M",
    dest_dir: str | Path = "contract_terms",
    *,
    timeout: float = 30.0,
) -> ContractTermsRecord:
    """Download a contract-terms PDF and record its hash.

    Raises on any network failure -- a missing document must never be
    interpreted as "the defaults are fine".
    """
    url = CONTRACT_TERMS_URLS.get(name)
    if url is None:
        raise KeyError(f"no known URL for {name!r}; known: {sorted(CONTRACT_TERMS_URLS)}")

    dest = Path(dest_dir)
    dest.mkdir(parents=True, exist_ok=True)

    try:
        resp = httpx.get(url, timeout=timeout, follow_redirects=True)
        resp.raise_for_status()
    except httpx.HTTPError as exc:
        raise RuntimeError(
            f"could not fetch {url}: {exc}. This machine may have no route to "
            "Kalshi. Run this on a networked host before trading -- do not "
            "assume the settlement defaults in settlement.py are correct."
        ) from exc

    body = resp.content
    pdf_path = dest / f"{name}.pdf"
    pdf_path.write_bytes(body)

    record = ContractTermsRecord(
        name=name,
        url=url,
        sha256=hashlib.sha256(body).hexdigest(),
        size_bytes=len(body),
        fetched_at=datetime.now(UTC).isoformat(),
        path=str(pdf_path),
    )
    (dest / f"{name}.provenance.json").write_text(record.to_json())
    return record


def verify_unchanged(record_path: str | Path) -> bool:
    """Re-hash a stored PDF against its provenance file.

    Contract terms change. Wire this into CI or a daily job so a change to the
    settlement source surfaces as a failure instead of as unexplained model
    drift.
    """
    record_path = Path(record_path)
    record = json.loads(record_path.read_text())
    pdf = Path(record["path"])
    if not pdf.exists():
        return False
    return hashlib.sha256(pdf.read_bytes()).hexdigest() == record["sha256"]
