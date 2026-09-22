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

**Never overwrite `originals`.** Every write path checks
`dest_original.exists()` first. The permanent archive must stay append-only.

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
