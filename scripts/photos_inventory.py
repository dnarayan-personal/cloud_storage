#!/usr/bin/env python3
"""Build a Google Photos inventory for one account, year by year.

Uses the Google Photos Picker API. For each year (starting at the current
year and counting down), it:

  1. Opens a picker session in your browser.
  2. You search for that year in the picker's search bar, range-select the
     results, and click Done.
  3. The script fetches metadata for everything you picked and shows you a
     summary (count, earliest/latest date) so you can sanity-check it.
  4. You confirm whether that year looks complete; if not, it retries the
     same year with a fresh session.

Results are appended (deduped by item id) to
data/inventory/<label>_photos.json after each confirmed year, so progress
is saved incrementally -- safe to stop and resume later.

No file bytes or hashes are fetched here; this is metadata-only (Phase 1).

Usage:
    uv run scripts/photos_inventory.py <label>
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import webbrowser
from datetime import UTC, datetime
from pathlib import Path

import requests
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow

from cloud_storage_tools.config import ConfigError, load_config

PICKER_SCOPES = ["https://www.googleapis.com/auth/photospicker.mediaitems.readonly"]
PICKER_API_BASE = "https://photospicker.googleapis.com/v1"
INVENTORY_DIR = Path("data/inventory")


def get_picker_credentials(client_secret_file: Path, token_path: Path) -> Credentials:
    creds: Credentials | None = None
    if token_path.exists():
        creds = Credentials.from_authorized_user_file(str(token_path), PICKER_SCOPES)
    if creds and creds.valid:
        return creds
    if creds and creds.expired and creds.refresh_token:
        creds.refresh(Request())
        token_path.write_text(creds.to_json(), encoding="utf-8")
        return creds

    flow = InstalledAppFlow.from_client_secrets_file(str(client_secret_file), PICKER_SCOPES)
    creds = flow.run_local_server(port=0)
    token_path.parent.mkdir(parents=True, exist_ok=True)
    token_path.write_text(creds.to_json(), encoding="utf-8")
    return creds


def run_picker_session(creds: Credentials, year: int) -> list[dict]:
    """Open one picker session, wait for the user to finish, return picked items."""
    session_resp = requests.post(
        f"{PICKER_API_BASE}/sessions",
        headers={"Authorization": f"Bearer {creds.token}"},
        json={},
        timeout=30,
    )
    session_resp.raise_for_status()
    session = session_resp.json()
    session_id = session["id"]
    picker_uri = session["pickerUri"]

    print(f"\n=== Year {year} ===")
    print(f"Opening picker in your browser:\n  {picker_uri}")
    print(f"In the picker, search for '{year}', range-select the results, then click Done.")
    print(
        "(If there are no photos for this year, the picker won't let you click Done "
        "with nothing selected -- press Ctrl+C here to treat this year as empty.)"
    )
    webbrowser.open(picker_uri)

    try:
        while True:
            time.sleep(3)
            poll_resp = requests.get(
                f"{PICKER_API_BASE}/sessions/{session_id}",
                headers={"Authorization": f"Bearer {creds.token}"},
                timeout=30,
            )
            poll_resp.raise_for_status()
            status = poll_resp.json()
            if status.get("mediaItemsSet"):
                break
            print(".", end="", flush=True)
    except KeyboardInterrupt:
        print("\nInterrupted -- treating this year as empty (0 items).")
        return []
    print()

    items: list[dict] = []
    page_token = None
    while True:
        params = {"sessionId": session_id, "pageSize": 100}
        if page_token:
            params["pageToken"] = page_token
        list_resp = requests.get(
            f"{PICKER_API_BASE}/mediaItems",
            headers={"Authorization": f"Bearer {creds.token}"},
            params=params,
            timeout=30,
        )
        list_resp.raise_for_status()
        data = list_resp.json()
        items.extend(data.get("mediaItems", []))
        page_token = data.get("nextPageToken")
        if not page_token:
            break
    return items


def summarize(items: list[dict], year: int) -> None:
    if not items:
        print("No items picked.")
        return
    times = sorted(
        datetime.fromisoformat(i["createTime"]) for i in items
    )
    print(f"Picked {len(items)} item(s).")
    print(f"  Earliest: {times[0].isoformat()}")
    print(f"  Latest:   {times[-1].isoformat()}")

    out_of_year = [t for t in times if t.year != year]
    if out_of_year:
        print(
            f"  ⚠ {len(out_of_year)} item(s) have a createTime outside {year} "
            f"(years seen: {sorted({t.year for t in out_of_year})}) -- double-check your "
            "selection before confirming."
        )


def to_record(item: dict, label: str, year: int) -> dict:
    mf = item.get("mediaFile", {})
    meta = mf.get("mediaFileMetadata", {})
    return {
        "id": item["id"],
        "account": label,
        "search_year": year,
        "createTime": item.get("createTime"),
        "type": item.get("type"),
        "filename": mf.get("filename"),
        "mimeType": mf.get("mimeType"),
        "width": meta.get("width"),
        "height": meta.get("height"),
        "cameraMake": meta.get("cameraMake"),
        "cameraModel": meta.get("cameraModel"),
    }


def load_inventory(path: Path) -> dict[str, dict]:
    if not path.exists():
        return {}
    with path.open(encoding="utf-8") as f:
        records = json.load(f)
    return {r["id"]: r for r in records}


def save_inventory(path: Path, by_id: dict[str, dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    records = sorted(by_id.values(), key=lambda r: r.get("createTime") or "")
    with path.open("w", encoding="utf-8") as f:
        json.dump(records, f, indent=2)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("label", help="Account label from config/accounts.yaml")
    parser.add_argument(
        "--start-year",
        type=int,
        default=datetime.now(UTC).year,
        help="Year to start at (counts down from here). Defaults to current year.",
    )
    args = parser.parse_args()

    try:
        config = load_config()
    except ConfigError as e:
        print(f"Config error: {e}", file=sys.stderr)
        return 1

    try:
        account = config.account(args.label)
    except ConfigError as e:
        print(str(e), file=sys.stderr)
        return 1

    token_path = Path("tokens") / f"{account.label}_picker.json"
    creds = get_picker_credentials(config.client_secret_file, token_path)

    inventory_path = INVENTORY_DIR / f"{account.label}_photos.json"
    by_id = load_inventory(inventory_path)
    print(f"Loaded existing inventory: {len(by_id)} item(s) from {inventory_path}")

    year = args.start_year
    while True:
        answer = (
            input(f"\nInventory year {year}? [Enter=yes / 'skip' / 'done' to stop]: ")
            .strip()
            .lower()
        )
        if answer == "done":
            break
        if answer == "skip":
            year -= 1
            continue

        while True:
            items = run_picker_session(creds, year)
            summarize(items, year)
            confirm = (
                input(
                    f"For {year}: [y]es all done / [m]ore batches needed (saves this "
                    "batch, keeps going) / [n]o discard and retry: "
                )
                .strip()
                .lower()
            )
            if confirm in ("y", "more", "m"):
                duplicates = [item for item in items if item["id"] in by_id]
                if duplicates:
                    print(
                        f"⚠ {len(duplicates)} item(s) in this batch already exist in the "
                        "inventory (unexpected during Phase 1 -- same photo picked twice?):"
                    )
                    for item in duplicates:
                        filename = item.get("mediaFile", {}).get("filename", "?")
                        print(f"    {filename} (id={item['id']})")
                for item in items:
                    record = to_record(item, account.label, year)
                    by_id[record["id"]] = record
                save_inventory(inventory_path, by_id)
                print(f"Saved. Inventory now has {len(by_id)} item(s) total.")
                if confirm == "y":
                    break
                print(f"Continuing to collect more of {year} -- opening another session...")
                continue
            print(f"Discarding this batch, retrying year {year}...")

        year -= 1

    print(f"\nDone. Final inventory: {len(by_id)} item(s) in {inventory_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
