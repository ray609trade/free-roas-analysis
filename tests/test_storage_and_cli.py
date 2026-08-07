"""Recorder plumbing and CLI smoke tests.

The TimescaleDB path needs a live database, so those tests skip unless
``KALSHI_TEST_DATABASE_URL`` is set. Everything else runs offline.
"""

from __future__ import annotations

import os

import pytest

from kalshi_crypto.cli import main
from kalshi_crypto.marketdata import BookTop, IndexSample, TradePrint
from kalshi_crypto.storage import MemoryRecorder, schema_path


class TestSchema:
    def test_schema_file_ships_with_the_package(self):
        path = schema_path()
        assert path.exists()
        sql = path.read_text()
        assert "create_hypertable" in sql
        # The tables the build order depends on.
        for table in (
            "index_samples", "constituent_tops", "kalshi_book_deltas",
            "settlements", "model_estimates",
        ):
            assert table in sql

    def test_settlements_table_holds_both_settlement_candidates(self):
        """Storing expiration_value beside mirror_average is how section 3 gets settled."""
        sql = schema_path().read_text()
        assert "expiration_value" in sql
        assert "mirror_average" in sql


class TestMemoryRecorder:
    def test_records_every_stream(self):
        rec = MemoryRecorder()
        rec.record_index(IndexSample("BTC", 64_000.0, 1.0, 3))
        rec.record_top(BookTop("coinbase", "BTC-USD", 1.0, 2.0, 1.0, 1.0, 1.0))
        rec.record_trade(TradePrint("kraken", "BTC-USD", 64_000.0, 0.5, "buy", 1.0))
        rec.record_estimate("T", "settlement", {"probability": 0.6})
        rec.flush()
        assert len(rec.index_samples) == 1
        assert len(rec.tops) == 1
        assert len(rec.trades) == 1
        assert rec.estimates[0][1] == "settlement"

    def test_satisfies_the_recorder_protocol(self):
        from kalshi_crypto.storage.recorder import Recorder

        rec: Recorder = MemoryRecorder()
        rec.flush()


@pytest.mark.skipif(
    not os.environ.get("KALSHI_TEST_DATABASE_URL"),
    reason="set KALSHI_TEST_DATABASE_URL to run TimescaleDB integration tests",
)
class TestTimescaleRecorder:
    def test_round_trip(self):
        from kalshi_crypto.storage import TimescaleRecorder

        dsn = os.environ["KALSHI_TEST_DATABASE_URL"]
        with TimescaleRecorder(dsn, batch_size=2) as rec:
            rec.record_index(IndexSample("BTC", 64_000.0, 1.0, 3))
            rec.record_index(IndexSample("BTC", 64_001.0, 2.0, 3))
            rec.flush()


class TestCLI:
    def test_fees_table(self, capsys):
        assert main(["fees", "--contracts", "100"]) == 0
        out = capsys.readouterr().out
        assert "Maker multiplier defaults to ZERO" in out
        assert "4.75%" in out or "4.7" in out

    def test_settle_reports_both_models(self, capsys):
        code = main([
            "settle", "--price", "64500", "--strike", "64400", "--sigma", "3",
        ])
        assert code == 0
        out = capsys.readouterr().out
        assert "probability" in out
        assert "naive_endpoint_probability" in out

    def test_settle_with_partial_samples(self, capsys):
        code = main([
            "settle", "--price", "64500", "--strike", "64400", "--sigma", "3",
            "--samples-done", "30", "--partial-mean", "64500",
        ])
        assert code == 0
        assert '"n_left": 30' in capsys.readouterr().out

    def test_backtest_runs_the_leakage_check(self, capsys):
        assert main(["backtest", "--markets", "60", "--strategy", "maker"]) == 0
        out = capsys.readouterr().out
        assert "leakage check: OK" in out

    def test_quote_pulls_inside_the_settlement_window(self, capsys):
        assert main(["quote", "--fair", "0.5", "--to-close", "30"]) == 0
        assert "(no quotes)" in capsys.readouterr().out

    def test_quote_posts_two_sides_when_far_from_close(self, capsys):
        assert main(["quote", "--fair", "0.5", "--to-close", "600",
                     "--bid", "48", "--ask", "52"]) == 0
        out = capsys.readouterr().out
        assert "BID" in out and "ASK" in out

    def test_schema_command_prints_a_real_path(self, capsys):
        assert main(["schema"]) == 0
        assert capsys.readouterr().out.strip().endswith("schema.sql")

    def test_terms_fails_loudly_without_network(self, capsys, monkeypatch, tmp_path):
        """A missing terms document must never be read as 'defaults are fine'."""
        import httpx

        def boom(*args, **kwargs):
            raise httpx.ConnectError("no route to host")

        monkeypatch.setattr(httpx, "get", boom)
        code = main(["terms", "--dest", str(tmp_path)])
        assert code == 1
        assert "FAILED" in capsys.readouterr().err
