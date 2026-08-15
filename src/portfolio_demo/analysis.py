"""Deterministic portfolio aggregation and risk analysis.

This module deliberately produces risk flags, not trade instructions. It has no
network access and accepts a Questrade-shaped snapshot as plain JSON data.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping


ALLOWED_STREAMS = {"core", "satellite", "event"}


def _number(value: Any) -> float:
    if value is None or value == "":
        return 0.0
    if isinstance(value, bool):
        raise ValueError("boolean values are not valid monetary values")
    return float(value)


def _rows(snapshot: Mapping[str, Any]) -> Iterable[tuple[Mapping[str, Any], str]]:
    accounts = snapshot.get("accounts", [])
    if not isinstance(accounts, list):
        raise ValueError("snapshot.accounts must be a list")
    for account_block in accounts:
        if not isinstance(account_block, Mapping):
            continue
        account = account_block.get("account") or {}
        account_type = str(account.get("type") or "unknown").upper()
        positions = (account_block.get("positions") or {}).get("positions") or []
        if not isinstance(positions, list):
            raise ValueError("account positions must be a list")
        for row in positions:
            if isinstance(row, Mapping):
                yield row, account_type


@dataclass
class PositionSummary:
    symbol: str
    quantity: float = 0.0
    total_cost: float = 0.0
    market_value: float = 0.0
    open_pnl: float = 0.0
    current_price: float = 0.0
    account_types: set[str] = field(default_factory=set)
    stream: str = "satellite"
    role: str = "uncatalogued holding"

    @property
    def open_pnl_pct(self) -> float:
        return self.open_pnl / self.total_cost if self.total_cost else 0.0


@dataclass(frozen=True)
class RiskFinding:
    code: str
    severity: str
    symbol: str | None
    message: str


@dataclass
class PortfolioAnalysis:
    positions: list[PositionSummary]
    findings: list[RiskFinding]
    total_market_value: float
    total_cost: float
    total_open_pnl: float
    cash_by_currency: dict[str, float]
    stream_values: dict[str, float]

    def weight(self, position: PositionSummary) -> float:
        if not self.total_market_value:
            return 0.0
        return position.market_value / self.total_market_value

    def stream_weight(self, stream: str) -> float:
        if not self.total_market_value:
            return 0.0
        return self.stream_values.get(stream, 0.0) / self.total_market_value


def _catalog_entry(catalog: Mapping[str, Any], symbol: str) -> Mapping[str, Any]:
    securities = catalog.get("securities", {}) if isinstance(catalog, Mapping) else {}
    if not isinstance(securities, Mapping):
        raise ValueError("catalog.securities must be an object")
    entry = securities.get(symbol, {})
    if not isinstance(entry, Mapping):
        raise ValueError(f"catalog entry for {symbol} must be an object")
    return entry


def _cash(snapshot: Mapping[str, Any]) -> dict[str, float]:
    totals: dict[str, float] = {}
    for account_block in snapshot.get("accounts", []):
        if not isinstance(account_block, Mapping):
            continue
        balances = (account_block.get("balances") or {}).get("perCurrencyBalances") or []
        for row in balances:
            if not isinstance(row, Mapping):
                continue
            currency = str(row.get("currency") or "").upper()
            if currency:
                totals[currency] = totals.get(currency, 0.0) + _number(row.get("cash"))
    return totals


def analyze_snapshot(
    snapshot: Mapping[str, Any],
    catalog: Mapping[str, Any] | None = None,
    *,
    concentration_limit: float = 0.20,
    event_sleeve_limit: float = 0.15,
) -> PortfolioAnalysis:
    """Aggregate accounts and return deterministic risk flags.

    ``concentration_limit`` and ``event_sleeve_limit`` are public demo defaults,
    not personalized financial recommendations.
    """

    if not 0 < concentration_limit <= 1:
        raise ValueError("concentration_limit must be in (0, 1]")
    if not 0 <= event_sleeve_limit <= 1:
        raise ValueError("event_sleeve_limit must be in [0, 1]")

    catalog = catalog or {"securities": {}}
    by_symbol: dict[str, PositionSummary] = {}
    findings: list[RiskFinding] = []

    for row, account_type in _rows(snapshot):
        symbol = str(row.get("symbol") or "").strip().upper()
        if not symbol:
            findings.append(
                RiskFinding("missing_symbol", "warning", None, "A position row has no symbol.")
            )
            continue
        entry = _catalog_entry(catalog, symbol)
        stream = str(entry.get("stream") or "satellite").lower()
        if stream not in ALLOWED_STREAMS:
            raise ValueError(f"invalid stream for {symbol}: {stream}")

        position = by_symbol.setdefault(
            symbol,
            PositionSummary(
                symbol=symbol,
                stream=stream,
                role=str(entry.get("role") or "uncatalogued holding"),
            ),
        )
        position.quantity += _number(row.get("openQuantity"))
        position.total_cost += _number(row.get("totalCost"))
        position.market_value += _number(row.get("currentMarketValue"))
        position.open_pnl += _number(row.get("openPnl"))
        position.current_price = _number(row.get("currentPrice"))
        position.account_types.add(account_type)

    positions = sorted(by_symbol.values(), key=lambda item: (-item.market_value, item.symbol))
    total_market_value = sum(item.market_value for item in positions)
    total_cost = sum(item.total_cost for item in positions)
    total_open_pnl = sum(item.open_pnl for item in positions)

    stream_values = {stream: 0.0 for stream in sorted(ALLOWED_STREAMS)}
    for position in positions:
        stream_values[position.stream] += position.market_value
        weight = position.market_value / total_market_value if total_market_value else 0.0
        if weight > concentration_limit:
            findings.append(
                RiskFinding(
                    "position_concentration",
                    "high" if weight >= 0.35 else "warning",
                    position.symbol,
                    f"{position.symbol} is {weight:.1%} of invested market value, above the "
                    f"{concentration_limit:.0%} demo threshold.",
                )
            )

    event_weight = stream_values["event"] / total_market_value if total_market_value else 0.0
    if event_weight > event_sleeve_limit:
        findings.append(
            RiskFinding(
                "event_sleeve_concentration",
                "warning",
                None,
                f"Event holdings are {event_weight:.1%} of invested market value, above the "
                f"{event_sleeve_limit:.0%} demo threshold.",
            )
        )

    if not positions:
        findings.append(RiskFinding("empty_portfolio", "info", None, "No positions were found."))

    findings.sort(key=lambda item: ({"high": 0, "warning": 1, "info": 2}.get(item.severity, 3), item.symbol or ""))
    return PortfolioAnalysis(
        positions=positions,
        findings=findings,
        total_market_value=total_market_value,
        total_cost=total_cost,
        total_open_pnl=total_open_pnl,
        cash_by_currency=_cash(snapshot),
        stream_values=stream_values,
    )
