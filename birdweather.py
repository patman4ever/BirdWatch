"""
BirdWeather API integratie voor BirdWatch
Uploadt soundscapes en detecties naar app.birdweather.com
"""

import logging
import threading
import urllib.request
import urllib.error
import json
import os
from datetime import timezone

log = logging.getLogger("birdwatch.birdweather")

BIRDWEATHER_API = "https://app.birdweather.com/api/v1/stations"

_token = None
_enabled = False
_lock = threading.Lock()


def configure(token: str, enabled: bool = True):
    global _token, _enabled
    with _lock:
        _token = token.strip() if token else ""
        _enabled = enabled and bool(_token)
    if _enabled:
        log.info(f"BirdWeather integratie ingeschakeld (token: {_token[:6]}...)")
    else:
        log.info("BirdWeather integratie uitgeschakeld")


def is_enabled() -> bool:
    with _lock:
        return _enabled and bool(_token)


def submit(detection: dict, audio_path: str = None):
    """
    Stuur een detectie naar BirdWeather (async, in achtergrondthread).
    detection: dict met common_name, scientific_name, confidence, timestamp, lat, lon
    audio_path: pad naar het WAV-bestand (optioneel maar aanbevolen)
    """
    if not is_enabled():
        return
    t = threading.Thread(target=_submit_worker, args=(detection, audio_path), daemon=True)
    t.start()


def test_connection(token: str) -> dict:
    """Test of een token geldig is door de station-info op te halen."""
    token = token.strip()
    if not token:
        return {"success": False, "error": "Geen token opgegeven"}
    url = f"{BIRDWEATHER_API}/{token}"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "BirdWatch/1.0"})
        with urllib.request.urlopen(req, timeout=8) as r:
            data = json.loads(r.read())
            if data.get("success"):
                station = data.get("station", {})
                return {
                    "success": True,
                    "station_name": station.get("name", "Onbekend"),
                    "station_id": station.get("id"),
                }
            return {"success": False, "error": "Ongeldig antwoord van BirdWeather"}
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return {"success": False, "error": "Token niet gevonden — controleer je BirdWeather token"}
        return {"success": False, "error": f"HTTP fout {e.code}"}
    except Exception as e:
        return {"success": False, "error": str(e)}


# ── Interne functies ────────────────────────────────────────────────

def _submit_worker(detection: dict, audio_path: str):
    with _lock:
        token = _token
    if not token:
        return

    soundscape_id = None

    # 1. Upload audio als soundscape
    if audio_path and os.path.exists(audio_path):
        soundscape_id = _upload_soundscape(token, detection, audio_path)

    # 2. Registreer detectie
    _upload_detection(token, detection, soundscape_id)


def _upload_soundscape(token: str, detection: dict, audio_path: str) -> int | None:
    url = f"{BIRDWEATHER_API}/{token}/soundscapes"
    ts = _iso_timestamp(detection.get("timestamp"))

    # Bouw multipart form-data handmatig
    boundary = "BirdWatchBoundary42"
    body_parts = []

    # timestamp veld
    body_parts.append(
        f'--{boundary}\r\nContent-Disposition: form-data; name="timestamp"\r\n\r\n{ts}'
    )
    # mode veld
    body_parts.append(
        f'--{boundary}\r\nContent-Disposition: form-data; name="mode"\r\n\r\nlive'
    )

    body_start = ("\r\n".join(body_parts) + "\r\n").encode()

    try:
        with open(audio_path, "rb") as f:
            audio_data = f.read()
    except Exception as e:
        log.warning(f"BirdWeather: kan audio niet lezen: {e}")
        return None

    filename = os.path.basename(audio_path)
    file_header = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="soundscape"; filename="{filename}"\r\n'
        f"Content-Type: audio/wav\r\n\r\n"
    ).encode()

    body_end = f"\r\n--{boundary}--\r\n".encode()
    body = body_start + file_header + audio_data + body_end

    try:
        req = urllib.request.Request(
            url,
            data=body,
            method="POST",
            headers={
                "Content-Type": f"multipart/form-data; boundary={boundary}",
                "User-Agent": "BirdWatch/1.0",
            },
        )
        with urllib.request.urlopen(req, timeout=30) as r:
            resp = json.loads(r.read())
            if resp.get("success"):
                sid = resp.get("soundscape", {}).get("id")
                log.debug(f"BirdWeather soundscape geüpload: id={sid}")
                return sid
            log.warning(f"BirdWeather soundscape mislukt: {resp}")
    except Exception as e:
        log.warning(f"BirdWeather soundscape upload fout: {e}")
    return None


def _upload_detection(token: str, detection: dict, soundscape_id: int | None):
    url = f"{BIRDWEATHER_API}/{token}/detections"
    ts = _iso_timestamp(detection.get("timestamp"))

    payload = {
        "timestamp": ts,
        "commonName": detection.get("english_name") or detection.get("common_name", ""),
        "scientificName": detection.get("scientific_name", ""),
        "confidence": round(float(detection.get("confidence", 0)), 4),
    }
    if detection.get("lat"):
        payload["lat"] = float(detection["lat"])
    if detection.get("lon"):
        payload["lon"] = float(detection["lon"])
    if soundscape_id:
        payload["soundscapeId"] = soundscape_id
        payload["soundscapeStartTime"] = 0
        payload["soundscapeEndTime"] = float(detection.get("clip_duration", 15))

    try:
        body = json.dumps(payload).encode()
        req = urllib.request.Request(
            url,
            data=body,
            method="POST",
            headers={
                "Content-Type": "application/json",
                "User-Agent": "BirdWatch/1.0",
            },
        )
        with urllib.request.urlopen(req, timeout=15) as r:
            resp = json.loads(r.read())
            if resp.get("success"):
                log.info(
                    f"BirdWeather ✓ {payload['commonName']} "
                    f"({payload['scientificName']}) "
                    f"conf={payload['confidence']:.0%}"
                )
            else:
                log.warning(f"BirdWeather detectie mislukt: {resp}")
    except Exception as e:
        log.warning(f"BirdWeather detectie upload fout: {e}")


def _iso_timestamp(ts) -> str:
    """Converteer timestamp naar ISO8601 formaat."""
    if not ts:
        from datetime import datetime
        return datetime.now(timezone.utc).isoformat()
    if isinstance(ts, str):
        # Zorg dat het een tijdzone heeft
        if ts.endswith("Z") or "+" in ts:
            return ts
        return ts + "Z"
    # datetime object
    try:
        from datetime import datetime
        if isinstance(ts, (int, float)):
            return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()
        if hasattr(ts, "isoformat"):
            return ts.isoformat()
    except Exception:
        pass
    from datetime import datetime
    return datetime.now(timezone.utc).isoformat()
