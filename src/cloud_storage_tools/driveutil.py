"""Helpers for querying Google Drive: quota and mimetype-bucketed inventory."""

from __future__ import annotations

from dataclasses import dataclass

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

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
