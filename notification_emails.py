"""
notification_emails: Send warning notifications about report processing issues.
"""

from __future__ import annotations

import os
import smtplib
from datetime import datetime
from email.message import EmailMessage, Message
from email.utils import getaddresses
from typing import Iterable


REQUIRED_SMTP_ENV_VARS = [
    "PROTONMAIL_HOST",
    "PROTONMAIL_SMTP_PORT",
    "PROTONMAIL_USER",
    "PROTONMAIL_PW",
    "PROTONMAIL_SMTP_FROM",
]
# Load internal issue inbox address from environment.
ISSUE_INBOX_RECIPIENT = os.getenv("PROTONMAIL_ISSUES_ADDRESS", "issues@example.com")

ISSUE_TITLES = {
    "mailbox_empty": "No report emails in mailbox",
    "latest_date_unavailable": "Could not determine latest report email date",
    "newest_email_fetch_failed": "Could not fetch newest report email",
    "report_missing_today": "No report email received today",
    "report_stale": "Report email is stale",
}


def _collect_report_recipients(report_email: Message | None, extra_recipients: Iterable[str] | None = None) -> list[str]:
    recipients: list[str] = []

    if report_email is not None:
        to_header = report_email.get("To", "")
        cc_header = report_email.get("Cc", "")

        for _, address in getaddresses([to_header, cc_header]):
            cleaned = address.strip()
            if cleaned and "@" in cleaned:
                recipients.append(cleaned)

    if extra_recipients:
        for address in extra_recipients:
            if not address:
                continue
            cleaned = address.strip()
            if cleaned and "@" in cleaned:
                recipients.append(cleaned)

    deduplicated: list[str] = []
    for address in recipients:
        if address not in deduplicated:
            deduplicated.append(address)
    return deduplicated


def send_report_issue_warning(
    report_email: Message | None,
    issue_title: str,
    issue_details: str,
    extra_recipients: Iterable[str] | None = None,
) -> list[str]:
    """
    Send a warning message about a report issue to all recipients of the report email.

    Required environment variables:
    - PROTONMAIL_HOST
    - PROTONMAIL_SMTP_PORT
    - PROTONMAIL_USER
    - PROTONMAIL_PW
    - PROTONMAIL_SMTP_FROM
    """
    recipients = _collect_report_recipients(report_email, extra_recipients)
    if not recipients:
        raise ValueError("No recipients were found in report email headers or extra recipients.")

    missing_env = [name for name in REQUIRED_SMTP_ENV_VARS if not os.getenv(name)]
    if missing_env:
        missing = ", ".join(missing_env)
        raise EnvironmentError(f"Missing SMTP environment variables: {missing}")

    smtp_host = os.environ["PROTONMAIL_HOST"]
    smtp_port = int(os.environ["PROTONMAIL_SMTP_PORT"])
    smtp_user = os.environ["PROTONMAIL_USER"]
    smtp_pw = os.environ["PROTONMAIL_PW"]
    smtp_from = os.environ["PROTONMAIL_SMTP_FROM"]
    subject_prefix = os.getenv("WARNING_EMAIL_SUBJECT_PREFIX", "")

    original_subject = report_email.get("Subject", "(no subject)") if report_email is not None else "(no source email)"
    original_date = report_email.get("Date", "(no date)") if report_email is not None else "(no source email)"

    warning = EmailMessage()
    warning["From"] = smtp_from
    warning["To"] = ", ".join(recipients)
    warning["Subject"] = f"{subject_prefix}[Solarchecker Warning] {issue_title}"

    warning.set_content(
        "\n".join(
            [
                "A report issue was detected by solarchecker.",
                "",
                f"Issue: {issue_title}",
                "",
                "Details:",
                issue_details,
                "",
                "Original report email metadata:",
                f"- Subject: {original_subject}",
                f"- Date: {original_date}",
            ]
        )
    )

    with smtplib.SMTP(smtp_host, smtp_port) as smtp:
        smtp.starttls()
        smtp.login(smtp_user, smtp_pw)
        smtp.send_message(warning)

    return recipients


def send_issue_warning_to_issue_inbox(issue_title: str, issue_details: str) -> list[str]:
    """
    Send warning email only to the internal issue inbox.
    """
    return send_report_issue_warning(
        report_email=None,
        issue_title=issue_title,
        issue_details=issue_details,
        extra_recipients=[ISSUE_INBOX_RECIPIENT],
    )


def detect_and_send_report_issue_warning(
    report_email: Message | None,
    *,
    mailbox_empty: bool = False,
    latest_date_unavailable: bool = False,
    newest_email_fetch_failed: bool = False,
    days_since_last_email: int | None = None,
    stale_days_threshold: int = 10,
    extra_recipients: Iterable[str] | None = None,
) -> bool:
    """
    Detect known report-email retrieval issues and send a warning if one is found.

    Returns True when an email warning was sent, otherwise False.
    """
    issue_title = ""
    issue_details = ""

    if mailbox_empty:
        issue_title = ISSUE_TITLES["mailbox_empty"]
        issue_details = "The configured report mailbox currently has no messages."
    elif latest_date_unavailable:
        issue_title = ISSUE_TITLES["latest_date_unavailable"]
        issue_details = "IMAP metadata did not contain a usable INTERNALDATE for identifying the latest report."
    elif newest_email_fetch_failed:
        issue_title = ISSUE_TITLES["newest_email_fetch_failed"]
        issue_details = "The newest report email could not be fetched via IMAP."
    elif days_since_last_email is not None and days_since_last_email >= stale_days_threshold:
        issue_title = ISSUE_TITLES["report_stale"]
        issue_details = (
            f"The newest report email is {days_since_last_email} days old, "
            f"which meets or exceeds the threshold of {stale_days_threshold} days."
        )
    elif days_since_last_email is not None and days_since_last_email > 0:
        issue_title = ISSUE_TITLES["report_missing_today"]
        issue_details = f"No new report email arrived today (last email age: {days_since_last_email} days)."

    if not issue_title:
        return False

    timestamp = datetime.now().isoformat(timespec="seconds")
    full_details = f"Detected at: {timestamp}\n{issue_details}"

    send_report_issue_warning(
        report_email=report_email,
        issue_title=issue_title,
        issue_details=full_details,
        extra_recipients=extra_recipients,
    )
    return True
