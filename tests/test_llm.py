"""Provider-selection logic (llm.provider) and the local-call request seam.

The conftest autouse fixture pins ASSESS_LLM_PROVIDER=anthropic for the rest of
the suite; these tests clear it to exercise the real auto-fallback default.
"""

from __future__ import annotations

import contextlib
import json
import urllib.error

import pytest

from assessment_agent import llm
from assessment_agent.llm import ollama_chat, ollama_max_tokens, provider

TIMEOUT = "raise-timeout"


def _capture_ollama(monkeypatch, replies: list[dict | str] | None = None):
    """Stub urlopen; hand back the list of request bodies ollama_chat sent.

    ``replies`` are served in order (the last one repeats); a dict is merged over
    a complete-reply default so a test states only what it cares about, and the
    ``TIMEOUT`` sentinel raises the client-timeout error instead of replying.
    """
    sent: list[dict] = []
    queue: list[dict | str] = list(replies or [{}])

    @contextlib.contextmanager
    def _fake_urlopen(req, timeout=None):
        sent.append(json.loads(req.data.decode()))
        reply = queue.pop(0) if len(queue) > 1 else queue[0]
        if reply == TIMEOUT:
            raise urllib.error.URLError(TimeoutError("timed out"))
        assert isinstance(reply, dict)
        payload = {
            "message": {"content": '{"ok": true}'},
            "prompt_eval_count": 1,
            "eval_count": 2,
            "done_reason": "stop",
        }
        payload.update(reply)

        class _Resp:
            def read(self):
                return json.dumps(payload).encode()

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
    assert sent[0]["options"]["temperature"] == 0.0
    assert sent[0]["options"]["num_predict"] == 8192
    assert len(sent) == 1  # a complete reply is never retried


def test_caller_can_raise_temperature_off_greedy(monkeypatch):
    """A surface that emits long repetitive structure opts out of greedy decoding
    (adversarial does); the judge keeps temperature 0."""
    sent = _capture_ollama(monkeypatch)
    ollama_chat(model="m", system="s", user="u", temperature=0.3)
    assert sent[0]["options"]["temperature"] == 0.3


def test_truncated_reply_retried_hotter_with_repeat_penalty(monkeypatch):
    """done_reason "length" = the reply was cut off at num_predict (a repetition
    loop inside a string field — the text is unparseable by construction). It must
    be retried exactly once, hotter AND with a repeat penalty: same-temperature
    sampling can replay the identical loop, and measured on qwen3-coder:30b the
    penalty is what actually breaks it (7/7 recovered vs 2/4 hotter-only). The
    first call must NOT carry the penalty — it would perturb the baselined happy
    path. Tokens from both attempts count toward usage."""
    sent = _capture_ollama(
        monkeypatch,
        [
            {"message": {"content": '{"cut": '}, "done_reason": "length", "eval_count": 8192},
            {"message": {"content": '{"ok": true}'}, "eval_count": 400},
        ],
    )
    text, _, out_tokens = ollama_chat(model="m", system="s", user="u", temperature=0.3)
    assert text == '{"ok": true}'
    assert len(sent) == 2
    assert "repeat_penalty" not in sent[0]["options"]
    assert sent[1]["options"]["temperature"] == 0.6
    assert sent[1]["options"]["repeat_penalty"] == 1.15
    assert sent[1]["options"]["repeat_last_n"] == 256
    assert out_tokens == 8192 + 400


def test_missing_done_reason_counts_as_incomplete(monkeypatch):
    """Observed live (qwen3-coder:30b): a malformed reply with done_reason absent
    entirely. Anything other than "stop" — including missing — triggers the retry;
    a complete reply always carries "stop"."""
    sent = _capture_ollama(
        monkeypatch,
        [
            {"message": {"content": '{"cut": "11111'}, "done_reason": None},
            {"message": {"content": '{"ok": true}'}},
        ],
    )
    text, _, _ = ollama_chat(model="m", system="s", user="u")
    assert text == '{"ok": true}'
    assert len(sent) == 2


def test_client_timeout_retried_like_truncation(monkeypatch):
    """At the default ASSESS_LLM_TIMEOUT_S a repetition loop hits the client
    timeout before the token ceiling — same failure, different face. It gets the
    same escalated retry (this is the only retry the adversarial surface has)."""
    sent = _capture_ollama(monkeypatch, [TIMEOUT, {"message": {"content": '{"ok": true}'}}])
    text, _, _ = ollama_chat(model="m", system="s", user="u", temperature=0.3)
    assert text == '{"ok": true}'
    assert len(sent) == 2
    assert sent[1]["options"]["repeat_penalty"] == 1.15


def test_twice_incomplete_raises_with_cause(monkeypatch):
    """Two incomplete replies raise a diagnosis (repetition loop / num_predict /
    timeout), never return truncated text — a JSON parse error downstream would
    bury the actual cause. Callers degrade non-fatally like any LLM failure."""
    _capture_ollama(
        monkeypatch,
        [{"message": {"content": '{"cut": '}, "done_reason": "length"}],
    )
    with pytest.raises(RuntimeError, match="done_reason.*length.*num_predict"):
        ollama_chat(model="m", system="s", user="u", temperature=0.3)


def test_timeout_twice_raises_not_propagates(monkeypatch):
    """A timeout on the retry too surfaces as the diagnostic RuntimeError (with
    the timeout chained as cause), so the operator sees the repetition-loop
    explanation rather than a bare socket error."""
    _capture_ollama(monkeypatch, [TIMEOUT])
    with pytest.raises(RuntimeError, match="client timeout.*client timeout"):
        ollama_chat(model="m", system="s", user="u", temperature=0.3)


def test_non_timeout_transport_error_not_retried(monkeypatch):
    """A connection failure (server down, wrong host) is not a repetition loop —
    it propagates untouched, no second call."""
    sent: list[int] = []

    def _refuse(req, timeout=None):
        sent.append(1)
        raise urllib.error.URLError(ConnectionRefusedError("refused"))

    monkeypatch.setattr(llm.urllib.request, "urlopen", _refuse)
    with pytest.raises(urllib.error.URLError):
        ollama_chat(model="m", system="s", user="u")
    assert len(sent) == 1


def test_max_tokens_env_override_and_bad_values(monkeypatch):
    monkeypatch.setenv("ASSESS_OLLAMA_MAX_TOKENS", "256")
    assert ollama_max_tokens() == 256
    # A non-numeric or non-positive value must fall back, never raise or uncap.
    monkeypatch.setenv("ASSESS_OLLAMA_MAX_TOKENS", "not-a-number")
    assert ollama_max_tokens() == 8192
    monkeypatch.setenv("ASSESS_OLLAMA_MAX_TOKENS", "0")
    assert ollama_max_tokens() == 8192
