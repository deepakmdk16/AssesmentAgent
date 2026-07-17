"""Email an assessment PDF to the interviewer (Phase 2).

The report is sent as a PDF attachment over Gmail SMTP. Credentials come from
the environment only (`SMTP_USERNAME` + `SMTP_PASSWORD`, where the password is a
Gmail *app password* — plain-password SMTP is blocked under 2FA). The recipient
is interviewer-supplied (CLI `--to`, API `email_to`), falling back to
`ASSESS_DEFAULT_RECIPIENT`.

There is deliberately **no** hard-coded fallback address. A report carries the
candidate's name, their source code and their verdict, so a misconfiguration must
fail loudly rather than quietly deliver that to whatever address happened to be
compiled in — a wrong recipient is a privacy incident whose failure mode would
otherwise look like success.

`dry_run=True` builds the message but sends nothing — use it to inspect the
email without SMTP credentials or an outbound send.
"""

from __future__ import annotations

import logging
import os
import smtplib
from email.message import EmailMessage
from pathlib import Path

logger = logging.getLogger(__name__)

SMTP_HOST = "smtp.gmail.com"
SMTP_PORT = 587
# Bound the SMTP conversation. smtplib defaults to the global socket timeout
# (normally none), which would park a worker thread indefinitely on a hung server.
SMTP_TIMEOUT_S = 30.0


def default_recipient() -> str:
    """Resolve the fallback recipient from the environment.

    Raises RuntimeError when unset: see the module docstring — silently mailing a
    candidate's report to a built-in address is worse than not sending it.
    """
    recipient = os.environ.get("ASSESS_DEFAULT_RECIPIENT")
    if not recipient:
        raise RuntimeError(
            "No report recipient: pass one explicitly (CLI --to / API email_to) or set "
            "ASSESS_DEFAULT_RECIPIENT. There is no built-in fallback address on purpose."
        )
    return recipient


def build_email(
    pdf_path: str | Path,
    *,
    candidate: str,
    verdict: str,
    score_pct: float,
    sender: str,
    recipient: str | None = None,
) -> EmailMessage:
    """Build the MIME message with the PDF attached (no network I/O)."""
    recipient = recipient or default_recipient()
    pdf_path = Path(pdf_path)
    msg = EmailMessage()
    msg["Subject"] = f"Assessment report — {candidate} — {verdict} ({score_pct:.0f}%)"
    msg["From"] = sender
    msg["To"] = recipient
    msg.set_content(
        f"Hello,\n\n"
        f"Please find attached the coding assessment report for {candidate}.\n\n"
        f"Verdict: {verdict}\n"
        f"Score:   {score_pct:.0f}%\n\n"
        f"The attached PDF contains the question, the candidate's code, every "
        f"test case (input/expected/actual), the coverage, and a code-quality "
        f"summary.\n\n"
        f"— Assessment Agent\n"
    )
    msg.add_attachment(
        pdf_path.read_bytes(),
        maintype="application",
        subtype="pdf",
        filename=pdf_path.name,
    )
    return msg


def send_report(
    pdf_path: str | Path,
    *,
    candidate: str,
    verdict: str,
    score_pct: float,
    recipient: str | None = None,
    dry_run: bool = False,
) -> EmailMessage:
    """Build and (unless dry_run) send the report email. Returns the message.

    Credentials come from `SMTP_USERNAME` / `SMTP_PASSWORD`; the sender is
    `SMTP_USERNAME`. Raises RuntimeError with a clear message if they are unset
    on a real send, or if SMTP itself fails — callers treat a send failure as a
    reportable outcome, not a reason to discard a completed assessment, so every
    failure mode here is normalised to RuntimeError.
    """
    username = os.environ.get("SMTP_USERNAME")
    password = os.environ.get("SMTP_PASSWORD")
    # In dry-run we don't need real credentials — use a placeholder sender.
    sender = username or "assessment-agent@localhost"
    # Resolve up front (rather than letting build_email do it) so the address is
    # known here for logging and error messages.
    recipient = recipient or default_recipient()
    msg = build_email(
        pdf_path,
        candidate=candidate,
        verdict=verdict,
        score_pct=score_pct,
        sender=sender,
        recipient=recipient,
    )
    if dry_run:
        return msg

    if not username or not password:
        raise RuntimeError(
            "SMTP_USERNAME and SMTP_PASSWORD must be set to send email "
            "(use a Gmail app password). Pass --email-dry-run to skip sending."
        )
    # smtplib raises SMTPException/OSError, neither of which is a RuntimeError.
    # Callers report a send failure and keep the assessment, so let the failure
    # cross this boundary as the one type they catch — otherwise a Gmail hiccup
    # escapes as an unhandled error and discards a grade that was already decided.
    try:
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=SMTP_TIMEOUT_S) as smtp:
            smtp.starttls()
            smtp.login(username, password)
            smtp.send_message(msg)
    except (smtplib.SMTPException, OSError) as exc:
        logger.warning("SMTP send to %s failed: %s", recipient, exc)
        raise RuntimeError(f"SMTP send failed: {exc}") from exc
    return msg
