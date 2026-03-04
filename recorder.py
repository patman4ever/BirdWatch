"""
Audio Recorder Module
Continuously records from USB microphone in configurable segments
"""

import os
import wave
import queue
import struct
import logging
import threading
from datetime import datetime

log = logging.getLogger("birdwatch.recorder")

# ── State ─────────────────────────────────────────────────────────────────────
_thread: threading.Thread = None
_stop_event = threading.Event()
_running = False

# Shared queue: analyzer picks up completed .wav file paths
recording_queue: queue.Queue = queue.Queue(maxsize=50)


def is_running() -> bool:
    return _running


def list_microphones() -> list:
    """Return list of available audio input devices"""
    try:
        import pyaudio
        pa = pyaudio.PyAudio()
        devices = []
        for i in range(pa.get_device_count()):
            info = pa.get_device_info_by_index(i)
            if info.get("maxInputChannels", 0) > 0:
                devices.append({
                    "index": i,
                    "name": info["name"],
                    "channels": info["maxInputChannels"],
                    "sample_rate": int(info["defaultSampleRate"]),
                })
        pa.terminate()
        return devices
    except Exception as e:
        log.warning(f"Cannot list microphones: {e}")
        return []


def start(
    recordings_path: str = "recordings",
    device_index=None,
    segment_seconds: int = 15,
    sample_rate: int = 48000,
):
    global _thread, _stop_event, _running
    if _running:
        return

    _stop_event.clear()
    _thread = threading.Thread(
        target=_record_loop,
        args=(recordings_path, device_index, segment_seconds, sample_rate),
        daemon=True,
        name="recorder"
    )
    _thread.start()
    _running = True
    log.info(f"Recorder started — device={device_index}, {segment_seconds}s segments @ {sample_rate}Hz")


def stop():
    global _running
    _stop_event.set()
    _running = False
    log.info("Recorder stopping...")


# ═══════════════════════════════════════════════════════════════════════════════
# Recording loop
# ═══════════════════════════════════════════════════════════════════════════════

def _record_loop(
    recordings_path: str,
    device_index,
    segment_seconds: int,
    sample_rate: int,
):
    global _running
    try:
        import pyaudio
    except ImportError:
        log.error("pyaudio not installed. Run: pip install pyaudio")
        _running = False
        return

    channels = 1
    chunk = 1024
    fmt = pyaudio.paInt16
    frames_per_segment = int(sample_rate * segment_seconds)

    pa = pyaudio.PyAudio()

    # Find device
    if device_index is None:
        device_index = _find_usb_mic(pa)

    log.info(f"Using audio device index: {device_index}")

    while not _stop_event.is_set():
        try:
            stream = pa.open(
                format=fmt,
                channels=channels,
                rate=sample_rate,
                input=True,
                input_device_index=device_index,
                frames_per_buffer=chunk,
            )

            log.info("Audio stream opened — recording...")

            while not _stop_event.is_set():
                frames = []
                frames_recorded = 0

                # Record one segment
                while frames_recorded < frames_per_segment and not _stop_event.is_set():
                    try:
                        data = stream.read(chunk, exception_on_overflow=False)
                        frames.append(data)
                        frames_recorded += chunk
                    except Exception as e:
                        log.warning(f"Read error: {e}")
                        break

                if frames and not _stop_event.is_set():
                    filepath = _save_segment(recordings_path, frames, sample_rate, channels, fmt)
                    if filepath:
                        try:
                            recording_queue.put_nowait(filepath)
                        except queue.Full:
                            log.warning("Recording queue full — dropping segment")

            stream.stop_stream()
            stream.close()

        except Exception as e:
            log.error(f"Recorder error: {e}")
            if not _stop_event.is_set():
                log.info("Retrying in 5 seconds...")
                _stop_event.wait(5)

    pa.terminate()
    _running = False
    log.info("Recorder stopped")


def _save_segment(recordings_path: str, frames: list, sample_rate: int, channels: int, fmt) -> str:
    """Save frames to a WAV file, returns path"""
    try:
        import pyaudio
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"segment_{ts}.wav"
        filepath = os.path.join(recordings_path, filename)

        with wave.open(filepath, "wb") as wf:
            wf.setnchannels(channels)
            wf.setsampwidth(pyaudio.get_sample_size(fmt))
            wf.setframerate(sample_rate)
            wf.writeframes(b"".join(frames))

        return filepath
    except Exception as e:
        log.error(f"Failed to save segment: {e}")
        return None


def _find_usb_mic(pa) -> int:
    """Auto-detect USB microphone, fallback to default"""
    try:
        for i in range(pa.get_device_count()):
            info = pa.get_device_info_by_index(i)
            if info.get("maxInputChannels", 0) > 0:
                name = info.get("name", "").lower()
                if "usb" in name or "microphone" in name or "mic" in name:
                    log.info(f"Auto-detected USB mic: {info['name']} (index {i})")
                    return i
    except Exception:
        pass

    # Fallback to default input
    try:
        default = pa.get_default_input_device_info()
        log.info(f"Using default input: {default['name']}")
        return default["index"]
    except Exception:
        return None
