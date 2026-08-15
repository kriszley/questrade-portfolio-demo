#!/usr/bin/env python3
"""Read-only Questrade portfolio snapshot utility.

The rotating refresh token and cached session stay in macOS Keychain. This
module exposes account, balance, position, and open-order reads only. It has no
order-placement, amendment, cancellation, or trading functions.
"""

from __future__ import annotations

import argparse
import contextlib
import datetime as dt
import fcntl
import getpass
import hashlib
import json
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

KEYCHAIN_ACCOUNT = "questrade"
REFRESH_TOKEN_SERVICE = "portfolio-demo-questrade-refresh-token"
API_SERVER_SERVICE = "portfolio-demo-questrade-api-server"
ACCESS_SESSION_SERVICE = "portfolio-demo-questrade-access-session"

# Reuse a still-valid access token instead of rotating the single-use refresh token on EVERY run.
# Questrade access tokens last ~1800s; refresh a couple minutes early so a request never rides an
# expiring token. This cuts refresh-token rotations from per-run to ~twice a day, shrinking the
# window where a crash/sleep/kill between redeem-and-store can orphan the token (the failure that
# bricked the chain).
_ACCESS_TOKEN_SAFETY_SECONDS = 120

LOGIN_URL = "https://login.questrade.com/oauth2/token"
PRACTICE_LOGIN_URL = "https://practicelogin.questrade.com/oauth2/token"
DEFAULT_HEADERS = {
    "Accept": "application/json",
    "User-Agent": "questrade-portfolio-demo/1.0",
}


class QuestradeError(RuntimeError):
    """Raised when Questrade or local credential handling fails."""


class QuestradeNetworkError(QuestradeError):
    """Raised for a TRANSIENT network/timeout failure (connection reset, read timeout) — as opposed
    to a definite HTTP rejection. Retryable: a read timeout during token redemption usually means
    the request never completed server-side, so retrying recovers the (still-valid) refresh token."""


class QuestradeHttpError(QuestradeError):
    """Raised for an HTTP response that is not successful."""

    def __init__(self, status_code: int, url: str, body: str) -> None:
        super().__init__(f"HTTP {status_code} from {url}: {body}")
        self.status_code = status_code
        self.url = url
        self.body = body


def utc_now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def iso_timestamp(value: dt.datetime) -> str:
    return value.replace(microsecond=0).isoformat().replace("+00:00", "Z")


