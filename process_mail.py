"""
process_mail: Download, normalize, archive, and analyze Solar.web report emails.

Warning tests implemented
==============================================

1. Zero-production test:
    If pv_production_system_total_kwh is zero or near zero on a day that should
    have non-trivial production, warn.
    Location: _check_zero_production()

2. Sudden production drop test:
    Compare today's total production against a rolling median of recent similar
    days. If it drops below a configurable fraction, warn.
    Location: _check_sudden_production_drop()

3. Inverter mismatch test:
    Compare the two inverter outputs. If one inverter is consistently much lower
    than the other relative to its historical ratio, warn.
    Location: _check_inverter_mismatch()

Trigger point:
    _run_panel_health_checks(), called from process() after the newest reports
    have been archived and aggregate exports have been refreshed.

Recommended first implementation
================================

If the customer has not defined the rule set yet, the safest first warning rule
is usually:

- a multi-day sudden production drop test, plus
- an inverter mismatch test, plus
- a zero-production hard failure test.

Those three together are simple to explain and catch both total outages and
partial inverter failures.
"""

from __future__ import annotations

import re
import shutil
import tempfile
import unicodedata
import os
from datetime import datetime
from email.message import Message
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlparse
from urllib.parse import unquote
from urllib.request import Request, urlopen

import pandas as pd
from build_history_exports import build_history_exports
from notification_emails import send_report_issue_warning
from report_mappings import (
    ENERGY_BALANCE_LABELS,
    PV_PRODUCTION_LABELS,
    REPORT_TYPE_PATTERNS,
    UNIT_NORMALIZATION,
)


ZERO_PRODUCTION_THRESHOLD_KWH = 0.1
SUDDEN_DROP_LOOKBACK_DAYS = 7
SUDDEN_DROP_MIN_HISTORY_DAYS = 3
SUDDEN_DROP_MIN_RATIO = 0.35
SUDDEN_DROP_BASELINE_MIN_KWH = 1.0
INVERTER_MISMATCH_LOOKBACK_DAYS = 14
INVERTER_MISMATCH_MIN_HISTORY_DAYS = 5
INVERTER_MISMATCH_DEVIATION_RATIO = 0.35


class DownloadLinkParser(HTMLParser):
    """Collect href targets for anchor tags whose visible text is exactly 'Download'."""

    def __init__(self) -> None:
        super().__init__()
        self.links: list[str] = []
        self._in_anchor = False
        self._current_href: str | None = None
        self._text_buffer: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() != "a":
            return
        self._in_anchor = True
        self._text_buffer = []
        self._current_href = None
        for key, value in attrs:
            if key.lower() == "href" and value:
                self._current_href = value
                break

    def handle_data(self, data: str) -> None:
        if self._in_anchor:
            self._text_buffer.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() != "a":
            return
        if self._in_anchor and self._current_href:
            anchor_text = "".join(self._text_buffer).strip()
            if anchor_text == "Download":
                self.links.append(self._current_href)
        self._in_anchor = False
        self._current_href = None
        self._text_buffer = []


def _slugify_token(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value)
    ascii_only = normalized.encode("ascii", "ignore").decode("ascii")
    token = re.sub(r"[^0-9A-Za-z]+", "_", ascii_only).strip("_").lower()
    return token or "value"


def _normalize_unit(unit_value: str) -> str:
    stripped = unit_value.strip().strip("[]")
    if not stripped:
        return ""
    return UNIT_NORMALIZATION.get(stripped, _slugify_token(stripped))


def _detect_report_type(file_name: str) -> str | None:
    lower_name = file_name.lower()
    for pattern, report_type in REPORT_TYPE_PATTERNS.items():
        if pattern in lower_name:
            return report_type
    return None


def _build_column_names(report_type: str, labels: list[str], units: list[str]) -> list[str]:
    prefix = report_type
    if report_type == "energy_balance":
        label_map = ENERGY_BALANCE_LABELS
    else:
        label_map = PV_PRODUCTION_LABELS

    columns: list[str] = []
    for label, unit in zip(labels, units):
        stripped_label = label.strip()
        if stripped_label == "Datum und Uhrzeit":
            columns.append("report_date")
            continue

        base_name = label_map.get(stripped_label, _slugify_token(stripped_label))
        unit_name = _normalize_unit(unit)
        if unit_name and unit_name != "date":
            columns.append(f"{prefix}_{base_name}_{unit_name}")
        else:
            columns.append(f"{prefix}_{base_name}")
    return columns


