# Privacy model

## Public repository

Only synthetic account references, positions, prices, cash, and P&L values are tracked. No real brokerage export, report, token, account number, personalized catalog, or strategy configuration belongs in this repository.

## Live local use

- Refresh tokens and cached access sessions stay in macOS Keychain.
- The snapshot client removes the raw account number and stores a 16-character SHA-256-derived reference.
- Holdings, balances, account types, prices, P&L, and open orders remain sensitive even after account-number removal.
- Live snapshots and reports are written under `private/`, which is gitignored.

Before every commit, verify that `git status` does not contain files under `private/`, `.env` files, brokerage exports, screenshots, or generated reports.
