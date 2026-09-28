#!/usr/bin/env python3
"""Match a Drive audio inventory against a YT Music uploaded-songs inventory.

Precision over recall: this is meant to find HIGH-CONFIDENCE duplicates
only, since a false positive here could lead to deleting music that isn't
actually backed up anywhere else. It never suggests fuzzy/similarity-score
matches -- only exact matches after conservative normalization (case,
whitespace, quote/punctuation differences). Anything else is left for
manual review rather than guessed at.

Two possible sources of Drive-side artist/track metadata (--source):
  - folder (default): parsed from the Music/Artist/Album/TrackName folder
    structure (scripts/drive_audio_inventory.py). Fast, no downloads, but
    can be inaccurate -- Drive folder names get truncated for long artist
    names, and special characters illegal in filenames get mangled.
  - id3: real ID3/metadata tags read from the files themselves
    (scripts/drive_audio_id3.py). More accurate, but requires that script
    to have been run first (it downloads/range-fetches file content once
    and caches the result -- see its docstring).

Each Drive file is classified into one of:
  - high_confidence: normalized artist AND title both match a YT entry
    exactly. This is the only tier meant to inform any future
    quarantine/delete decision -- and even then, only after you review it.
  - needs_review: normalized title matches exactly but artist does not
    (e.g. missing/mistagged artist on one side, or a generic title like
    "Intro" shared by unrelated tracks) -- shown for you to judge, never
    auto-actioned.
  - no_match: nothing found.

Usage:
    uv run scripts/match_drive_ytmusic.py <drive_label> [--ytmusic-label LABEL] [--source folder|id3]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from cloud_storage_tools.matching import build_ytmusic_index, match_one

INVENTORY_DIR = Path("data/inventory")
REPORTS_DIR = Path("data/reports")

SOURCE_SUFFIXES = {
    "folder": "_drive_audio.json",
    "id3": "_drive_audio_id3.json",
}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("drive_label", help="Account label whose Drive audio inventory to check")
    parser.add_argument(
        "--ytmusic-label",
        default="main",
        help="Account label whose YT Music inventory to match against (default: main)",
    )
    parser.add_argument(
        "--source",
        choices=sorted(SOURCE_SUFFIXES),
        default="folder",
        help="Which Drive-side metadata to match with (default: folder)",
    )
    args = parser.parse_args()

    drive_path = INVENTORY_DIR / f"{args.drive_label}{SOURCE_SUFFIXES[args.source]}"
    ytmusic_path = INVENTORY_DIR / f"{args.ytmusic_label}_ytmusic.json"
    if not drive_path.exists():
        hint = "Run drive_audio_inventory.py first." if args.source == "folder" else "Run drive_audio_id3.py first."
        print(f"No Drive audio inventory at {drive_path}. {hint}", file=sys.stderr)
        return 1
    if not ytmusic_path.exists():
        print(f"No YT Music inventory at {ytmusic_path}. Run ytmusic_inventory.py first.", file=sys.stderr)
        return 1

    with drive_path.open(encoding="utf-8") as f:
        drive_records = json.load(f)
    with ytmusic_path.open(encoding="utf-8") as f:
        ytmusic_records = json.load(f)

    yt_index = build_ytmusic_index(ytmusic_records)

    results = []
    counts = {"high_confidence": 0, "needs_review": 0, "no_match": 0}
    size_by_tier = {"high_confidence": 0, "needs_review": 0, "no_match": 0}
    for drive_rec in drive_records:
        match = match_one(drive_rec.get("artist"), drive_rec.get("track_name") or "", yt_index)
        counts[match["tier"]] += 1
        size_by_tier[match["tier"]] += drive_rec.get("size", 0)
        results.append({**drive_rec, **match})

    output_path = REPORTS_DIR / f"{args.drive_label}_vs_{args.ytmusic_label}_ytmusic_match.json"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8") as f:
        json.dump(results, f, indent=2)

    gb = 1024**3
    print(
        f"Matched {len(drive_records)} Drive audio file(s) [source={args.source}] "
        f"against {len(ytmusic_records)} YT Music upload(s)."
    )
    for tier in ("high_confidence", "needs_review", "no_match"):
        print(f"  {tier}: {counts[tier]} file(s), {size_by_tier[tier] / gb:.2f} GB")
    print(f"\nSaved full results to {output_path}")
    print(
        "\nNote: high_confidence means artist+title matched exactly after "
        "normalization -- review before deleting anything; this is metadata "
        "only, not a byte-for-byte/audio comparison."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
