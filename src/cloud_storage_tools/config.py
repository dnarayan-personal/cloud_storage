"""Load and validate the accounts.yaml configuration file."""

from __future__ import annotations

import datetime as _dt
from dataclasses import dataclass, field
from pathlib import Path

import yaml

DEFAULT_CONFIG_PATH = Path("config/accounts.yaml")


class ConfigError(RuntimeError):
    """Raised when accounts.yaml is missing or malformed."""


@dataclass
class DateRange:
    start: _dt.date
    end: _dt.date

    def contains(self, when: _dt.date) -> bool:
        return self.start <= when < self.end


@dataclass
class Account:
    label: str
    email: str
    notes: str = ""
    roles: list[str] = field(default_factory=list)
    free_photo_ranges: list[DateRange] = field(default_factory=list)

    def is_free_photo_date(self, when: _dt.date) -> bool:
        return any(r.contains(when) for r in self.free_photo_ranges)

    @property
    def token_path(self) -> Path:
        return Path("tokens") / f"{self.label}.json"


@dataclass
class Config:
    client_secret_file: Path
    accounts: list[Account]

    def account(self, label: str) -> Account:
        for acct in self.accounts:
            if acct.label == label:
                return acct
        raise ConfigError(f"No account with label {label!r} in config")


def _parse_date(value: str) -> _dt.date:
    return _dt.date.fromisoformat(value)


def load_config(path: str | Path = DEFAULT_CONFIG_PATH) -> Config:
    path = Path(path)
    if not path.exists():
        raise ConfigError(
            f"Config file {path} not found. Copy config/accounts.example.yaml "
            "to config/accounts.yaml and fill in your accounts."
        )
    with path.open("r", encoding="utf-8") as fh:
        raw = yaml.safe_load(fh) or {}

    if "accounts" not in raw or not raw["accounts"]:
        raise ConfigError(f"{path} has no 'accounts' entries")

    accounts: list[Account] = []
    seen_labels: set[str] = set()
    for entry in raw["accounts"]:
        try:
            label = entry["label"]
            email = entry["email"]
        except KeyError as exc:
            raise ConfigError(f"Account entry missing required field: {exc}") from exc
        if label in seen_labels:
            raise ConfigError(f"Duplicate account label: {label}")
        seen_labels.add(label)

        ranges = [
            DateRange(_parse_date(r["start"]), _parse_date(r["end"]))
            for r in entry.get("free_photo_ranges", []) or []
        ]
        accounts.append(
            Account(
                label=label,
                email=email,
                notes=entry.get("notes", ""),
                roles=list(entry.get("roles", []) or []),
                free_photo_ranges=ranges,
            )
        )

    client_secret_file = Path(raw.get("client_secret_file", "config/client_secret.json"))
    return Config(client_secret_file=client_secret_file, accounts=accounts)
