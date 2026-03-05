"""
BirdNET Analyzer Worker - Docker/Coolify versie
Ondersteunt meerdere talen voor vogelnamen
"""

import os
import logging
import threading
from datetime import datetime
from typing import Callable

import recorder
import database as db
from translations import translate

log = logging.getLogger("birdwatch.analyzer")

_thread = None
_stop_event = threading.Event()
_running = False
_settings = {
    "lat": 52.0, "lon": 5.0,
    "min_confidence": 0.25, "sensitivity": 1.0,
    "locale": "nl"
}
_settings_lock = threading.Lock()
_on_detection = None
_analyzer_instance = None
_analyzer_lock = threading.Lock()


def is_running():
    return _running


def start(recordings_path="recordings", lat=52.0, lon=5.0,
          min_confidence=0.25, sensitivity=1.0, on_detection=None, locale="nl"):
    global _thread, _stop_event, _running, _on_detection
    if _running:
        return
    with _settings_lock:
        _settings.update({
            "lat": lat, "lon": lon,
            "min_confidence": min_confidence,
            "sensitivity": sensitivity,
            "locale": locale,
        })
    _on_detection = on_detection
    _stop_event.clear()
    _thread = threading.Thread(target=_analyze_loop, args=(recordings_path,),
                               daemon=True, name="analyzer")
    _thread.start()
    _running = True
    log.info(f"Analyzer started lat={lat} lon={lon} locale={locale}")


def stop():
    global _running
    _stop_event.set()
    _running = False
    log.info("Analyzer stopping...")


def update_settings(lat=None, lon=None, min_confidence=None, sensitivity=None, locale=None):
    global _analyzer_instance
    with _settings_lock:
        if lat is not None: _settings["lat"] = lat
        if lon is not None: _settings["lon"] = lon
        if min_confidence is not None: _settings["min_confidence"] = min_confidence
        if sensitivity is not None: _settings["sensitivity"] = sensitivity
        if locale is not None and locale != _settings.get("locale"):
            _settings["locale"] = locale
            # Reset analyzer so it reloads with new locale
            with _analyzer_lock:
                _analyzer_instance = None
            log.info(f"Locale changed to {locale} — reloading model")


def _get_analyzer():
    global _analyzer_instance
    with _analyzer_lock:
        if _analyzer_instance is None:
            try:
                from birdnetlib.analyzer import Analyzer
                with _settings_lock:
                    locale = _settings.get("locale", "nl")
                log.info(f"Loading BirdNET model (locale={locale})...")
                _analyzer_instance = Analyzer(locale=locale)
                log.info("BirdNET model loaded successfully")
            except TypeError:
                # Older birdnetlib without locale param
                try:
                    from birdnetlib.analyzer import Analyzer
                    _analyzer_instance = Analyzer()
                    log.info("BirdNET model loaded (locale not supported by this version)")
                except Exception as e:
                    log.error(f"Failed to load BirdNET model: {e}")
                    _analyzer_instance = "failed"
            except Exception as e:
                log.error(f"Failed to load BirdNET model: {e}")
                _analyzer_instance = "failed"
    return _analyzer_instance


def _analyze_loop(recordings_path):
    global _running

    analyzer = _get_analyzer()
    if analyzer == "failed":
        log.error("BirdNET model unavailable")
        _running = False
        return

    log.info("Analyzer loop running...")

    while not _stop_event.is_set():
        try:
            try:
                filepath = recorder.recording_queue.get(timeout=2)
            except Exception:
                continue

            if not os.path.exists(filepath):
                continue

            # Re-get analyzer in case locale changed
            analyzer = _get_analyzer()
            if analyzer == "failed":
                continue

            with _settings_lock:
                lat = _settings["lat"]
                lon = _settings["lon"]
                min_conf = _settings["min_confidence"]

            detections = _run_birdnet(analyzer, filepath, lat, lon, min_conf)

            if detections:
                now = datetime.now().isoformat()
                week = int(datetime.now().strftime("%V"))
                for det in detections:
                    det_id = db.insert_detection(
                        timestamp=now,
                        common_name=det["common_name"],
                        scientific_name=det["scientific_name"],
                        confidence=det["confidence"],
                        audio_file=filepath,
                        latitude=lat, longitude=lon, week=week,
                    )
                    det.update({"id": det_id, "timestamp": now, "audio_file": filepath})
                    if _on_detection:
                        _on_detection(det)
            else:
                _maybe_delete(filepath)

        except Exception as e:
            log.error(f"Analyzer error: {e}", exc_info=True)

    _running = False
    log.info("Analyzer stopped")


def _run_birdnet(analyzer, filepath, lat, lon, min_conf):
    try:
        from birdnetlib import Recording
        recording = Recording(
            analyzer, filepath,
            lat=lat, lon=lon,
            date=datetime.now(),
            min_conf=min_conf,
        )
        recording.analyze()
        results = []
        for d in recording.detections:
            english_name = d.get("common_name", "Unknown")
            scientific = d.get("scientific_name", "")
            with _settings_lock:
                locale = _settings.get("locale", "nl")
            results.append({
                "common_name": translate(english_name, locale, scientific),
                "scientific_name": scientific,
                "confidence": round(d.get("confidence", 0.0), 4),
                "start_time": d.get("start_time", 0),
                "end_time": d.get("end_time", 0),
            })
        return results
    except Exception as e:
        log.error(f"BirdNET analysis failed: {e}", exc_info=True)
        return []


def _maybe_delete(filepath):
    try:
        if os.path.exists(filepath):
            os.remove(filepath)
    except Exception:
        pass
