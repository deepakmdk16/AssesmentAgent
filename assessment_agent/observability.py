"""Request correlation, structured logs, error reporting and metrics (X08).

The worker's operational surface. Three of the four concerns mirror the
platform's module of the same name — a request id on every log line, a JSON log
format for an aggregator, DSN-gated Sentry — deliberately as separate code
rather than a mirrored file: only `signing.py` and the callback contract are
parity-gated, and adding a third file that two repos must keep byte-identical
would be a gate nobody asked for.

The fourth concern, metrics, is where the two services genuinely differ. The
platform derives its numbers by querying rows it already stores. This worker is
stateless by design — it has no database and `_JOBS` is explicitly not one — so
its numbers are in-process counters. That is a real limitation and it is bounded
by a real fact: `api.main` hands uvicorn the app *object*, not an import string,
which structurally rules out `workers=N`. One process, one set of counters. Scale
the worker to N replicas and each reports its own; sum them by instance, which is
what a Prometheus scraper does anyway.

Counters also reset when the worker restarts. For a `_total` that is expected —
Prometheus detects a counter reset — and the platform's durable, DB-derived view
of the same jobs is the other half of the picture.

Untrusted input reaches this module: a candidate's submission is what fails, and
a caller-supplied header is what names the request. Both are treated as hostile —
the request id is pattern-checked before it reaches a log line, and an error
report is scrubbed of candidate code before it leaves the box.
"""

from __future__ import annotations

import json
import logging
import os
import re
import threading
import uuid
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from contextvars import ContextVar
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # the SDK is imported lazily; its types are needed at check time
    from sentry_sdk.types import Event

logger = logging.getLogger(__name__)

REQUEST_ID_HEADER = "X-Request-Id"

# Same shape as `job_id` (api.AssessmentRequest): an id accepted from the caller
# and echoed into logs must be too dull to be a log-injection vector.
_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,64}$")

# Outside a request — a CLI grade, the shutdown flush — there is genuinely no id,
# and seeing that is better than seeing an invented one.
NO_REQUEST_ID = "-"

_REQUEST_ID: ContextVar[str] = ContextVar("request_id", default=NO_REQUEST_ID)


def new_request_id() -> str:
    """A fresh correlation id. Short: a human reads it off a log line."""
    return uuid.uuid4().hex[:16]


def adopt_request_id(inbound: str | None) -> str:
    """Make `inbound` current when it is well formed, else mint one.

    Replaces rather than rejects: failing a grade because a proxy mangled a
    debugging header would be a terrible trade.
    """
    request_id = inbound if inbound and _ID_PATTERN.match(inbound) else new_request_id()
    _REQUEST_ID.set(request_id)
    return request_id


def current_request_id() -> str:
    """The id of the request being served, or `NO_REQUEST_ID` outside one.

    Set by the middleware before the route runs, so it survives into the
    background grade (starlette copies the context into the worker thread) and is
    still current when the callback is posted from there.
    """
    return _REQUEST_ID.get()


# --------------------------------------------------------------------------- #
# Logging                                                                       #
# --------------------------------------------------------------------------- #


