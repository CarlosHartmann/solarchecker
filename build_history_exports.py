"""
build_history_exports: Aggregate report files from history into combined Excel files.
"""

from __future__ import annotations

import re
import unicodedata
from pathlib import Path

import pandas as pd

from report_mappings import (
    ENERGY_BALANCE_LABELS,
    PV_PRODUCTION_LABELS,
    REPORT_TYPE_PATTERNS,
    UNIT_NORMALIZATION,
)


ENERGY_BALANCE_EXPORT_NAME = "Energiebilanz_gesamt.xlsx"
PV_PRODUCTION_EXPORT_NAME = "PV-Produktion_gesamt.xlsx"


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
    label_map = ENERGY_BALANCE_LABELS if report_type == "energy_balance" else PV_PRODUCTION_LABELS

    columns: list[str] = []
    for label, unit in zip(labels, units):
        stripped_label = label.strip()
        if stripped_label == "Datum und Uhrzeit":
            columns.append("report_date")
            continue

        base_name = label_map.get(stripped_label, _slugify_token(stripped_label))
        unit_name = _normalize_unit(unit)
        if unit_name and unit_name != "date":
            columns.append(f"{report_type}_{base_name}_{unit_name}")
        else:
            columns.append(f"{report_type}_{base_name}")
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


def _load_history_reports_by_type(history_root: Path) -> dict[str, pd.DataFrame]:
    xlsx_files = sorted(history_root.glob("*/*.xlsx"))
    parsed_by_type: dict[str, list[pd.DataFrame]] = {}

    for report_path in xlsx_files:
        report_type = _detect_report_type(report_path.name)
        if report_type is None:
            continue

        report_df = _load_single_report_dataframe(report_path, report_type)
        if not report_df.empty:
            parsed_by_type.setdefault(report_type, []).append(report_df)

    deduplicated_by_type: dict[str, pd.DataFrame] = {}
    keys = ["archive_folder", "report_date"]
    for report_type, frames in parsed_by_type.items():
        source_column = f"{report_type}_source_file"
        combined = pd.concat(frames, ignore_index=True)
        combined = combined.sort_values(keys + [source_column])
        combined = combined.drop_duplicates(subset=keys, keep="last")
        deduplicated_by_type[report_type] = combined.sort_values(["report_date", "archive_folder"]).reset_index(drop=True)

    return deduplicated_by_type


def load_history_dataframe(history_root: Path) -> pd.DataFrame:
    parsed_by_type = _load_history_reports_by_type(history_root)
    if not parsed_by_type:
        return pd.DataFrame()

    keys = ["archive_folder", "report_date"]
    combined: pd.DataFrame | None = None

    for report_type in sorted(parsed_by_type.keys()):
        type_df = parsed_by_type[report_type]
        if combined is None:
            combined = type_df
        else:
            combined = combined.merge(type_df, on=keys, how="outer")

    if combined is None:
        return pd.DataFrame()

    return combined.sort_values(["report_date", "archive_folder"]).reset_index(drop=True)


def build_history_exports(history_root: Path, output_dir: Path | None = None) -> dict[str, Path]:
    output_root = output_dir if output_dir is not None else history_root
    output_root.mkdir(parents=True, exist_ok=True)

    parsed_by_type = _load_history_reports_by_type(history_root)
    written_files: dict[str, Path] = {}

    energy_balance_df = parsed_by_type.get("energy_balance", pd.DataFrame())
    energy_balance_path = output_root / ENERGY_BALANCE_EXPORT_NAME
    energy_balance_df.to_excel(energy_balance_path, index=False)
    written_files["energy_balance"] = energy_balance_path

    pv_production_df = parsed_by_type.get("pv_production", pd.DataFrame())
    pv_production_path = output_root / PV_PRODUCTION_EXPORT_NAME
    pv_production_df.to_excel(pv_production_path, index=False)
    written_files["pv_production"] = pv_production_path

    return written_files


def main() -> None:
    base_dir = Path(__file__).resolve().parent
    history_root = base_dir / "history"
    written_files = build_history_exports(history_root)

    print("Updated aggregate history exports:")
    for report_type, output_path in written_files.items():
        print(f"- {report_type}: {output_path}")


if __name__ == "__main__":
    main()