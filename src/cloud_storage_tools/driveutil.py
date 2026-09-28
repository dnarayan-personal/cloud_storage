"""Helpers for querying Google Drive: quota and mimetype-bucketed inventory."""

from __future__ import annotations

import io
from dataclasses import dataclass

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload

# Categories we bucket Drive files into for reporting. Order matters: first
# matching prefix wins.
_MIME_CATEGORIES = [
    ("image", "image/"),
    ("video", "video/"),
    ("audio", "audio/"),
    ("google-doc", "application/vnd.google-apps."),
]


def categorize_mime(mime_type: str) -> str:
    for category, prefix in _MIME_CATEGORIES:
        if mime_type.startswith(prefix):
            return category
    return "other"


@dataclass
class DriveQuota:
    limit_bytes: int | None  # None means unlimited
    usage_bytes: int
    usage_in_drive_bytes: int
    usage_in_drive_trash_bytes: int

    @property
    def usage_outside_drive_bytes(self) -> int:
        """Usage not attributable to Drive files themselves.

        The Drive API does not expose a separate Photos or Gmail figure --
        this is everything else (mostly Gmail attachments + Photos, lumped
        together). See README for the known API limitation.
        """
        return self.usage_bytes - self.usage_in_drive_bytes


@dataclass
class DriveFileSummary:
    """Aggregated counts/sizes for one mimetype category, non-trashed vs trashed."""

    count: int = 0
    size_bytes: int = 0
    trashed_count: int = 0
    trashed_size_bytes: int = 0


def build_drive_service(creds: Credentials):
    return build("drive", "v3", credentials=creds, cache_discovery=False)


def get_quota(service) -> DriveQuota:
    about = service.about().get(fields="storageQuota").execute()
    quota = about.get("storageQuota", {})
    limit = quota.get("limit")
    return DriveQuota(
        limit_bytes=int(limit) if limit is not None else None,
        usage_bytes=int(quota.get("usage", 0)),
        usage_in_drive_bytes=int(quota.get("usageInDrive", 0)),
        usage_in_drive_trash_bytes=int(quota.get("usageInDriveTrash", 0)),
    )


def summarize_files_by_category(service) -> dict[str, DriveFileSummary]:
    """Walk all Drive files (including trashed) and bucket by mime category.

    Uses files.list with includeItemsFromAllDrives=False (personal My Drive
    only) since these are personal accounts, not shared/team drives.
    """
    summaries: dict[str, DriveFileSummary] = {}
    page_token = None
    fields = "nextPageToken, files(id, mimeType, size, trashed)"
    while True:
        resp = (
            service.files()
            .list(
                pageSize=1000,
                fields=fields,
                pageToken=page_token,
                spaces="drive",
                q="'me' in owners",
            )
            .execute()
        )
        for f in resp.get("files", []):
            category = categorize_mime(f.get("mimeType", ""))
            summary = summaries.setdefault(category, DriveFileSummary())
            size = int(f.get("size", 0)) if "size" in f else 0
            if f.get("trashed"):
                summary.trashed_count += 1
                summary.trashed_size_bytes += size
            else:
                summary.count += 1
                summary.size_bytes += size
        page_token = resp.get("nextPageToken")
        if not page_token:
            break
    return summaries


FOLDER_MIME_TYPE = "application/vnd.google-apps.folder"


def list_folders_and_audio_files(service) -> tuple[dict[str, dict], list[dict]]:
    """Fetch (in one listing pass) a folder-id map and all audio files.

    Returns:
      - folder_map: {folder_id: {"name": str, "parents": list[str]}}
      - audio_files: list of {id, name, size, parents, trashed} dicts, for
        every non-folder file whose mimeType starts with "audio/".

    This is metadata-only -- no file content is downloaded (see README's
    note on why this is preferred over reading ID3 tags).
    """
    folder_map: dict[str, dict] = {}
    audio_files: list[dict] = []
    page_token = None
    fields = "nextPageToken, files(id, name, mimeType, size, parents, trashed)"
    while True:
        resp = (
            service.files()
            .list(
                pageSize=1000,
                fields=fields,
                pageToken=page_token,
                spaces="drive",
                q="'me' in owners",
            )
            .execute()
        )
        for f in resp.get("files", []):
            mime_type = f.get("mimeType", "")
            if mime_type == FOLDER_MIME_TYPE:
                folder_map[f["id"]] = {"name": f.get("name", ""), "parents": f.get("parents", [])}
            elif mime_type.startswith("audio/"):
                audio_files.append(
                    {
                        "id": f["id"],
                        "name": f.get("name", ""),
                        "size": int(f.get("size", 0)) if "size" in f else 0,
                        "parents": f.get("parents", []),
                        "trashed": f.get("trashed", False),
                    }
                )
        page_token = resp.get("nextPageToken")
        if not page_token:
            break
    return folder_map, audio_files


def resolve_folder_path(parents: list[str], folder_map: dict[str, dict]) -> list[str]:
    """Walk up from a file's immediate parent to the root, return folder names root-first.

    Assumes a single parent chain (true for personal My Drive files, which
    normally have exactly one parent). If a parent id isn't in folder_map
    (e.g. shared-drive edge cases), stops there.
    """
    names: list[str] = []
    current = parents[0] if parents else None
    seen: set[str] = set()
    while current and current in folder_map and current not in seen:
        seen.add(current)
        folder = folder_map[current]
        names.append(folder["name"])
        parent_list = folder.get("parents") or []
        current = parent_list[0] if parent_list else None
    names.reverse()
    return names


def download_file(service, file_id: str, dest_path: str) -> None:
    """Download a Drive file's full content to dest_path.

    Used sparingly (e.g. sampling a few files to read ID3 tags) -- bulk
    metadata operations should use files.list instead, see module docstring.
    """
    request = service.files().get_media(fileId=file_id)
    with open(dest_path, "wb") as fh:
        downloader = MediaIoBaseDownload(fh, request)
        done = False
        while not done:
            _, done = downloader.next_chunk()


def download_file_range(service, file_id: str, start: int, end: int) -> bytes:
    """Download bytes [start, end] (inclusive) of a Drive file's content.

    Used to read just enough of a file to extract ID3/metadata tags without
    downloading the whole thing -- see scripts/drive_audio_id3.py.
    """
    request = service.files().get_media(fileId=file_id)
    request.headers["Range"] = f"bytes={start}-{end}"
    return request.execute()


def download_file_bytes(service, file_id: str) -> bytes:
    """Download a Drive file's full content into memory (no temp file)."""
    request = service.files().get_media(fileId=file_id)
    buf = io.BytesIO()
    downloader = MediaIoBaseDownload(buf, request)
    done = False
    while not done:
        _, done = downloader.next_chunk()
    return buf.getvalue()
