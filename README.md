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

- `main.py`: entrypoint for the full workflow
- `access_mails.py`: ProtonMail IMAP access and newest-email retrieval
- `process_mail.py`: email parsing, downloading, staging, archiving, dataframe loading, export refresh
- `build_history_exports.py`: standalone script to rebuild the aggregate Excel exports from `history/`
- `notification_emails.py`: warning email sending
- `report_mappings.py`: centralized report header and unit mappings
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
poetry run python main.py
```

Rebuild aggregate exports from the current archive only:

```bash
poetry run python build_history_exports.py
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

## Notes

- `history/` and `seziertisch/` are ignored by git.
- Aggregate exports are rebuilt from the archive, so they stay consistent with the contents of `history/`.