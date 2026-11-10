"""PayPal Payouts client (sandbox only). Deterministic, idempotent, no float money math.

Idempotency rules:
- The caller passes a stable sender_batch_id; it is also sent as PayPal-Request-Id (docs: optional, keys kept 30 days).
- Timeouts, connection errors and 5xx are retried up to MAX_RETRIES times with the SAME ids.
- If PayPal rejects the request because the sender_batch_id was already used (docs: rejected within 30 days, error
  includes a HATEOAS link to the original payout), the original batch is fetched and returned instead of failing.
  The exact error name/status of that rejection is NOT documented and was not observed (no smoke_report.json),
  so it is detected by the documented link, and the fetched batch must carry our sender_batch_id.
"""
from __future__ import annotations

import hashlib
import json
import re
import time
from decimal import Decimal

import requests

SANDBOX_BASE_URL = "https://api-m.sandbox.paypal.com"
TERMINAL_BATCH_STATUSES = {"SUCCESS", "DENIED", "CANCELED"}
MAX_RETRIES = 3
TIMEOUT_S = 30
_BATCH_LINK = re.compile(r"/v1/payments/payouts/([A-Za-z0-9]+)/?(?:\?|$)")


class PayPalError(RuntimeError):
    def __init__(self, status: int | None, name: str, message: str, debug_id: str | None, details=None):
        super().__init__(f"PayPal {status} {name}: {message} (debug_id={debug_id})")
        self.status, self.name, self.message, self.debug_id, self.details = status, name, message, debug_id, details

    def to_dict(self) -> dict:
        return dict(status=self.status, name=self.name, message=self.message, debug_id=self.debug_id, details=self.details)


def money(amount: str) -> str:
    """'12.5' -> '12.50'. Input must already be validated (<= 2 decimals); never goes through float."""
    d = Decimal(amount)
    q = d.quantize(Decimal("0.01"))
    if q != d:
        raise ValueError(f"amount {amount!r} has more than 2 decimal places")
    return str(q)


def sender_batch_id(batch_id: str, approved: list[tuple[str, str, str, str]]) -> str:
    """'pg-' + sha256(batch_id + sorted approved (row_id, email, amount, currency))[:24]."""
    canon = json.dumps([batch_id, sorted([list(t) for t in approved])], separators=(",", ":"))
    return "pg-" + hashlib.sha256(canon.encode("utf-8")).hexdigest()[:24]


def _error_from(r) -> PayPalError:
    try:
        body = r.json()
    except ValueError:
        body = {"message": r.text[:500]}
    if not isinstance(body, dict):
        body = {"message": str(body)[:500]}
    return PayPalError(r.status_code, body.get("name") or body.get("error") or "UNKNOWN",
                       body.get("message") or body.get("error_description") or "",
                       body.get("debug_id") or r.headers.get("Paypal-Debug-Id"), body.get("details"))


class PayPalClient:
    def __init__(self, client_id: str, client_secret: str, base_url: str = SANDBOX_BASE_URL,
                 session=None, sleep=time.sleep):
        if base_url != SANDBOX_BASE_URL:
            raise ValueError(f"PAYPAL_BASE_URL must be {SANDBOX_BASE_URL} (sandbox only), got {base_url!r}")
        if not client_id or not client_secret:
            raise ValueError("PAYPAL_CLIENT_ID and PAYPAL_CLIENT_SECRET must be set")
        self._id, self._secret, self.base = client_id, client_secret, base_url
        self.s = session or requests.Session()
        self.sleep = sleep
        self._token, self._token_exp = None, 0.0

    def _access_token(self) -> str:
        if self._token and time.time() < self._token_exp:
            return self._token
        r = self.s.post(self.base + "/v1/oauth2/token", auth=(self._id, self._secret),
                        data={"grant_type": "client_credentials"}, timeout=TIMEOUT_S)
        if r.status_code != 200:
            raise _error_from(r)
        body = r.json()
        self._token = body["access_token"]
        self._token_exp = time.time() + int(body.get("expires_in", 0)) - 60
        return self._token

    def _request(self, method: str, path: str, body=None, request_id: str | None = None):
        """Retries timeouts, connection errors and 5xx with identical body and PayPal-Request-Id."""
        last_exc = None
        for attempt in range(MAX_RETRIES + 1):
            if attempt:
                self.sleep(2 ** (attempt - 1))
            headers = {"Authorization": f"Bearer {self._access_token()}", "Content-Type": "application/json"}
            if request_id:
                headers["PayPal-Request-Id"] = request_id
            try:
                r = self.s.request(method, self.base + path, json=body, headers=headers, timeout=TIMEOUT_S)
            except (requests.Timeout, requests.ConnectionError) as e:
                last_exc = e
                continue
            if r.status_code >= 500:
                last_exc = _error_from(r)
                continue
            return r
        if isinstance(last_exc, PayPalError):
            raise last_exc
        raise PayPalError(None, "NETWORK_ERROR", f"{type(last_exc).__name__}: {last_exc} after {MAX_RETRIES} retries", None)

    def get_batch(self, payout_batch_id: str) -> dict:
        r = self._request("GET", f"/v1/payments/payouts/{payout_batch_id}?total_required=true")
        if r.status_code != 200:
            raise _error_from(r)
        return r.json()

    def create_batch(self, sbid: str, items: list[dict], email_subject: str = "You have a payout") -> dict:
        body = {"sender_batch_header": {"sender_batch_id": sbid, "email_subject": email_subject}, "items": items}
        r = self._request("POST", "/v1/payments/payouts", body, request_id=sbid)
        if r.status_code in (200, 201):
            return r.json()
        err = _error_from(r)
        original = self._original_from_duplicate(r)
        if original is None:
            raise err
        got = self.get_batch(original)
        if got.get("batch_header", {}).get("sender_batch_header", {}).get("sender_batch_id") != sbid:
            raise PayPalError(err.status, "DUPLICATE_LINK_MISMATCH",
                              f"PayPal rejected the batch and linked {original}, which does not carry sender_batch_id {sbid}",
                              err.debug_id, err.details)
        return got

    @staticmethod
    def _original_from_duplicate(r) -> str | None:
        if not 400 <= r.status_code < 500:
            return None
        try:
            body = r.json()
        except ValueError:
            return None
        if not isinstance(body, dict):
            return None
        # Observed in the sandbox (2026-11-10): the link to the original batch is in details[].link[],
        # and the top-level links list is empty. PayPal's docs describe a top-level link, so both are read.
        links = list(body.get("links") or [])
        for d in body.get("details") or []:
            if isinstance(d, dict):
                links += list(d.get("link") or []) + list(d.get("links") or [])
        for link in links:
            m = _BATCH_LINK.search(link.get("href", "")) if isinstance(link, dict) else None
            if m:
                return m.group(1)
        return None
