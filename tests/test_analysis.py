from __future__ import annotations

import json
import unittest
from pathlib import Path

from portfolio_demo.analysis import analyze_snapshot
from portfolio_demo.report import render_markdown


ROOT = Path(__file__).resolve().parents[1]


class AnalysisTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.snapshot = json.loads((ROOT / "examples/synthetic_snapshot.json").read_text())
        cls.catalog = json.loads((ROOT / "examples/synthetic_catalog.json").read_text())

    def test_aggregates_same_symbol_across_accounts(self) -> None:
        result = analyze_snapshot(self.snapshot, self.catalog)
        voo = next(position for position in result.positions if position.symbol == "VOO")
        self.assertEqual(voo.quantity, 10)
        self.assertEqual(voo.market_value, 5300)
        self.assertEqual(voo.account_types, {"TFSA", "RRSP"})

    def test_totals_and_cash_are_deterministic(self) -> None:
        result = analyze_snapshot(self.snapshot, self.catalog)
        self.assertEqual(result.total_market_value, 8300)
        self.assertEqual(result.total_cost, 7850)
        self.assertEqual(result.total_open_pnl, 450)
        self.assertEqual(result.cash_by_currency, {"CAD": 800, "USD": 250})

    def test_flags_large_positions(self) -> None:
        result = analyze_snapshot(self.snapshot, self.catalog)
        concentrated = {finding.symbol for finding in result.findings if finding.code == "position_concentration"}
        self.assertEqual(concentrated, {"VOO", "QQQ"})

    def test_flags_event_sleeve_when_threshold_is_lowered(self) -> None:
        result = analyze_snapshot(self.snapshot, self.catalog, event_sleeve_limit=0.01)
        self.assertIn("event_sleeve_concentration", {finding.code for finding in result.findings})

    def test_rejects_invalid_catalog_stream(self) -> None:
        with self.assertRaisesRegex(ValueError, "invalid stream"):
            analyze_snapshot(self.snapshot, {"securities": {"VOO": {"stream": "trade-now"}}})

    def test_markdown_report_has_guardrails(self) -> None:
        report = render_markdown(analyze_snapshot(self.snapshot, self.catalog), generated_at="demo")
        self.assertIn("# Synthetic Portfolio Risk Report", report)
        self.assertIn("| VOO | 10 | $5,300.00 |", report)
        self.assertIn("cannot place, amend, or cancel orders", report)
        self.assertIn("Not financial advice", report)


if __name__ == "__main__":
    unittest.main()
