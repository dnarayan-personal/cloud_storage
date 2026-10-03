# Photo migration plan

Revised 2026-10-03. Goals, in order: free space in `main` for Gmail growth;
keep chronological ranges together where practical; optionally redirect
future photo backups away from `main`. Calendar-year boundaries are not
required. Do not clean up Gmail or Other as part of this plan.

## Evidence and limits

Sources: local `data/inventory/*_photos.json`,
`data/inventory/photo_sizes.json`, and
`data/reports/usage_report.json` (generated 2026-10-02 16:04 UTC).
Most historical inventory rows have not had their account identity verified.
See [API findings](api_findings.md) for API sources and attribution limits,
and [anomalies](photo_anomalies.md) for the unresolved items.

Quota figures are dated snapshots, not current guarantees. Downloaded byte
sizes describe the fetched representation, not guaranteed source quota
recovery or destination billing. Picker video downloads may be transcoded
and image downloads omit location metadata; do not assume they are archival
originals. Google's download documentation:
https://developers.google.com/photos/picker/guides/media-items

## Reassessment of proposed moves

| Proposed move | Capacity benefit / chronology | Recommendation |
|---|---|---|
| Three November 28, 2023 photos from main to narayanan.dushyanth | 4,208,079 downloaded bytes; destination already spans all of 2023, including 52 November items | Optional tidying/pilot, not meaningful headroom |
| Two March 2024 images from main to ambient.dancer | Source items unresolved; destination's recorded range starts March 11 | Defer; do not assume their location or size |
| December 2025 from main to ambient.dancer | No chronological overlap: ambient ends November 20, main starts December 6; 985,118,139 downloaded bytes already exceed destination's 832,079,529 free bytes | Not useful for contiguity; do not move merely to consolidate a year |
| Main's January-May 2021 items to narayanan.dushyanth | Already a clean boundary: destination starts June 1; main's items fall in its configured free range | Keep in main |
| Other main photos in its configured free ranges | Moving them provides no expected main quota benefit and may consume destination quota | Leave alone for this capacity-focused plan |

`narayanan.dushyanth` had 21,083,917 free bytes, so even the small November
move requires a fresh capacity check and leaves very little headroom.

## Capacity-focused choices

1. Redirect future phone photo backups to an account with adequate capacity.
   This stops new photo growth in main, but does not free existing storage.
   `keseruseg` is a candidate: its saved Photos inventory is empty and the
   quota snapshot shows 5,484,325,609 free bytes. Check current quota first.
2. If existing main headroom (2,394,645,914 bytes in the snapshot) is too
   small, consider moving a contiguous recent block to that same new account.
   The main inventory has 131 December 2025 entries and 547 entries in 2026:
   678 entries in the recent block, not 678 measured/located files. The full
   block's byte size is unknown. Alternatively choose a later date cutoff
   and move the suffix through the present, then continue new backups there.
3. A destination with enough room for the entire December-2025-onward block
   would preserve the existing boundary after ambient.dancer's November
   2025 range while freeing substantially more main storage than the
   three-photo pilot. Do not assume the block fits in keseruseg's current
   free space. Earlier Drive-audio cleanup proposals could make more room,
   but require independently verified backups and explicit deletion approval.

No uploads, source deletions, backup-account switches, or quota refreshes
have been performed as part of this reassessment. Before any source removal,
verify the destination copies, capture dates, video playback, preservation
requirements, and actual quota impact. Retain local downloads in the meantime.

## Concrete execution plan (supersedes the choices above)

### Local USB verification

Clone the repository locally and separately download the gitignored
`data/manifest/keseruseg_backup_manifest.json`. No Google credentials or
third-party Python packages are needed; use Python 3.10 or newer:

```sh
python scripts/verify_usb_backup.py --manifest keseruseg_backup_manifest.json --root "/path/to/USB/music" --report keseruseg_usb_verification.json
```

Use `python3` instead of `python` where appropriate. Select the keseruseg
backup folder as the root if available; a larger folder also works.
The report must be outside the scanned folder. The verifier reads the USB
without modifying files, matches original SHA-256 plus byte length even
after renames, and reports nonmatches and read failures explicitly. Symlinks
are not followed and make the scan incomplete. Files with irrelevant byte
sizes need not be hashed. Exit 0 means all entries verified with a complete
scan; 1 means review is needed; 2 means the run failed.
Return the generated JSON to the Codespace for review. It contains local
paths and hashes, not music content or credentials. It does not authorize
deletion or establish the current state of Drive/YT Music.

