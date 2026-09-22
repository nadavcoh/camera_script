# camera.py — Automatic Photo/Video Import & Upload

Watches a camera's memory card (mounted as a drive letter), imports anything
new into a local archive, cleans it up, uploads it to Google Photos via
`rclone`, and safely ejects the card. Designed to be fired by Task Scheduler
when the camera is plugged in.

## What it does, in order

1. **Detect new files** on the camera (`E:\DCIM\100MEDIA`) by comparing
   capture time — not filename — against everything already archived in
   `originals`. This matters because SD cards get swapped/reset and cameras
   reuse filenames (e.g. `SUNP0288.JPG`); a plain name-based diff would
   silently skip a brand-new photo that happens to share a name with an old
   one.
2. **Copy new files** into `inbox` (working copy) and `originals` (permanent
   local archive, untouched forever) under a collision-proof name:
   `YYYYMMDD_HHMMSS_<originalname>`, using the EXIF `DateTimeOriginal` when
   available, falling back to the file's own mtime.
3. **Convert `.AVI` videos to `.mp4`** (inbox copy only) with the correct
   `creation_time` embedded, because Google Photos reads a video's "date
   taken" from MP4/MOV container metadata and this camera's AVI files don't
   carry anything it recognizes — uploaded as-is, they'd all show today's
   date instead of when they were shot.
4. **Eject the camera** safely (Windows volume lock/dismount/eject).
5. **Strip GPS data** from JPEGs and set the correct UTC offset tags
   (`exiftool`), so timestamps display correctly without leaking location.
6. **Move to `upload`**, then **upload to Google Photos** via `rclone` into
   the `vered_camera` album, then **move to `done`** on success.
7. **Send a push notification** via ntfy on completion, on "nothing to do",
   and on failure.

## Folder layout

| Path | Purpose |
|---|---|
| `E:\DCIM\100MEDIA` | Camera's memory card (source, read-only in practice) |
| `D:\Files\photos\vered\originals` | Permanent local archive — never modified after write |
| `D:\Files\photos\vered\inbox` | Working copy, being processed |
| `D:\Files\photos\vered\upload` | Ready to upload |
| `D:\Files\photos\vered\done` | Successfully uploaded |

## Dependencies

- Python 3 with `pywin32`, `requests`
- [`exiftool`](https://exiftool.org/) on `PATH`
- [`ffmpeg`](https://ffmpeg.org/) on `PATH` (for AVI→MP4 conversion)
- [`rclone`](https://rclone.org/) on `PATH`, configured with a `gphoto:`
  remote pointing at a **your-own `client_id`** Google Photos OAuth app
  (the shared/default rclone client_id is being retired during 2026, and
  Google's March 2025 API changes mean an app can only list/manage albums
  and media items it created itself — the `vered_camera` album must have
  been created by this same rclone remote/client_id)
- An [ntfy.sh](https://ntfy.sh/) topic for push notifications

## Task Scheduler setup

Triggered on camera-arrival (StorSvc device event). Two things to watch:

- **Run level**: if set to "Run with highest privileges," the local
  success/failure sound (`winsound.PlaySound`) will reliably fail with
  `RuntimeError: Failed to play sound` — an elevated, event-triggered
  process often can't reach the interactive audio session even though a
  user is actively logged in. This is now caught and logged rather than
  crashing the script, but if you want the chime to actually play, try
  running at standard (non-elevated) privilege first, or drop the local
  sound entirely and rely on the ntfy push instead.
- Only elevate if the volume eject actually requires it for your hardware —
  test without it first.

## Known limitations / things to keep an eye on

- If `ffmpeg` isn't installed or isn't on `PATH`, AVI files upload
  unconverted (and will show the upload date in Google Photos rather than
  the real capture date) — the script logs this clearly instead of failing.
- Google Photos' web search is not a reliable way to verify an upload
  landed — its search is content/AI-based, not a filename grep. Check the
  album directly, or use `rclone lsjson gphoto:album/vered_camera` to
  confirm an item exists server-side.
- The video conversion step transcodes only when a lossless remux fails
  (unsupported audio codec in the MP4 container); otherwise it's a fast,
  lossless container swap.
