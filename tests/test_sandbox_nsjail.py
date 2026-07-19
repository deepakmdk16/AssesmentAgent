"""Live nsjail integration — the argv-shape unit tests live in test_sandbox.py.

These actually build the jail, so they SKIP unless nsjail is installed (macOS dev,
CI without nsjail). They are the bring-up check for the two things the unit tests
can't prove: that a normal submission still runs inside the jail, and that network
egress is really blocked. Same "SKIP offline" posture as the eval harnesses.
"""

import shutil
import sys

import pytest

from assessment_agent import sandbox
from assessment_agent.questions import TestCase
from assessment_agent.runner import run_submission

pytestmark = [
    pytest.mark.skipif(not sys.platform.startswith("linux"), reason="nsjail is Linux-only"),
    pytest.mark.skipif(shutil.which("nsjail") is None, reason="nsjail not installed"),
]


@pytest.fixture(autouse=True)
def force_nsjail(monkeypatch):
    monkeypatch.setattr(sandbox, "_BACKEND", "nsjail")


def test_correct_submission_still_passes_inside_the_jail():
    src = "import sys\nd = sys.stdin.read().split()\nprint(int(d[0]) + int(d[1]))\n"
    report = run_submission(src, "python", (TestCase("t", "2 3\n", "5"),))
    assert report.infra_error is None, report.infra_error
    assert report.all_passed


def test_network_egress_is_blocked():
    # Prints NET_BLOCKED when it cannot reach the network — which is the pass
    # condition here, proving the jail's net namespace has no route out.
    src = (
        "import socket\n"
        "try:\n"
        "    socket.create_connection(('1.1.1.1', 80), timeout=2)\n"
        "    print('NET_OK')\n"
        "except OSError:\n"
        "    print('NET_BLOCKED')\n"
    )
    report = run_submission(src, "python", (TestCase("net", "", "NET_BLOCKED"),))
    assert report.infra_error is None, report.infra_error
    assert report.all_passed, f"egress not blocked: {report.outcomes[0].actual!r}"
