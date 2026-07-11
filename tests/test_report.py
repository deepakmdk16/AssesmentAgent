"""Phase 2: the PDF report renders for both judged and judge-skipped results."""

from __future__ import annotations

import pytest

from assessment_agent.agent import assess
from assessment_agent.eval_cases import EVAL_CASES
from assessment_agent.report import build_report_pdf

STRONG = next(c for c in EVAL_CASES if c.id == "strong").source


@pytest.fixture(autouse=True)
def _force_offline(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)


def test_pdf_renders_a_passing_result(tmp_path):
    result = assess(STRONG, "python")
    out = build_report_pdf(result, tmp_path / "report.pdf", candidate="alice.py")
    data = out.read_bytes()
    assert data[:5] == b"%PDF-"
    assert len(data) > 1500  # a real multi-section document, not an empty shell


def test_pdf_renders_when_quality_was_skipped(tmp_path):
    # A crashing submission skips the judge; the report must still render, with
    # the quality section noting it was not assessed.
    result = assess("raise ValueError('boom')\n", "python")
    assert result.quality_engine == "skipped"
    out = build_report_pdf(result, tmp_path / "skipped.pdf", candidate="bob.py")
    assert out.read_bytes()[:5] == b"%PDF-"
