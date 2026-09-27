#!/usr/bin/env python3
"""Render a stacked bar chart of storage usage by type, across all accounts.

Reads data/reports/usage_report.json (produced by scripts/usage_report.py --
run that first/again to refresh the data) and plots one horizontal bar per
account, split into segments:

  - Each Drive file category (audio/image/video/google-doc/other) as its own
    segment, in GB.
  - "outside Drive (Gmail+Photos)" as one combined segment, since the Drive
    API doesn't break that figure down further (see README's API
    limitations note).

Saves the chart to data/reports/usage_by_type.png.

Usage:
    uv run scripts/visualize_usage.py
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

REPORT_JSON_PATH = Path("data/reports/usage_report.json")
OUTPUT_PATH = Path("data/reports/usage_by_type.png")
OUTSIDE_DRIVE_LABEL = "outside Drive (Gmail+Photos)"
GB = 1024**3

# Fixed segment order/colors so the same category always gets the same color
# across charts.
SEGMENT_COLORS = {
    "audio": "tab:blue",
    "image": "tab:orange",
    "video": "tab:red",
    "google-doc": "tab:green",
    "other": "tab:gray",
    OUTSIDE_DRIVE_LABEL: "tab:purple",
}


def build_segments(accounts: list[dict]) -> tuple[list[str], dict[str, list[float]]]:
    """Return (account_labels, {segment_name: [GB per account, in same order]})."""
    labels = [a["label"] for a in accounts]
    segments: dict[str, list[float]] = {name: [0.0] * len(accounts) for name in SEGMENT_COLORS}

    for i, account in enumerate(accounts):
        quota = account.get("quota")
        if quota is None:
            continue
        for category, summary in (account.get("drive_files") or {}).items():
            gb = summary["size_bytes"] / GB
            segments.setdefault(category, [0.0] * len(accounts))[i] += gb
        segments[OUTSIDE_DRIVE_LABEL][i] = quota["usage_outside_drive_bytes"] / GB

    # Drop segments that are all-zero (e.g. a category no account has).
    segments = {name: values for name, values in segments.items() if any(values)}
    return labels, segments


def plot(labels: list[str], segments: dict[str, list[float]], output_path: Path) -> None:
    fig, ax = plt.subplots(figsize=(10, 0.6 * len(labels) + 2))
    left = [0.0] * len(labels)
    for name, values in segments.items():
        color = SEGMENT_COLORS.get(name)
        ax.barh(labels, values, left=left, label=name, color=color)
        left = [total + value for total, value in zip(left, values, strict=True)]

    ax.set_xlabel("GB")
    ax.set_title("Storage usage by type, per account")
    ax.legend(loc="upper right", bbox_to_anchor=(1.3, 1))
    ax.invert_yaxis()  # first account (from JSON order) at top
    fig.tight_layout()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output_path, dpi=150, bbox_inches="tight")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--input", type=Path, default=REPORT_JSON_PATH, help="Path to usage_report.json"
    )
    parser.add_argument(
        "--output", type=Path, default=OUTPUT_PATH, help="Path to write the chart PNG"
    )
    args = parser.parse_args()

    if not args.input.exists():
        print(
            f"No report found at {args.input}. Run scripts/usage_report.py first.",
            file=sys.stderr,
        )
        return 1

    with args.input.open(encoding="utf-8") as f:
        report = json.load(f)

    labels, segments = build_segments(report["accounts"])
    plot(labels, segments, args.output)
    print(f"Saved chart to {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