def _load_single_report_dataframe(report_path: Path, report_type: str) -> pd.DataFrame:
    raw = pd.read_excel(report_path, header=None, engine="openpyxl")
    if raw.shape[0] < 3:
        return pd.DataFrame()

    labels = [str(value).strip() if pd.notna(value) else "" for value in raw.iloc[0].tolist()]
    units = [str(value).strip() if pd.notna(value) else "" for value in raw.iloc[1].tolist()]
    columns = _build_column_names(report_type, labels, units)

    data = raw.iloc[2:, : len(columns)].copy()
    if data.empty:
        return pd.DataFrame()

    data.columns = columns
    if "report_date" not in data.columns:
        return pd.DataFrame()

    report_date_series = data["report_date"].astype(str).str.strip()
    valid_rows = report_date_series.ne("") & report_date_series.str.lower().ne("nan")
    data = data.loc[valid_rows].copy()
    if data.empty:
        return pd.DataFrame()

    data["report_date"] = pd.to_datetime(data["report_date"], format="%d.%m.%Y", errors="coerce")
    data = data.dropna(subset=["report_date"])
    if data.empty:
        return pd.DataFrame()

    for column in data.columns:
        if column != "report_date":
            data[column] = pd.to_numeric(data[column], errors="coerce")

    data["archive_folder"] = report_path.parent.name
    data[f"{report_type}_source_file"] = report_path.name
    return data


def load_history_dataframe(history_root: Path) -> pd.DataFrame:
    xlsx_files = sorted(history_root.glob("*/*.xlsx"))
    if not xlsx_files:
        return pd.DataFrame()

    parsed_by_type: dict[str, list[pd.DataFrame]] = {}
    for report_path in xlsx_files:
        report_type = _detect_report_type(report_path.name)
        if report_type is None:
            continue

        report_df = _load_single_report_dataframe(report_path, report_type)
        if not report_df.empty:
            parsed_by_type.setdefault(report_type, []).append(report_df)

    if not parsed_by_type:
        return pd.DataFrame()

    keys = ["archive_folder", "report_date"]
    combined: pd.DataFrame | None = None

    for report_type in sorted(parsed_by_type.keys()):
        source_column = f"{report_type}_source_file"
        type_df = pd.concat(parsed_by_type[report_type], ignore_index=True)
        type_df = type_df.sort_values(keys + [source_column])
        type_df = type_df.drop_duplicates(subset=keys, keep="last")

        if combined is None:
            combined = type_df
        else:
            combined = combined.merge(type_df, on=keys, how="outer")

    if combined is None:
        return pd.DataFrame()

    return combined.sort_values(["report_date", "archive_folder"]).reset_index(drop=True)


def _check_zero_production(latest_row: pd.Series) -> str | None:
    production = latest_row.get("pv_production_system_total_kwh")
    if pd.isna(production):
        return None

    production_value = float(production)
    if production_value <= ZERO_PRODUCTION_THRESHOLD_KWH:
        return (
            "Zero-production test failed: "
            f"pv_production_system_total_kwh is {production_value:.3f} kWh "
            f"for {latest_row['report_date'].date()}."
        )
    return None


def _check_sudden_production_drop(history_df: pd.DataFrame, latest_row: pd.Series) -> str | None:
    production_column = "pv_production_system_total_kwh"
    if production_column not in history_df.columns or pd.isna(latest_row.get(production_column)):
        return None

    prior_rows = history_df.loc[history_df["report_date"] < latest_row["report_date"]].sort_values("report_date")
    prior_values = prior_rows[production_column].dropna().tail(SUDDEN_DROP_LOOKBACK_DAYS)
    if len(prior_values) < SUDDEN_DROP_MIN_HISTORY_DAYS:
        return None

    baseline = float(prior_values.median())
    if baseline < SUDDEN_DROP_BASELINE_MIN_KWH:
        return None

    current_production = float(latest_row[production_column])
    ratio = current_production / baseline if baseline else 0.0
    if ratio < SUDDEN_DROP_MIN_RATIO:
        return (
            "Sudden production drop test failed: "
            f"current production is {current_production:.3f} kWh versus a recent median of {baseline:.3f} kWh "
            f"({ratio:.1%} of baseline)."
        )
    return None


