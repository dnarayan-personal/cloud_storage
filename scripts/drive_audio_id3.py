#!/usr/bin/env python3
"""Pull real ID3/metadata tags for one account's Drive audio files.

This is the "metadata pull" half of the ID3-based matching pipeline: it
fetches artist/album/title (and duration) directly from each audio file's
embedded tags, and caches the result to
data/inventory/<label>_drive_audio_id3.json. Matching against YT Music is a
separate, purely-local step (match_drive_ytmusic.py --source id3) that reads
this cached file -- so once this has been run, you can freely iterate on
matching rules without re-hitting Drive.

To minimize data transferred, most files are read via small HTTP Range
requests rather than full downloads:
  - .mp3: read a small header first (4KB) to find the ID3v2 tag's declared
    size, then re-fetch exactly that many bytes if the tag is bigger than
    what was already read. If no ID3v2 header is found (or parsing still
    fails), fall back to a larger 256KB range, then to a full download.
  - .m4a/other: metadata (the "moov" atom) can be at the start OR end of
    the file depending on how it was encoded, so a small range isn't
    reliably safe. Try a 256KB range first; fall back to a full download if
    tags don't parse.

Runs with a thread pool (network-I/O-bound work, not CPU-bound -- GIL is
released during the blocking HTTP calls, so threads are the right tool
here, not multiprocessing) with retry/backoff on transient Drive API
errors (403/429/500/503).

Usage:
    uv run scripts/drive_audio_id3.py <label> [--refresh] [--workers N]
"""

from __future__ import annotations

import argparse
import io
import json
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from googleapiclient.errors import HttpError
from mutagen import File as MutagenFile
from tqdm import tqdm

from cloud_storage_tools.auth import AuthError, get_credentials
from cloud_storage_tools.config import ConfigError, load_config
from cloud_storage_tools.driveutil import (
    build_drive_service,
    download_file_bytes,
    download_file_range,
)

INVENTORY_DIR = Path("data/inventory")

HEADER_PROBE_BYTES = 4096  # enough to read an ID3v2 header + declared size
FALLBACK_RANGE_BYTES = 256 * 1024  # for m4a, and mp3 without a usable ID3v2 header
RETRYABLE_STATUSES = {403, 429, 500, 503}
MAX_RETRIES = 5


def with_retries(fn, *args, **kwargs):
    delay = 1.0
    for attempt in range(MAX_RETRIES):
        try:
            return fn(*args, **kwargs)
        except HttpError as e:
            status = getattr(e.resp, "status", None)
            if status in RETRYABLE_STATUSES and attempt < MAX_RETRIES - 1:
                time.sleep(delay)
                delay *= 2
                continue
            raise


def parse_tags_from_bytes(data: bytes) -> dict | None:
    """Parse artist/album/title/duration from raw audio bytes, in memory.

    mutagen can identify format from content (magic bytes) alone, so no
    temp file or filename hint is needed. Returns None if it can't make
    sense of the (possibly truncated) bytes, so the caller can fall back
    to a bigger read.
    """
    try:
        audio = MutagenFile(io.BytesIO(data), easy=True)
    except Exception:  # noqa: BLE001 - any parse failure means "try more bytes"
        return None
    if audio is None or audio.tags is None:
        return None

    def first(key: str) -> str | None:
        values = audio.tags.get(key)
        return values[0] if values else None

    artist = first("artist")
    title = first("title")
    if not artist and not title:
        return None
    return {
        "artist": artist,
        "album": first("album"),
        "title": title,
        "duration_sec": round(audio.info.length, 1) if getattr(audio, "info", None) else None,
    }


def ID3v2_declared_size(header: bytes) -> int | None:
    """Return the full ID3v2 tag length (header + body) from a 10+ byte header, or None."""
    if len(header) < 10 or header[:3] != b"ID3":
        return None
    b6, b7, b8, b9 = header[6], header[7], header[8], header[9]
    body_size = (b6 << 21) | (b7 << 14) | (b8 << 7) | b9
    return 10 + body_size


