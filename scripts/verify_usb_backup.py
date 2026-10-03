#!/usr/bin/env python3
"""Read-only USB backup verification. Requires only Python 3.10+.

python scripts/verify_usb_backup.py --manifest keseruseg_backup_manifest.json \
    --root /path/to/usb/music --report keseruseg_usb_verification.json
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import tempfile
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path


def hash_file(path: Path) -> tuple[str, int]:
    before = path.stat()
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
            size += len(block)
    after = path.stat()
    if ((before.st_size, before.st_mtime_ns, before.st_ino)
            != (after.st_size, after.st_mtime_ns, after.st_ino)
            or size != after.st_size):
        raise OSError("File changed while being read")
    return digest.hexdigest(), size


def load_manifest(path: Path) -> list[dict]:
    rows = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(rows, list) or not rows:
        raise ValueError("Manifest must be a nonempty JSON list")
    ids = set()
    for row in rows:
        if not isinstance(row, dict) or not isinstance(row.get("id"), str) or not row["id"]:
            raise ValueError("Every manifest row must have a nonempty string ID")
        if row["id"] in ids:
            raise ValueError(f"Duplicate manifest ID: {row['id']}")
        ids.add(row["id"])
    return rows


def verify(rows: list[dict], root: Path) -> dict:
    expectations = {}
    invalid = {}
    for row in rows:
        digest = row.get("sha256")
        try:
            size = int(row["downloaded_size"])
            if size < 0 or not isinstance(digest, str) or not re.fullmatch(
                r"[a-fA-F0-9]{64}", digest
            ):
                raise ValueError("Invalid hash or size")
        except (KeyError, TypeError, ValueError):
            invalid[row["id"]] = "Missing or invalid original SHA-256/downloaded_size"
            continue
        expectations[row["id"]] = (digest.lower(), size)

    wanted_sizes = {size for _, size in expectations.values()}
    matches = defaultdict(list)
    observed = {}
    errors = []
    skipped_links = []
    scanned = hashed = 0

    def walk_error(error):
        errors.append({"path": str(error.filename), "error": str(error)})

    for directory, directories, files in os.walk(root, onerror=walk_error, followlinks=False):
        for name in list(directories):
            path = Path(directory) / name
            if path.is_symlink():
                directories.remove(name)
                skipped_links.append(str(path.relative_to(root)))
        for name in files:
            path = Path(directory) / name
            relative = path.relative_to(root).as_posix()
            if path.is_symlink():
                skipped_links.append(relative)
                continue
            scanned += 1
            try:
                size = path.stat().st_size
                if not path.is_file():
                    continue
                observed[relative] = {"size_bytes": size}
                if size not in wanted_sizes:
                    continue
                digest, size = hash_file(path)
                hashed += 1
                observed[relative]["sha256"] = digest
                matches[(digest, size)].append(relative)
            except OSError as error:
                observed[relative] = {"error": str(error)}
                errors.append({"path": relative, "error": str(error)})

    results = []
    for row in rows:
        result = {
            "id": row["id"], "source_path": row.get("path"),
            "upload_status": row.get("upload_status"),
            "expected_sha256": row.get("sha256"),
            "expected_size_bytes": row.get("downloaded_size"),
        }
        if row["id"] in invalid:
            result.update(status="unverifiable", reason=invalid[row["id"]])
        else:
            copies = sorted(matches.get(expectations[row["id"]], []))
            if copies:
                result.update(status="verified", local_paths=copies)
            else:
                original = str(row.get("path", "")).replace("\\", "/")
                at_path = observed.get(original)
                if at_path and "error" in at_path:
                    status = "unreadable"
                elif at_path:
                    status = "different"
                elif errors or skipped_links:
                    status = "not_found_scan_incomplete"
                else:
                    status = "not_found"
                result.update(status=status, local_paths=[])
        results.append(result)
    return {
        "schema_version": 1,
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "root": str(root),
        "scan_complete": not errors and not skipped_links,
        "files_scanned": scanned, "files_hashed": hashed,
        "counts": dict(Counter(row["status"] for row in results)),
        "errors": errors, "skipped_symlinks": skipped_links, "items": results,
        "warning": "Content verification only; this report does not authorize Drive deletion.",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--root", type=Path, required=True, help="USB backup folder to scan recursively")
    parser.add_argument("--report", type=Path, required=True, help="Output JSON outside the USB scan folder")
    args = parser.parse_args()
    temporary = None
    try:
        root = args.root.resolve(strict=True)
        manifest = args.manifest.resolve(strict=True)
        report_path = args.report.resolve()
        if not root.is_dir():
            raise ValueError("--root must be a directory")
        if report_path == manifest or report_path == root or root in report_path.parents:
            raise ValueError("--report must be outside the scan folder and must not overwrite the manifest")
        rows = load_manifest(manifest)
        manifest_hash, _ = hash_file(manifest)
        print(f"Scanning {root} for {len(rows)} manifest entries. No USB files will be modified.",
              flush=True)
        report = verify(rows, root)
        report["manifest_sha256"] = manifest_hash
        report["manifest_filename"] = manifest.name
        if hash_file(manifest)[0] != manifest_hash:
            raise ValueError("Manifest changed during verification; rerun")
        report_path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=report_path.parent,
                                         delete=False) as stream:
            temporary = Path(stream.name)
            json.dump(report, stream, indent=2)
        temporary.replace(report_path)
        print(json.dumps(report["counts"], sort_keys=True))
        print(f"Report: {report_path}")
        return 0 if report["scan_complete"] and all(
            row["status"] == "verified" for row in report["items"]
        ) else 1
    except (OSError, ValueError) as error:
        print(f"Verification failed: {error}", file=sys.stderr)
        return 2
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


if __name__ == "__main__":
    raise SystemExit(main())
