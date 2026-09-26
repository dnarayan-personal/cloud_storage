"""Small shared formatting helpers."""

from __future__ import annotations


def human_bytes(n: int | None) -> str:
    if n is None:
        return "unlimited"
    value = float(n)
    for unit in ["B", "KB", "MB", "GB", "TB", "PB"]:
        if value < 1024 or unit == "PB":
            return f"{value:.2f} {unit}"
        value /= 1024
    return f"{value:.2f} PB"
