"""Email an assessment PDF to the interviewer (Phase 2).

The report is sent as a PDF attachment over Gmail SMTP. Credentials come from
the environment only (`SMTP_USERNAME` + `SMTP_PASSWORD`, where the password is a
Gmail *app password* — plain-password SMTP is blocked under 2FA). The recipient
is hard-coded for now so results land in a known inbox during bring-up.

`dry_run=True` builds the message but sends nothing — use it to inspect the
email without SMTP credentials or an outbound send.
"""

from __future__ import annotations

import os
import smtplib
from email.message import EmailMessage
from pathlib import Path

# Hard-coded during bring-up (Phase 2); becomes interviewer-supplied later.
RECIPIENT = "deepakmdk16@gmail.com"
SMTP_HOST = "smtp.gmail.com"
SMTP_PORT = 587


def build_email(
    pdf_path: str | Path,
    *,
    candidate: str,
    verdict: str,
    score_pct: float,
    sender: str,
    recipient: str = RECIPIENT,
) -> EmailMessage:
    """Build the MIME message with the PDF attached (no network I/O)."""
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
    recipient: str = RECIPIENT,
    dry_run: bool = False,
) -> EmailMessage:
    """Build and (unless dry_run) send the report email. Returns the message.

    Credentials come from `SMTP_USERNAME` / `SMTP_PASSWORD`; the sender is
    `SMTP_USERNAME`. Raises RuntimeError with a clear message if they are unset
    on a real send.
    """
    username = os.environ.get("SMTP_USERNAME")
    password = os.environ.get("SMTP_PASSWORD")
    # In dry-run we don't need real credentials — use a placeholder sender.
    sender = username or "assessment-agent@localhost"
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
    with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as smtp:
        smtp.starttls()
        smtp.login(username, password)
        smtp.send_message(msg)
    return msg
