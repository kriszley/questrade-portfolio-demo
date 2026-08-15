# Architecture notes

The public edition separates three trust zones:

1. **Credential zone:** macOS Keychain holds refresh and access tokens.
2. **Broker boundary:** the client exchanges OAuth tokens, then performs only Questrade GET requests for accounts, balances, positions, and open orders.
3. **Analysis zone:** local JSON is treated as input to a network-free deterministic analyzer and Markdown renderer.

The account number exists only while fetching an account's child resources. `sanitize_account()` removes it before persistence and replaces it with a short stable hash so positions from the same account can be correlated without storing the number.

The refresh token is single-use. `_rotation_lock()` prevents two local runs from redeeming it concurrently, and `refresh_session()` persists the rotated refresh token before caching the short-lived access token. A cached access token that receives HTTP 401 is refreshed once and retried.
