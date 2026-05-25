"""
report_mappings: Centralized report type, header, and unit mappings.
"""

REPORT_TYPE_PATTERNS = {
    "energiebilanz": "energy_balance",
    "pv-produktion": "pv_production",
}

ENERGY_BALANCE_LABELS = {
    "Gesamt Erzeugung": "total_generation",
    "Gesamt Verbrauch": "total_consumption",
    "Eigenverbrauch": "self_consumption",
    "Energie ins Netz eingespeist": "energy_fed_to_grid",
    "Energie vom Netz bezogen": "energy_drawn_from_grid",
}

PV_PRODUCTION_LABELS = {
    "Energie Pro Wechselrichter | Symo 12.5-3-M (2)": "inverter_energy_symo_12_5_3_m_2",
    "Energie Pro Wechselrichter | Symo 17.5-3-M (1)": "inverter_energy_symo_17_5_3_m_1",
    "Energie Pro Wechselrichter pro kWp | Symo 12.5-3-M (2)": "inverter_energy_per_kwp_symo_12_5_3_m_2",
    "Energie Pro Wechselrichter pro kWp | Symo 17.5-3-M (1)": "inverter_energy_per_kwp_symo_17_5_3_m_1",
    "Gesamtanlage": "system_total",
}

UNIT_NORMALIZATION = {
    "Wh": "wh",
    "kWh": "kwh",
    "kWh/kWp": "kwh_per_kwp",
    "dd.MM.yyyy": "date",
}
