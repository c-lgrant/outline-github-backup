import hashlib
import hmac

import pytest

from outline_backup.core.signature import SignatureError, verify_signature

SECRET = "whsec_test"
BODY = b'{"event":"documents.update"}'


def make_header(ts_ms: int, secret: str = SECRET, body: bytes = BODY) -> str:
    sig = hmac.new(secret.encode(), f"{ts_ms}.".encode() + body, hashlib.sha256).hexdigest()
    return f"t={ts_ms},s={sig}"


def test_valid_signature_passes():
    verify_signature(make_header(1_700_000_000_000), BODY, SECRET, now=1_700_000_000.0)


def test_missing_header_rejected():
    with pytest.raises(SignatureError):
        verify_signature(None, BODY, SECRET)


def test_tampered_body_rejected():
    with pytest.raises(SignatureError):
        verify_signature(make_header(1_700_000_000_000), b"{}", SECRET, now=1_700_000_000.0)


def test_wrong_secret_rejected():
    with pytest.raises(SignatureError):
        verify_signature(make_header(1_700_000_000_000, secret="other"), BODY, SECRET, now=1_700_000_000.0)


def test_stale_timestamp_rejected():
    with pytest.raises(SignatureError):
        verify_signature(make_header(1_700_000_000_000), BODY, SECRET, now=1_700_000_000.0 + 600)


def test_malformed_header_rejected():
    with pytest.raises(SignatureError):
        verify_signature("garbage", BODY, SECRET)
