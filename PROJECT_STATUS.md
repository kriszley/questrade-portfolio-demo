# Project status

This is an in-progress side project and sanitized portfolio snapshot.

## Implemented

- Keychain-only rotating refresh-token handling
- cached access-session reuse and 401 refresh recovery
- single-flight refresh locking
- read-only account, balance, position, and open-order snapshot collection
- account-number hashing before persistence
- deterministic aggregation and concentration analysis
- Markdown reporting, synthetic fixtures, unit tests, and CI

## Intentionally omitted from the public edition

- real holdings, reports, tokens, and account identifiers
- personalized allocation rules, watchlists, event calendars, and research prompts
- alert schedules and notification credentials
- private recommendation and evaluation history
- any capability to place, modify, or cancel trades

## Possible next work

- JSON Schema validation for broker responses
- additional malformed-response tests
- Windows/Linux credential-provider abstractions
- a local-only HTML report renderer
