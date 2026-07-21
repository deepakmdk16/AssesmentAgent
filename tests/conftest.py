"""Shared test fixtures.

The API's auth is fail-closed: with no `ASSESS_API_TOKEN` configured, every
authenticated route returns 503 rather than silently serving an endpoint that
executes submitted code. The suite exercises the routes themselves, not auth, so
it takes the explicit opt-out — the same one a developer would.

`tests/test_api.py` covers the auth behaviour directly by overriding this.
"""

from __future__ import annotations

import pytest

from assessment_agent.ratelimit import limiter


@pytest.fixture(autouse=True)
def _auth_disabled_by_default(monkeypatch):
    monkeypatch.delenv("ASSESS_API_TOKEN", raising=False)
    monkeypatch.setenv("ASSESS_AUTH_DISABLED", "1")


@pytest.fixture(autouse=True)
def _pin_provider_anthropic(monkeypatch):
    """Pin the LLM provider so no test silently reaches a live backend.

    In production `provider()` auto-falls-back to the local Ollama model when
    `ANTHROPIC_API_KEY` is absent. That is exactly what we do NOT want by default
    in the suite — a keyless pipeline test would try to hit a real Ollama server.
    Pinning `anthropic` restores the historical default (no key → offline
    heuristic via the Anthropic branch's fall-through); tests that exercise the
    Ollama path set `ASSESS_LLM_PROVIDER=ollama` themselves, and the auto-default
    itself is covered directly in `test_llm.py`.
    """
    monkeypatch.setenv("ASSESS_LLM_PROVIDER", "anthropic")


@pytest.fixture(autouse=True)
def _reset_rate_limiter():
    """The limiter is a process-global singleton keyed by client IP, and the
    TestClient's IP is constant — so without a reset, hits accumulate across the
    whole suite and later tests would 429. Clear it before each test."""
    limiter.reset()
