# Security

## Read-only invariant

The Questrade module contains reads for accounts, balances, positions, and open orders. It has no order placement, amendment, cancellation, or trading function. The OAuth token exchange uses POST only to obtain a session; broker data access uses GET.

## Credential handling

- Refresh tokens and cached sessions are stored in macOS Keychain.
- Tokens are never printed or written to project files.
- Refresh rotation is serialized with a local file lock.
- A rotated refresh token is persisted before the short-lived access session is cached.
- Account numbers are removed before snapshots are written.

## Intended use

This is a local command-line portfolio demo, not a hosted service. It implements no web server or remote authentication boundary. Generated live snapshots are sensitive and must remain private.

Report suspected vulnerabilities through a private GitHub security advisory. Never include real tokens or holdings in an issue.
