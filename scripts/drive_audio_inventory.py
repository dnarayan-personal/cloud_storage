#!/usr/bin/env python3
"""Build a Drive audio-file inventory for one account, using folder structure.

Google Drive's API doesn't expose ID3 tags (artist/album/title) -- only
filesystem-level metadata. Since audio files are organized as
`Music/Artist/Album/TrackName.ext`, this reconstructs each file's full
folder path from Drive's folder tree and parses out artist/album/track_name
from it. No file content is downloaded -- this is metadata-only and fast.

The raw Drive listing (folder map + audio file list) is fetched once and
cached to data/inventory/<label>_drive_audio_raw.json; it's just a metadata
snapshot, not something that needs refreshing per run, so by default this
script reuses that cached file and only re-parses it (useful for iterating
on the path-parsing rules without re-hitting the Drive API). Pass --refresh
to re-fetch from Drive (e.g. after adding/moving files there).

Saves the parsed inventory to data/inventory/<label>_drive_audio.json.

Usage:
    uv run scripts/drive_audio_inventory.py <label> [--refresh]
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

from cloud_storage_tools.auth import AuthError, get_credentials
from cloud_storage_tools.config import ConfigError, load_config
from cloud_storage_tools.driveutil import (
    build_drive_service,
    list_folders_and_audio_files,
    resolve_folder_path,
)

INVENTORY_DIR = Path("data/inventory")

# Folder name (case-insensitive) under which the Artist/Album/TrackName
# structure is expected to start. Path segments before this are ignored.
MUSIC_ROOT_FOLDER_NAME = "music"

# Folder names that are disc subdivisions of a multi-disc album (e.g.
# "CD1", "Disc 2"), not the album name itself -- skip these when picking
# the album folder and use the next one up instead.
DISC_FOLDER_RE = re.compile(r"^(cd|disc|disk)[\s_-]*\d+$", re.IGNORECASE)


def parse_path(path_names: list[str], filename: str) -> dict:
    """Extract artist/album/track_name from a folder path, if it matches
    the expected Music/Artist/.../Album/TrackName.ext convention.

    Returns {"artist": str|None, "album": str|None, "track_name": str|None,
    "matched_convention": bool, "extra_nesting": bool}. track_name is the
    filename without its extension. artist is the first folder under Music;
    album is the last non-disc-subdivision folder below that (skipping
    folders like "CD1"/"Disc 2", and tolerating extra grouping folders in
    between, e.g. a "Remixes & Rarities" collection folder -- flagged via
    extra_nesting for visibility, not treated as an error). If the Music
    root isn't found, or there's no folder at all under it,
    matched_convention is False -- caller should treat these as needing
    manual review rather than guessing.
    """
    track_name = filename.rsplit(".", 1)[0] if "." in filename else filename

    lowered = [p.lower() for p in path_names]
    if MUSIC_ROOT_FOLDER_NAME not in lowered:
        return {
            "artist": None,
            "album": None,
            "track_name": track_name,
            "matched_convention": False,
            "extra_nesting": False,
        }

    idx = lowered.index(MUSIC_ROOT_FOLDER_NAME)
    below = path_names[idx + 1 :]
    if not below:
        return {
            "artist": None,
            "album": None,
            "track_name": track_name,
            "matched_convention": False,
            "extra_nesting": False,
        }

    artist = below[0]
    non_disc = [name for name in below[1:] if not DISC_FOLDER_RE.match(name.strip())]
    album = non_disc[-1] if non_disc else None
    return {
        "artist": artist,
        "album": album,
        "track_name": track_name,
        "matched_convention": True,
        "extra_nesting": len(below) > 2,
    }


def fetch_raw(config, account) -> dict:
    try:
        creds = get_credentials(config, account)
    except AuthError as e:
        print(str(e), file=sys.stderr)
        sys.exit(1)

    service = build_drive_service(creds)
    print("Listing folders and audio files from Drive...")
    folder_map, audio_files = list_folders_and_audio_files(service)
    print(f"Found {len(folder_map)} folder(s), {len(audio_files)} audio file(s).")
    return {"folder_map": folder_map, "audio_files": audio_files}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("label", help="Account label from config/accounts.yaml")
    parser.add_argument(
        "--refresh",
        action="store_true",
        help="Re-fetch the raw folder/file listing from Drive instead of reusing the cached snapshot",
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

    raw_path = INVENTORY_DIR / f"{account.label}_drive_audio_raw.json"
    if args.refresh or not raw_path.exists():
        raw = fetch_raw(config, account)
        raw_path.parent.mkdir(parents=True, exist_ok=True)
        with raw_path.open("w", encoding="utf-8") as f:
            json.dump(raw, f, indent=2)
    else:
        print(f"Reusing cached raw listing from {raw_path} (pass --refresh to re-fetch).")
        with raw_path.open(encoding="utf-8") as f:
            raw = json.load(f)

    folder_map = raw["folder_map"]
    audio_files = raw["audio_files"]

    records = []
    unmatched = 0
    extra_nesting = 0
    for f in audio_files:
        path_names = resolve_folder_path(f["parents"], folder_map)
        parsed = parse_path(path_names, f["name"])
        if not parsed["matched_convention"]:
            unmatched += 1
        elif parsed["extra_nesting"]:
            extra_nesting += 1
        records.append(
            {
                "id": f["id"],
                "filename": f["name"],
                "size": f["size"],
                "trashed": f["trashed"],
                "path": "/".join(path_names + [f["name"]]),
                **parsed,
            }
        )

    records.sort(key=lambda r: r["path"])

    inventory_path = INVENTORY_DIR / f"{account.label}_drive_audio.json"
    inventory_path.parent.mkdir(parents=True, exist_ok=True)
    with inventory_path.open("w", encoding="utf-8") as f:
        json.dump(records, f, indent=2)

    print(f"Saved {len(records)} audio file record(s) to {inventory_path}")
    if extra_nesting:
        print(
            f"  ({extra_nesting} file(s) had extra grouping folders between artist and "
            "album -- still parsed fine, just flagged via extra_nesting)"
        )
    if unmatched:
        print(
            f"⚠ {unmatched} file(s) weren't under a 'Music' folder at all -- these have "
            "artist/album set to null and need manual review."
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

