import argparse
import secrets
import shutil
import subprocess
from pathlib import Path
from datetime import datetime, timezone
import sys
import time
import win32file
import winioctlcon
import pywintypes
import winsound
import requests
from zoneinfo import ZoneInfo

TZ = ZoneInfo("Asia/Jerusalem")

def eject_drive(drive_letter: str):
    volume_path = f"\\\\.\\{drive_letter}"
    try:
        # Open the volume
        handle = win32file.CreateFile(
            volume_path,
            win32file.GENERIC_READ,
            win32file.FILE_SHARE_READ | win32file.FILE_SHARE_WRITE,
            None,
            win32file.OPEN_EXISTING,
            0,
            None
        )

        # Lock the volume
        win32file.DeviceIoControl(handle, winioctlcon.FSCTL_LOCK_VOLUME, None, 0, None)

        # Dismount the volume
        win32file.DeviceIoControl(handle, winioctlcon.FSCTL_DISMOUNT_VOLUME, None, 0, None)

        # Eject media
        win32file.DeviceIoControl(handle, winioctlcon.IOCTL_STORAGE_EJECT_MEDIA, None, 0, None)

        handle.Close()
        print(f"Drive {drive_letter} ejected.")
        try:
            winsound.PlaySound(r"C:\Windows\Media\Windows Print complete.wav", winsound.SND_FILENAME)
        except RuntimeError as e:
            log(f"Failed to play success sound (non-fatal): {e}")

        notify(f"Drive {drive_letter} ejected 😀")

    except pywintypes.error as e:
        print("Failed:", e)
        try:
            winsound.PlaySound(r"C:\Windows\Media\Windows Critical Stop.wav", winsound.SND_FILENAME)
        except RuntimeError as sound_e:
            log(f"Failed to play failure sound (non-fatal): {sound_e}")

        notify(f"Failed to eject drive {drive_letter} 😞")

def log(msg):
    """Print message with timestamp."""
    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {msg}")

NTFY_TOPIC_FILE = Path(__file__).resolve().parent / "ntfy_topic.txt"
_ntfy_warned = False

def notify(message: str, retries: int = 3) -> bool:
    """Send an ntfy push notification. Never raises and never hangs: every
    attempt has a timeout and failures are retried with a short backoff.
    The topic is read from ntfy_topic.txt next to this script (it acts as a
    shared secret, so the file is gitignored and must not live in the repo)."""
    global _ntfy_warned
    try:
        # utf-8-sig so a BOM from Windows Notepad doesn't end up in the URL
        topic = NTFY_TOPIC_FILE.read_text(encoding="utf-8-sig").strip()
    except OSError:
        topic = ""
    if not topic:
        if not _ntfy_warned:
            log(f"No ntfy topic found in {NTFY_TOPIC_FILE}; skipping ntfy notifications.")
            _ntfy_warned = True
        return False
    for attempt in range(1, retries + 1):
        try:
            resp = requests.post(
                f"https://ntfy.sh/{topic}",
                data=message.encode("utf-8"),
                headers={"Connection": "close"},
                timeout=10,
            )
            resp.raise_for_status()
            return True
        except Exception as e:
            log(f"ntfy attempt {attempt}/{retries} failed: {e}")
            if attempt < retries:
                time.sleep(2 * attempt)
    return False

def generate_ntfy_topic(force: bool = False) -> int:
    """Create a hard-to-guess ntfy topic and save it to ntfy_topic.txt.
    Refuses to replace an existing topic unless force is set, since that would
    silently orphan the subscription in the ntfy app. Returns an exit code."""
    if not force:
        try:
            existing = NTFY_TOPIC_FILE.read_text(encoding="utf-8-sig").strip()
        except OSError:
            existing = ""
        if existing:
            print(f"{NTFY_TOPIC_FILE} already contains a topic. Re-run with --force to replace it.")
            return 1
    # 18 random bytes = 144 bits of entropy; token_urlsafe only emits
    # A-Z a-z 0-9 - _, all valid in an ntfy topic name.
    topic = "camera-" + secrets.token_urlsafe(18)
    NTFY_TOPIC_FILE.write_text(topic + "\n", encoding="utf-8")
    print(f"Saved new topic to {NTFY_TOPIC_FILE}")
    print(f"Topic: {topic}")
    print(f"Subscribe in the ntfy app (or open https://ntfy.sh/{topic}) to receive notifications.")
    return 0