def _check_inverter_mismatch(history_df: pd.DataFrame, latest_row: pd.Series) -> str | None:
    inverter_a_column = "pv_production_inverter_energy_per_kwp_symo_12_5_3_m_2_kwh_per_kwp"
    inverter_b_column = "pv_production_inverter_energy_per_kwp_symo_17_5_3_m_1_kwh_per_kwp"
    if inverter_a_column not in history_df.columns or inverter_b_column not in history_df.columns:
        return None

    current_a = latest_row.get(inverter_a_column)
    current_b = latest_row.get(inverter_b_column)
    if pd.isna(current_a) or pd.isna(current_b) or float(current_b) <= 0:
        return None

    prior_rows = history_df.loc[history_df["report_date"] < latest_row["report_date"]].sort_values("report_date")
    ratio_frame = prior_rows[[inverter_a_column, inverter_b_column]].dropna()
    ratio_frame = ratio_frame.loc[ratio_frame[inverter_b_column] > 0]
    historical_ratios = (ratio_frame[inverter_a_column] / ratio_frame[inverter_b_column]).tail(INVERTER_MISMATCH_LOOKBACK_DAYS)
    if len(historical_ratios) < INVERTER_MISMATCH_MIN_HISTORY_DAYS:
        return None

    historical_ratio = float(historical_ratios.median())
    if historical_ratio <= 0:
        return None

    current_ratio = float(current_a) / float(current_b)
    deviation = abs(current_ratio - historical_ratio) / historical_ratio
    if deviation < INVERTER_MISMATCH_DEVIATION_RATIO:
        return None

    underperforming_inverter = "Symo 12.5-3-M (2)"
    if current_ratio > historical_ratio:
        underperforming_inverter = "Symo 17.5-3-M (1)"

    return (
        "Inverter mismatch test failed: "
        f"historical normalized inverter ratio median is {historical_ratio:.3f}, current ratio is {current_ratio:.3f}. "
        f"This suggests underperformance by {underperforming_inverter}."
    )


def _run_panel_health_checks(history_root: Path, report_email: Message) -> list[str]:
    history_df = load_history_dataframe(history_root)
    if history_df.empty:
        return []

    latest_row = history_df.sort_values(["report_date", "archive_folder"]).iloc[-1]
    findings = [
        _check_zero_production(latest_row),
        _check_sudden_production_drop(history_df, latest_row),
        _check_inverter_mismatch(history_df, latest_row),
    ]
    findings = [finding for finding in findings if finding]
    if not findings:
        print("No panel-health warning conditions triggered for the latest report.")
        return []

    print("Panel-health warning conditions triggered:")
    for finding in findings:
        print(f"- {finding}")

    try:
        send_report_issue_warning(
            report_email=report_email,
            issue_title="Solar production anomaly detected",
            issue_details="\n\n".join(findings),
            extra_recipients=[os.getenv("PROTONMAIL_ISSUES_ADDRESS")],
        )
    except Exception as warning_error:
        print(f"ERROR: Could not send panel-health warning email: {warning_error}")

    return findings


def _get_email_html(email_message: Message) -> str:
    if email_message.is_multipart():
        for part in email_message.walk():
            content_type = part.get_content_type()
            if content_type == "text/html":
                payload = part.get_payload(decode=True)
                if payload is None:
                    continue
                charset = part.get_content_charset() or "utf-8"
                return payload.decode(charset, errors="replace")
    else:
        if email_message.get_content_type() == "text/html":
            payload = email_message.get_payload(decode=True)
            if payload is None:
                return ""
            charset = email_message.get_content_charset() or "utf-8"
            return payload.decode(charset, errors="replace")
    return ""


def _extract_download_links(html_body: str) -> list[str]:
    parser = DownloadLinkParser()
    parser.feed(html_body)
    return parser.links


def _safe_filename_from_headers(url: str, content_disposition: str | None) -> str:
    if content_disposition:
        match = re.search(r'filename\*?=(?:UTF-8\'\')?"?([^";]+)"?', content_disposition)
        if match:
            candidate = unquote(match.group(1)).replace("/", "_").replace("\\", "_")
            candidate = Path(candidate).name
            if candidate:
                return candidate

    parsed = urlparse(url)
    fallback = Path(unquote(parsed.path)).name
    return fallback or f"download_{datetime.now().strftime('%Y%m%d_%H%M%S')}"


def _download_to_temp(url: str, temp_dir: Path) -> Path:
    request = Request(
        url,
        headers={
            "User-Agent": "solarchecker/0.1 (+https://local)",
        },
    )
    with urlopen(request, timeout=60) as response:
        content_disposition = response.headers.get("Content-Disposition")
        filename = _safe_filename_from_headers(url, content_disposition)
        target_path = temp_dir / filename
        unique_counter = 1
        while target_path.exists():
            target_path = temp_dir / f"{Path(filename).stem}_{unique_counter}{Path(filename).suffix}"
            unique_counter += 1
        with target_path.open("wb") as output_file:
            shutil.copyfileobj(response, output_file)
    return target_path


