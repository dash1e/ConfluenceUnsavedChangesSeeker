#!/usr/bin/env python3
"""
Generate a Markdown report of Confluence sites with unsaved changes (drafts).

This script is read-only. It performs GET requests only.
"""

from __future__ import annotations

import argparse
import getpass
import json
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import base64
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError

DEFAULT_TIMEOUT = 30


@dataclass
class SiteResult:
    site_url: str
    site_name: str
    owner: str
    last_editor: str
    link: str
    status: str
    error: str = ""


def normalize_site_url(raw: str) -> str:
    value = raw.strip()
    if not value:
        raise ValueError("Site URL is empty")
    if not value.startswith(("http://", "https://")):
        value = f"https://{value}"
    parsed = urlparse(value)
    if not parsed.netloc:
        raise ValueError(f"Invalid site URL: {raw}")
    return f"{parsed.scheme}://{parsed.netloc}"


def api_get(base_url: str, auth_header: str, path: str, params: dict[str, str] | None = None) -> dict[str, Any]:
    url = f"{base_url}{path}"
    if params:
        url += "?" + urlencode(params)

    req = Request(url, headers={"Authorization": auth_header, "Accept": "application/json"}, method="GET")
    try:
        with urlopen(req, timeout=DEFAULT_TIMEOUT) as resp:
            raw = resp.read().decode("utf-8")
            return json.loads(raw) if raw else {}
    except HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")[:400]
        raise RuntimeError(f"HTTP {exc.code} from {url}: {body}") from exc
    except URLError as exc:
        raise RuntimeError(f"Network error calling {url}: {exc.reason}") from exc


def detect_site_name(base_url: str, auth_header: str) -> str:
    # Most reliable lightweight endpoint for identification in Confluence Cloud.
    payload = api_get(base_url, auth_header, "/wiki/rest/api/settings/systemInfo")
    return str(payload.get("displayName") or payload.get("baseUrl") or urlparse(base_url).netloc)


def detect_site_owner(base_url: str, auth_header: str) -> str:
    # Confluence Cloud API does not consistently expose a canonical "site owner"
    # with API token auth. Keep this explicit for report consumers.
    _ = base_url, auth_header
    return "Not exposed by Confluence API token"


def parse_confluence_datetime(raw: str) -> datetime:
    # Confluence usually returns RFC3339 timestamps such as 2026-04-14T07:55:10.123Z.
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    return datetime.fromisoformat(raw)


def fetch_latest_draft(base_url: str, auth_header: str) -> tuple[str, str, int]:
    payload = api_get(
        base_url,
        auth_header,
        "/wiki/rest/api/content",
        params={
            "status": "draft",
            "type": "page",
            "limit": "250",
            "expand": "history.lastUpdated",
        },
    )

    results = payload.get("results") or []
    draft_count = len(results)
    if draft_count == 0:
        return "No unsaved changes", "", 0

    def draft_updated_at(item: dict[str, Any]) -> datetime:
        when = (((item.get("history") or {}).get("lastUpdated") or {}).get("when") or "")
        try:
            return parse_confluence_datetime(when)
        except Exception:
            return datetime.min.replace(tzinfo=timezone.utc)

    latest = max(results, key=draft_updated_at)
    editor = (
        ((latest.get("history") or {}).get("lastUpdated") or {}).get("by") or {}
    ).get("displayName") or "Unknown"

    webui = (latest.get("_links") or {}).get("webui")
    if webui:
        link = f"{base_url}{webui}"
    else:
        content_id = latest.get("id")
        link = f"{base_url}/wiki/pages/viewpage.action?pageId={content_id}" if content_id else ""

    return editor, link, draft_count


def build_report(results: list[SiteResult]) -> str:
    generated = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    lines = [
        "# Confluence Unsaved Changes Report",
        "",
        f"Generated: {generated}",
        "",
        "| Site Name | Owner | Last Editor | Link | Status | Error |",
        "|---|---|---|---|---|---|",
    ]

    def esc(val: str) -> str:
        return (val or "").replace("|", "\\|").strip()

    for item in results:
        link = f"[Open]({item.link})" if item.link else ""
        lines.append(
            "| "
            + " | ".join(
                [
                    esc(item.site_name),
                    esc(item.owner),
                    esc(item.last_editor),
                    link,
                    esc(item.status),
                    esc(item.error),
                ]
            )
            + " |"
        )

    return "\n".join(lines) + "\n"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Find Confluence sites with unsaved changes (draft pages) and output a Markdown table report."
    )
    parser.add_argument(
        "--sites",
        required=True,
        help="Comma-separated Confluence site URLs or hostnames (example: yoursite.atlassian.net,othersite.atlassian.net)",
    )
    parser.add_argument("--email", help="Atlassian account email. If omitted, you will be prompted.")
    parser.add_argument(
        "--api-token",
        help="Atlassian API token. If omitted, you will be securely prompted (input hidden).",
    )
    parser.add_argument(
        "--output",
        default="confluence_unsaved_changes_report.md",
        help="Output Markdown file path (default: confluence_unsaved_changes_report.md)",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()

    email = args.email or input("Atlassian email: ").strip()
    if not email:
        print("ERROR: email is required", file=sys.stderr)
        return 2

    api_token = args.api_token or getpass.getpass("Atlassian API token (input hidden): ").strip()
    if not api_token:
        print("ERROR: API token is required", file=sys.stderr)
        return 2

    try:
        sites = [normalize_site_url(part) for part in args.sites.split(",") if part.strip()]
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    if not sites:
        print("ERROR: no valid sites provided", file=sys.stderr)
        return 2

    token_bytes = f"{email}:{api_token}".encode("utf-8")
    auth_header = "Basic " + base64.b64encode(token_bytes).decode("ascii")
    results: list[SiteResult] = []

    print("Starting Confluence unsaved-changes scan (read-only GET requests)...")
    for site in sites:
        print(f"- Checking {site} ...")
        try:
            site_name = detect_site_name(site, auth_header)
            owner = detect_site_owner(site, auth_header)
            last_editor, link, draft_count = fetch_latest_draft(site, auth_header)

            if draft_count > 0:
                status = f"Unsaved changes found ({draft_count} draft page(s))"
            else:
                status = "No unsaved changes found"

            results.append(
                SiteResult(
                    site_url=site,
                    site_name=site_name,
                    owner=owner,
                    last_editor=last_editor,
                    link=link,
                    status=status,
                )
            )
            print(f"  OK: {status}")
        except Exception as exc:
            results.append(
                SiteResult(
                    site_url=site,
                    site_name=urlparse(site).netloc,
                    owner="",
                    last_editor="",
                    link="",
                    status="Error",
                    error=str(exc),
                )
            )
            print(f"  ERROR: {exc}")

    output_path = Path(args.output)
    output_path.write_text(build_report(results), encoding="utf-8")
    print(f"\nReport written to: {output_path.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
