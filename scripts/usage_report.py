#!/usr/bin/env python3
"""Cross-account storage usage report (read-only).

For every account in config/accounts.yaml, reports:
  - Drive storage quota (limit / total usage / usage attributable to Drive
    files / usage outside Drive i.e. Gmail+Photos combined -- see the API
    limitation note in README.md)
  - Drive file inventory bucketed by type (image/video/audio/other), flagging
    unexpected photos/videos sitting in Drive instead of Photos
  - Google Photos item counts (photo vs video) and creation-date span,
    including how many photos fall inside a configured "free" date range

This script only reads data -- it makes no changes to any account.

Usage:
    uv run scripts/usage_report.py [--account LABEL ...]
"""

from __future__ import annotations

import argparse
import datetime as _dt
import sys

from googleapiclient.errors import HttpError
from tabulate import tabulate

from cloud_storage_tools.auth import AuthError, get_credentials
from cloud_storage_tools.config import Account, Config, ConfigError, load_config
from cloud_storage_tools.driveutil import (
    build_drive_service,
    get_quota,
    summarize_files_by_category,
)
from cloud_storage_tools.formatting import human_bytes
from cloud_storage_tools.photosutil import build_photos_service, summarize_photos


def _creation_date(iso_ts: str | None) -> _dt.date | None:
    if not iso_ts:
        return None
    return _dt.datetime.fromisoformat(iso_ts).date()


def report_for_account(config: Config, account: Account) -> dict:
    result: dict = {"label": account.label, "email": account.email, "roles": account.roles}

    try:
        creds = get_credentials(config, account)
    except AuthError as exc:
        result["error"] = str(exc)
        return result

    # Drive quota + file inventory.
    try:
        drive = build_drive_service(creds)
        quota = get_quota(drive)
        result["quota"] = quota
        result["drive_files"] = summarize_files_by_category(drive)
    except HttpError as exc:
        result["drive_error"] = f"Drive API error: {exc}"

    # Photos counts (no size data available via API -- see README).
    try:
        photos = build_photos_service(creds)
        result["photos"] = summarize_photos(photos)
    except HttpError as exc:
        result["photos_error"] = f"Photos API error: {exc}"

    return result


def print_report(account: Account, result: dict) -> None:
    print(f"\n{'=' * 70}")
    print(f"Account: {account.label} <{account.email}>  roles={account.roles or '-'}")
    print("=" * 70)

    if "error" in result:
        print(f"  ! {result['error']}")
        return

    quota = result.get("quota")
    if quota is not None:
        rows = [
            ["Limit", human_bytes(quota.limit_bytes)],
            ["Total usage", human_bytes(quota.usage_bytes)],
            ["  usage in Drive files", human_bytes(quota.usage_in_drive_bytes)],
            ["  usage in Drive trash", human_bytes(quota.usage_in_drive_trash_bytes)],
            [
                "  usage outside Drive (Gmail+Photos, combined)",
                human_bytes(quota.usage_outside_drive_bytes),
            ],
        ]
        print(tabulate(rows, tablefmt="plain"))
    elif "drive_error" in result:
        print(f"  ! {result['drive_error']}")

    drive_files = result.get("drive_files")
    if drive_files:
        rows = []
        flags = []
        for category, summary in sorted(drive_files.items()):
            rows.append(
                [
                    category,
                    summary.count,
                    human_bytes(summary.size_bytes),
                    summary.trashed_count,
                    human_bytes(summary.trashed_size_bytes),
                ]
            )
            if category in ("image", "video") and summary.count > 0:
                flags.append(
                    f"  \u26a0 {summary.count} {category} file(s) found IN DRIVE "
                    f"({human_bytes(summary.size_bytes)}) -- expected only in Photos, verify"
                )
            if category == "audio" and summary.count > 0 and "music_in_drive" not in account.roles:
                flags.append(
                    f"  \u26a0 {summary.count} audio file(s) found in Drive but account has no "
                    "'music_in_drive' role in config -- verify this is expected"
                )
        print("\n  Drive files by type (non-trashed / trashed):")
        print(
            tabulate(
                rows,
                headers=["type", "count", "size", "trashed count", "trashed size"],
                tablefmt="plain",
            )
        )
        for flag in flags:
            print(flag)

    photos = result.get("photos")
    if photos:
        print("\n  Google Photos:")
        print(f"    photos: {photos.photo_count}   videos: {photos.video_count}")
        print(
            "    creation date span: "
            f"{photos.earliest_creation_time or '-'} .. {photos.latest_creation_time or '-'}"
        )
        earliest = _creation_date(photos.earliest_creation_time)
        latest = _creation_date(photos.latest_creation_time)
        if account.free_photo_ranges:
            print("    configured free date ranges:")
            for r in account.free_photo_ranges:
                inside = ""
                if earliest and r.contains(earliest):
                    inside = " (library start falls inside this free range)"
                print(f"      {r.start} .. {r.end}{inside}")
        if latest and account.free_photo_ranges and not any(
            r.contains(latest) for r in account.free_photo_ranges
        ):
            print(
                "    \u26a0 most recent photo is OUTSIDE the configured free ranges -- "
                "these newer photos are counting against quota"
            )
    elif "photos_error" in result:
        print(f"\n  ! {result['photos_error']}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--account", action="append", dest="labels", help="Limit to this account label (repeatable)"
    )
    args = parser.parse_args()

    try:
        config = load_config()
    except ConfigError as exc:
        print(f"Config error: {exc}", file=sys.stderr)
        return 1

    accounts = config.accounts
    if args.labels:
        wanted = set(args.labels)
        accounts = [a for a in accounts if a.label in wanted]
        missing = wanted - {a.label for a in accounts}
        if missing:
            print(f"Unknown account label(s): {', '.join(sorted(missing))}", file=sys.stderr)
            return 1

    total_usage = 0
    total_limit = 0
    had_unlimited = False

    for account in accounts:
        result = report_for_account(config, account)
        print_report(account, result)
        quota = result.get("quota")
        if quota is not None:
            total_usage += quota.usage_bytes
            if quota.limit_bytes is None:
                had_unlimited = True
            else:
                total_limit += quota.limit_bytes

    print(f"\n{'=' * 70}")
    print("TOTAL across reported accounts")
    print("=" * 70)
    print(f"  Total usage: {human_bytes(total_usage)}")
    print(
        f"  Total limit: {human_bytes(total_limit)}"
        f"{' (+ unlimited account(s))' if had_unlimited else ''}"
    )
    print(
        "\nNote: YouTube Music library storage is not exposed by any public "
        "Google API and is not included above -- verify manually via the "
        "Google One storage manager / YT Music app. See README.md."
    )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
