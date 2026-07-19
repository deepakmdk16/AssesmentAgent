"""HMAC body-signing — the security-critical core, so exercised thoroughly.

Mirrored verbatim in the platform repo; if these ever diverge the two sides stop
agreeing and every signed request 401s."""

from __future__ import annotations

import time

from assessment_agent import signing

SECRET = "super-secret-signing-key"
BODY = b'{"job_id":"abc","code":"print(1)"}'


def test_sign_then_verify_roundtrips() -> None:
    header = signing.sign(SECRET, BODY)
    assert header.startswith("t=")
    assert ",v1=" in header
    assert signing.verify(SECRET, BODY, header) is True


def test_verify_rejects_a_tampered_body() -> None:
    header = signing.sign(SECRET, BODY)
    assert signing.verify(SECRET, BODY + b" ", header) is False


def test_verify_rejects_the_wrong_secret() -> None:
    header = signing.sign(SECRET, BODY)
    assert signing.verify("different-secret", BODY, header) is False


def test_verify_rejects_a_stale_timestamp() -> None:
    old = int(time.time()) - 3600
    header = signing.sign(SECRET, BODY, timestamp=old)
    assert signing.verify(SECRET, BODY, header) is False
    # ...but a generous tolerance would accept it.
    assert signing.verify(SECRET, BODY, header, tolerance_s=7200) is True


def test_verify_rejects_a_future_timestamp_beyond_tolerance() -> None:
    future = int(time.time()) + 3600
    header = signing.sign(SECRET, BODY, timestamp=future)
    assert signing.verify(SECRET, BODY, header) is False


def test_verify_rejects_missing_or_malformed_headers() -> None:
    assert signing.verify(SECRET, BODY, None) is False
    assert signing.verify(SECRET, BODY, "") is False
    assert signing.verify(SECRET, BODY, "garbage") is False
    assert signing.verify(SECRET, BODY, "t=notanumber,v1=abc") is False
    assert signing.verify(SECRET, BODY, "v1=abc") is False  # no timestamp
    assert signing.verify(SECRET, BODY, f"t={int(time.time())}") is False  # no digest


def test_tampering_with_the_timestamp_invalidates_it() -> None:
    # The timestamp is inside the MAC input, so bumping a stale signature's `t` to
    # dodge the freshness window breaks the digest rather than extending its life.
    header = signing.sign(SECRET, BODY, timestamp=int(time.time()) - 3600)
    _, _, rest = header.partition(",")  # rest == "v1=<digest over the old ts>"
    forged = f"t={int(time.time())},{rest}"  # fresh ts, but the old digest
    assert signing.verify(SECRET, BODY, forged) is False