def _analyze_downloads(downloaded_files: list[Path]) -> None:
    # File-level analysis entry point for freshly downloaded reports.
    for file_path in downloaded_files:
        size = file_path.stat().st_size
        print(f"Prepared for analysis: {file_path.name} ({size} bytes)")


def _print_history_dataframe_summary(history_root: Path) -> None:
    history_df = load_history_dataframe(history_root)
    if history_df.empty:
        print("No parseable history report data found.")
        return

    print("History dataframe columns:")
    for column in history_df.columns:
        print(f"- {column}")

    print("History dataframe preview:")
    print(history_df.tail(5).to_string(index=False))


def _refresh_history_exports(history_root: Path) -> None:
    written_files = build_history_exports(history_root)
    print("Updated aggregate history exports:")
    for report_type, output_path in written_files.items():
        print(f"- {report_type}: {output_path}")


def _make_folder_stamp(email_message: Message) -> str:
    date_header = email_message.get("Date")
    if date_header:
        return re.sub(r"[^0-9A-Za-z_-]", "_", date_header)[:80]
    return datetime.now().strftime("%Y-%m-%d_%H%M%S")


def _move_to_analysis_folder(downloaded_files: list[Path], email_message: Message) -> list[Path]:
    base_dir = Path(__file__).resolve().parent
    analysis_root = base_dir / "seziertisch"
    analysis_root.mkdir(parents=True, exist_ok=True)

    folder_stamp = _make_folder_stamp(email_message)
    destination_dir = analysis_root / folder_stamp
    destination_dir.mkdir(parents=True, exist_ok=True)

    staged_paths: list[Path] = []
    for source_path in downloaded_files:
        target_path = destination_dir / source_path.name
        if target_path.exists():
            target_path.unlink()

        shutil.move(str(source_path), str(target_path))
        staged_paths.append(target_path)

    return staged_paths


def _archive_downloads(analyzed_files: list[Path], email_message: Message) -> list[Path]:
    base_dir = Path(__file__).resolve().parent
    history_root = base_dir / "history"
    history_root.mkdir(parents=True, exist_ok=True)

    folder_stamp = _make_folder_stamp(email_message)
    destination_dir = history_root / folder_stamp
    destination_dir.mkdir(parents=True, exist_ok=True)

    stored_paths: list[Path] = []
    for source_path in analyzed_files:
        target_path = destination_dir / source_path.name
        if target_path.exists():
            target_path.unlink()

        shutil.move(str(source_path), str(target_path))
        stored_paths.append(target_path)

    return stored_paths


def process(email_message: Message | None) -> None:
    base_dir = Path(__file__).resolve().parent
    history_root = base_dir / "history"

    if email_message is None:
        print("No message to process.")
        _refresh_history_exports(history_root)
        _print_history_dataframe_summary(history_root)
        return

    html_body = _get_email_html(email_message)
    if not html_body:
        print("No HTML content found in message.")
        _refresh_history_exports(history_root)
        _print_history_dataframe_summary(history_root)
        return

    download_links = _extract_download_links(html_body)
    if len(download_links) < 2:
        print(f"Expected 2 download links, found {len(download_links)}.")
        print("Links found:")
        for link in download_links:
            print(f"- {link}")
        _refresh_history_exports(history_root)
        _print_history_dataframe_summary(history_root)
        return

    selected_links = download_links[:2]
    print("Found download links:")
    for link in selected_links:
        print(f"- {link}")

    with tempfile.TemporaryDirectory(prefix="solarchecker_") as temp_dir_str:
        temp_dir = Path(temp_dir_str)
        downloaded_files: list[Path] = []

        for link in selected_links:
            downloaded_path = _download_to_temp(link, temp_dir)
            downloaded_files.append(downloaded_path)
            print(f"Downloaded to temp: {downloaded_path}")

        analysis_files = _move_to_analysis_folder(downloaded_files, email_message)
        print("Moved files to analysis folder:")
        for staged in analysis_files:
            print(f"- {staged}")

        _analyze_downloads(analysis_files)
        stored_paths = _archive_downloads(analysis_files, email_message)

    print("Stored files:")
    for stored in stored_paths:
        print(f"- {stored}")

    _refresh_history_exports(history_root)
    _run_panel_health_checks(history_root, email_message)
    _print_history_dataframe_summary(history_root)