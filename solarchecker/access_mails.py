'''
access_mails: Access emails from Protonmail to check if a new report has arrived.
'''


import os
import re

import email
import imaplib

from datetime import datetime

from .notification_emails import send_issue_warning_to_issue_inbox, send_report_issue_warning


def parse_internal_date(fetch_meta_bytes):
    if not isinstance(fetch_meta_bytes, (bytes, bytearray)):
        return None
    fetch_meta = fetch_meta_bytes.decode('utf-8', errors='replace')
    internal_match = re.search(r'INTERNALDATE "([^"]+)"', fetch_meta)
    if not internal_match:
        return None
    return datetime.strptime(internal_match.group(1), '%d-%b-%Y %H:%M:%S %z')


REQUIRED_PROTONMAIL_ENV_VARS = ["PROTONMAIL_HOST", "PROTONMAIL_IMAP_PORT", "PROTONMAIL_USER", "PROTONMAIL_PW"]


def _warn_missing_protonmail_env_vars(missing_env_vars):
    missing = ", ".join(missing_env_vars)
    print(f"ERROR: Missing Protonmail environment variables: {missing}")
    try:
        send_issue_warning_to_issue_inbox(
            issue_title="Missing Protonmail environment variables",
            issue_details=f"The following required variables are missing: {missing}",
        )
    except Exception as warning_error:
        print(f"ERROR: Could not send missing-env warning email: {warning_error}")

def connect_to_protonmail():
    missing_env_vars = [var for var in REQUIRED_PROTONMAIL_ENV_VARS if not os.getenv(var)]
    if missing_env_vars:
        _warn_missing_protonmail_env_vars(missing_env_vars)
        raise EnvironmentError(
            "Please set the environment variables PROTONMAIL_HOST, PROTONMAIL_IMAP_PORT, PROTONMAIL_USER, PROTONMAIL_PW"
        )

    protonmail_host = os.getenv("PROTONMAIL_HOST")
    protonmail_port = int(os.getenv("PROTONMAIL_IMAP_PORT", "0"))
    protonmail_user = os.getenv("PROTONMAIL_USER")
    protonmail_pw = os.getenv("PROTONMAIL_PW")

    # Connect to the Protonmail IMAP server using imaplib
    # Use STARTTLS to encrypt the connection
    protonmail = imaplib.IMAP4(protonmail_host, protonmail_port)
    protonmail.starttls()
    protonmail.login(protonmail_user, protonmail_pw)
    return protonmail


def retrieve_newest_email(protonmail):
    # Check the inbox folder where solarweb reports arrive
    # Load PROTONMAIL_SOLARWEB_PATH from env variable
    protonmail_solarweb_path = os.getenv("PROTONMAIL_SOLARWEB_PATH", "INBOX")
    protonmail.select(mailbox=protonmail_solarweb_path, readonly=True)

    # Load all message IDs once, then determine newest email from INTERNALDATE in one pass.
    result, data = protonmail.search(None, 'ALL')
    all_email_ids = data[0].split() if data and data[0] else []

    if len(all_email_ids) == 0:
        print("ERROR: No emails exist in this mailbox.")
        try:
            send_issue_warning_to_issue_inbox(
                issue_title="No report emails in mailbox",
                issue_details=f"No messages were found in configured mailbox path: {protonmail_solarweb_path}",
            )
        except Exception as warning_error:
            print(f"ERROR: Could not send mailbox-empty warning email: {warning_error}")
        return None

    latest_internal_date = None
    latest_email_id = None

    for email_id in all_email_ids:
        result, fetched = protonmail.fetch(email_id, '(INTERNALDATE)')
        if result != 'OK' or not fetched:
            continue

        meta_bytes = None
        for item in fetched:
            if isinstance(item, tuple) and isinstance(item[0], (bytes, bytearray)):
                meta_bytes = item[0]
                break
            if isinstance(item, (bytes, bytearray)) and b'INTERNALDATE' in item:
                meta_bytes = item
                break

        current_internal_date = parse_internal_date(meta_bytes)
        if current_internal_date is None:
            continue

        if latest_internal_date is None or current_internal_date > latest_internal_date:
            latest_internal_date = current_internal_date
            latest_email_id = email_id

    if latest_internal_date is None or latest_email_id is None:
        print("ERROR: Could not determine the latest email date.")
        return None

    # Fetch newest email once for details.
    result, fetched = protonmail.fetch(latest_email_id, '(RFC822 INTERNALDATE)')
    if result != 'OK' or not fetched or not isinstance(fetched[0], tuple):
        print("ERROR: Could not fetch the newest email.")
        return None

    raw_email = fetched[0][1]
    email_message = email.message_from_bytes(raw_email)

    print(latest_internal_date.strftime('%a, %d %b %Y %H:%M:%S %z'))
    print(email_message['Date'])
    print(email_message['Subject'])

    days_since_last_email = (datetime.now().date() - latest_internal_date.astimezone().date()).days
    if days_since_last_email == 0:
        return email_message
    else:
        print("No new email has arrived today.")
        print(f"Most recent email is from {latest_internal_date.strftime('%a, %d %b %Y %H:%M:%S %z')}")
        if days_since_last_email >= 10:
            print(f"ERROR: Last email arrived {days_since_last_email} days ago.")
            try:
                send_issue_warning_to_issue_inbox(
                    issue_title="Report email is stale",
                    issue_details=(
                        f"Most recent report email is {days_since_last_email} days old, "
                        "which is above the 10-day threshold."
                    ),
                )
            except Exception as warning_error:
                print(f"ERROR: Could not send stale-report warning email: {warning_error}")
                    

def main():
    protonmail = connect_to_protonmail()
    retrieve_newest_email(protonmail)
    protonmail.logout()

if __name__ == "__main__":
    main()