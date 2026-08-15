"""Markdown rendering for the deterministic portfolio analysis."""

from __future__ import annotations

from .analysis import PortfolioAnalysis


def _money(value: float) -> str:
    sign = "-" if value < 0 else ""
    return f"{sign}${abs(value):,.2f}"


def render_markdown(analysis: PortfolioAnalysis, *, generated_at: str = "unknown") -> str:
    lines = [
        "# Synthetic Portfolio Risk Report",
        "",
        f"- Snapshot generated: {generated_at}",
        "- Mode: deterministic, read-only demo",
        "",
        "> Educational portfolio engineering demo. Not financial advice and not an order recommendation.",
        "",
        "## Summary",
        "",
        f"- Invested market value: **{_money(analysis.total_market_value)}**",
        f"- Total cost: **{_money(analysis.total_cost)}**",
        f"- Open P&L: **{_money(analysis.total_open_pnl)}**",
        "",
        "## Positions",
        "",
        "| Symbol | Qty | Market value | Weight | Open P&L | Stream | Accounts |",
        "| --- | ---: | ---: | ---: | ---: | --- | --- |",
    ]
    for position in analysis.positions:
        lines.append(
            "| {symbol} | {qty:g} | {value} | {weight:.1%} | {pnl} | {stream} | {accounts} |".format(
                symbol=position.symbol,
                qty=position.quantity,
                value=_money(position.market_value),
                weight=analysis.weight(position),
                pnl=_money(position.open_pnl),
                stream=position.stream,
                accounts=", ".join(sorted(position.account_types)),
            )
        )

    lines.extend(
        [
            "",
            "## Allocation by stream",
            "",
            "| Stream | Market value | Weight |",
            "| --- | ---: | ---: |",
        ]
    )
    for stream in ("core", "satellite", "event"):
        lines.append(
            f"| {stream} | {_money(analysis.stream_values.get(stream, 0.0))} | "
            f"{analysis.stream_weight(stream):.1%} |"
        )

    lines.extend(["", "## Risk flags", ""])
    if analysis.findings:
        for finding in analysis.findings:
            scope = f" ({finding.symbol})" if finding.symbol else ""
            lines.append(f"- **{finding.severity.upper()}** `{finding.code}`{scope}: {finding.message}")
    else:
        lines.append("- No demo thresholds were breached.")

    if analysis.cash_by_currency:
        lines.extend(["", "## Cash observed in snapshot", ""])
        for currency, amount in sorted(analysis.cash_by_currency.items()):
            lines.append(f"- {currency}: {_money(amount)}")

    lines.extend(
        [
            "",
            "## Guardrails",
            "",
            "- This report cannot place, amend, or cancel orders.",
            "- Thresholds are illustrative defaults, not personalized advice.",
            "- Live holdings remain local and must never be committed.",
            "",
        ]
    )
    return "\n".join(lines)
