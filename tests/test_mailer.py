"""Phase 2: the emailer builds a correct MIME message and never sends in dry-run.

Recipient resolution is part of the contract under test: there is deliberately no
built-in fallback address, because a report carries the candidate's code and
verdict and must never be delivered to a compiled-in default by accident.
"""

from __future__ import annotations

import smtplib
from pathlib import Path

import pytest

from assessment_agent.mailer import build_email, default_recipient, send_report


def _fake_pdf(tmp_path) -> Path:
    p = tmp_path / "report.pdf"
    p.write_bytes(b"%PDF-1.4 fake content")
    return p


def test_build_email_attaches_the_pdf(tmp_path):
    msg = build_email(
        _fake_pdf(tmp_path),
        candidate="alice.py",
        verdict="PASS",
        score_pct=100.0,
        sender="me@example.com",
        recipient="interviewer@example.com",
    )
    assert msg["To"] == "interviewer@example.com"
    assert "alice.py" in msg["Subject"] and "PASS" in msg["Subject"]
    attachments = list(msg.iter_attachments())
    assert len(attachments) == 1
    assert attachments[0].get_content_type() == "application/pdf"
    assert attachments[0].get_filename() == "report.pdf"


def test_dry_run_builds_but_does_not_need_credentials(tmp_path, monkeypatch):
    monkeypatch.delenv("SMTP_USERNAME", raising=False)
    monkeypatch.delenv("SMTP_PASSWORD", raising=False)
    msg = send_report(
        _fake_pdf(tmp_path),
        candidate="a.py",
        verdict="FAIL",
        score_pct=0.0,
        recipient="interviewer@example.com",
        dry_run=True,
    )
    assert msg["To"] == "interviewer@example.com"  # built and returned, nothing sent


def test_default_recipient_honors_env_override(tmp_path, monkeypatch):
    monkeypatch.setenv("ASSESS_DEFAULT_RECIPIENT", "team@example.com")
    msg = build_email(
        _fake_pdf(tmp_path),
        candidate="a.py",
        verdict="PASS",
        score_pct=100.0,
        sender="me@example.com",
    )
    assert msg["To"] == "team@example.com"


def test_no_recipient_and_no_env_raises_rather_than_guessing(monkeypatch):
    """The whole point of dropping the hard-coded address: an unresolved
    recipient must fail loudly, not quietly mail someone the wrong report."""
    monkeypatch.delenv("ASSESS_DEFAULT_RECIPIENT", raising=False)
    with pytest.raises(RuntimeError, match="No report recipient"):
        default_recipient()


def test_real_send_without_credentials_raises(tmp_path, monkeypatch):
    monkeypatch.delenv("SMTP_USERNAME", raising=False)
    monkeypatch.delenv("SMTP_PASSWORD", raising=False)
    with pytest.raises(RuntimeError, match="SMTP_USERNAME"):
        send_report(
            _fake_pdf(tmp_path),
            candidate="a.py",
            verdict="FAIL",
            score_pct=0.0,
            recipient="interviewer@example.com",
            dry_run=False,
        )


def test_smtp_failure_surfaces_as_runtimeerror(tmp_path, monkeypatch):
    """Callers report a send failure and keep the assessment, so they catch
    RuntimeError only. smtplib's own SMTPException/OSError must not escape past
    this boundary and take a decided verdict down with them."""
    monkeypatch.setenv("SMTP_USERNAME", "bot@example.com")
    monkeypatch.setenv("SMTP_PASSWORD", "app-password")

    def _boom(*args, **kwargs):
        raise smtplib.SMTPConnectError(421, "service unavailable")

    monkeypatch.setattr(smtplib, "SMTP", _boom)
    with pytest.raises(RuntimeError, match="SMTP send failed"):
        send_report(
            _fake_pdf(tmp_path),
            candidate="a.py",
            verdict="PASS",
            score_pct=100.0,
            recipient="interviewer@example.com",
            dry_run=False,
        )
