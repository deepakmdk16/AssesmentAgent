"""Phase 2: the emailer builds a correct MIME message and never sends in dry-run."""

from __future__ import annotations

from pathlib import Path

import pytest

from assessment_agent.mailer import RECIPIENT, build_email, send_report


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
    )
    assert msg["To"] == RECIPIENT
    assert "alice.py" in msg["Subject"] and "PASS" in msg["Subject"]
    attachments = list(msg.iter_attachments())
    assert len(attachments) == 1
    assert attachments[0].get_content_type() == "application/pdf"
    assert attachments[0].get_filename() == "report.pdf"


def test_dry_run_builds_but_does_not_need_credentials(tmp_path, monkeypatch):
    monkeypatch.delenv("SMTP_USERNAME", raising=False)
    monkeypatch.delenv("SMTP_PASSWORD", raising=False)
    msg = send_report(
        _fake_pdf(tmp_path), candidate="a.py", verdict="FAIL", score_pct=0.0, dry_run=True
    )
    assert msg["To"] == RECIPIENT  # built and returned, nothing sent


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


def test_real_send_without_credentials_raises(tmp_path, monkeypatch):
    monkeypatch.delenv("SMTP_USERNAME", raising=False)
    monkeypatch.delenv("SMTP_PASSWORD", raising=False)
    with pytest.raises(RuntimeError, match="SMTP_USERNAME"):
        send_report(
            _fake_pdf(tmp_path), candidate="a.py", verdict="FAIL", score_pct=0.0, dry_run=False
        )
