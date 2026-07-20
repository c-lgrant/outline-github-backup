"""Verification of Outline's Outline-Signature webhook header."""

from __future__ import annotations

import hashlib
import hmac
import time


class SignatureError(Exception):
    """The webhook signature is missing, malformed, wrong, or stale."""


def verify_signature(
    header: str | None,
    body: bytes,
    secret: str,
    *,
    tolerance_seconds: int = 300,
    now: float | None = None,
) -> None:
    if not header:
        raise SignatureError("missing Outline-Signature header")
    parts = dict(p.split("=", 1) for p in header.split(",") if "=" in p)
    ts, sig = parts.get("t"), parts.get("s")
    if not ts or not sig or not ts.isdigit():
        raise SignatureError("malformed Outline-Signature header")
    expected = hmac.new(secret.encode(), f"{ts}.".encode() + body, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, sig):
        raise SignatureError("signature mismatch")
    current = now if now is not None else time.time()
    if abs(current - int(ts) / 1000) > tolerance_seconds:
        raise SignatureError("timestamp outside tolerance window")
