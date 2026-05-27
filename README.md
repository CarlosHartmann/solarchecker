# solarchecker

Solarchecker fetches daily Solar.web report emails from ProtonMail, downloads the two attached report files, stores them locally, and keeps aggregate Excel exports up to date.

## What It Does

On each run, the script:

1. Connects to ProtonMail via IMAP.
2. Opens the configured mailbox folder.
3. Finds the newest report email.
4. Extracts the two `Download` links from the email HTML.
5. Downloads both report files.
6. Moves them through a local analysis staging folder.
7. Archives them in `history/`.
8. Rebuilds two aggregate Excel files from all archived reports:
   - `history/Energiebilanz_gesamt.xlsx`
   - `history/PV-Produktion_gesamt.xlsx`

The current reports are:

1. `Energiebilanz`
2. `PV-Produktion`

## Repository Layout

- `solarchecker/`: installable package containing the application code
- `solarchecker/access_mails.py`: ProtonMail IMAP access and newest-email retrieval
- `solarchecker/process_mail.py`: email parsing, downloading, staging, archiving, dataframe loading, export refresh
- `solarchecker/notification_emails.py`: warning email sending
- `solarchecker/report_mappings.py`: centralized report header and unit mappings
- `history/`: archived daily report files and aggregate exports, ignored by git
- `seziertisch/`: local staging folder for analysis, ignored by git

## Requirements

- Python 3.11
- Poetry

Install dependencies with:

```bash
poetry install
```

## Environment Variables

### Required for IMAP report retrieval

- `PROTONMAIL_HOST`
- `PROTONMAIL_IMAP_PORT`
- `PROTONMAIL_USER`
- `PROTONMAIL_PW`
- `PROTONMAIL_SOLARWEB_PATH`

### Required for warning emails

- `PROTONMAIL_HOST`
- `PROTONMAIL_SMTP_PORT`
- `PROTONMAIL_USER`
- `PROTONMAIL_PW`
- `PROTONMAIL_SMTP_FROM`
- `PROTONMAIL_ISSUES_ADDRESS`

If no warning path is triggered, the SMTP variables are not needed for a successful normal run.

## Usage

Run the full workflow:

```bash
poetry run solarchecker
```

Rebuild aggregate exports from the current archive only:

```bash
poetry run python -m solarchecker.build_history_exports
```

## Output Files

Daily downloaded reports are archived under:

```text
history/<email-date-folder>/
```

Aggregate exports are written to:

```text
history/Energiebilanz_gesamt.xlsx
history/PV-Produktion_gesamt.xlsx
```

Existing files at the target location are overwritten intentionally.

## Data Model

The scripts normalize report headers into human-readable English dataframe columns.

Examples:

- `energy_balance_total_generation_wh`
- `energy_balance_total_consumption_wh`
- `pv_production_system_total_kwh`
- `pv_production_inverter_energy_symo_17_5_3_m_1_kwh`

The header mappings live in `report_mappings.py`.

## Warning Behavior

Warning emails are sent only in selected failure cases:

1. Missing required ProtonMail environment variables
2. Configured mailbox contains no messages
3. Stale report email older than 10 days

Other retrieval issues currently print to terminal only.

## Panel Health Checks

After a new report is archived, the script evaluates the latest available day in
the normalized history dataframe and runs four panel-health checks.

If one or more checks fail, the script sends one combined warning email with all
findings instead of sending one email per failed test.

### 1. Zero-production test

This is a hard-failure check.

- Field used: `pv_production_system_total_kwh`
- Current threshold: production less than or equal to `0.1 kWh`

If the total system production for the newest report day is effectively zero,
the script treats that as a strong sign that the installation may not be
working.

### 2. Sudden production drop test

This checks whether the newest day is implausibly weak compared with recent
history.

- Field used: `pv_production_system_total_kwh`
- Baseline: median of the previous 7 available report days
- Minimum history required: 3 earlier days
- Minimum baseline required: `1.0 kWh`
- Warning threshold: current production below `35%` of that recent median

This is meant to catch abrupt system-wide underperformance while avoiding noisy
warnings on very low-production days.

### 3. Inverter mismatch test

This checks whether one inverter suddenly underperforms relative to the other.

- Fields used:
   - `pv_production_inverter_energy_per_kwp_symo_12_5_3_m_2_kwh_per_kwp`
   - `pv_production_inverter_energy_per_kwp_symo_17_5_3_m_1_kwh_per_kwp`
- Baseline: median of the historical ratio between those two normalized values
   over the previous 14 available report days
- Minimum history required: 5 earlier days
- Warning threshold: deviation of more than `35%` from the historical median
   ratio

Because the values are normalized per kWp, this test is intended to detect a
partial failure on one inverter rather than a site-wide low-production day.

### 4. Inverter minimum-share test

This checks whether either inverter contributes implausibly little to the
day's total system production.

- Fields used:
   - `pv_production_inverter_energy_symo_12_5_3_m_2_kwh`
   - `pv_production_inverter_energy_symo_17_5_3_m_1_kwh`
   - `pv_production_system_total_kwh`
- Minimum total production required: `3.0 kWh`
- Warning threshold: any single inverter below `30%` of the day's total

This guards against single-inverter underperformance even when total production
is not low enough to trigger the sudden-drop test.

### When checks run

These checks run automatically as part of:

```bash
poetry run solarchecker
```

They use the archived files in `history/`, so the aggregate exports and the
warning logic are both based on the same normalized history data.

## Notes

- `history/` and `seziertisch/` are ignored by git.
- Aggregate exports are rebuilt from the archive, so they stay consistent with the contents of `history/`.

## Yearly February Upload Task (launchd)

An additional launchd task can upload yearly aggregate files to the remote
storage target.

- Launch agent file: `~/Library/LaunchAgents/solarchecker.february_history_upload.plist`
- Schedule: every year on February 5 at 13:00 local time
- Execution mode: inline `zsh -lc` command in the launch agent

What it does on each run:

1. Computes the remote subdirectory name dynamically as `YYYY-FebYYYY+1`
   (example for 2026: `2026-Feb2027`).
2. Creates the remote directory via:
   `rclone mkdir almazen:solardaten/<computed-dir>`
3. Finds `*gesamt*.xlsx` files in `history/`.
4. Moves each of those files via:
   `rclone-custom move <file> almazen:solardaten/<computed-dir>`

The launch command sources `~/.zshrc` so that the `rclone-custom` shell
function is available in the non-interactive launchd environment.