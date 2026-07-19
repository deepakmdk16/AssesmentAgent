"""A tiny in-process rate limiter for the HTTP API.

Fixed-window counters keyed by (bucket, client-ip), kept in memory. Enough to
blunt abuse of the code-execution and LLM-cost endpoints on this single-process
worker; a multi-process deploy would swap this for Redis. Enforcement is
opt-out: a `max_requests` of 0 disables the bucket.

Ported from the platform's `ratelimit.py` — the two are the same shape on
purpose, so the proxy-trust reasoning holds identically on both sides.
"""

from __future__ import annotations

import os
import threading
import time

from fastapi import HTTPException, Request

# Above this many tracked (bucket, client) keys, drop the ones whose hits have all
# aged out. Keys are only pruned lazily on their own next hit, so without this a
# public endpoint — where the set of client IPs is unbounded — grows the dict
# forever with entries for callers never seen again.
_MAX_TRACKED_KEYS = 10_000

# Behind a proxy the socket peer is the PROXY for every request; set this so the
# limiter keys on the forwarded client address instead (see client_ip).
_TRUST_PROXY_HEADERS = os.environ.get("ASSESS_TRUST_PROXY_HEADERS", "false").lower() == "true"


class RateLimiter:
    def __init__(self) -> None:
        self._hits: dict[tuple[str, str], list[float]] = {}
        self._lock = threading.Lock()

    def _purge_expired(self, cutoff: float) -> None:
        """Drop keys with no hits left inside the window. Caller must hold the lock."""
        for key in [k for k, hits in self._hits.items() if not any(t > cutoff for t in hits)]:
            del self._hits[key]

    def check(self, bucket: str, client: str, max_requests: int, window_s: int) -> None:
        """Record a hit for (bucket, client); raise 429 if over the limit."""
        if max_requests <= 0:
            return
        now = time.monotonic()
        cutoff = now - window_s
        key = (bucket, client)
        with self._lock:
            if len(self._hits) > _MAX_TRACKED_KEYS:
                self._purge_expired(cutoff)
            hits = [t for t in self._hits.get(key, []) if t > cutoff]
            if len(hits) >= max_requests:
                raise HTTPException(
                    status_code=429, detail="too many requests; slow down and retry shortly."
                )
            hits.append(now)
            self._hits[key] = hits

    def reset(self) -> None:
        with self._lock:
            self._hits.clear()


limiter = RateLimiter()


def client_ip(request: Request) -> str:
    """The address to rate-limit this request against.

    Direct (the default): the socket peer. Behind a proxy that peer is the PROXY
    for every request, so every caller shares one bucket and the first few exhaust
    the limit for everybody — hence `ASSESS_TRUST_PROXY_HEADERS`.

    When trusted, take the RIGHTMOST X-Forwarded-For entry, not the leftmost: a
    proxy appends the peer it actually saw, so the rightmost hop is the only
    address your own infrastructure vouches for; anything to its left arrived from
    the client and can be forged to win a fresh bucket per request. Assumes
    exactly one trusted proxy — a chain (CDN in front of an LB) moves the trusted
    hop one place left per proxy; revisit then rather than guessing a depth now.
    """
    if _TRUST_PROXY_HEADERS:
        forwarded = request.headers.get("x-forwarded-for")
        if forwarded:
            return forwarded.split(",")[-1].strip()
    return request.client.host if request.client else "unknown"
