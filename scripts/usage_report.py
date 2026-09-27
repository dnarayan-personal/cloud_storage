#!/usr/bin/env python3
"""Cross-account storage usage report (read-only).

For every account in config/accounts.yaml, reports:
  - Drive storage quota (limit / total usage / usage attributable to Drive
    files / usage outside Drive i.e. Gmail+Photos combined -- see the API
    limitation note in README.md)
  - Drive file inventory bucketed by type (image/video/audio/other), flagging
    unexpected photos/videos sitting in Drive instead of Photos
  - Google Photos item counts, split into "free" (falls inside a configured
    free_photo_range) vs "charged", read from
    data/inventory/<label>_photos.json if present (built via
    scripts/photos_inventory.py -- the Photos Library API can no longer list
    a user's whole library, and no API exposes a Photos-only byte figure;
    see README)

This script only reads data -- it makes no changes to any account. It also
writes a full JSON dump of everything above (per-account quota, remaining,
Drive-by-category breakdown, Photos summary) to
data/reports/usage_report.json on every run, for later analysis/graphing.

Usage:
    uv run scripts/usage_report.py [--account LABEL ...]
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import sys
from dataclasses import asdict
from pathlib import Path

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

INVENTORY_DIR = Path("data/inventory")
REPORTS_DIR = Path("data/reports")
REPORT_JSON_PATH = REPORTS_DIR / "usage_report.json"



def _creation_date(iso_ts: str | None) -> _dt.date | None:
    if not iso_ts:
        return None
    return _dt.datetime.fromisoformat(iso_ts).date()


def load_photo_inventory(label: str) -> list[dict] | None:
    """Load data/inventory/<label>_photos.json if it exists, else None."""
    path = INVENTORY_DIR / f"{label}_photos.json"
    if not path.exists():
        return None
    with path.open(encoding="utf-8") as f:
        return json.load(f)


def summarize_photo_inventory(account: Account, items: list[dict]) -> dict:
    photo_count = sum(1 for i in items if i.get("type") == "PHOTO")
    video_count = sum(1 for i in items if i.get("type") == "VIDEO")
    free_count = 0
    charged_count = 0
    for item in items:
        date = _creation_date(item.get("createTime"))
        if date is not None and account.is_free_photo_date(date):
            free_count += 1
        else:
            charged_count += 1
    dates = [_creation_date(i.get("createTime")) for i in items]
    dates = [d for d in dates if d is not None]
    return {
        "total": len(items),
        "photo_count": photo_count,
        "video_count": video_count,
        "free_count": free_count,
        "charged_count": charged_count,
        "earliest": min(dates) if dates else None,
        "latest": max(dates) if dates else None,
    }


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

    # Photos counts, from local inventory built via scripts/photos_inventory.py
    # (no live API exists anymore to list a user's whole Photos library).
    items = load_photo_inventory(account.label)
    if items is not None:
        result["photos"] = summarize_photo_inventory(account, items)

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
        print("\n  Google Photos (from local inventory, metadata-only, no size data):")
        print(
            f"    total: {photos['total']}   photos: {photos['photo_count']}   "
            f"videos: {photos['video_count']}"
        )
        print(f"    creation date span: {photos['earliest'] or '-'} .. {photos['latest'] or '-'}")
        print(f"    free (per configured free_photo_ranges): {photos['free_count']}")
        print(f"    charged (counts against quota): {photos['charged_count']}")
        if account.free_photo_ranges:
            print("    configured free date ranges:")
            for r in account.free_photo_ranges:
                print(f"      {r.start} .. {r.end}")
        else:
            print("    (no free_photo_ranges configured -- all items counted as charged)")
    else:
        print("\n  Google Photos: no local inventory found -- run scripts/photos_inventory.py")


def account_report_dict(account: Account, result: dict) -> dict:
    """Build a full JSON-serializable record of everything we know about `account`."""
    quota = result.get("quota")
    photos = result.get("photos")
    drive_files = result.get("drive_files")

    quota_dict = None
    if quota is not None:
        remaining = None if quota.limit_bytes is None else quota.limit_bytes - quota.usage_bytes
        quota_dict = {
            **asdict(quota),
            "usage_outside_drive_bytes": quota.usage_outside_drive_bytes,
            "remaining_bytes": remaining,
        }

    drive_files_dict = None
    if drive_files is not None:
        drive_files_dict = {category: asdict(summary) for category, summary in drive_files.items()}

    photos_dict = None
    if photos is not None:
        photos_dict = {
            **photos,
            "earliest": photos["earliest"].isoformat() if photos["earliest"] else None,
            "latest": photos["latest"].isoformat() if photos["latest"] else None,
        }

    return {
        "label": account.label,
        "email": account.email,
        "roles": account.roles,
        "error": result.get("error") or result.get("drive_error"),
        "quota": quota_dict,
        "drive_files": drive_files_dict,
        "photos": photos_dict,
    }


def save_report_json(accounts_data: list[dict], totals: dict, path: Path = REPORT_JSON_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    report = {
        "generated_at": _dt.datetime.now(_dt.UTC).isoformat(),
        "accounts": accounts_data,
        "totals": totals,
    }
    with path.open("w", encoding="utf-8") as f:
        json.dump(report, f, indent=2)


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
    total_photos = 0
    total_photos_free = 0
    total_photos_charged = 0
    accounts_data: list[dict] = []

    for account in accounts:
        result = report_for_account(config, account)
        print_report(account, result)
        accounts_data.append(account_report_dict(account, result))
        quota = result.get("quota")
        if quota is not None:
            total_usage += quota.usage_bytes
            if quota.limit_bytes is None:
                had_unlimited = True
            else:
                total_limit += quota.limit_bytes
        photos = result.get("photos")
        if photos is not None:
            total_photos += photos["total"]
            total_photos_free += photos["free_count"]
            total_photos_charged += photos["charged_count"]

    total_remaining = None if had_unlimited else total_limit - total_usage

    print(f"\n{'=' * 70}")
    print("TOTAL across reported accounts")
    print("=" * 70)
    print(f"  Total usage: {human_bytes(total_usage)}")
    print(
        f"  Total limit: {human_bytes(total_limit)}"
        f"{' (+ unlimited account(s))' if had_unlimited else ''}"
    )
    print(
        f"  Total remaining: "
        f"{'unlimited (some account has no cap)' if total_remaining is None else human_bytes(total_remaining)}"
    )
    print(
        f"  Total Photos: {total_photos}   free: {total_photos_free}   "
        f"charged: {total_photos_charged}"
    )
    print(
        "\nNote: YouTube Music library storage is not exposed by any public "
        "Google API and is not included above -- verify manually via the "
        "Google One storage manager / YT Music app. See README.md."
    )

    totals = {
        "usage_bytes": total_usage,
        "limit_bytes": total_limit if not had_unlimited else None,
        "had_unlimited_account": had_unlimited,
        "remaining_bytes": total_remaining,
        "photos_total": total_photos,
        "photos_free": total_photos_free,
        "photos_charged": total_photos_charged,
    }
    save_report_json(accounts_data, totals)
    print(f"\nSaved full JSON report to {REPORT_JSON_PATH}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
