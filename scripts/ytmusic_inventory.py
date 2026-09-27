#!/usr/bin/env python
"""Download the full YT Music "uploaded songs" inventory for one account.

Unlike scripts/photos_inventory.py, this needs no per-item manual picking --
ytmusicapi's get_library_upload_songs() returns everything in one call. This
script just fetches it and saves it to data/inventory/<label>_ytmusic.json
for later comparison against Drive audio files.

Prerequisite: an authenticated headers file at tokens/<label>_ytmusic.json.
See scripts/test_ytmusic.py's docstring (or
https://ytmusicapi.readthedocs.io/en/stable/setup/browser.html) for how to
create it from your browser's dev tools.

Usage:
    uv run scripts/ytmusic_inventory.py <label>
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from ytmusicapi import YTMusic

TOKENS_DIR = Path("tokens")
INVENTORY_DIR = Path("data/inventory")


def to_record(song: dict) -> dict:
    artists = [a["name"] for a in song.get("artists") or []]
    album = song.get("album")
    return {
        "entityId": song.get("entityId"),
        "videoId": song.get("videoId"),
        "title": song.get("title"),
        "artists": artists,
        "album": album.get("name") if album else None,
        "likeStatus": song.get("likeStatus"),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("label", help="Account label, e.g. 'main'")
    args = parser.parse_args()

    auth_file = TOKENS_DIR / f"{args.label}_ytmusic.json"
    if not auth_file.exists():
        print(f"No auth file at {auth_file}.", file=sys.stderr)
        print(
            "See scripts/test_ytmusic.py's docstring for how to create one.",
            file=sys.stderr,
        )
        return 1

    yt = YTMusic(str(auth_file))
    songs = yt.get_library_upload_songs(limit=None)
    records = sorted(
        (to_record(song) for song in songs),
        key=lambda r: (r["title"] or "", ", ".join(r["artists"])),
    )

    inventory_path = INVENTORY_DIR / f"{args.label}_ytmusic.json"
    inventory_path.parent.mkdir(parents=True, exist_ok=True)
    with inventory_path.open("w", encoding="utf-8") as f:
        json.dump(records, f, indent=2)

    print(f"Saved {len(records)} uploaded song(s) to {inventory_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
