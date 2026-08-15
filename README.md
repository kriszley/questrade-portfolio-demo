# Questrade Portfolio Demo

An in-progress, **read-only** brokerage portfolio engineering demo. It creates privacy-aware Questrade snapshots, aggregates positions across accounts, applies deterministic concentration checks, and renders a Markdown risk report. It cannot place, amend, or cancel trades.

This is a fresh-history, sanitized portfolio edition distilled from a larger private research workflow. Real holdings, reports, account identifiers, tokens, personalized strategy rules, scheduler configuration, and private evaluation history are intentionally omitted.

> Educational decision support only. Not financial advice and not a trading system.

This independent project is not affiliated with, endorsed by, or sponsored by Questrade.

## What it demonstrates

- rotating OAuth refresh-token handling with transient retry and single-flight locking
- macOS Keychain storage with no plaintext credential fallback
- a broker boundary limited to account, balance, position, and open-order reads
- account-number removal plus stable hashed references in stored snapshots
- deterministic multi-account aggregation, cash summaries, stream allocation, and concentration flags
- synthetic fixtures, standard-library tests, immutable CI action pins, and zero runtime package dependencies

## Architecture

```text
Questrade OAuth + read endpoints
            │
            v
  read-only snapshot client ──> private/snapshots/*.json (gitignored)
            │
            v
 deterministic analyzer ─────> private/reports/*.md (gitignored)
            │
            └── synthetic examples + offline tests in this repository
```

The only non-GET broker call is the OAuth token exchange required to obtain a session. There are no order mutation methods or trade tools.

## Run the synthetic demo

```bash
PYTHONPATH=src python3 -m portfolio_demo.cli \
  examples/synthetic_snapshot.json \
  --catalog examples/synthetic_catalog.json \
  --output private/reports/demo.md
```

The fixture uses invented account references, quantities, prices, cash, and P&L values.

## Run tests

```bash
PYTHONPATH=src python3 -m unittest discover -s tests -v
PYTHONPATH=src python3 -m compileall -q src tests
```

## Optional local Questrade snapshot

Live setup is macOS-only because credentials are stored in Keychain. Read [PRIVACY.md](PRIVACY.md) and [SECURITY.md](SECURITY.md) first.

```bash
PYTHONPATH=src python3 -m portfolio_demo.questrade_readonly status
PYTHONPATH=src python3 -m portfolio_demo.questrade_readonly --practice init
PYTHONPATH=src python3 -m portfolio_demo.questrade_readonly --practice snapshot
```

Use Questrade practice mode while evaluating the integration. A live snapshot contains sensitive holdings even though the account number is removed, so generated snapshots remain gitignored.

## Scope

See [PROJECT_STATUS.md](PROJECT_STATUS.md). This public edition focuses on the infrastructure and privacy boundaries most relevant to backend/platform engineering. It does not publish the private project's personalized allocation rules, live research prompts, alert schedule, holdings, or recommendations.

## License

Copyright © 2026 Chris Lee. Public for portfolio review; no reuse license is granted. See [LICENSE](LICENSE).