def keychain_get_raw(service: str) -> str | None:
    result = subprocess.run(
        ["security", "find-generic-password", "-a", KEYCHAIN_ACCOUNT, "-s", service, "-w"],
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return None
    return result.stdout.rstrip("\n")


def keychain_get(service: str) -> str | None:
    return keychain_get_raw(service)


def credential_get_with_source(service: str) -> tuple[str | None, str | None]:
    value = keychain_get_raw(service)
    if value:
        return value, "keychain"
    return None, None


def keychain_set_raw(service: str, value: str) -> None:
    result = subprocess.run(
        [
            "security",
            "add-generic-password",
            "-U",
            "-a",
            KEYCHAIN_ACCOUNT,
            "-s",
            service,
            "-w",
            value,
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        message = result.stderr.strip() or "security add-generic-password failed"
        raise QuestradeError(message)


def keychain_set(service: str, value: str) -> None:
    keychain_set_raw(service, value)
    if keychain_get_raw(service) != value:
        raise QuestradeError("macOS Keychain did not return the value after writing it")


def request_json(
    url: str,
    *,
    method: str = "GET",
    headers: dict[str, str] | None = None,
    data: dict[str, str] | None = None,
    error_url: str | None = None,
) -> dict[str, Any]:
    encoded_data = None
    request_headers = dict(DEFAULT_HEADERS)
    request_headers.update(headers or {})
    if data is not None:
        encoded_data = urllib.parse.urlencode(data).encode("utf-8")
        request_headers["Content-Type"] = "application/x-www-form-urlencoded"

    request = urllib.request.Request(
        url,
        data=encoded_data,
        headers=request_headers,
        method=method,
    )

    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            payload = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise QuestradeHttpError(exc.code, error_url or url, body) from exc
    except (socket.timeout, TimeoutError) as exc:   # read timeout -> transient, retryable
        raise QuestradeNetworkError(f"Timeout calling {url}: {exc}") from exc
    except urllib.error.URLError as exc:   # connection reset / DNS / refused -> transient, retryable
        raise QuestradeNetworkError(f"Network error calling {url}: {exc.reason}") from exc

    try:
        decoded = json.loads(payload)
    except json.JSONDecodeError as exc:
        raise QuestradeError(f"Invalid JSON from {url}") from exc

    if not isinstance(decoded, dict):
        raise QuestradeError(f"Unexpected JSON response from {url}")
    return decoded


def token_url(practice: bool) -> str:
    return PRACTICE_LOGIN_URL if practice else LOGIN_URL


def redeem_refresh_token_post(refresh_token: str, *, practice: bool) -> dict[str, Any]:
    return request_json(
        token_url(practice),
        method="POST",
        data={"grant_type": "refresh_token", "refresh_token": refresh_token},
        error_url=token_url(practice),
    )


def redeem_refresh_token_get(refresh_token: str, *, practice: bool) -> dict[str, Any]:
    query = urllib.parse.urlencode(
        {"grant_type": "refresh_token", "refresh_token": refresh_token}
    )
    url = f"{token_url(practice)}?{query}"
    return request_json(url, method="GET", error_url=token_url(practice))


_REDEEM_NET_RETRIES = 2   # a single-use refresh token bricks if a network blip loses the rotation


def _redeem_backoff(attempt: int) -> None:
    time.sleep(min(1.5 * (attempt + 1), 5.0))


def _redeem_once(token: str, *, practice: bool) -> dict[str, Any]:
    """One redemption attempt: POST, falling back to the documented URL-style GET on a 400/403/405
    login-edge rejection. A 400 on BOTH is a definite rejection (bad/expired token)."""
    try:
        response = redeem_refresh_token_post(token, practice=practice)
    except QuestradeHttpError as post_error:
        can_fallback = post_error.status_code in {400, 403, 405}
        if not can_fallback:
            raise
        try:
            response = redeem_refresh_token_get(token, practice=practice)
        except QuestradeHttpError as get_error:
            raise QuestradeError(
                "Questrade rejected the token using both POST and URL-style redemption. "
                "Generate a fresh manual authorization token, verify live vs. practice mode, "
                "and paste only the token text. "
                f"POST status: {post_error.status_code}; GET status: {get_error.status_code}."
            ) from get_error
    required = {"access_token", "refresh_token", "api_server"}
    missing = sorted(required.difference(response))
    if missing:
        raise QuestradeError(f"Token response missing required field(s): {', '.join(missing)}")
    return response


def redeem_refresh_token(refresh_token: str, *, practice: bool) -> dict[str, Any]:
    """Redeem the single-use refresh token, RETRYING on a transient network/timeout error. This is
    the token-brick preventer: a read timeout usually means the request never reached Questrade, so
    the old token wasn't consumed and a fresh attempt recovers it. A definite HTTP rejection (400/
    403) is NOT retried — it falls through to the 'generate a fresh token' error immediately."""
    token = refresh_token.strip()
    if not token:
        raise QuestradeError("No refresh token provided")
    for attempt in range(_REDEEM_NET_RETRIES + 1):
        try:
            return _redeem_once(token, practice=practice)
        except QuestradeNetworkError:
            if attempt >= _REDEEM_NET_RETRIES:
                raise
            _redeem_backoff(attempt)
    raise QuestradeNetworkError("token redemption exhausted network retries")  # unreachable


def normalize_api_server(api_server: str) -> str:
    base = api_server.rstrip("/")
    if not base.endswith("/v1"):
        base = f"{base}/v1"
    return base


def api_get(api_server: str, access_token: str, path: str) -> dict[str, Any]:
    base = normalize_api_server(api_server)
    url = f"{base}/{path.lstrip('/')}"
    return request_json(url, headers={"Authorization": f"Bearer {access_token}"})


def account_ref(account_number: str) -> str:
    digest = hashlib.sha256(account_number.encode("utf-8")).hexdigest()
    return digest[:16]


def sanitize_account(account: dict[str, Any]) -> dict[str, Any]:
    sanitized = {key: value for key, value in account.items() if key != "number"}
    number = str(account.get("number", ""))
    sanitized["accountRef"] = account_ref(number) if number else None
    return sanitized


def _load_cached_session() -> dict[str, Any] | None:
    raw = keychain_get_raw(ACCESS_SESSION_SERVICE)
    if not raw:
        return None
    try:
        obj = json.loads(raw)
    except (ValueError, TypeError):
        return None
    return obj if isinstance(obj, dict) else None


def _store_cached_session(access_token: str, api_server: str, expires_at_iso: str) -> None:
    """Cache the access token + expiry in Keychain ONLY (never the env file — it's short-lived)."""
    keychain_set_raw(ACCESS_SESSION_SERVICE, json.dumps(
        {"access_token": access_token, "api_server": api_server, "expires_at": expires_at_iso}))


def _session_valid(session: dict[str, Any] | None, now: dt.datetime) -> bool:
    """True if the cached access token is present and not within the safety buffer of expiry."""
    if not isinstance(session, dict) or not session.get("access_token") or not session.get("api_server"):
        return False
    try:
        exp = dt.datetime.fromisoformat(str(session.get("expires_at")).replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return False
    if exp.tzinfo is None:
        exp = exp.replace(tzinfo=dt.timezone.utc)
    return now < exp - dt.timedelta(seconds=_ACCESS_TOKEN_SAFETY_SECONDS)


@contextlib.contextmanager
def _rotation_lock():
    """Single-flight guard so two concurrent runs can't both redeem the single-use refresh token
    (the second would fail + could clobber the rotation). Best-effort: if the OS lock is
    unavailable, proceed rather than block the daily run."""
    lock_path = Path(tempfile.gettempdir()) / "portfolio-demo-questrade-refresh.lock"
    fh = None
    try:
        fh = open(lock_path, "w")
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
    except OSError:
        if fh is not None:
            fh.close()
        fh = None
    try:
        yield
    finally:
        if fh is not None:
            try:
                fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
            finally:
                fh.close()


def refresh_session(*, practice: bool, now: dt.datetime | None = None,
                    force: bool = False) -> dict[str, Any]:
    """Return a usable session ({access_token, api_server, ...}). Reuses a still-valid cached
    access token (no rotation); only redeems the single-use refresh token when the access token
    is missing/expired or `force` is set."""
    now = now or utc_now()
    if not force:
        cached = _load_cached_session()
        if _session_valid(cached, now):
            return {"access_token": cached["access_token"], "api_server": cached["api_server"],
                    "cached": True}

    with _rotation_lock():
        # double-checked: another process may have just rotated + cached while we waited for the lock
        if not force:
            cached = _load_cached_session()
            if _session_valid(cached, now):
                return {"access_token": cached["access_token"], "api_server": cached["api_server"],
                        "cached": True}

        refresh_token, _source = credential_get_with_source(REFRESH_TOKEN_SERVICE)
        if not refresh_token:
            raise QuestradeError(
                "No Questrade refresh token found in macOS Keychain. "
                "Run init first with a fresh token."
            )

        token_response = redeem_refresh_token(refresh_token, practice=practice)
        # ATOMIC ORDER: persist the rotated refresh token (verified by keychain_set) BEFORE caching
        # the access token — so a failure can never leave us with a cached access token but a lost
        # refresh token. keychain_set writes to Keychain and verifies the write.
        keychain_set(REFRESH_TOKEN_SERVICE, str(token_response["refresh_token"]))
        keychain_set(API_SERVER_SERVICE, str(token_response["api_server"]))
        expires_in = int(token_response.get("expires_in") or 1800)
        expires_at = iso_timestamp(now + dt.timedelta(seconds=expires_in))
        try:
            _store_cached_session(str(token_response["access_token"]),
                                  str(token_response["api_server"]), expires_at)
        except QuestradeError:
            pass  # caching is best-effort; a failed cache just means the next run rotates again
        return token_response


def command_init(args: argparse.Namespace) -> int:
    print("Paste a fresh Questrade manual authorization token.")
    print("Input is hidden. The token will be redeemed once and stored in macOS Keychain.")
    manual_token = getpass.getpass("Questrade token: ")
    token_response = redeem_refresh_token(manual_token, practice=args.practice)
    keychain_set(REFRESH_TOKEN_SERVICE, str(token_response["refresh_token"]))
    keychain_set(API_SERVER_SERVICE, str(token_response["api_server"]))
    expires_in = token_response.get("expires_in", "unknown")
    print("Stored Questrade refresh token in macOS Keychain.")
    print(f"API server: {token_response['api_server']}")
    print(f"Access token expiry: {expires_in} seconds")
    return 0


_OPEN_ORDER_LOOKBACK_DAYS = 90  # Questrade GET orders defaults to TODAY; look back so open
                                # GTD orders placed on prior days are still returned.


def _open_orders_path(account_number: str, now: dt.datetime) -> str:
    """Build the Questrade open-orders query. stateFilter=Open alone returns only the current
    trading day's orders, so a startTime lookback is required to capture every live order."""
    # floor the start to midnight so an order placed earlier on the boundary day isn't missed
    start = (now - dt.timedelta(days=_OPEN_ORDER_LOOKBACK_DAYS)).replace(
        hour=0, minute=0, second=0, microsecond=0)
    params = urllib.parse.urlencode({
        "stateFilter": "Open",
        "startTime": iso_timestamp(start),
        "endTime": iso_timestamp(now),
    })
    return f"accounts/{account_number}/orders?{params}"


def _session_fetch_with_retry(practice: bool, fetch):
    """Get a session (possibly a cached access token) and run fetch(session). If the access token
    is REJECTED (HTTP 401), force a fresh rotation and retry once. This makes the access-token
    cache always safe: a stale cached token (e.g. invalidated server-side by a later rotation) is
    detected by the 401 and recovered, instead of hard-failing the whole run."""
    session = refresh_session(practice=practice)
    try:
        return fetch(session)
    except QuestradeHttpError as exc:
        if exc.status_code != 401:
            raise
        session = refresh_session(practice=practice, force=True)
        return fetch(session)


def command_snapshot(args: argparse.Namespace) -> int:
    generated_at = utc_now()

    def fetch(session: dict[str, Any]) -> list[dict[str, Any]]:
        return _fetch_accounts(str(session["access_token"]), str(session["api_server"]),
                               generated_at)

    snapshot_accounts = _session_fetch_with_retry(args.practice, fetch)

    snapshot = {
        "generatedAt": iso_timestamp(generated_at),
        "source": "questrade",
        "environment": "practice" if args.practice else "live",
        "accounts": snapshot_accounts,
    }

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    filename = f"questrade_snapshot_{generated_at.strftime('%Y%m%dT%H%M%SZ')}.json"
    output_path = output_dir / filename
    output_path.write_text(json.dumps(snapshot, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    total_positions = 0
    for item in snapshot_accounts:
        positions = item.get("positions", {}).get("positions", [])
        if isinstance(positions, list):
            total_positions += len(positions)

    print(f"Wrote snapshot: {output_path}")
    print(f"Accounts: {len(snapshot_accounts)}")
    print(f"Positions: {total_positions}")
    return 0


def _fetch_accounts(access_token: str, api_server: str,
                    generated_at: dt.datetime) -> list[dict[str, Any]]:
    accounts_response = api_get(api_server, access_token, "accounts")
    accounts = accounts_response.get("accounts", [])
    if not isinstance(accounts, list):
        raise QuestradeError("Unexpected accounts response")

    snapshot_accounts: list[dict[str, Any]] = []
    for account in accounts:
        if not isinstance(account, dict):
            continue
        account_number = str(account.get("number", ""))
        if not account_number:
            continue

        balances = api_get(api_server, access_token, f"accounts/{account_number}/balances")
        positions = api_get(api_server, access_token, f"accounts/{account_number}/positions")
        try:  # working orders are supplementary; a missing scope / transient error must not kill the snapshot
            orders = api_get(api_server, access_token,
                             _open_orders_path(account_number, generated_at))
        except QuestradeError as exc:  # network/HTTP/scope only — a code bug here still surfaces
            err = f"HTTP {exc.status_code}" if isinstance(exc, QuestradeHttpError) else "fetch error"
            print(f"⚠️ open-orders fetch failed for account {account_ref(account_number)} ({err}); "
                  "continuing without it.", file=sys.stderr)   # account# + URL never logged
            orders = {"orders": [], "error": err}   # sanitized; tags dedup unavailable downstream

        snapshot_accounts.append(
            {
                "account": sanitize_account(account),
                "balances": balances,
                "positions": positions,
                "orders": orders,
            }
        )

    return snapshot_accounts


def command_status(args: argparse.Namespace) -> int:
    refresh_token = keychain_get(REFRESH_TOKEN_SERVICE)
    api_server = keychain_get(API_SERVER_SERVICE)
    print(f"Refresh token available: {'yes' if refresh_token else 'no'}")
    print(f"API server available: {api_server or 'no'}")
    print(f"Refresh token in Keychain: {'yes' if keychain_get_raw(REFRESH_TOKEN_SERVICE) else 'no'}")
    print(f"API server in Keychain: {keychain_get_raw(API_SERVER_SERVICE) or 'no'}")
    cached = _load_cached_session()
    if cached and cached.get("expires_at"):
        valid = "valid" if _session_valid(cached, utc_now()) else "expired"
        print(f"Cached access token: {valid} (expires {cached.get('expires_at')})")
    else:
        print("Cached access token: none (next run will rotate the refresh token)")
    print(f"Environment: {'practice' if args.practice else 'live'}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Create read-only Questrade portfolio snapshots using macOS Keychain."
    )
    parser.add_argument(
        "--practice",
        action="store_true",
        help="Use Questrade practice login endpoint.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    init_parser = subparsers.add_parser("init", help="Redeem and store a fresh manual token.")
    init_parser.set_defaults(func=command_init)

    snapshot_parser = subparsers.add_parser("snapshot", help="Write a local portfolio snapshot.")
    snapshot_parser.add_argument(
        "--output-dir",
        default="private/snapshots",
        help="Directory for sensitive local snapshot JSON files.",
    )
    snapshot_parser.set_defaults(func=command_snapshot)

    status_parser = subparsers.add_parser("status", help="Show whether local credentials exist.")
    status_parser.set_defaults(func=command_status)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.func(args))
    except QuestradeError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\nInterrupted.", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
