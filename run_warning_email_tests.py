"""
run_warning_email_tests: Trigger all warning-email scenarios through the real logic.

This script is meant for controlled end-to-end testing of warning paths.
It uses fake IMAP contexts and temporary history workbooks so that the normal
decision logic is exercised before emails are sent.
"""

from __future__ import annotations

import os
import tempfile
from contextlib import contextmanager
from email.message import EmailMessage
from pathlib import Path

import access_mails
from process_mail import _run_panel_health_checks


TEST_SUBJECT_PREFIX = "[TEST -- PLEASE IGNORE] "
ENERGY_BALANCE_HEADERS = [
    "Datum und Uhrzeit",
    "Gesamt Erzeugung",
    "Gesamt Verbrauch",
    "Eigenverbrauch",
    "Energie ins Netz eingespeist",
    "Energie vom Netz bezogen",
]
ENERGY_BALANCE_UNITS = ["[dd.MM.yyyy]", "[Wh]", "[Wh]", "[Wh]", "[Wh]", "[Wh]"]
PV_HEADERS = [
    "Datum und Uhrzeit",
    "Energie Pro Wechselrichter | Symo 12.5-3-M (2)",
    "Energie Pro Wechselrichter | Symo 17.5-3-M (1)",
    "Energie Pro Wechselrichter pro kWp | Symo 12.5-3-M (2)",
    "Energie Pro Wechselrichter pro kWp | Symo 17.5-3-M (1)",
    "Gesamtanlage",
]
PV_UNITS = ["[dd.MM.yyyy]", "[kWh]", "[kWh]", "[kWh/kWp]", "[kWh/kWp]", "[kWh]"]


@contextmanager
def temporary_env(overrides: dict[str, str | None]):
    previous = {key: os.environ.get(key) for key in overrides}
    try:
        for key, value in overrides.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        yield
    finally:
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


class EmptyMailboxProtonmail:
    def select(self, mailbox: str, readonly: bool = True):
        return "OK", [b""]

    def search(self, *_args):
        return "OK", [b""]


class StaleReportProtonmail:
    def __init__(self, raw_email: bytes) -> None:
        self.raw_email = raw_email

    def select(self, mailbox: str, readonly: bool = True):
        return "OK", [b""]

    def search(self, *_args):
        return "OK", [b"1"]

    def fetch(self, email_id: bytes, query: str):
        if query == "(INTERNALDATE)":
            return "OK", [(b'1 (INTERNALDATE "12-May-2026 08:15:01 +0000")', None)]
        if query == "(RFC822 INTERNALDATE)":
            return "OK", [(b'1 (RFC822 {123})', self.raw_email)]
        return "NO", []


def _build_report_email() -> EmailMessage:
    message = EmailMessage()
    message["Subject"] = "Solar.web report"
    message["To"] = os.getenv("PROTONMAIL_ISSUES_ADDRESS", "issues@example.com")
    message["Cc"] = os.getenv("PROTONMAIL_SMTP_FROM", "issues@example.com")
    message["Date"] = "Mon, 12 May 2026 10:14:54 +0200"
    message.set_content("Synthetic report for warning-email test.")
    return message


def _write_report_workbooks(history_root: Path, folder_name: str, rows: list[dict[str, float | str]]) -> None:
    report_dir = history_root / folder_name
    report_dir.mkdir(parents=True, exist_ok=True)

    energy_balance_rows = [
        [row["date"], row["generation_wh"], row["consumption_wh"], row["self_consumption_wh"], row["fed_to_grid_wh"], row["drawn_from_grid_wh"]]
        for row in rows
    ]
    pv_rows = [
        [row["date"], row["inv_a_kwh"], row["inv_b_kwh"], row["inv_a_norm"], row["inv_b_norm"], row["system_total_kwh"]]
        for row in rows
    ]

    import pandas as pd

    energy_balance_df = pd.DataFrame([ENERGY_BALANCE_HEADERS, ENERGY_BALANCE_UNITS, *energy_balance_rows])
    pv_df = pd.DataFrame([PV_HEADERS, PV_UNITS, *pv_rows])

    energy_balance_df.to_excel(report_dir / "Energiebilanz_test.xlsx", header=False, index=False)
    pv_df.to_excel(report_dir / "PV-Produktion_test.xlsx", header=False, index=False)


