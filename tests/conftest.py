"""Shared test fixtures.

The API's auth is fail-closed: with no `ASSESS_API_TOKEN` configured, every
authenticated route returns 503 rather than silently serving an endpoint that
executes submitted code. The suite exercises the routes themselves, not auth, so
it takes the explicit opt-out — the same one a developer would.

`tests/test_api.py` covers the auth behaviour directly by overriding this.
"""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _auth_disabled_by_default(monkeypatch):
    monkeypatch.delenv("ASSESS_API_TOKEN", raising=False)
    monkeypatch.setenv("ASSESS_AUTH_DISABLED", "1")
