import shutil
import subprocess
from pathlib import Path
from datetime import datetime
import sys
import time
import win32file
import winioctlcon
import pywintypes
import winsound
import requests
from zoneinfo import ZoneInfo

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

        # ntfy
        try:
            requests.post("https://ntfy.sh/jowfuf-quPtid-suwza5",
                data=f"Drive {drive_letter} ejected 😀".encode(encoding='utf-8'),
                headers={"Connection": "close"})
        except Exception as e:
            log(f"Failed to send ntfy notification: {e}")

    except pywintypes.error as e:
        print("Failed:", e)
        try:
            winsound.PlaySound(r"C:\Windows\Media\Windows Critical Stop.wav", winsound.SND_FILENAME)
        except RuntimeError as sound_e:
            log(f"Failed to play failure sound (non-fatal): {sound_e}")

        try:
            requests.post("https://ntfy.sh/jowfuf-quPtid-suwza5",
                data=f"Failed to eject drive {drive_letter} 😞".encode(encoding='utf-8'),
                headers={"Connection": "close"})
        except Exception as e:
            log(f"Failed to send ntfy notification: {e}")

def log(msg):
    """Print message with timestamp."""
    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {msg}")

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

# Step 1 & 2: Compare filenames and copy missing files to inbox
camera_files = {f.name: f for f in camera_path.iterdir() if f.is_file()}
original_files = {f.name for f in originals_path.iterdir() if f.is_file()}

log(f"Found {len(camera_files)} files on camera.")
log(f"Found {len(original_files)} files in originals.")

new_files = []

for fname, fpath in camera_files.items():
    if fname not in original_files:
        dest_inbox = inbox_path / fname
        shutil.copy2(fpath, dest_inbox)
        dest_original = originals_path / fname
        shutil.copy2(fpath, dest_original)
        new_files.append(dest_inbox)
        log(f"Copied to inbox and originals: {fname} {camera_icon}")

# Sefely remove camera
log("Safely removing camera...")
eject_drive("E:")

if not new_files:
    log("No new files to process. Exiting script.")
    try:
        requests.post("https://ntfy.sh/jowfuf-quPtid-suwza5",
            data="No new files to process. Exiting script. 😐".encode(encoding='utf-8'),
            headers={"Connection": "close"})
    except Exception as e:
        log(f"Failed to send ntfy notification: {e}")
else:
    total_files = len(new_files)
    log(f"{total_files} new files to process.")

    # Step 4: ExifTool GPS removal (verbose)
    jpg_files = [str(f) for f in inbox_path.glob("*.JPG")]
    if jpg_files:
        log("Running ExifTool to remove GPS data (verbose)...")
        
        TZ = ZoneInfo("Asia/Jerusalem")

        for file in jpg_files:
            # Read DateTimeOriginal
            result = subprocess.run(
                ["exiftool", "-s", "-s", "-s", "-DateTimeOriginal", file],
                capture_output=True,
                text=True,
            )

            ts = result.stdout.strip()

            log(f"DateTimeOriginal for {file}: {ts}")

            if not ts:
                raise ValueError(f"No DateTimeOriginal found for {file}")

            # Parse EXIF timestamp
            dt = datetime.strptime(ts, "%Y:%m:%d %H:%M:%S")

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
    moved_files = []
    log("Moving files to upload folder with blinking flash:")

    for i, f in enumerate(new_files, start=1):
        dest_upload = upload_path / f.name
        shutil.move(str(f), dest_upload)
        moved_files.append(dest_upload)
        blink_flash()
        if i % 50 == 0 or i == total_files:
            print()
    print()

    # Step 6: Upload files using Rclone with blinking flash
    uploaded_count = 0
    if moved_files:
        log(f"Uploading {len(moved_files)} files to Google Photos via Rclone with blinking flash:")

        for f in moved_files:
            blink_flash()
            try:
                process = subprocess.run(
                    ["rclone", "copy", str(f), "gphoto:album/vered_camera"], check=True,
                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True
                )
                uploaded_count += 1
            except subprocess.CalledProcessError as e:
                log(f"Rclone upload failed for {f.name}: {e}")
                try:
                    requests.post("https://ntfy.sh/jowfuf-quPtid-suwza5",
                        data=f"Rclone upload failed for {f.name} 😞\n{process.stdout}".encode(encoding='utf-8'),
                        headers={"Connection": "close"})
                except Exception as e2:
                    log(f"Failed to send ntfy notification: {e2}")
                break
        if uploaded_count == len(moved_files):
            log("Upload complete via Rclone.")

                    # Step 7: Move uploaded files to 'done' folder
            done_count = 0
            for f in moved_files:
                shutil.move(str(f), done_path / f.name)
                done_count += 1
        else:
            log("Upload interrupted due to errors.")

    # ASCII summary
    ascii_summary = r"""
     _____  _    _  ____   ___   ___  _  _ 
    |  __ \| |  | |/ __ \ / _ \ / _ \| || |
    | |__) | |  | | |  | | | | | | | | || |_
    |  ___/| |  | | |  | | | | | | | |__   _|
    | |    | |__| | |__| | |_| | |_| |  | |  
    |_|     \____/ \____/ \___/ \___/   |_|  
    """
    print(ascii_summary)
    log("==== Summary ====")
    log(f"Total files on camera: {len(camera_files)}")
    log(f"New files copied: {len(new_files)}")
    log(f"Files uploaded: {uploaded_count}")
    log(f"Files moved to done: {done_count}")
    log("All new files processed and uploaded successfully! 🎉")
    try:
        requests.post("https://ntfy.sh/jowfuf-quPtid-suwza5",
            data=f"Processed {len(new_files)} new files successfully! 🎉".encode(encoding='utf-8'),
            headers={"Connection": "close"}
            )
    except Exception as e:
        log(f"Failed to send ntfy notification: {e}")