def fetch_tags(service, file_id: str, filename: str, size: int) -> tuple[dict | None, str]:
    """Return (tags_dict_or_None, extraction_method)."""
    ext = Path(filename).suffix.lower()

    if ext == ".mp3":
        header = with_retries(download_file_range, service, file_id, 0, HEADER_PROBE_BYTES - 1)
        declared = ID3v2_declared_size(header)
        if declared is not None:
            if declared <= len(header):
                data = header[:declared]
            else:
                end = min(declared, size - 1) if size else declared
                data = with_retries(download_file_range, service, file_id, 0, end)
            tags = parse_tags_from_bytes(data)
            if tags:
                return tags, "range-mp3-id3v2"
        # No usable ID3v2 header, or parse failed -- try a bigger window.
        data = with_retries(
            download_file_range, service, file_id, 0, min(FALLBACK_RANGE_BYTES, size or FALLBACK_RANGE_BYTES) - 1
        )
        tags = parse_tags_from_bytes(data)
        if tags:
            return tags, "range-mp3-256k"
    else:
        data = with_retries(
            download_file_range, service, file_id, 0, min(FALLBACK_RANGE_BYTES, size or FALLBACK_RANGE_BYTES) - 1
        )
        tags = parse_tags_from_bytes(data)
        if tags:
            return tags, "range-256k"

    # Last resort: full download (still in memory, no temp file).
    full_data = with_retries(download_file_bytes, service, file_id)
    tags = parse_tags_from_bytes(full_data)
    if tags:
        return tags, "full-download"
    return None, "failed"


_thread_local = threading.local()


def get_thread_service(creds):
    if not hasattr(_thread_local, "service"):
        _thread_local.service = build_drive_service(creds)
    return _thread_local.service


def process_record(creds, drive_rec: dict) -> dict:
    service = get_thread_service(creds)
    tags, method = fetch_tags(service, drive_rec["id"], drive_rec["filename"], drive_rec.get("size", 0))
    result = {
        "id": drive_rec["id"],
        "filename": drive_rec["filename"],
        "size": drive_rec.get("size", 0),
        "path": drive_rec.get("path"),
        "folder_artist": drive_rec.get("artist"),
        "folder_track_name": drive_rec.get("track_name"),
        "extraction_method": method,
    }
    if tags:
        result["artist"] = tags["artist"] or drive_rec.get("artist")
        result["album"] = tags["album"] or drive_rec.get("album")
        result["track_name"] = tags["title"] or drive_rec.get("track_name")
        result["duration_sec"] = tags["duration_sec"]
        result["id3_success"] = True
    else:
        # Fall back to folder-derived metadata so the file still has
        # *something* to match on -- extraction_method="failed" makes this
        # visible/auditable.
        result["artist"] = drive_rec.get("artist")
        result["album"] = drive_rec.get("album")
        result["track_name"] = drive_rec.get("track_name")
        result["duration_sec"] = None
        result["id3_success"] = False
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("label", help="Account label whose Drive audio files to read tags from")
    parser.add_argument(
        "--refresh", action="store_true", help="Re-pull tags even if a cached result file already exists"
    )
    parser.add_argument("--workers", type=int, default=15, help="Thread pool size (default: 15)")
    args = parser.parse_args()

    drive_audio_path = INVENTORY_DIR / f"{args.label}_drive_audio.json"
    output_path = INVENTORY_DIR / f"{args.label}_drive_audio_id3.json"

    if not drive_audio_path.exists():
        print(f"No Drive audio inventory at {drive_audio_path}. Run drive_audio_inventory.py first.", file=sys.stderr)
        return 1

    if output_path.exists() and not args.refresh:
        print(f"{output_path} already exists. Pass --refresh to re-pull tags.")
        return 0

    with drive_audio_path.open(encoding="utf-8") as f:
        drive_records = json.load(f)

    try:
        config = load_config()
        account = config.account(args.label)
        creds = get_credentials(config, account)
    except (ConfigError, AuthError) as e:
        print(f"Auth/config error for {args.label}: {e}", file=sys.stderr)
        return 1

    results = []
    method_counts: dict[str, int] = {}
    with ThreadPoolExecutor(max_workers=args.workers) as executor:
        futures = {executor.submit(process_record, creds, rec): rec for rec in drive_records}
        for future in tqdm(as_completed(futures), total=len(futures), unit="file", desc="Reading tags"):
            rec = futures[future]
            try:
                result = future.result()
            except Exception as e:  # noqa: BLE001 - keep going, report at the end
                tqdm.write(f"Failed on {rec.get('path')}: {e}")
                result = {
                    **{k: rec.get(k) for k in ("id", "filename", "size", "path")},
                    "folder_artist": rec.get("artist"),
                    "folder_track_name": rec.get("track_name"),
                    "artist": rec.get("artist"),
                    "album": rec.get("album"),
                    "track_name": rec.get("track_name"),
                    "duration_sec": None,
                    "id3_success": False,
                    "extraction_method": "error",
                }
            results.append(result)
            method_counts[result["extraction_method"]] = method_counts.get(result["extraction_method"], 0) + 1

    with output_path.open("w", encoding="utf-8") as f:
        json.dump(results, f, indent=2)

    print(f"\nSaved {len(results)} record(s) to {output_path}")
    print("Extraction method breakdown:")
    for method, count in sorted(method_counts.items(), key=lambda kv: -kv[1]):
        print(f"  {method}: {count}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
