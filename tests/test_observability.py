"""The worker's operational surface (X08): request ids, JSON logs, Sentry
scrubbing, and the counters behind `GET /metrics`.

Fully offline. Nothing here reaches a network, an LLM or a real Sentry project —
`init_sentry` is only ever exercised with the DSN unset, and the scrubber is
tested on a hand-built event rather than through the SDK.
"""

from __future__ import annotations

import json
import logging
import re
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from assessment_agent import api, observability
from assessment_agent.api import app
from assessment_agent.observability import metrics

EXAMPLE = Path(__file__).resolve().parent.parent / "examples" / "sum_of_n.json"
QUESTION = json.loads(EXAMPLE.read_text())

CORRECT_PY = "import sys\nd = sys.stdin.read().split()\nn = int(d[0])\nprint(sum(int(x) for x in d[1 : 1 + n]))\n"


@pytest.fixture(autouse=True)
def _offline(monkeypatch):
    # Same posture as test_api.py: no paid judge, no Sentry, auth left disabled
    # by conftest unless a test opts back in.
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("ASSESS_SENTRY_DSN", raising=False)
    monkeypatch.delenv("CALLBACK_TOKEN", raising=False)


@pytest.fixture(autouse=True)
def _fresh_job_state():
    api._JOBS.clear()
    api._PENDING_CALLBACKS.clear()
    yield
    api._JOBS.clear()
    api._PENDING_CALLBACKS.clear()


@pytest.fixture(autouse=True)
def _fresh_request_id():
    """The correlation id is a ContextVar, and pytest runs every test in the same
    context — so an id adopted by one test would still be current in the next."""
    _clear_request_id()
    yield
    _clear_request_id()


def _clear_request_id() -> None:
    """Back to "outside a request", which is what a fresh process looks like."""
    observability._REQUEST_ID.set(observability.NO_REQUEST_ID)


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


def _job(**extra) -> dict:
    return {
        "question": QUESTION,
        "code": CORRECT_PY,
        "language": "python",
        "candidate": "Jane Doe",
        **extra,
    }


class _FakeResponse:
    def __init__(self, status_code: int = 200, text: str = "") -> None:
        self.status_code = status_code
        self.text = text


def _record(msg: str = "hello", *, exc_info=None, **extra) -> logging.LogRecord:
    record = logging.LogRecord("t.logger", logging.INFO, "f.py", 1, msg, None, exc_info)
    for key, value in extra.items():
        setattr(record, key, value)
    return record


def _samples(text: str) -> dict[str, str]:
    """The non-comment lines of a scrape, as {series: value}."""
    return dict(
        line.split(" ", 1) for line in text.splitlines() if line and not line.startswith("#")
    )


# --------------------------------------------------------------------------- #
# Request ids                                                                   #
# --------------------------------------------------------------------------- #


def test_adopt_request_id_takes_a_well_formed_inbound_id():
    assert observability.adopt_request_id("sub-42_AZ") == "sub-42_AZ"
    assert observability.current_request_id() == "sub-42_AZ"


@pytest.mark.parametrize("bad", ["", None, "has space", "line\nbreak", "x" * 65, "semi;colon"])
def test_adopt_request_id_replaces_a_malformed_one_rather_than_raising(bad):
    # A proxy mangling a debugging header must never fail the grade.
    minted = observability.adopt_request_id(bad)
    assert minted != bad
    assert re.fullmatch(r"[0-9a-f]{16}", minted)
    assert observability.current_request_id() == minted


def test_current_request_id_is_a_dash_outside_a_request():
    assert observability.current_request_id() == observability.NO_REQUEST_ID


def test_middleware_stamps_a_minted_id_on_the_response(client):
    resp = client.get("/health")
    assert resp.status_code == 200
    assert re.fullmatch(r"[0-9a-f]{16}", resp.headers[observability.REQUEST_ID_HEADER])


def test_middleware_echoes_a_well_formed_inbound_id(client):
    resp = client.get("/health", headers={observability.REQUEST_ID_HEADER: "platform-req-7"})
    assert resp.headers[observability.REQUEST_ID_HEADER] == "platform-req-7"


def test_middleware_replaces_a_hostile_inbound_id(client):
    hostile = 'evil\nX-Injected: yes"'
    resp = client.get("/health", headers={observability.REQUEST_ID_HEADER: hostile})
    echoed = resp.headers[observability.REQUEST_ID_HEADER]
    assert echoed != hostile
    assert re.fullmatch(r"[0-9a-f]{16}", echoed)


# --------------------------------------------------------------------------- #
# Logging                                                                       #
# --------------------------------------------------------------------------- #