def fat_wall_clock(mtime: float) -> datetime:
    """Recover the true local wall-clock time from the mtime of a file on the
    camera's FAT card.

    FAT stores local time with no timezone, and Windows converts it to UTC
    using the *current* DST offset rather than the one in effect on the file's
    date. Python's fromtimestamp() then applies the file date's offset, so
    summer-dated files read 1 hour late once DST has ended (and vice versa).
    Adding back the current offset undoes exactly what Windows applied, so the
    result is stable no matter when the script runs."""
    utc = datetime.fromtimestamp(mtime, timezone.utc)
    return (utc + datetime.now(TZ).utcoffset()).replace(tzinfo=None)

def get_capture_dt(file_path: Path) -> datetime:
    """Return the file's capture time: EXIF DateTimeOriginal if available,
    otherwise fall back to the file's own mtime (logging a warning)."""
    try:
        result = subprocess.run(
            ["exiftool", "-s", "-s", "-s", "-DateTimeOriginal", str(file_path)],
            capture_output=True,
            text=True,
        )
        ts = result.stdout.strip()
        if ts:
            return datetime.strptime(ts, "%Y:%m:%d %H:%M:%S")
    except Exception as e:
        log(f"exiftool timestamp read failed for {file_path.name}: {e}")

    log(f"WARNING: no EXIF DateTimeOriginal for {file_path.name}, falling back to file mtime")
    return fat_wall_clock(file_path.stat().st_mtime)

def canonical_name(file_path: Path) -> str:
    """Prefix the filename with its capture timestamp so a reused camera
    filename (e.g. after swapping/resetting an SD card) can never collide
    with an unrelated, previously-imported photo of the same name."""
    dt = get_capture_dt(file_path)
    return f"{dt.strftime('%Y%m%d_%H%M%S')}_{file_path.name}"

def capture_dt_from_canonical_name(canonical_path: Path) -> datetime:
    """Recover the capture datetime already encoded in a canonical filename
    (e.g. '20260922_212536_SUNP0290.AVI'), so downstream steps stay
    consistent with the timestamp already baked into the filename instead
    of re-deriving it (and potentially disagreeing)."""
    date_part, time_part, _ = canonical_path.name.split("_", 2)
    return datetime.strptime(f"{date_part}_{time_part}", "%Y%m%d_%H%M%S")

camera_icon = "📷"
flash_icon = "✨"

def blink_flash():
    """Simulate a blinking camera flash."""
    sys.stdout.write(flash_icon)
    sys.stdout.flush()
    time.sleep(0.05)
    sys.stdout.write("\b ")  # clear flash
    sys.stdout.flush()
    time.sleep(0.05)
    sys.stdout.write("\b" + flash_icon)
    sys.stdout.flush()
    time.sleep(0.05)
    sys.stdout.write("\b" + camera_icon)
    sys.stdout.flush()

# NFO-style camera with flash ASCII banner
ascii_banner = r"""
         .-~~~~~~~~~-._       _.-~~~~~~~~~-.
     __.'              ~.   .~              `.__
   .'//                  \./                  \\`.
 .'//                     |                     \\`.
.'// .-~"""""""~~~~-._     |     _,-~~~~"""""""~-. \\`.
.'//.-"                 `-.  |  .-'                 "-.\\`.
.'//______.============-..   \\ | /   ..-============.______\\`.
:______________________________\\|/____________________________:;
       AUTOMATIC PHOTO IMPORT & UPLOAD
"""
parser = argparse.ArgumentParser(description="Automatic camera photo import & upload.")
parser.add_argument("--generate-topic", action="store_true",
                    help="Generate a random ntfy topic, save it to ntfy_topic.txt, and exit.")
