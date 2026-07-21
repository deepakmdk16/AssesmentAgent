"""Provider-selection logic (llm.provider) and the local-call request seam.

The conftest autouse fixture pins ASSESS_LLM_PROVIDER=anthropic for the rest of
the suite; these tests clear it to exercise the real auto-fallback default.
"""

from __future__ import annotations

import contextlib
import json

from assessment_agent import llm
from assessment_agent.llm import ollama_chat, ollama_max_tokens, provider


def _capture_ollama(monkeypatch, reply: str = '{"ok": true}'):
    """Stub urlopen and hand back the request body ollama_chat would have sent."""
    sent: dict = {}

    @contextlib.contextmanager
    def _fake_urlopen(req, timeout=None):
        sent.update(json.loads(req.data.decode()))

        class _Resp:
            def read(self):
                return json.dumps(
                    {"message": {"content": reply}, "prompt_eval_count": 1, "eval_count": 2}
                ).encode()

        yield _Resp()

    monkeypatch.setattr(llm.urllib.request, "urlopen", _fake_urlopen)
    return sent


def test_auto_default_uses_ollama_without_key(monkeypatch):
    """No explicit provider and no Anthropic key → the local model, not offline."""
    monkeypatch.delenv("ASSESS_LLM_PROVIDER", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    assert provider() == "ollama"


def test_auto_default_uses_anthropic_with_key(monkeypatch):
    """No explicit provider but a key present → Anthropic."""
    monkeypatch.delenv("ASSESS_LLM_PROVIDER", raising=False)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    assert provider() == "anthropic"


def test_explicit_ollama_wins_over_key(monkeypatch):
    """An explicit ollama choice is honoured even when a key is set."""
    monkeypatch.setenv("ASSESS_LLM_PROVIDER", "ollama")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    assert provider() == "ollama"


def test_explicit_anthropic_without_key(monkeypatch):
    """Explicitly selecting Anthropic without a key stays 'anthropic' (the surface
    dispatch then falls through to the offline heuristic), never 'ollama'."""
    monkeypatch.setenv("ASSESS_LLM_PROVIDER", "anthropic")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    assert provider() == "anthropic"


def test_unknown_value_is_safe(monkeypatch):
    """An unrecognised value degrades to anthropic rather than raising."""
    monkeypatch.setenv("ASSESS_LLM_PROVIDER", "bogus")
    assert provider() == "anthropic"


def test_local_call_is_greedy_and_capped_by_default(monkeypatch):
    """Every local call carries an output ceiling. Without one, greedy decoding
    that falls into a repetition loop runs until the request times out — observed
    as a 17-minute hang on the adversarial surface."""
    sent = _capture_ollama(monkeypatch)
    ollama_chat(model="m", system="s", user="u")
    assert sent["options"]["temperature"] == 0.0
    assert sent["options"]["num_predict"] == 8192


def test_caller_can_raise_temperature_off_greedy(monkeypatch):
    """A surface that emits long repetitive structure opts out of greedy decoding
    (adversarial does); the judge keeps temperature 0."""
    sent = _capture_ollama(monkeypatch)
    ollama_chat(model="m", system="s", user="u", temperature=0.3)
    assert sent["options"]["temperature"] == 0.3


def test_max_tokens_env_override_and_bad_values(monkeypatch):
    monkeypatch.setenv("ASSESS_OLLAMA_MAX_TOKENS", "256")
    assert ollama_max_tokens() == 256
    # A non-numeric or non-positive value must fall back, never raise or uncap.
    monkeypatch.setenv("ASSESS_OLLAMA_MAX_TOKENS", "not-a-number")
    assert ollama_max_tokens() == 8192
    monkeypatch.setenv("ASSESS_OLLAMA_MAX_TOKENS", "0")
    assert ollama_max_tokens() == 8192