def test_json_formatter_emits_the_fixed_keys():
    observability.adopt_request_id("req-1")
    record = _record("job done", request_id=observability.current_request_id())
    payload = json.loads(observability.JsonFormatter().format(record))
    assert payload["level"] == "INFO"
    assert payload["logger"] == "t.logger"
    assert payload["msg"] == "job done"
    assert payload["request_id"] == "req-1"
    assert payload["ts"]


def test_json_formatter_defaults_the_request_id_when_no_filter_ran():
    payload = json.loads(observability.JsonFormatter().format(_record()))
    assert payload["request_id"] == observability.NO_REQUEST_ID


def test_json_formatter_carries_extra_fields():
    payload = json.loads(observability.JsonFormatter().format(_record(job_id="j7", score=91.5)))
    assert payload["job_id"] == "j7"
    assert payload["score"] == 91.5


def test_json_formatter_renders_an_exception():
    try:
        raise ValueError("boom")
    except ValueError:
        record = _record("job failed", exc_info=sys.exc_info())
    payload = json.loads(observability.JsonFormatter().format(record))
    assert "ValueError: boom" in payload["exc"]
    assert "Traceback" in payload["exc"]


def test_json_formatter_never_raises_on_an_unserializable_extra():
    # A formatter is the one place in a worker that must not raise.
    payload = json.loads(observability.JsonFormatter().format(_record(blob=object())))
    assert payload["blob"].startswith("<object object")


def test_request_id_filter_stamps_the_current_id():
    observability.adopt_request_id("req-filter")
    record = _record()
    assert observability.RequestIdFilter().filter(record) is True
    assert record.request_id == "req-filter"


def test_logging_config_selects_the_format():
    assert (
        observability.logging_config("INFO", json_format=True)["handlers"]["console"]["formatter"]
        == "json"
    )
    text = observability.logging_config("DEBUG", json_format=False)
    assert text["handlers"]["console"]["formatter"] == "plain"
    assert text["root"]["level"] == "DEBUG"
    assert text["handlers"]["console"]["filters"] == ["request_id"]


# --------------------------------------------------------------------------- #
# Counters                                                                      #
# --------------------------------------------------------------------------- #


def test_counters_start_at_zero_and_are_still_reported():
    # A series that is absent from a scrape is not the same as one that is zero.
    body = metrics.render()
    values = _samples(body)
    assert values['agent_jobs_total{outcome="accepted"}'] == "0"
    assert values['agent_callbacks_total{outcome="failed"}'] == "0"
    assert values["agent_jobs_inflight"] == "0"


def test_job_and_callback_counters_count():
    metrics.job(observability.JOB_ACCEPTED)
    metrics.job(observability.JOB_ACCEPTED)
    metrics.job(observability.JOB_DONE)
    metrics.job(observability.JOB_ERROR)
    metrics.callback(observability.CALLBACK_DELIVERED)
    metrics.callback(observability.CALLBACK_REJECTED)
    metrics.callback(observability.CALLBACK_FAILED)
    metrics.callback(observability.CALLBACK_FAILED)

    values = _samples(metrics.render())
    assert values['agent_jobs_total{outcome="accepted"}'] == "2"
    assert values['agent_jobs_total{outcome="done"}'] == "1"
    assert values['agent_jobs_total{outcome="error"}'] == "1"
    assert values['agent_callbacks_total{outcome="delivered"}'] == "1"
    assert values['agent_callbacks_total{outcome="rejected"}'] == "1"
    assert values['agent_callbacks_total{outcome="failed"}'] == "2"
    # Derived, not passed in: 2 accepted - 1 done - 1 error.
    assert values["agent_jobs_inflight"] == "0"


def test_inflight_is_accepted_minus_finished():
    """Derived under the counters' own lock rather than by walking the API's
    `_JOBS` registry, which grading threads mutate while a scrape reads it."""
    for _ in range(3):
        metrics.job(observability.JOB_ACCEPTED)
    assert _samples(metrics.render())["agent_jobs_inflight"] == "3"
    metrics.job(observability.JOB_DONE)
    metrics.job(observability.JOB_ERROR)
    assert _samples(metrics.render())["agent_jobs_inflight"] == "1"


def test_inflight_never_goes_negative():
    """The shutdown flush can retire a job the process never finished, so more
    completions than intakes is reachable; a negative gauge is not."""
    metrics.job(observability.JOB_DONE)
    metrics.job(observability.JOB_DONE)
    assert _samples(metrics.render())["agent_jobs_inflight"] == "0"


def test_reset_drops_every_counter():
    metrics.job(observability.JOB_DONE)
    metrics.callback(observability.CALLBACK_DELIVERED)
    metrics.grade_latency(2.0)
    metrics.reset()

    values = _samples(metrics.render())
    assert values['agent_jobs_total{outcome="done"}'] == "0"
    assert values['agent_callbacks_total{outcome="delivered"}'] == "0"
    assert values["agent_grade_latency_seconds_count"] == "0"
    assert values["agent_grade_latency_seconds_sum"] == "0"


