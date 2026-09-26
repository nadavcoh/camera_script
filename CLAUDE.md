# CLAUDE.md

Context for working on `camera.py`. This is a single-file Windows automation
script (Task Scheduler-triggered on camera insertion), not a package — keep
changes to that single-file shape unless asked otherwise.

## Environment

- Runs on Windows only (`win32file`, `winioctlcon`, `winsound`), triggered by
  Task Scheduler on a StorSvc device-arrival event.
- Hardcoded paths throughout (`E:\DCIM\100MEDIA`, `D:\Files\photos\vered\...`)
  — this is a personal single-user script, not meant to be generalized/
  parameterized unless explicitly asked.
- External tool dependencies, all expected on `PATH`: `exiftool`, `ffmpeg`,
  `rclone`. Don't assume any of the three; check before adding calls that
  depend on a specific version's flags.

## Design decisions worth knowing before changing dedup or upload logic

**Dedup is by capture time (mtime), not filename.** The camera reuses
filenames after an SD card swap/reset (confirmed: `SUNP0288.JPG` reused
~7 months apart). `shutil.copy2` preserves mtime through every copy in the
pipeline, so mtime doubles as a stable content-identity signal. Do not
revert to filename-only comparison — it silently drops genuinely new photos
whose name collides with an old archived one.

**Canonical filenames are `YYYYMMDD_HHMMSS_<original name>`**, derived from
EXIF `DateTimeOriginal` (falling back to mtime with a logged warning if EXIF
is absent, which is the norm for this camera's AVI videos). This exists
specifically so a reused camera filename can never collide with an
unrelated file either locally (`originals`) or on the Google Photos side
(rclone's own dedup is filename-based against the album's contents, and
Google's `lsjson` returns `Size: -1` for existing library items, so rclone's
"unchanged, skipping" check is weak and trusts a filename match alone).

**AVI→MP4 conversion recovers its timestamp from the canonical filename**
(`capture_dt_from_canonical_name`), not by re-running exiftool. This keeps
the embedded video `creation_time` and the filename from ever disagreeing
if the two were computed at different points in a future refactor.

**`creation_time` is written as literal local wall-clock time, not UTC —
this is deliberate, not a bug.** The MP4 spec says `creation_time` should
be UTC, but Google Photos displays it verbatim with no timezone conversion
and no offset annotation (unlike photos, which carry an explicit EXIF
offset tag Google does honor and label, e.g. "GMT+03:00"). Confirmed by
comparing an uploaded photo and video from the same moment: converting to
UTC before writing the tag made the video display exactly `TZ`'s offset
early, with no timezone label at all. If you "fix" this back to UTC because
it looks more spec-correct, every video's displayed time will be off by
`TZ`'s offset again. If this camera or `TZ` ever changes, re-verify by
comparing a freshly uploaded photo and video from the same moment.

**Never overwrite `originals`.** Every write path checks
`dest_original.exists()` first. The permanent archive must stay append-only.

**An additional experimental tag, `Keys:CreationDate`, is written on top of
the `creation_time` fix above** — local time with an explicit baked-in
offset (e.g. `2026:09:26 09:15:58+03:00`), matching exactly what iPhone
videos carry natively (confirmed by inspecting a real `IMG_XXXX.MOV`'s
metadata) and what appears to correlate with Google Photos showing a
`GMT+03:00`-style label. This is genuinely unproven for non-first-party
(rclone/API) uploads, unlike the `creation_time` fix, which is
proven-correct. It's written deliberately as an *addition*, never a
replacement — if it fails, it's logged as a non-fatal warning and the
primary `creation_time` result is untouched. Don't remove the
`creation_time` write in favor of relying on this tag alone. To check
whether it's actually having any effect: upload a video processed by this
script, then check whether it shows a GMT label like the sample iPhone/
Samsung videos did, or just the correct time with no label like before.
Related dead ends already ruled out, so don't re-investigate: embedded GPS
coordinates (tested with/without, no correlation with the label appearing);
the Samsung-proprietary `com.samsung.android.utc.offset` tag (real key,
reverse-engineered from ExifTool's source, but not writable by any
available tool without hand-authoring raw MP4 `moov/meta/keys` boxes —
not attempted given the corruption risk and uncertain payoff).

## Known-fragile areas / things to verify before "fixing"

- **`winsound.PlaySound` failing under Task Scheduler** is not a bug to
  "properly fix" — it's an elevated/event-triggered process failing to
  reach the interactive audio session, a Windows quirk, not something the
  script can reliably work around in-process. Current handling
  (try/except around each call, log and continue) is the intended fix;
  don't spend time trying to make the sound "always work" without changing
  the Task Scheduler run level.
- **Google Photos API restrictions (effective March 31, 2025)**: an app can
  only list/search albums and media items it created itself. If the
  `vered_camera` album or the rclone `client_id` ever changes, rclone may
  silently create a *new* album with the same display name rather than
  finding the existing one — always check `rclone lsd gphoto:album` for
  duplicates before assuming an upload "went missing."
- **rclone's shared/default `client_id` is being retired during 2026.**
  This config already uses a dedicated client_id — don't revert to the
  default one.
- If extending video handling beyond `.AVI`, check whether the new
  extension's date metadata is already readable by Google Photos before
  adding a conversion step — MP4/MOV usually don't need one; older
  container formats (AVI, WMV, MTS) generally do.

## Testing notes

There's no test suite (single-user Windows automation script talking to
physical hardware, an OS-level volume API, and live cloud services — not
practical to unit test meaningfully). Verify changes by:
- `python -m py_compile camera.py` for a fast syntax check (this sandbox
  can't import `win32file`/`winsound`, so that's the ceiling of local
  verification here — real behavior needs testing on the actual Windows
  machine).
- Reasoning through the dedup/rename logic against the actual `originals`
  folder contents rather than assuming.
