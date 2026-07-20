"""HMAC body-signing for the platform <-> agent link.

Signs the *exact* request-body bytes with HMAC-SHA256 under a shared secret that
is **never transmitted** (unlike the bearer token, which rides in a header in the
clear and so cannot double as the key). A valid signature proves the sender holds
the secret and that the body wasn't altered in flight. A timestamp is folded into
the signed message and checked against a tolerance window, so a captured request
can't be replayed indefinitely.

Header format (Stripe-style): `X-Assess-Signature: t=<unix>,v1=<hex-hmac-sha256>`
Signed message:                `f"{t}.".encode() + body`

This module is mirrored **verbatim** in the companion repo (agent <-> platform);
the two sides must agree byte for byte. `scripts/checkpoints.sh` fails the push
if they diverge.
"""

from __future__ import annotations

import hashlib
import hmac
import time

SIGNATURE_HEADER = "X-Assess-Signature"
# Reject a signature whose timestamp is more than this far from now (either way):
# clock skew is allowed within it, replays beyond it are not.
DEFAULT_TOLERANCE_S = 300


def sign(secret: str, body: bytes, *, timestamp: int | None = None) -> str:
    """Return the `t=<ts>,v1=<hex>` value for `X-Assess-Signature` over `body`."""
    ts = int(time.time()) if timestamp is None else timestamp
    digest = hmac.new(secret.encode(), _signed_message(ts, body), hashlib.sha256).hexdigest()
    return f"t={ts},v1={digest}"


def verify(
    secret: str, body: bytes, header: str | None, *, tolerance_s: int = DEFAULT_TOLERANCE_S
) -> bool:
    """True iff `header` is a fresh, valid signature of `body` under `secret`.

    Fails closed on anything off: missing/malformed header, a timestamp outside
    the tolerance window, or a digest that doesn't match (compared in constant
    time to avoid leaking where it diverged).
    """
    if not header:
        return False
    ts, provided = _parse(header)
    if ts is None or provided is None:
        return False
    if abs(int(time.time()) - ts) > tolerance_s:
        return False
    expected = hmac.new(secret.encode(), _signed_message(ts, body), hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, provided)


def _signed_message(ts: int, body: bytes) -> bytes:
    # The timestamp is inside the MAC input, so it can't be altered independently
    # of the signature.
    return f"{ts}.".encode() + body


def _parse(header: str) -> tuple[int | None, str | None]:
    ts: int | None = None
    sig: str | None = None
    for part in header.split(","):
        key, sep, value = part.strip().partition("=")
        if not sep:
            continue
        if key == "t":
            try:
                ts = int(value)
            except ValueError:
                return None, None
        elif key == "v1":
            sig = value
    return ts, sig