Choose **keseruseg** for December 2025 onward and future photo backups.
Keep the existing ambient.dancer boundary at November 2025. Keep main's
configured free photos in main. The ordered operations are:

1. **Prepare a reviewed music deletion list for keseruseg.** The current
   backup manifest contains 2,252 `SUCCEEDED` entries totaling
   10,521,052,407 source bytes and one failed 3,063,856-byte `.m4p`.
   The user previously reported a USB backup of all Drive files. Verify
   the USB originals against recorded SHA-256 hashes, and reconcile the
   current Drive IDs/sizes against the proposed deletion list. Keep the
   failed `.m4p`, album artwork, unrelated files, and any entry failing
   verification. YT upload success alone is not proof of archival fidelity
   or a resolved library match; the verified USB originals are essential.
2. **Free keseruseg first.** With explicit approval of the exact list, move
   those verified audio IDs to Drive Trash. Review the list and USB copy
   again, then permanently delete only those approved IDs from Trash.
   Do not empty the whole Trash (it contains unrelated pre-existing files).
   Refresh quota after Google processes the deletions. The old snapshot
   plus the eligible audio suggests about 16.0 GB free, not a guarantee.
   Retain USB originals and YT Music uploads.
3. **Export main's recent photo block before uploading.** Use a dedicated
   Google Photos album for items dated December 1, 2025 through the cutover
   date, and Takeout that album's original media plus JSON sidecars. The
   inventory's first item is December 6. Expected baseline: 678 inventory
   entries (131 in December 2025 and 547 in 2026), of which 12 December
   entries remain unlocated; include any newer items since inventory.
   Do not demand 678 files if only 666 baseline items can be located, and
   do not silently mark unlocated entries migrated.
   Keep the existing Picker downloads as inspection copies. The export
   replaces them as the migration source where original/location/video
   preservation matters; Picker downloads are not guaranteed originals.
   Retain a second local/USB copy of the export and sidecars.
4. **Measure and upload to keseruseg.** Unpack and index media separately
   from sidecars, preserve their associations, hash the files, and measure
   total media bytes. Refresh keseruseg quota. Proceed only if that total
   fits with at least 2 GB spare (chosen working reserve). Otherwise stop
   before uploading or deleting photos and revise capacity. Upload a
   representative pilot first (photo, motion photo, video), check dates,
   quality and playback, then upload the remaining located recent block.
   Associate destination IDs with source IDs where the mapping is
   established; unresolved associations block source deletion.
5. **Switch future phone backups to keseruseg.** Do this after the initial
   import is verified, while all main originals still exist. Check every
   backed-up device and enabled folder. Verify a newly taken photo reaches
   keseruseg. Account switching may also back up older media still on the
   device; inspect pending uploads and restrict/archive older local media
   first as needed. Reconcile the short cutover interval for new files
   arriving in main, without deleting device files merely to force a cutoff.
6. **Remove main's migrated copies only after verification.** Verify each
   planned source removal has a destination copy, correct capture date,
   preserved media behavior, and an independent retained export. Use the
   main web account to move only approved source items to Photos Trash;
   test a small batch first and confirm destination/device copies remain.
   After review, permanently delete only those approved trashed items to
   reclaim quota. Never bulk-delete everything matching a year or empty
   unrelated Trash. Leave unlocated/unmatched entries and free photos alone.
7. **Finish small tidying and remaining music later.** Move the three
   November 2023 photos (4.21 MB measured) to narayanan.dushyanth only after
   refreshing its narrow headroom and verifying the copies; this is tidying,
   not the main capacity operation. Leave the two unresolved March 2024
   images pending. Then apply the same reviewed USB/hash/Drive-ID deletion
   procedure to bitterheid (2,951 successful entries, 15,572,218,993 bytes)
   and verbitterung (3,083 successful entries, 16,057,084,698 bytes), retaining
   artwork and unrelated files. This creates reserve accounts; it is not
   a prerequisite for the first photo move.

Planned end state: main retains Gmail and its free photo archive;
narayanan.dushyanth retains its existing mid-2021 through early-2024 period
and the optional November 2023 tidy-up; ambient.dancer retains its existing
2024 through November 2025 period; keseruseg holds the located
December-2025-onward block and future backups. Existing 2024 overlaps and
unresolved items are not claimed to be fixed by this plan.

No deletion or photo-upload implementation is implied by this document:
the reviewed per-ID deletion tools, archival export reconciliation, and
destination verification still need to be prepared before those steps.
