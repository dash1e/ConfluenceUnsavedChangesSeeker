# Confluence Unsaved Changes Seeker

This repository contains a read-only script that queries Confluence Cloud APIs and generates a Markdown report of sites that currently have unsaved changes (draft pages).

## What it outputs

A `.md` table containing:

- Site name
- Owner (if exposed by API; otherwise noted)
- Last editor (for the most recently modified draft)
- Link (to open the latest draft)
- Current status
- Possible errors

## Safety

The script performs **GET requests only** and does not create, modify, or delete data.

## Prerequisites

- Python 3.9+
- Atlassian account email
- Atlassian API token

## Usage

```bash
python3 confluence_unsaved_changes_report.py \
  --sites yoursite.atlassian.net,othersite.atlassian.net \
  --email you@example.com
```

If `--api-token` is omitted, the script securely prompts for it.

To write to a custom output file:

```bash
python3 confluence_unsaved_changes_report.py \
  --sites yoursite.atlassian.net \
  --email you@example.com \
  --output my_report.md
```

## Notes

- "Site owner" is not consistently available via Confluence Cloud API token auth; the script reports this explicitly.
- The report includes per-site status and detailed error text when API calls fail (e.g., auth/permission issues).
