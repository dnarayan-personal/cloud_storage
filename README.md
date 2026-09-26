# cloud_storage

Scripts for auditing and tidying up storage across multiple Google accounts
(Drive, Google Photos, and — to the extent possible — YouTube Music), with a
focus on:

1. **Usage report** — see current usage across all accounts (read-only, safe).
2. **Duplicate finder** — find redundant copies of the same file across/within
   accounts (read-only, produces a report; planned, not yet implemented).
3. **Cleanup/move** — quarantine duplicates and move data between accounts to
   free up space on your active/latest-photos account, without losing data
   (planned; will require explicit re-authorization with write scopes and
   will never hard-delete anything automatically).

Only step 1 is implemented so far. Everything is designed to be read-only
until you explicitly opt into a destructive step, and even then deletions are
routed through a quarantine folder for manual review rather than deleted
outright.

## Requirements

- Python (managed via [uv](https://docs.astral.sh/uv/); a `uv.lock` is
  committed). No global installs needed — just `uv run ...`.
- A Google Cloud project with the **Drive API** and **Photos Library API**
  enabled, and an OAuth **Desktop app** client secret downloaded as JSON.
  This is a one-time setup, shared across all your accounts:
  1. Go to https://console.cloud.google.com/, create (or reuse) a project.
  2. Enable "Google Drive API" and "Photos Library API" under APIs & Services.
  3. Under "OAuth consent screen", set up an external/testing app and add
     each of your Google accounts as a test user (required while the app is
     unverified — fine for personal use across your own accounts).
  4. Under "Credentials", create an OAuth client ID of type **Desktop app**,
     download the JSON, save it as `config/client_secret.json`.

## Setup

```bash
cp config/accounts.example.yaml config/accounts.yaml
# edit config/accounts.yaml: fill in your ~8 accounts, roles, and any
# "free" (unlimited/legacy) photo date ranges that should never be touched.
```

`config/accounts.yaml` and `config/client_secret.json` are gitignored — they
contain personal data and must never be committed.

Authorize each account once (opens a browser consent flow per account; log
in with that specific account when prompted):

```bash
uv run scripts/authorize_account.py --all
# or one at a time:
uv run scripts/authorize_account.py main
```

Tokens are cached per-account under `tokens/<label>.json` (gitignored) and
refreshed automatically afterwards.

## Usage

```bash
# Report for every configured account:
uv run scripts/usage_report.py

# Just one or two accounts:
uv run scripts/usage_report.py --account main --account acct2
```

For each account this prints:

- Drive storage quota: limit, total usage, usage attributable to Drive files,
  usage in Drive trash, and usage outside Drive (Gmail + Photos combined).
- Drive files bucketed by type (image/video/audio/other/google-doc), flagging
  any image/video files sitting in Drive (unexpected — should be in Photos)
  and any audio files in accounts not tagged `music_in_drive` in your config.
- Google Photos item counts (photos vs videos) and the creation-date span of
  the library, flagging when the most recent photo falls outside your
  configured "free" date ranges (i.e. it's actively consuming quota).
- A combined total usage/limit across all reported accounts.

This script only reads data. It makes no changes to any account.

### Known API limitations (read before trusting exact numbers)

- **Drive quota vs Photos size**: The Drive `about.get` quota endpoint reports
  `usage`, `usageInDrive`, and `usageInDriveTrash`, but does **not** break out
  Gmail vs Photos. `usage - usageInDrive` is "everything else" (Gmail
  attachments + Photos combined). There is no public API that returns a
  precise Photos-only byte count.
- **Photos Library API has no file size field**: `mediaItems.list` returns
  metadata (mimeType, creation time, dimensions) but not bytes. This script
  reports Photos **counts** and date ranges, not bytes, for that reason.
- **YouTube Music has no public storage API**: whether an uploaded track
  counts against quota (vs. being deduplicated for free against Google's
  catalog) is not something any API currently exposes. You'll need to check
  the Google One storage manager UI and/or YT Music's own upload/library UI
  manually to confirm this per account — the usage report prints a reminder
  of this each run.
- Because of the above, treat "usage outside Drive" as a **directional**
  Gmail+Photos figure, not an exact Photos total. Cross-check against the
  Google One storage dashboard (https://one.google.com/storage) per account
  if you need precise figures.

## Roadmap

- [x] Cross-account usage report (read-only)
- [ ] Duplicate finder: exact-match (hash-based) duplicate detection for
      Drive files first within an account, then across accounts; extend to
      Photos via content hash where available. Start with exact matches only;
      perceptual/near-duplicate (edited/resized) matching is a later phase.
- [ ] Quarantine workflow: move detected duplicates into a
      `to-delete-review/` folder (Drive) or album (Photos) instead of
      deleting, so nothing is destroyed automatically. Requires re-running
      `authorize_account.py` with write scopes (`drive.file` /
      `photoslibrary.edit.appendonly` etc.) which will be requested
      explicitly and separately from the read-only scopes used today.
- [ ] Cross-account move: copy+verify+quarantine-original workflow to shift
      data (especially music-in-Drive found on non-`music_in_drive`-tagged
      accounts) onto accounts with spare quota, prioritizing freeing up the
      account tagged `latest_photos`/`active_gmail`.
- [ ] Safety rails: dry-run-by-default flag on any script that would move or
      delete anything; a manifest/log of every action taken so moves can be
      audited or reversed.

## Project layout

```
config/
  accounts.example.yaml   # template — copy to accounts.yaml (gitignored)
scripts/
  authorize_account.py    # one-time OAuth consent flow per account
  usage_report.py         # cross-account usage report (read-only)
src/cloud_storage_tools/
  config.py               # accounts.yaml loading/validation
  auth.py                 # per-account OAuth credential management
  driveutil.py            # Drive quota + file inventory helpers
  photosutil.py           # Photos Library counts/date-range helpers
  formatting.py           # human-readable byte formatting
tokens/                   # per-account cached OAuth tokens (gitignored)
```
