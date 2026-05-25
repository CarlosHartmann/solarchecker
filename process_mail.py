"""
process_mail: Cleanly process a solarweb update email.
"""

from __future__ import annotations

import re
import shutil
import tempfile
import unicodedata
from datetime import datetime
from email.message import Message
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlparse
from urllib.parse import unquote
from urllib.request import Request, urlopen

import pandas as pd
from report_mappings import (
    ENERGY_BALANCE_LABELS,
    PV_PRODUCTION_LABELS,
    REPORT_TYPE_PATTERNS,
    UNIT_NORMALIZATION,
)


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
    # Placeholder for upcoming analysis logic for freshly downloaded files.
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
        unique_counter = 1
        while target_path.exists():
            target_path = destination_dir / f"{source_path.stem}_{unique_counter}{source_path.suffix}"
            unique_counter += 1

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
        unique_counter = 1
        while target_path.exists():
            target_path = destination_dir / f"{source_path.stem}_{unique_counter}{source_path.suffix}"
            unique_counter += 1

        shutil.move(str(source_path), str(target_path))
        stored_paths.append(target_path)

    return stored_paths


def process(email_message: Message | None) -> None:
    base_dir = Path(__file__).resolve().parent
    history_root = base_dir / "history"

    if email_message is None:
        print("No message to process.")
        _print_history_dataframe_summary(history_root)
        return

    html_body = _get_email_html(email_message)
    if not html_body:
        print("No HTML content found in message.")
        _print_history_dataframe_summary(history_root)
        return

    download_links = _extract_download_links(html_body)
    if len(download_links) < 2:
        print(f"Expected 2 download links, found {len(download_links)}.")
        print("Links found:")
        for link in download_links:
            print(f"- {link}")
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

    _print_history_dataframe_summary(history_root)