parser.add_argument("--force", action="store_true",
                    help="With --generate-topic: replace an existing topic.")
args = parser.parse_args()
if args.generate_topic:
    sys.exit(generate_ntfy_topic(args.force))

print(ascii_banner)

# Paths
camera_path = Path(r"E:\DCIM\100MEDIA")
originals_path = Path(r"D:\Files\photos\vered\originals")
inbox_path = Path(r"D:\Files\photos\vered\inbox")
upload_path = Path(r"D:\Files\photos\vered\upload")
done_path = Path(r"D:\Files\photos\vered\done")

log("Starting photo import script...")

# Ensure directories exist
for p in [inbox_path, upload_path, done_path]:
    p.mkdir(parents=True, exist_ok=True)
    log(f"Ensured directory exists: {p}")

# Step 1 & 2: Identify files already imported (by capture time, not filename
# alone -- a swapped/reset SD card can reuse old filenames for brand new
# photos) and copy the genuinely new ones to inbox, renamed to be collision-proof.
camera_files = {f.name: f for f in camera_path.iterdir() if f.is_file()}

# Index of everything already archived: size -> list of mtimes. copy2 preserves
# both through the pipeline, so (size, mtime) is a stable content identity.
originals_index = {}
originals_count = 0
for f in originals_path.iterdir():
    if f.is_file():
        st = f.stat()
        originals_index.setdefault(st.st_size, []).append(round(st.st_mtime))
        originals_count += 1

def already_archived(fpath: Path) -> bool:
    """True if a file with the same size and capture time is already in
    originals, regardless of its name. FAT mtimes read from the card shift by
    exactly one hour across a DST change (see fat_wall_clock), while the
    archived copies keep whatever value they had when imported -- so a
    difference of 0 or exactly 3600 s counts as the same file. Requiring the
    size to match as well keeps this safe from coincidental collisions."""
    st = fpath.stat()
    mt = round(st.st_mtime)
    return any(abs(m - mt) in (0, 3600) for m in originals_index.get(st.st_size, ()))

log(f"Found {len(camera_files)} files on camera.")
log(f"Found {originals_count} files in originals.")

# Pick up anything an earlier interrupted run left behind. Those files are
# already in originals, so the dedup check above would never see them again.
resumed_inbox = sorted(f for f in inbox_path.iterdir() if f.is_file())
resumed_upload = sorted(f for f in upload_path.iterdir() if f.is_file())
if resumed_inbox or resumed_upload:
    log(f"Resuming leftovers from an earlier run: {len(resumed_inbox)} in inbox, {len(resumed_upload)} in upload.")

new_files = []

for fname, fpath in camera_files.items():
    if already_archived(fpath):
        continue

    canon = canonical_name(fpath)
    dest_inbox = inbox_path / canon
    dest_original = originals_path / canon
    if dest_original.exists():
        # Can happen for a file the (size, mtime) check missed; never silently
        # overwrite an existing original.
        log(f"WARNING: {canon} already exists in originals despite dedup check; skipping to avoid overwrite")
        continue

    shutil.copy2(fpath, dest_inbox)
    shutil.copy2(fpath, dest_original)
    new_files.append(dest_inbox)
    log(f"Copied to inbox and originals: {fname} -> {canon} {camera_icon}")

# Sefely remove camera
log("Safely removing camera...")
eject_drive("E:")

pending = resumed_inbox + new_files

if not pending and not resumed_upload:
    log("No new files to process. Exiting script.")
    notify("No new files to process. Exiting script. 😐")