def _run_zero_production_scenario(report_email: EmailMessage) -> None:
    with tempfile.TemporaryDirectory(prefix="solarchecker_zero_") as temp_dir:
        history_root = Path(temp_dir)
        _write_report_workbooks(
            history_root,
            "2026-05-25_zero",
            [
                {
                    "date": "25.05.2026",
                    "generation_wh": 0,
                    "consumption_wh": 5000,
                    "self_consumption_wh": 0,
                    "fed_to_grid_wh": 0,
                    "drawn_from_grid_wh": 5000,
                    "inv_a_kwh": 0,
                    "inv_b_kwh": 0,
                    "inv_a_norm": 0,
                    "inv_b_norm": 0,
                    "system_total_kwh": 0,
                }
            ],
        )
        _run_panel_health_checks(history_root, report_email)


def _run_sudden_drop_scenario(report_email: EmailMessage) -> None:
    with tempfile.TemporaryDirectory(prefix="solarchecker_drop_") as temp_dir:
        history_root = Path(temp_dir)
        rows = []
        for day in range(20, 24):
            rows.append(
                {
                    "date": f"{day:02d}.05.2026",
                    "generation_wh": 15000,
                    "consumption_wh": 9000,
                    "self_consumption_wh": 6000,
                    "fed_to_grid_wh": 9000,
                    "drawn_from_grid_wh": 3000,
                    "inv_a_kwh": 7.5,
                    "inv_b_kwh": 7.5,
                    "inv_a_norm": 1.0,
                    "inv_b_norm": 1.0,
                    "system_total_kwh": 15.0,
                }
            )
        rows.append(
            {
                "date": "25.05.2026",
                "generation_wh": 1000,
                "consumption_wh": 9000,
                "self_consumption_wh": 1000,
                "fed_to_grid_wh": 0,
                "drawn_from_grid_wh": 8000,
                "inv_a_kwh": 0.5,
                "inv_b_kwh": 0.5,
                "inv_a_norm": 1.0,
                "inv_b_norm": 1.0,
                "system_total_kwh": 1.0,
            }
        )
        _write_report_workbooks(history_root, "2026-05_drop", rows)
        _run_panel_health_checks(history_root, report_email)


def _run_inverter_mismatch_scenario(report_email: EmailMessage) -> None:
    with tempfile.TemporaryDirectory(prefix="solarchecker_inverter_") as temp_dir:
        history_root = Path(temp_dir)
        rows = []
        for day in range(18, 24):
            rows.append(
                {
                    "date": f"{day:02d}.05.2026",
                    "generation_wh": 20000,
                    "consumption_wh": 12000,
                    "self_consumption_wh": 6000,
                    "fed_to_grid_wh": 14000,
                    "drawn_from_grid_wh": 6000,
                    "inv_a_kwh": 10.0,
                    "inv_b_kwh": 10.0,
                    "inv_a_norm": 1.0,
                    "inv_b_norm": 1.0,
                    "system_total_kwh": 20.0,
                }
            )
        rows.append(
            {
                "date": "25.05.2026",
                "generation_wh": 20000,
                "consumption_wh": 12000,
                "self_consumption_wh": 6000,
                "fed_to_grid_wh": 14000,
                "drawn_from_grid_wh": 6000,
                "inv_a_kwh": 5.0,
                "inv_b_kwh": 15.0,
                "inv_a_norm": 0.3,
                "inv_b_norm": 1.0,
                "system_total_kwh": 20.0,
            }
        )
        _write_report_workbooks(history_root, "2026-05_inverter", rows)
        _run_panel_health_checks(history_root, report_email)


def main() -> None:
    print("Running warning-email scenario tests.")
    report_email = _build_report_email()

    with temporary_env({"WARNING_EMAIL_SUBJECT_PREFIX": TEST_SUBJECT_PREFIX}):
        print("Scenario 1: Missing env vars")
        with temporary_env({"PROTONMAIL_IMAP_PORT": None}):
            try:
                access_mails.connect_to_protonmail()
            except EnvironmentError:
                pass

        print("Scenario 2: Mailbox empty")
        access_mails.retrieve_newest_email(EmptyMailboxProtonmail())

        print("Scenario 3: Stale report")
        stale_email = _build_report_email()
        access_mails.retrieve_newest_email(StaleReportProtonmail(stale_email.as_bytes()))

        print("Scenario 4: Zero production")
        _run_zero_production_scenario(report_email)

        print("Scenario 5: Sudden production drop")
        _run_sudden_drop_scenario(report_email)

        print("Scenario 6: Inverter mismatch")
        _run_inverter_mismatch_scenario(report_email)

    print("Finished warning-email scenario tests.")


if __name__ == "__main__":
    main()