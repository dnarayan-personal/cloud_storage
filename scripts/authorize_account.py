#!/usr/bin/env python3
"""Authorize (or re-authorize) one Google account for use by these scripts.

Usage:
    uv run scripts/authorize_account.py <label> [<label2> ...]
    uv run scripts/authorize_account.py --all

Opens a browser consent flow per account and caches the resulting token in
tokens/<label>.json. Run this once per account before usage_report.py or
other scripts can talk to that account.
"""

from __future__ import annotations

import argparse
import sys

from cloud_storage_tools.auth import get_credentials
from cloud_storage_tools.config import ConfigError, load_config


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("labels", nargs="*", help="Account labels to authorize")
    parser.add_argument(
        "--all", action="store_true", help="Authorize every account in config/accounts.yaml"
    )
    args = parser.parse_args()

    try:
        config = load_config()
    except ConfigError as exc:
        print(f"Config error: {exc}", file=sys.stderr)
        return 1

    if args.all:
        labels = [a.label for a in config.accounts]
    elif args.labels:
        labels = args.labels
    else:
        parser.print_help()
        return 1

    for label in labels:
        try:
            account = config.account(label)
        except ConfigError as exc:
            print(f"Skipping: {exc}", file=sys.stderr)
            continue
        print(f"\n=== Authorizing {label} ({account.email}) ===")
        print("A browser window will open. Log in with THIS account's credentials.")
        get_credentials(config, account, interactive=True)
        print(f"Saved token to {account.token_path}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
