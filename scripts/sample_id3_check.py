#!/usr/bin/env python3
"""Sample a few files per match tier, download them, and compare ID3 tags to
the folder-derived metadata and YT Music candidate (if any).

This is a spot-check, not a bulk operation: it downloads only a handful of
files (default 5 per tier) to see whether the folder-structure-derived
artist/track_name used by match_drive_ytmusic.py agree with the actual
embedded ID3 tags, and whether those tags in turn agree with YT Music's
metadata for high_confidence/needs_review matches. It does not change any
match result or delete anything.

Downloaded files are saved to a temp directory and deleted after tags are
read (nothing is kept on disk).

Usage:
    uv run scripts/sample_id3_check.py <drive_label> [--ytmusic-label LABEL] [--per-tier N]
"""

from __future__ import annotations

import argparse
import json
import random
import sys
import tempfile
from pathlib import Path

from mutagen import File as MutagenFile

from cloud_storage_tools.auth import AuthError, get_credentials
from cloud_storage_tools.config import ConfigError, load_config
from cloud_storage_tools.driveutil import build_drive_service, download_file

REPORTS_DIR = Path("data/reports")
TIERS = ("high_confidence", "needs_review", "no_match")


def read_id3_tags(path: Path) -> dict:
    """Return a small dict of the tags we care about, or {} if unreadable."""
    try:
        audio = MutagenFile(path, easy=True)
    except Exception as e:  # noqa: BLE001 - just report and move on
        return {"error": str(e)}
    if audio is None:
        return {"error": "unrecognized audio format"}
    tags = audio.tags or {}

    def first(key: str) -> str | None:
        values = tags.get(key)
        return values[0] if values else None

    return {
        "artist": first("artist"),
        "album": first("album"),
        "title": first("title"),
        "duration_sec": round(audio.info.length, 1) if audio.info else None,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("drive_label", help="Account label whose match report to sample from")
    parser.add_argument(
        "--ytmusic-label",
        default="main",
        help="Account label used as the YT Music match target (default: main)",
    )
    parser.add_argument(
        "--per-tier", type=int, default=5, help="Number of files to sample per tier (default: 5)"
    )
    args = parser.parse_args()

    report_path = REPORTS_DIR / f"{args.drive_label}_vs_{args.ytmusic_label}_ytmusic_match.json"
    if not report_path.exists():
        print(f"No match report at {report_path}. Run match_drive_ytmusic.py first.", file=sys.stderr)
        return 1

    with report_path.open(encoding="utf-8") as f:
        records = json.load(f)

    try:
        config = load_config()
        account = config.account(args.drive_label)
        creds = get_credentials(config, account)
    except (ConfigError, AuthError) as e:
        print(f"Auth/config error for {args.drive_label}: {e}", file=sys.stderr)
        return 1

    service = build_drive_service(creds)

    by_tier: dict[str, list[dict]] = {tier: [] for tier in TIERS}
    for rec in records:
        by_tier.setdefault(rec["tier"], []).append(rec)

    rng = random.Random(42)
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir)
        for tier in TIERS:
            candidates = by_tier.get(tier, [])
            sample = rng.sample(candidates, min(args.per_tier, len(candidates)))
            print(f"\n===== {tier} ({len(candidates)} total, sampling {len(sample)}) =====")
            for rec in sample:
                print(f"\nDrive path: {rec['path']}")
                print(
                    f"  Folder-derived: artist={rec.get('artist')!r} "
                    f"album={rec.get('album')!r} track_name={rec.get('track_name')!r}"
                )
                dest = tmp_path / f"{rec['id']}_{Path(rec['filename']).suffix}"
                try:
                    download_file(service, rec["id"], str(dest))
                    tags = read_id3_tags(dest)
                finally:
                    dest.unlink(missing_ok=True)
                print(f"  ID3 tags:       {tags}")
                if rec.get("matches"):
                    m = rec["matches"][0]
                    print(f"  YT candidate:   artists={m.get('artists')} title={m.get('title')!r}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
