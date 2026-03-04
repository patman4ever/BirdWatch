"""
Disk Manager
Watches disk usage and removes oldest recordings when threshold is exceeded
"""

import os
import glob
import shutil
import logging
import threading

log = logging.getLogger("birdwatch.disk")

_stop_event = threading.Event()
_thread: threading.Thread = None
_recordings_path = "recordings"
_max_disk_pct = 95.0
_check_interval = 60  # seconds


def start(recordings_path: str = "recordings", max_disk_pct: float = 95.0):
    global _thread, _recordings_path, _max_disk_pct
    _recordings_path = recordings_path
    _max_disk_pct = max_disk_pct
    _stop_event.clear()
    _thread = threading.Thread(target=_watch_loop, daemon=True, name="disk_manager")
    _thread.start()
    log.info(f"Disk manager started — max {max_disk_pct}% usage")


def stop():
    _stop_event.set()


def update_max_pct(max_pct: float):
    global _max_disk_pct
    _max_disk_pct = max_pct


def get_disk_info(recordings_path: str = "recordings") -> dict:
    try:
        usage = shutil.disk_usage(recordings_path if os.path.exists(recordings_path) else ".")
        total_gb = usage.total / (1024 ** 3)
        used_gb = usage.used / (1024 ** 3)
        free_gb = usage.free / (1024 ** 3)
        pct = (usage.used / usage.total) * 100

        # Count recording files
        wav_files = glob.glob(os.path.join(recordings_path, "*.wav"))
        recordings_size = sum(os.path.getsize(f) for f in wav_files if os.path.exists(f))

        return {
            "total_gb": round(total_gb, 2),
            "used_gb": round(used_gb, 2),
            "free_gb": round(free_gb, 2),
            "used_pct": round(pct, 1),
            "recordings_count": len(wav_files),
            "recordings_size_mb": round(recordings_size / (1024 ** 2), 1),
            "max_pct": _max_disk_pct,
            "warning": pct >= _max_disk_pct * 0.9,
        }
    except Exception as e:
        log.error(f"Disk info error: {e}")
        return {"error": str(e), "used_pct": 0, "free_gb": 0}


def _watch_loop():
    while not _stop_event.is_set():
        _enforce_limit()
        _stop_event.wait(_check_interval)
    log.info("Disk manager stopped")


def _enforce_limit():
    try:
        if not os.path.exists(_recordings_path):
            return

        usage = shutil.disk_usage(_recordings_path)
        pct = (usage.used / usage.total) * 100

        if pct < _max_disk_pct:
            return

        log.warning(f"Disk usage {pct:.1f}% exceeds {_max_disk_pct}% — cleaning old recordings")

        # Get all WAV files sorted by modification time (oldest first)
        wav_files = glob.glob(os.path.join(_recordings_path, "*.wav"))
        wav_files.sort(key=os.path.getmtime)

        deleted = 0
        for filepath in wav_files:
            try:
                os.remove(filepath)
                deleted += 1
                log.info(f"Deleted old recording: {os.path.basename(filepath)}")

                # Re-check after each deletion
                usage = shutil.disk_usage(_recordings_path)
                new_pct = (usage.used / usage.total) * 100
                if new_pct < _max_disk_pct * 0.85:
                    break
            except Exception as e:
                log.warning(f"Could not delete {filepath}: {e}")

        if deleted:
            log.info(f"Deleted {deleted} old recording(s) to free disk space")

    except Exception as e:
        log.error(f"Disk enforcement error: {e}")