def test_render_declares_help_and_type_for_every_family():
    body = metrics.render()
    for name, kind in (
        ("agent_jobs_total", "counter"),
        ("agent_jobs_inflight", "gauge"),
        ("agent_callbacks_total", "counter"),
        ("agent_grade_latency_seconds", "histogram"),
    ):
        assert re.search(rf"^# HELP {name} \S.*$", body, re.MULTILINE), name
        assert f"# TYPE {name} {kind}\n" in body


def test_render_is_valid_prometheus_text_exposition():
    metrics.job(observability.JOB_DONE)
    metrics.grade_latency(1.5)
    sample = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]*(\{[a-zA-Z_][^}]*\})? -?[0-9][0-9.e+-]*$")
    for line in metrics.render().splitlines():
        assert line, "no blank lines in an exposition"
        if line.startswith("#"):
            assert re.match(r"^# (HELP|TYPE) ", line), line
        else:
            assert sample.match(line), line


def test_grade_latency_buckets_are_cumulative():
    for seconds in (0.5, 3.0, 7.0):
        metrics.grade_latency(seconds)
    values = _samples(metrics.render())

    counts = [
        int(values[f'agent_grade_latency_seconds_bucket{{le="{le}"}}'])
        for le in ("1", "2.5", "5", "10", "30", "60", "120", "300")
    ]
    assert counts == [1, 1, 2, 3, 3, 3, 3, 3]
    assert counts == sorted(counts), "buckets must be monotonically non-decreasing"

    assert values['agent_grade_latency_seconds_bucket{le="+Inf"}'] == "3"
    assert values["agent_grade_latency_seconds_count"] == "3"
    assert values["agent_grade_latency_seconds_sum"] == "10.5"


def test_an_observation_past_the_last_bucket_only_reaches_inf():
    metrics.grade_latency(600.0)
    values = _samples(metrics.render())
    assert values['agent_grade_latency_seconds_bucket{le="300"}'] == "0"
    assert values['agent_grade_latency_seconds_bucket{le="+Inf"}'] == "1"
    assert values["agent_grade_latency_seconds_count"] == "1"


# --------------------------------------------------------------------------- #
# GET /metrics                                                                  #
# --------------------------------------------------------------------------- #


def test_metrics_endpoint_serves_text(client):
    resp = client.get("/metrics")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/plain")
    assert "# TYPE agent_jobs_total counter" in resp.text


def test_metrics_endpoint_requires_the_token_when_configured(client, monkeypatch):
    # Job counts and grade latencies are a customer's hiring volume.
    monkeypatch.setenv("ASSESS_API_TOKEN", "s3cret")
    assert client.get("/metrics").status_code == 401
    assert client.get("/metrics", headers={"X-Assess-Token": "nope"}).status_code == 401
    assert client.get("/metrics", headers={"X-Assess-Token": "s3cret"}).status_code == 200


def test_metrics_endpoint_is_fail_closed_when_unconfigured(client, monkeypatch):
    monkeypatch.delenv("ASSESS_API_TOKEN", raising=False)
    monkeypatch.delenv("ASSESS_AUTH_DISABLED", raising=False)
    resp = client.get("/metrics")
    assert resp.status_code == 503
    assert "ASSESS_API_TOKEN" in resp.json()["detail"]


def test_a_real_job_moves_the_counters(client):
    # TestClient runs the background task before returning, so the job is already
    # done by the time we scrape.
    assert client.post("/assessments", json=_job()).status_code == 202
    values = _samples(client.get("/metrics").text)
    assert values['agent_jobs_total{outcome="accepted"}'] == "1"
    assert values['agent_jobs_total{outcome="done"}'] == "1"
    assert values['agent_jobs_total{outcome="error"}'] == "0"
    assert int(values["agent_grade_latency_seconds_count"]) == 1
    assert values["agent_jobs_inflight"] == "0"  # nothing still accepted


def test_a_failed_job_counts_as_an_error(client, monkeypatch):
    monkeypatch.setattr(api, "assess", _boom)
    assert client.post("/assessments", json=_job()).status_code == 202
    values = _samples(client.get("/metrics").text)
    assert values['agent_jobs_total{outcome="error"}'] == "1"
    assert values['agent_jobs_total{outcome="done"}'] == "0"
    # A failure's duration is a different population and is deliberately untimed.
    assert values["agent_grade_latency_seconds_count"] == "0"


def _boom(*_args, **_kwargs):
    raise RuntimeError("grading blew up")


