from __future__ import annotations

import datetime as dt
import inspect
import unittest
from unittest import mock

from portfolio_demo import questrade_readonly as qt


class QuestradeReadOnlyTests(unittest.TestCase):
    def test_sanitize_account_removes_number(self) -> None:
        sanitized = qt.sanitize_account({"number": "12345678", "type": "TFSA", "status": "Active"})
        self.assertNotIn("number", sanitized)
        self.assertEqual(sanitized["type"], "TFSA")
        self.assertEqual(sanitized["accountRef"], qt.account_ref("12345678"))
        self.assertEqual(len(sanitized["accountRef"]), 16)

    def test_open_orders_path_is_get_only_and_has_lookback(self) -> None:
        now = dt.datetime(2026, 1, 15, 22, 0, tzinfo=dt.timezone.utc)
        path = qt._open_orders_path("123", now)
        self.assertTrue(path.startswith("accounts/123/orders?"))
        self.assertIn("stateFilter=Open", path)
        self.assertIn("startTime=", path)
        self.assertIn("endTime=", path)

    def test_api_get_builds_authorized_get(self) -> None:
        with mock.patch.object(qt, "request_json", return_value={"positions": []}) as request:
            result = qt.api_get("https://api.example/", "demo-access", "accounts/1/positions")
        self.assertEqual(result, {"positions": []})
        request.assert_called_once_with(
            "https://api.example/v1/accounts/1/positions",
            headers={"Authorization": "Bearer demo-access"},
        )

    def test_transient_redemption_is_retried(self) -> None:
        response = {"access_token": "a", "refresh_token": "r2", "api_server": "https://api.example"}
        with mock.patch.object(
            qt,
            "_redeem_once",
            side_effect=[qt.QuestradeNetworkError("timeout"), response],
        ) as redeem, mock.patch.object(qt, "_redeem_backoff"):
            self.assertEqual(qt.redeem_refresh_token("r1", practice=True), response)
        self.assertEqual(redeem.call_count, 2)

    def test_get_style_token_fallback_redacts_token_from_errors(self) -> None:
        with mock.patch.object(qt, "request_json", return_value={}) as request:
            qt.redeem_refresh_token_get("sensitive-demo-token", practice=True)
        called_url = request.call_args.args[0]
        self.assertIn("sensitive-demo-token", called_url)
        self.assertEqual(request.call_args.kwargs["error_url"], qt.PRACTICE_LOGIN_URL)

    def test_module_exposes_no_trade_mutation_functions(self) -> None:
        source = inspect.getsource(qt)
        for forbidden in (
            "def place_order",
            "def submit_order",
            "def amend_order",
            "def cancel_order",
            "def api_post",
            "def api_put",
            "def api_delete",
        ):
            self.assertNotIn(forbidden, source)

    def test_fetch_accounts_uses_read_endpoints_only(self) -> None:
        calls: list[str] = []

        def fake_get(_server: str, _token: str, path: str):
            calls.append(path)
            if path == "accounts":
                return {"accounts": [{"number": "123", "type": "TFSA"}]}
            if path.endswith("/balances"):
                return {"perCurrencyBalances": []}
            if path.endswith("/positions"):
                return {"positions": []}
            if "/orders?" in path:
                return {"orders": []}
            raise AssertionError(path)

        with mock.patch.object(qt, "api_get", side_effect=fake_get):
            accounts = qt._fetch_accounts("access", "https://api.example", qt.utc_now())
        self.assertEqual(len(accounts), 1)
        self.assertNotIn("number", accounts[0]["account"])
        self.assertTrue(all(path == "accounts" or "/balances" in path or "/positions" in path or "/orders?" in path for path in calls))


if __name__ == "__main__":
    unittest.main()