class RequestIdFilter(logging.Filter):
    """Stamp every record with the current request id.

    A filter rather than an `extra=` at each call site, so it also reaches the
    records no call site of ours controls: uvicorn's access lines, a library
    warning, a traceback from inside httpx.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = current_request_id()
        return True


_RECORD_BUILTINS = frozenset(
    """args asctime created exc_info exc_text filename funcName levelname levelno
    lineno message module msecs msg name pathname process processName
    relativeCreated stack_info taskName thread threadName""".split()
)

_PLAIN_FORMAT = "%(asctime)s %(levelname)-8s %(name)s [%(request_id)s]: %(message)s"


class JsonFormatter(logging.Formatter):
    """One JSON object per line, for an aggregator to ingest.

    Hand-rolled rather than a dependency: five fixed keys plus whatever `extra=`
    carried, and a formatter is the one place in a worker that must never raise —
    hence `default=str` over anything unexpected.
    """

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "logger": record.name,
            "request_id": getattr(record, "request_id", NO_REQUEST_ID),
            "msg": record.getMessage(),
        }
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        for key, value in record.__dict__.items():
            if key not in _RECORD_BUILTINS and key not in payload:
                payload[key] = value
        return json.dumps(payload, default=str)


def logging_config(level: str, *, json_format: bool) -> dict[str, Any]:
    """A `logging.config.dictConfig` dict for the whole worker process.

    Explicit uvicorn logger entries because uvicorn installs its own handlers and
    sets `propagate = False`: without them the access lines would keep uvicorn's
    format while this package's lines changed, and the output would be half
    converted. A dictConfig rather than `basicConfig` for the same reason plus
    one more — `basicConfig` is a no-op once a handler exists, which under
    uvicorn it always is.
    """
    formatter = "json" if json_format else "plain"
    return {
        "version": 1,
        "disable_existing_loggers": False,
        "filters": {"request_id": {"()": RequestIdFilter}},
        "formatters": {
            "plain": {"format": _PLAIN_FORMAT},
            "json": {"()": JsonFormatter},
        },
        "handlers": {
            "console": {
                "class": "logging.StreamHandler",
                "stream": "ext://sys.stderr",
                "formatter": formatter,
                "filters": ["request_id"],
            }
        },
        "root": {"level": level, "handlers": ["console"]},
        "loggers": {
            "uvicorn": {"handlers": [], "propagate": True},
            "uvicorn.error": {"handlers": [], "propagate": True},
            "uvicorn.access": {"handlers": [], "propagate": True},
        },
    }


# --------------------------------------------------------------------------- #
# Error reporting                                                               #
# --------------------------------------------------------------------------- #

_SAFE_HEADERS = frozenset({"host", "user-agent", "content-type", "content-length"})


def _scrub_event(event: Event, _hint: dict[str, Any]) -> Event:
    """Strip candidate data out of an error report before it leaves the box.

    This worker's request bodies ARE the candidate's source code, and its headers
    carry the shared secret. `send_default_pii=False` covers what Sentry would
    volunteer; this covers what we handed it.
    """
    request = event.get("request")
    if isinstance(request, dict):
        request.pop("data", None)
        request.pop("cookies", None)
        request.pop("query_string", None)
        # No route here takes a secret in the URL today, so this drops nothing of
        # value — it is here so the two repos' scrubs cannot silently diverge, the
        # way they had when the platform gained URL handling and this did not.
        url = request.get("url")
        if isinstance(url, str):
            request["url"] = url.partition("?")[0]
        headers = request.get("headers")
        if isinstance(headers, dict):
            request["headers"] = {
                name: value for name, value in headers.items() if name.lower() in _SAFE_HEADERS
            }
    event.pop("user", None)
    return event


def init_sentry() -> bool:
    """Start Sentry when `ASSESS_SENTRY_DSN` is set. Returns whether it did.

    A separate variable from the platform's `SENTRY_DSN`, and not an oversight:
    the agent namespaces its configuration with `ASSESS_`, and the two services
    want separate Sentry projects anyway — a grading crash and a billing crash
    are different on-call problems.

    Errors here are never fatal. A worker that cannot reach its error reporter
    must still grade.
    """
    dsn = os.environ.get("ASSESS_SENTRY_DSN")
    if not dsn:
        return False
    try:
        import sentry_sdk
    except ImportError:  # pragma: no cover - the dependency is declared
        logger.warning("ASSESS_SENTRY_DSN is set but sentry-sdk is not installed.")
        return False
    sentry_sdk.init(
        dsn=dsn,
        environment=os.environ.get("ASSESS_SENTRY_ENVIRONMENT", "production"),
        release=os.environ.get("ASSESS_SENTRY_RELEASE") or None,
        traces_sample_rate=float(os.environ.get("ASSESS_SENTRY_TRACES_SAMPLE_RATE", "0")),
        send_default_pii=False,
        max_request_body_size="never",
        # The submission is a local variable on every frame of the grading path.
        include_local_variables=False,
        before_send=_scrub_event,
    )
    global _sentry_started
    _sentry_started = True
    return True


# Set by `init_sentry`, read by `tag_request`. A module flag rather than asking
# the SDK whether it is running: the SDK is only imported when a DSN exists, and
# `tag_request` runs on every single request.
_sentry_started = False


def tag_request(request_id: str) -> None:
    """Attach the request id to any Sentry event raised while serving this request.

    Without it a crash report and the log lines around it share no key, which is
    most of the point of having both. A no-op when Sentry was never started.
    """
    if not _sentry_started:
        return
    import sentry_sdk

    sentry_sdk.set_tag("request_id", request_id)


# --------------------------------------------------------------------------- #
# Prometheus text exposition                                                    #
# --------------------------------------------------------------------------- #

Labels = Mapping[str, str]
Samples = Sequence[tuple[Labels, float]]

# Seconds. A deterministic run finishes in single digits; one waiting on the LLM
# judge takes tens; past five minutes it has effectively failed.
LATENCY_BUCKETS: tuple[float, ...] = (1.0, 2.5, 5.0, 10.0, 30.0, 60.0, 120.0, 300.0)


def _escape(value: str) -> str:
    return value.replace("\\", r"\\").replace('"', r"\"").replace("\n", r"\n")


def _labels(labels: Labels) -> str:
    if not labels:
        return ""
    return "{" + ",".join(f'{k}="{_escape(v)}"' for k, v in sorted(labels.items())) + "}"


def _number(value: float) -> str:
    """Render a value the way Prometheus expects: integers without a `.0` tail."""
    return str(int(value)) if float(value).is_integer() else repr(float(value))


def metric(name: str, help_text: str, kind: str, samples: Samples) -> str:
    """One `gauge` or `counter` family as text exposition."""
    header = f"# HELP {name} {help_text}\n# TYPE {name} {kind}\n"
    body = "".join(f"{name}{_labels(ls)} {_number(v)}\n" for ls, v in samples)
    return header + body


def render(families: Iterable[str]) -> str:
    """Join metric families into a scrape response."""
    return "".join(families)


# --------------------------------------------------------------------------- #
# The worker's counters                                                         #
# --------------------------------------------------------------------------- #

# Job outcomes. "accepted" counts intake, so accepted - (done + error) is the
# work the process believes it still owes — a number that should return to zero.
JOB_ACCEPTED, JOB_DONE, JOB_ERROR = "accepted", "done", "error"
# Callback outcomes. "rejected" is the platform refusing the payload (4xx, not
# retried). "failed" is the normal delivery budget exhausted, which loses a
# result outright and is the single most important number this worker reports.
# "requeued" is the shutdown flush's one-shot budget running out — the same code
# path, but NOT a lost result, because the platform's reaper re-triggers the job.
# They are separate series for exactly that reason: alerting on "failed" should
# not page someone for an ordinary deploy.
CALLBACK_DELIVERED, CALLBACK_REJECTED = "delivered", "rejected"
CALLBACK_FAILED, CALLBACK_REQUEUED = "failed", "requeued"

_JOB_OUTCOMES = (JOB_ACCEPTED, JOB_DONE, JOB_ERROR)
_CALLBACK_OUTCOMES = (CALLBACK_DELIVERED, CALLBACK_REJECTED, CALLBACK_FAILED, CALLBACK_REQUEUED)


class Metrics:
    """Thread-safe counters for one worker process.

    Locked because grading runs in starlette's thread pool: `_run_job` and the
    shutdown flush's executor both write from threads other than the event
    loop's. The lock is held only for arithmetic, never across I/O.

    Latency is accumulated into fixed buckets rather than kept as a list of
    observations — a list would grow without bound for the life of the process,
    and a mean would describe neither of the two populations that make up a grade
    (a deterministic run, and one waiting on the LLM judge).
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._jobs: Counter[str] = Counter()
        self._callbacks: Counter[str] = Counter()
        self._buckets = [0] * len(LATENCY_BUCKETS)
        self._latency_sum = 0.0
        self._latency_count = 0

    def job(self, outcome: str) -> None:
        with self._lock:
            self._jobs[outcome] += 1

    def callback(self, outcome: str) -> None:
        with self._lock:
            self._callbacks[outcome] += 1

    def grade_latency(self, seconds: float) -> None:
        """Record one grade's wall-clock duration."""
        with self._lock:
            self._latency_sum += seconds
            self._latency_count += 1
            for index, bound in enumerate(LATENCY_BUCKETS):
                if seconds <= bound:
                    self._buckets[index] += 1

    def reset(self) -> None:
        """Drop every counter. For tests, which share one process."""
        with self._lock:
            self._jobs.clear()
            self._callbacks.clear()
            self._buckets = [0] * len(LATENCY_BUCKETS)
            self._latency_sum = 0.0
            self._latency_count = 0

    def render(self) -> str:
        """The scrape body.

        In-flight is derived from the counters (accepted minus finished) rather
        than measured by walking the API's `_JOBS` registry: that registry is an
        unsynchronized OrderedDict written by grading threads, so iterating it
        from a scrape raced them and could raise "mutated during iteration"
        mid-response. Subtraction under this lock is O(1) and cannot race.
        Floored at zero because the shutdown flush can retire a job the process
        never got to finish.
        """
        with self._lock:
            jobs = dict(self._jobs)
            inflight = max(
                0,
                jobs.get(JOB_ACCEPTED, 0) - jobs.get(JOB_DONE, 0) - jobs.get(JOB_ERROR, 0),
            )
            callbacks = dict(self._callbacks)
            buckets = list(self._buckets)
            latency_sum = self._latency_sum
            latency_count = self._latency_count

        name = "agent_grade_latency_seconds"
        histogram = [f"# HELP {name} Wall-clock seconds to grade one submission.\n"]
        histogram.append(f"# TYPE {name} histogram\n")
        # Already cumulative: `grade_latency` bumps every bucket the observation
        # fits under, which is what the format's `le` ("less than or equal") means.
        for bound, count in zip(LATENCY_BUCKETS, buckets, strict=True):
            histogram.append(f'{name}_bucket{{le="{_number(bound)}"}} {count}\n')
        histogram.append(f'{name}_bucket{{le="+Inf"}} {latency_count}\n')
        histogram.append(f"{name}_sum {_number(latency_sum)}\n")
        histogram.append(f"{name}_count {latency_count}\n")

        return render(
            [
                metric(
                    "agent_jobs_total",
                    "Grading jobs by outcome since this worker started.",
                    "counter",
                    [({"outcome": o}, jobs.get(o, 0)) for o in _JOB_OUTCOMES],
                ),
                metric(
                    "agent_jobs_inflight",
                    "Jobs accepted whose result has not been delivered yet.",
                    "gauge",
                    [({}, inflight)],
                ),
                metric(
                    "agent_callbacks_total",
                    "Result deliveries by outcome; 'failed' means a lost result, "
                    "'requeued' means the shutdown flush gave up and the reaper will retry.",
                    "counter",
                    [({"outcome": o}, callbacks.get(o, 0)) for o in _CALLBACK_OUTCOMES],
                ),
                "".join(histogram),
            ]
        )


# One per process, like the rate limiter's.
metrics = Metrics()
