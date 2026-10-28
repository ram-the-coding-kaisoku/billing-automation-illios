# Billing Automation

Automates billing reports for configured clients and cloud providers. The script
uses Playwright to collect billing tables from the configured dashboard, saves
individual HTML and text reports under `billing_reports/`, and can open an
Outlook draft containing the reports and a summarized observation table.
Outlook drafts are displayed for review and are not sent automatically.

## Requirements

- Python 3
- Playwright for Python
- Chromium installed for Playwright
- Microsoft Outlook on Windows, if you want the script to create an Outlook
  draft (`pywin32` is optional)

Install the runtime dependencies:

```powershell
py -m pip install playwright
py -m playwright install chromium
```

For Outlook draft support:

```powershell
py -m pip install pywin32
```

## Configuration

Create a local `billing_config.json` next to `billing_report.py`. This file is
ignored by Git because it can contain private client or account information.
Do not commit or publish it.

The JSON has one top-level `clients` object. Each property name is a client
name as it appears in the billing dashboard, and its value is an array of
providers to report for that client. Use one or more supported providers:
`AWS`, `GCP`, or `Azure`.

For example:

```json
{
  "clients": {
    "Example AWS Account": ["AWS"],
    "Example Multi-Cloud Account": ["AWS", "GCP"],
    "Example Azure Account": ["Azure"]
  }
}
```

The example client names are placeholders. Replace them with the client names
in your dashboard and assign each account its provider or providers. A client
with multiple providers generates one report for each provider.

## Run

From the project directory:

```powershell
py billing_report.py
```

Reports, run summaries, logs, and diagnostic captures are written to
`billing_reports/`. The dashboard URL and report behavior are configured in
`billing_report.py`.

## Privacy

`billing_config.json`, generated reports, logs, debug captures, and local
dependencies are excluded by `.gitignore`. Review the staged files before
pushing changes to ensure no private configuration or billing data is included.