def test_a_delivered_callback_counts(client, monkeypatch):
    monkeypatch.setattr(api.httpx, "post", lambda url, **kw: _FakeResponse())
    client.post("/assessments", json=_job(callback_url="https://platform/cb"))
    values = _samples(client.get("/metrics").text)
    assert values['agent_callbacks_total{outcome="delivered"}'] == "1"


def test_a_rejected_callback_counts(client, monkeypatch):
    monkeypatch.setattr(api.httpx, "post", lambda url, **kw: _FakeResponse(400, "bad payload"))
    client.post("/assessments", json=_job(callback_url="https://platform/cb"))
    values = _samples(client.get("/metrics").text)
    assert values['agent_callbacks_total{outcome="rejected"}'] == "1"
    assert values['agent_callbacks_total{outcome="delivered"}'] == "0"


def test_an_exhausted_callback_budget_counts_as_failed(client, monkeypatch):
    monkeypatch.setattr(api, "_CALLBACK_ATTEMPTS", 2)
    monkeypatch.setattr(api, "_CALLBACK_BACKOFF_S", 0.0)
    monkeypatch.setattr(api.httpx, "post", lambda url, **kw: _FakeResponse(503, "down"))
    client.post("/assessments", json=_job(callback_url="https://platform/cb"))
    values = _samples(client.get("/metrics").text)
    assert values['agent_callbacks_total{outcome="failed"}'] == "1"


# --------------------------------------------------------------------------- #
# The id on the way back out                                                    #
# --------------------------------------------------------------------------- #


def test_post_callback_forwards_the_current_request_id(monkeypatch):
    captured: dict = {}

    def _post(url, **kw):
        captured.update(kw)
        return _FakeResponse()

    monkeypatch.setattr(api.httpx, "post", _post)
    observability.adopt_request_id("platform-req-7")
    api._post_callback("j", "https://platform/cb", {"job_id": "j"})
    assert captured["headers"][observability.REQUEST_ID_HEADER] == "platform-req-7"


def test_post_callback_omits_the_header_outside_a_request(monkeypatch):
    """The shutdown flush has no request context. Sending "-" would be worse than
    sending nothing — the platform would adopt it as a real id."""
    captured: dict = {}

    def _post(url, **kw):
        captured.update(kw)
        return _FakeResponse()

    monkeypatch.setattr(api.httpx, "post", _post)
    _clear_request_id()
    api._post_callback("j", "https://platform/cb", {"job_id": "j"})
    assert observability.REQUEST_ID_HEADER not in captured["headers"]


def test_the_platforms_id_survives_into_the_callback(client, monkeypatch):
    """The whole point: submit -> trigger -> grade -> callback under one id."""
    captured: dict = {}

    def _post(url, **kw):
        captured.update(kw)
        return _FakeResponse()

    monkeypatch.setattr(api.httpx, "post", _post)
    resp = client.post(
        "/assessments",
        json=_job(callback_url="https://platform/cb"),
        headers={observability.REQUEST_ID_HEADER: "platform-req-7"},
    )
    assert resp.headers[observability.REQUEST_ID_HEADER] == "platform-req-7"
    assert captured["headers"][observability.REQUEST_ID_HEADER] == "platform-req-7"


# --------------------------------------------------------------------------- #
# Error reporting                                                               #
# --------------------------------------------------------------------------- #


def test_init_sentry_is_a_no_op_without_a_dsn(monkeypatch):
    monkeypatch.delenv("ASSESS_SENTRY_DSN", raising=False)
    assert observability.init_sentry() is False


def test_scrub_event_drops_candidate_data_and_secrets():
    event = {
        "request": {
            "url": "http://agent/assessments",
            "method": "POST",
            "data": {"code": "print('candidate source')"},
            "cookies": {"session": "abc"},
            "query_string": "candidate=jane",
            "headers": {
                "Host": "agent.internal",
                "User-Agent": "platform/1.0",
                "Content-Type": "application/json",
                "Authorization": "Bearer super-secret",
                "X-Assess-Token": "s3cret",
            },
        },
        "user": {"id": "jane", "email": "jane@example.com"},
        "exception": {"values": []},
    }

    scrubbed = observability._scrub_event(event, {})

    request = scrubbed["request"]
    assert "data" not in request
    assert "cookies" not in request
    assert "query_string" not in request
    assert request["headers"] == {
        "Host": "agent.internal",
        "User-Agent": "platform/1.0",
        "Content-Type": "application/json",
    }
    assert "user" not in scrubbed
    # Everything it was not asked to touch survives — an event with no request
    # data is still worth sending.
    assert request["url"] == "http://agent/assessments"
    assert scrubbed["exception"] == {"values": []}


def test_scrub_event_tolerates_an_event_with_no_request():
    assert observability._scrub_event({"level": "error"}, {}) == {"level": "error"}
