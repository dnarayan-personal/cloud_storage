"""Helpers for querying the Google Photos Library API.

Known limitation: the Photos Library API does NOT return file size in
mediaItem metadata, so we can only report *counts* and creation-date ranges
here, not bytes. Combined with Drive quota's `usage_outside_drive_bytes`
(usage - usageInDrive), you get a directional picture but not an exact
Photos-only byte figure. See README for details and workarounds.
"""

from __future__ import annotations

from dataclasses import dataclass

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build


@dataclass
class PhotosSummary:
    photo_count: int = 0
    video_count: int = 0
    earliest_creation_time: str | None = None
    latest_creation_time: str | None = None


def build_photos_service(creds: Credentials):
    return build(
        "photoslibrary",
        "v1",
        credentials=creds,
        cache_discovery=False,
        static_discovery=False,
        discoveryServiceUrl=(
            "https://photoslibrary.googleapis.com/$discovery/rest?version=v1"
        ),
    )


def summarize_photos(service) -> PhotosSummary:
    summary = PhotosSummary()
    page_token = None
    while True:
        resp = (
            service.mediaItems()
            .list(pageSize=100, pageToken=page_token)
            .execute()
        )
        for item in resp.get("mediaItems", []):
            mime = item.get("mimeType", "")
            creation_time = item.get("mediaMetadata", {}).get("creationTime")
            if mime.startswith("video/"):
                summary.video_count += 1
            else:
                summary.photo_count += 1
            if creation_time:
                if (
                    summary.earliest_creation_time is None
                    or creation_time < summary.earliest_creation_time
                ):
                    summary.earliest_creation_time = creation_time
                if (
                    summary.latest_creation_time is None
                    or creation_time > summary.latest_creation_time
                ):
                    summary.latest_creation_time = creation_time
        page_token = resp.get("nextPageToken")
        if not page_token:
            break
    return summary
