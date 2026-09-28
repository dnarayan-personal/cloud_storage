"""Shared conservative artist/title normalization and matching logic used by
both the folder-metadata matcher and the ID3-tag matcher
(scripts/match_drive_ytmusic.py).

Precision over recall throughout -- see match_drive_ytmusic.py's module
docstring for the full rationale.
"""

from __future__ import annotations

import re
from collections import defaultdict

# Leading track-number prefix in a filename-derived track name, e.g.
# "10. Breakadawn", "01 - Breakadawn", or a disc-track combo "1-14 Hammer"
# (disc 1, track 14).
TRACK_NUMBER_RE = re.compile(r"^\s*\d{1,3}(-\d{1,3})?\s*[.\-_)]?\s*")

# Non-word punctuation to drop for comparison (keeps letters/digits/spaces
# and apostrophes, since those distinguish words like "don't").
PUNCTUATION_RE = re.compile(r"[^\w\s']", re.UNICODE)


def normalize(s: str | None) -> str:
    """Conservative normalization: case/whitespace/punctuation only.

    Deliberately does NOT strip bracketed qualifiers like "(Live)" or
    "(Remix)" -- those often indicate a genuinely different recording, and
    stripping them risks false-positive matches. Under-matching (a missed
    duplicate) is the safe failure mode here, not over-matching.
    """
    if not s:
        return ""
    s = s.strip().lower()
    s = s.replace("\u2019", "'").replace("\u2018", "'")  # curly quotes -> straight
    s = s.strip("\"'")  # YT titles are sometimes wrapped in quotes
    s = PUNCTUATION_RE.sub("", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def clean_drive_track_name(track_name: str, artist: str | None) -> str:
    """Strip a leading track number and a leading "Artist - " prefix, if present.

    Drive filenames often look like "10. De La Soul - Breakadawn" (track
    number + artist name baked into the filename); we want just the song
    title for comparison against YT Music's title field. This is a no-op if
    track_name doesn't have those patterns (e.g. an already-clean ID3 title).
    """
    s = TRACK_NUMBER_RE.sub("", track_name)
    if artist:
        prefix_re = re.compile(rf"^\s*{re.escape(artist)}\s*[-:_]\s*", re.IGNORECASE)
        s = prefix_re.sub("", s)
    return s.strip()


def build_ytmusic_index(ytmusic_records: list[dict]) -> dict[str, list[dict]]:
    """Index YT Music records by normalized title -> list of records."""
    index: dict[str, list[dict]] = defaultdict(list)
    for rec in ytmusic_records:
        index[normalize(rec.get("title"))].append(rec)
    return index


def match_one(artist: str | None, track_name: str, yt_index: dict[str, list[dict]]) -> dict:
    """Classify one (artist, track_name) pair against the YT Music index.

    Returns {"tier": ..., "matches": [...]}. See match_drive_ytmusic.py's
    module docstring for what each tier means.
    """
    cleaned_title = clean_drive_track_name(track_name or "", artist)
    norm_title = normalize(cleaned_title)
    norm_artist = normalize(artist)

    candidates = yt_index.get(norm_title, [])
    if not candidates:
        return {"tier": "no_match", "matches": []}

    if norm_artist:
        exact_artist_matches = [
            c for c in candidates if norm_artist in (normalize(a) for a in c.get("artists") or [])
        ]
        if exact_artist_matches:
            return {"tier": "high_confidence", "matches": exact_artist_matches}

    return {"tier": "needs_review", "matches": candidates}