else:
    total_files = len(pending)
    log(f"{total_files} new files to process.")

    # Step 3: Convert AVI videos to MP4 with the real capture time embedded.
    # Google Photos reads a video's "date taken" from the MP4/MOV creation_time
    # metadata; AVI files from this camera carry no metadata Google recognizes,
    # so uncoverted AVI uploads always get dated "today". The pristine AVI
    # stays archived untouched in `originals` -- only the inbox copy that
    # actually gets uploaded is converted.
    avi_files = [f for f in pending if f.suffix.upper() == ".AVI"]
    if avi_files:
        log(f"Converting {len(avi_files)} AVI video(s) to MP4 with correct capture-time metadata...")
        for avi_path in avi_files:
            # Google Photos displays a video's creation_time verbatim as
            # local wall-clock time -- it does NOT convert UTC to the
            # viewer's timezone the way it does for photos (which carry an
            # explicit EXIF offset tag Google honors). So, counter to the
            # MP4 spec's "creation_time is UTC" convention, we deliberately
            # write the local capture time here rather than converting it,
            # or every video ends up displayed several hours off (confirmed:
            # a 09:15 local capture showed as "06:15" with no GMT label,
            # while the UTC-converted value was exactly right -- Google just
            # never converted it back).
            dt_local = capture_dt_from_canonical_name(avi_path)
            creation_time = dt_local.strftime("%Y-%m-%dT%H:%M:%SZ")
            mp4_path = avi_path.with_suffix(".mp4")

            def run_ffmpeg(extra_args):
                return subprocess.run(
                    ["ffmpeg", "-y", "-i", str(avi_path), *extra_args,
                     "-metadata", f"creation_time={creation_time}", str(mp4_path)],
                    capture_output=True, text=True,
                )

            try:
                # Try a fast lossless remux first.
                result = run_ffmpeg(["-c", "copy"])
                if result.returncode != 0:
                    log(f"ffmpeg remux failed for {avi_path.name}, retrying with re-encode: {result.stderr.strip()[-300:]}")
                    result = run_ffmpeg(["-c:v", "libx264", "-c:a", "aac"])

                if result.returncode == 0 and mp4_path.exists():
                    avi_path.unlink()
                    pending[pending.index(avi_path)] = mp4_path
                    log(f"Converted {avi_path.name} -> {mp4_path.name} (creation_time={creation_time}) {camera_icon}")

                    # Experimental: additionally write the Apple-style
                    # Keys:CreationDate tag (local time with an explicit
                    # baked-in offset, e.g. "2026:09:26 09:15:58+03:00") --
                    # this is the one real, standard, exiftool-writable tag
                    # that iPhone videos carry and that display correctly
                    # with a GMT label in Google Photos. Unproven for
                    # non-first-party uploads, so it's purely additive: if
                    # Google ignores it, nothing changes from the working
                    # creation_time fix above; if it doesn't, we might also
                    # get the timezone label. Never let a failure here
                    # affect the (already correct) primary result.
                    offset = dt_local.replace(tzinfo=TZ).strftime("%z")
                    offset = offset[:3] + ":" + offset[3:]
                    apple_creation_date = f"{dt_local.strftime('%Y:%m:%d %H:%M:%S')}{offset}"
                    tag_result = subprocess.run(
                        ["exiftool", f"-Keys:CreationDate={apple_creation_date}",
                         "-overwrite_original", str(mp4_path)],
                        capture_output=True, text=True,
                    )
                    if tag_result.returncode != 0:
                        log(f"WARNING: could not set experimental Keys:CreationDate on {mp4_path.name} (non-fatal): {tag_result.stderr.strip()[-300:]}")
                else:
                    log(f"ffmpeg conversion failed for {avi_path.name} (exit {result.returncode}): {result.stderr.strip()[-300:]}")
                    log(f"Uploading {avi_path.name} as-is; its date in Google Photos will show the upload time.")
            except FileNotFoundError:
                log(f"ffmpeg not found on PATH; skipping conversion for {avi_path.name}. Its date in Google Photos will show the upload time.")
                break

    # Step 4: ExifTool GPS removal (verbose)
    jpg_files = [str(f) for f in pending if f.suffix.upper() == ".JPG"]
    if jpg_files:
        log("Running ExifTool to remove GPS data (verbose)...")

        for file in jpg_files:
            # Use the capture time already baked into the canonical filename
            # (derived from EXIF) instead of re-reading it, so a missing tag
            # can no longer abort the run after files were already archived.
            try:
                dt = capture_dt_from_canonical_name(Path(file))
            except ValueError:
                dt = get_capture_dt(Path(file))
            log(f"Capture time for {file}: {dt}")

            # Attach timezone (this applies correct DST automatically)
            dt_local = dt.replace(tzinfo=TZ)

            # Format offset as +HH:MM
            offset = dt_local.strftime("%z")
            offset = offset[:3] + ":" + offset[3:]

            log(f"Setting OffsetTime for {file} to {offset}")

            process = subprocess.Popen(
                [
                    "exiftool", 
                    "-v", 
                    "-gps:all=", 
                    "-overwrite_original",
                    f"-OffsetTime={offset}",
                    f"-OffsetTimeOriginal={offset}",
                    f"-OffsetTimeDigitized={offset}",
                    file,
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True
            )
            for line in process.stdout:
                print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {line}", end='')
            process.wait()
            if process.returncode != 0:
                log(f"ExifTool exited with code {process.returncode}")
            else:
                log(f"ExifTool finished processing GPS data for {file}")

    # Step 5: Move files from inbox to upload folder with blinking flash
    moved_files = list(resumed_upload)
    log("Moving files to upload folder with blinking flash:")

    for i, f in enumerate(pending, start=1):
        dest_upload = upload_path / f.name
        shutil.move(str(f), dest_upload)
        moved_files.append(dest_upload)
        blink_flash()
        if i % 50 == 0 or i == total_files:
            print()
    print()

    # Step 6: Upload files using Rclone with blinking flash. Each file moves to
    # 'done' as soon as it uploads, so an interrupted run never re-uploads files
    # that already made it, and the rest stay in 'upload' for the next run.
    uploaded_count = 0
    done_count = 0
    upload_failed = False
    if moved_files:
        log(f"Uploading {len(moved_files)} files to Google Photos via Rclone with blinking flash:")

        for f in moved_files:
            blink_flash()
            try:
                subprocess.run(
                    ["rclone", "copy", str(f), "gphoto:album/vered_camera"], check=True,
                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True
                )
            except (subprocess.CalledProcessError, FileNotFoundError) as e:
                print()
                log(f"Rclone upload failed for {f.name}: {e}")
                output = (getattr(e, "stdout", None) or "")[-500:]
                notify(f"Rclone upload failed for {f.name} 😞\n{output}")
                upload_failed = True
                break
            uploaded_count += 1
            shutil.move(str(f), done_path / f.name)
            done_count += 1
        print()
        if upload_failed:
            log("Upload interrupted due to errors; remaining files stay in 'upload' and will be retried on the next run.")
        else:
            log("Upload complete via Rclone.")

    # ASCII summary
    ascii_summary = r"""
     _____  _    _  ____   ___   ___  _  _ 
    |  __ \| |  | |/ __ \ / _ \ / _ \| || |
    | |__) | |  | | |  | | | | | | | | || |_
    |  ___/| |  | | |  | | | | | | | |__   _|
    | |    | |__| | |__| | |_| | |_| |  | |  
    |_|     \____/ \____/ \___/ \___/   |_|  
    """
    if not upload_failed:
        print(ascii_summary)
    log("==== Summary ====")
    log(f"Total files on camera: {len(camera_files)}")
    log(f"New files copied: {len(new_files)}")
    if resumed_inbox or resumed_upload:
        log(f"Resumed from an earlier run: {len(resumed_inbox) + len(resumed_upload)}")
    log(f"Files uploaded: {uploaded_count}")
    log(f"Files moved to done: {done_count}")
    if upload_failed:
        log("Finished WITH ERRORS -- see above.")
    else:
        log("All new files processed and uploaded successfully! 🎉")
        notify(f"Processed {uploaded_count} files successfully! 🎉")
