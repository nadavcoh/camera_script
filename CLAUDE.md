# CLAUDE.md

Context for working on `camera.py`. This is a single-file Windows automation
script (Task Scheduler-triggered on camera insertion), not a package — keep
changes to that single-file shape unless asked otherwise.

## Environment

- Runs on Windows only (`win32file`, `winioctlcon`, `winsound`), triggered by
  Task Scheduler on a StorSvc device-arrival event.
- Hardcoded paths throughout (`H:\DCIM\100MEDIA`, `D:\media\photos\vered\...`)
  — this is a personal single-user script, not meant to be generalized/
  parameterized unless explicitly asked.
- **The camera's drive letter lives in one constant, `CAMERA_DRIVE`** (top of
  `camera.py`). It is used both to build the source path and to eject the
  volume. Previously the path (`h:`) and the eject call (`"E:"`) were separate
  literals that could silently disagree, ejecting the wrong drive. Never
  reintroduce a second literal for the drive letter.
- Notifications go through `notify()` (timeout + retries, never raises). The
  ntfy topic is read from `ntfy_topic.txt` next to `camera.py` (gitignored) —
  it acts as a shared secret, so never hardcode it or commit that file. If the
  file is missing or empty, notifications are skipped with one logged warning.
  `python camera.py --generate-topic` creates a random topic and writes that
  file (refuses to overwrite one unless `--force`).
- External tool dependencies, all expected on `PATH`: `exiftool`, `ffmpeg`,
  `rclone`. Don't assume any of the three; check before adding calls that
  depend on a specific version's flags.

## Design decisions worth knowing before changing dedup or upload logic

**Dedup is by content identity (size + capture-time mtime), not filename.**
The camera reuses filenames after an SD card swap/reset (confirmed:
`SUNP0288.JPG` reused ~7 months apart). `shutil.copy2` preserves size and
mtime through every copy in the pipeline, so `already_archived()` matches a
camera file against `originals` on (size, mtime). Do not revert to
filename-only comparison — it silently drops genuinely new photos whose name
collides with an old archived one.

**mtimes from the FAT card shift by exactly 1 hour across a DST change.**
Windows converts FAT local time to UTC using the *current* DST offset, not
the offset on the file's date. After Israel's DST ended (2026-10-25) every
summer-dated file read 1 hour late, a pure-mtime check matched nothing, and
~290 already-uploaded photos were re-imported and re-uploaded. Hence:
`already_archived()` treats an mtime difference of exactly 0 or 3600 s as the
same file (size must also match), and `fat_wall_clock()` undoes the quirk
for the AVI timestamp fallback so canonical names stay stable no matter when
the script runs. Don't compare raw mtimes without the size + 1 h tolerance.

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

**Interrupted runs resume.** Files are moved to `done` right after the rclone
run that uploaded them succeeds (per chunk, see below). Anything left in
`inbox`/`upload` by an earlier run is picked up at the start of the next one
(those files are already in `originals`, so the dedup check would never see
them again).

**GPS removal failures hold the file back; they never upload it.** If
`exiftool` exits non-zero for a JPEG (or isn't on `PATH`), that file stays in
`inbox`, is dropped from `pending` so it never reaches `upload`, an ntfy alert
is sent, and the run ends with "Finished WITH ERRORS". The next run retries it
via the resume path above. Don't change this to log-and-continue: that would
upload a photo with its location data.

**Uploads run in chunks, one rclone process per chunk** (`upload_chunk()`,
`UPLOAD_CHUNK_SIZE` = 50, `UPLOAD_TRANSFERS` = 8, files listed with
`--files-from-raw`). The old one-`rclone copy`-per-file loop took ~26 s per
photo (83 photos = 36 min). The likely cause is the Google Photos backend's
batch commit: each single-file process pays the commit/idle wait on its own,
whereas concurrent uploads inside one process share a batch
(`--gphotos-batch-size` is set equal to `--transfers` so batches fill and commit
immediately). A chunk's files move to `done` only after rclone exits 0 for the
whole chunk. If a chunk fails, its files stay in `upload` and the whole chunk is
retried next run; that is safe because rclone skips names that already exist in
the album and our canonical names are unique. This is untested against the
real Google Photos API: after the first real run, compare the upload time and
check `rclone lsd gphoto:album` for duplicates; if rclone rejects a flag it
fails loudly (non-zero exit), never silently. Tune `UPLOAD_TRANSFERS` down if
Google returns 429s.

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
