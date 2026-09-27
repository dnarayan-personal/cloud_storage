"""Per-account OAuth2 credential management.

Each Google account gets its own cached token file under tokens/<label>.json.
The same OAuth "Desktop app" client secret is reused across accounts; only
the token differs. Run `scripts/authorize_account.py <label>` once per
account to create/refresh its token via the browser consent flow.
"""

from __future__ import annotations

from pathlib import Path

from google.auth.exceptions import RefreshError
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow

from cloud_storage_tools.config import Account, Config

# Read-only scopes for phase 1 (usage report / inventory / dedup scan).
# Deliberately does NOT request write/delete scopes yet -- those will be
# added explicitly for the future move/quarantine scripts, gated behind
# a re-authorization and explicit user confirmation.
#
# Note: Photos access is handled separately via the Picker API (see
# scripts/photos_inventory.py), which uses its own
# photospicker.mediaitems.readonly scope requested per-session -- the old
# bulk photoslibrary.readonly scope was removed by Google in March 2025 and
# is intentionally not requested here.
READONLY_SCOPES = [
    "https://www.googleapis.com/auth/drive.readonly",
]


class AuthError(RuntimeError):
    pass


def get_credentials(
    config: Config,
    account: Account,
    scopes: list[str] | None = None,
    interactive: bool = False,
) -> Credentials:
    """Return valid credentials for `account`, refreshing if possible.

    If no cached token exists and interactive=False, raises AuthError telling
    the caller to run the authorize_account script first. Set interactive=True
    (used by scripts/authorize_account.py) to run the browser consent flow.
    """
    scopes = scopes or READONLY_SCOPES
    token_path = account.token_path
    creds: Credentials | None = None

    if token_path.exists():
        creds = Credentials.from_authorized_user_file(str(token_path), scopes)

    if creds and creds.valid:
        return creds

    if creds and creds.expired and creds.refresh_token:
        try:
            creds.refresh(Request())
            _save_token(creds, token_path)
            return creds
        except RefreshError:
            creds = None  # fall through to re-auth

    if not interactive:
        raise AuthError(
            f"No valid cached credentials for account '{account.label}' "
            f"({account.email}). Run: uv run scripts/authorize_account.py "
            f"{account.label}"
        )

    if not config.client_secret_file.exists():
        raise AuthError(
            f"Client secret file not found at {config.client_secret_file}. "
            "Download an OAuth 'Desktop app' client secret from Google Cloud "
            "Console and save it there."
        )

    flow = InstalledAppFlow.from_client_secrets_file(str(config.client_secret_file), scopes)
    creds = flow.run_local_server(port=0)
    _save_token(creds, token_path)
    return creds


def _save_token(creds: Credentials, token_path: Path) -> None:
    token_path.parent.mkdir(parents=True, exist_ok=True)
    token_path.write_text(creds.to_json(), encoding="utf-8")
