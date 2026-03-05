#!/usr/bin/env python3
"""
BirdWatch - Real-time Bird Sound Detection Dashboard
"""

import os
import json
import time
import threading
import logging
from datetime import datetime, timedelta
from flask import Flask, render_template, jsonify, request, send_file, Response, session
from flask_socketio import SocketIO, emit
import database as db
import recorder
import analyzer
import disk_manager
import birdweather

# ── Logging ───────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.FileHandler("logs/birdwatch.log"),
        logging.StreamHandler()
    ]
)
log = logging.getLogger("birdwatch")

# ── Flask App ──────────────────────────────────────────────────────────────────
app = Flask(__name__)
app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", "birdwatch-secret-2024")
app.config["ADMIN_USER"] = os.environ.get("ADMIN_USER", "admin")
app.config["ADMIN_PASSWORD"] = os.environ.get("ADMIN_PASSWORD", "birdwatch")
socketio = SocketIO(app, cors_allowed_origins="*", async_mode="threading")


# ── Auth helpers ───────────────────────────────────────────────────────────────

def is_logged_in() -> bool:
    return session.get("authenticated") is True


def require_auth():
    """Return 401 JSON if not authenticated."""
    if not is_logged_in():
        return jsonify({"error": "Unauthorized", "login_required": True}), 401
    return None


@app.route("/api/auth/login", methods=["POST"])
def api_login():
    data = request.json or {}
    user = data.get("username", "")
    pw = data.get("password", "")
    if user == app.config["ADMIN_USER"] and pw == app.config["ADMIN_PASSWORD"]:
        session["authenticated"] = True
        session.permanent = True
        return jsonify({"success": True})
    return jsonify({"success": False, "error": "Onjuiste gebruikersnaam of wachtwoord"}), 401


@app.route("/api/auth/logout", methods=["POST"])
def api_logout():
    session.clear()
    return jsonify({"success": True})


@app.route("/api/auth/status")
def api_auth_status():
    return jsonify({"authenticated": is_logged_in()})

running = False


# ═══════════════════════════════════════════════════════════════════════════════
# Routes
# ═══════════════════════════════════════════════════════════════════════════════

@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/status")
def api_status():
    settings = db.get_settings()
    disk = disk_manager.get_disk_info(settings.get("recordings_path", "recordings"))
    return jsonify({
        "running": running,
        "recorder_active": recorder.is_running(),
        "analyzer_active": analyzer.is_running(),
        "disk": disk,
        "uptime": _get_uptime(),
        "version": "1.0.0"
    })


@app.route("/api/detections/<int:detection_id>", methods=["DELETE"])
def api_delete_detection(detection_id):
    err = require_auth()
    if err: return err
    detection = db.get_detection_by_id(detection_id)
    if not detection:
        return jsonify({"success": False, "error": "Niet gevonden"}), 404
    # Verwijder ook het audiobestand als het bestaat
    audio_file = detection.get("audio_file")
    if audio_file:
        audio_path = os.path.join("recordings", audio_file)
        try:
            if os.path.exists(audio_path):
                os.remove(audio_path)
        except Exception as e:
            log.warning(f"Kon audiobestand niet verwijderen: {e}")
    deleted = db.delete_detection(detection_id)
    return jsonify({"success": deleted})


@app.route("/api/detections")
def api_detections():
    limit = int(request.args.get("limit", 50))
    offset = int(request.args.get("offset", 0))
    species = request.args.get("species", None)
    date_from = request.args.get("date_from", None)
    date_to = request.args.get("date_to", None)
    min_confidence = float(request.args.get("min_confidence", 0.0))
    rows = db.get_detections(limit=limit, offset=offset, species=species,
                             date_from=date_from, date_to=date_to,
                             min_confidence=min_confidence)
    total = db.count_detections(species=species, date_from=date_from,
                                date_to=date_to, min_confidence=min_confidence)
    return jsonify({"detections": rows, "total": total})


@app.route("/api/detections/latest")
def api_latest():
    return jsonify(db.get_detections(limit=10, offset=0))


@app.route("/api/species/top")
def api_top_species():
    days = int(request.args.get("days", 7))
    limit = int(request.args.get("limit", 10))
    return jsonify(db.get_top_species(days=days, limit=limit))


@app.route("/api/species/list")
def api_species_list():
    return jsonify(db.get_all_species())


@app.route("/api/stats/daily")
def api_daily_stats():
    days = int(request.args.get("days", 30))
    return jsonify(db.get_daily_stats(days=days))


@app.route("/api/stats/hourly")
def api_hourly_stats():
    days = int(request.args.get("days", 7))
    return jsonify(db.get_hourly_stats(days=days))


@app.route("/api/stats/summary")
def api_summary():
    return jsonify(db.get_summary_stats())


@app.route("/api/audio/<int:detection_id>")
def api_audio(detection_id):
    row = db.get_detection_by_id(detection_id)
    if not row or not row.get("audio_file"):
        return jsonify({"error": "Not found"}), 404
    audio_path = row["audio_file"]
    if not os.path.exists(audio_path):
        return jsonify({"error": "Audio file missing"}), 404
    return send_file(audio_path, mimetype="audio/wav")


@app.route("/api/audio/live-stream")
def api_audio_live_stream():
    """Stream live microphone audio als WAV via arecord."""
    err = require_auth()
    if err: return err

    settings = db.get_settings()
    sample_rate = int(settings.get("sample_rate", 44100))
    mic_index = settings.get("mic_device_index", "")

    # Bepaal ALSA device string
    if mic_index and str(mic_index).strip().isdigit():
        card = int(mic_index)
        hw_device = f"hw:{card},0"
    else:
        hw_device = "default"

    import subprocess
    import struct

    def wav_header(sample_rate=44100, channels=1, bits=16):
        """Genereer een WAV-header voor een oneindige stream (grootte 0xFFFFFFFF)."""
        num_channels = channels
        bits_per_sample = bits
        byte_rate = sample_rate * num_channels * bits_per_sample // 8
        block_align = num_channels * bits_per_sample // 8
        header = struct.pack(
            "<4sI4s4sIHHIIHH4sI",
            b"RIFF", 0xFFFFFFFF,          # ChunkSize (streaming)
            b"WAVE",
            b"fmt ", 16,                   # Subchunk1Size
            1,                             # AudioFormat PCM
            num_channels,
            sample_rate,
            byte_rate,
            block_align,
            bits_per_sample,
            b"data", 0xFFFFFFFF,          # Subchunk2Size (streaming)
        )
        return header

    def generate():
        yield wav_header(sample_rate=sample_rate)

        cmd = [
            "arecord",
            "-D", hw_device,
            "-f", "S16_LE",
            "-r", str(sample_rate),
            "-c", "1",
            "-t", "raw",       # raw PCM, geen WAV-header van arecord zelf
            "--buffer-size=4096",
        ]
        log.info(f"Live audio stream gestart: {' '.join(cmd)}")
        proc = None
        try:
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
            )
            while True:
                chunk = proc.stdout.read(4096)
                if not chunk:
                    break
                yield chunk
        except GeneratorExit:
            pass
        finally:
            if proc:
                proc.terminate()
                try:
                    proc.wait(timeout=2)
                except Exception:
                    proc.kill()
            log.info("Live audio stream gestopt")

    return Response(
        generate(),
        mimetype="audio/wav",
        headers={
            "Cache-Control": "no-cache",
            "X-Content-Type-Options": "nosniff",
        },
    )


@app.route("/api/settings", methods=["GET"])
def api_settings_get():
    err = require_auth()
    if err: return err
    return jsonify(db.get_settings())


@app.route("/api/settings", methods=["POST"])
def api_settings_post():
    err = require_auth()
    if err: return err
    data = request.json
    db.save_settings(data)
    _apply_settings(data)
    return jsonify({"success": True})


@app.route("/api/control/start", methods=["POST"])
def api_start():
    err = require_auth()
    if err: return err
    global running
    if not running:
        _start_services()
    return jsonify({"running": running})


@app.route("/api/control/stop", methods=["POST"])
def api_stop():
    err = require_auth()
    if err: return err
    global running
    if running:
        _stop_services()
    return jsonify({"running": running})


@app.route("/api/microphones")
def api_microphones():
    err = require_auth()
    if err: return err
    return jsonify(recorder.list_microphones())


@app.route("/api/bird-image")
def api_bird_image():
    """Fetch bird image URL from Wikipedia by scientific name"""
    scientific = request.args.get("scientific", "")
    common = request.args.get("common", "")
    if not scientific and not common:
        return jsonify({"url": None})
    url = _get_wikipedia_image(scientific, common)
    return jsonify({"url": url})


_image_cache = {}

def _get_wikipedia_image(scientific: str, common: str) -> str:
    key = scientific or common
    if key in _image_cache:
        return _image_cache[key]

    import urllib.request, urllib.parse, json as _json

    for query in [scientific, common]:
        if not query:
            continue
        try:
            q = urllib.parse.quote(query)
            url = f"https://en.wikipedia.org/api/rest_v1/page/summary/{q}"
            req = urllib.request.Request(url, headers={"User-Agent": "BirdWatch/1.0"})
            with urllib.request.urlopen(req, timeout=5) as r:
                data = _json.loads(r.read())
                img = data.get("thumbnail", {}).get("source")
                if img:
                    _image_cache[key] = img
                    return img
        except Exception:
            continue

    _image_cache[key] = None
    return None


@app.route("/api/birdweather/test", methods=["POST"])
def api_birdweather_test():
    err = require_auth()
    if err: return err
    data = request.json or {}
    token = data.get("token", "")
    result = birdweather.test_connection(token)
    return jsonify(result)


@app.route("/api/logs")
def api_logs():
    err = require_auth()
    if err: return err
    lines = int(request.args.get("lines", 100))
    try:
        with open("logs/birdwatch.log", "r") as f:
            return jsonify({"logs": f.readlines()[-lines:]})
    except FileNotFoundError:
        return jsonify({"logs": []})


# ═══════════════════════════════════════════════════════════════════════════════
# SocketIO
# ═══════════════════════════════════════════════════════════════════════════════

@socketio.on("connect")
def on_connect():
    log.info(f"Client connected: {request.sid}")
    emit("status", {"running": running})


@socketio.on("disconnect")
def on_disconnect():
    log.info(f"Client disconnected: {request.sid}")


def broadcast_detection(detection: dict):
    socketio.emit("detection", detection)
    log.info(f"🐦 {detection.get('common_name')} ({detection.get('confidence', 0):.1%})")
    # Stuur naar BirdWeather als ingeschakeld
    if birdweather.is_enabled():
        audio_path = None
        if detection.get("audio_file"):
            audio_path = os.path.join("recordings", detection["audio_file"])
        birdweather.submit(detection, audio_path)


def broadcast_status(status: dict):
    socketio.emit("status_update", status)


# ═══════════════════════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════════════════════

_start_time = datetime.now()


def _get_uptime():
    delta = datetime.now() - _start_time
    h, rem = divmod(int(delta.total_seconds()), 3600)
    m, s = divmod(rem, 60)
    return f"{h:02d}:{m:02d}:{s:02d}"


def _start_services():
    global running
    settings = db.get_settings()
    recordings_path = settings.get("recordings_path", "recordings")
    os.makedirs(recordings_path, exist_ok=True)

    mic_index = settings.get("mic_device_index", None)
    if isinstance(mic_index, str):
        mic_index = int(mic_index) if mic_index.strip().isdigit() else None

    recorder.start(
        recordings_path=recordings_path,
        device_index=mic_index,
        segment_seconds=int(settings.get("segment_seconds", 15)),
        sample_rate=int(settings.get("sample_rate", 44100)),
    )

    analyzer.start(
        recordings_path=recordings_path,
        lat=float(settings.get("latitude", 52.0)),
        lon=float(settings.get("longitude", 5.0)),
        min_confidence=float(settings.get("min_confidence", 0.25)),
        sensitivity=float(settings.get("sensitivity", 1.0)),
        locale=settings.get("locale", "nl"),
        on_detection=broadcast_detection,
    )

    disk_manager.start(
        recordings_path=recordings_path,
        max_disk_pct=float(settings.get("max_disk_pct", 95.0)),
    )

    running = True
    broadcast_status({"running": True})
    log.info("BirdWatch services started")


def _stop_services():
    global running
    recorder.stop()
    analyzer.stop()
    disk_manager.stop()
    running = False
    broadcast_status({"running": False})
    log.info("BirdWatch services stopped")


def _apply_settings(data: dict):
    if running:
        analyzer.update_settings(
            lat=float(data.get("latitude", 52.0)),
            lon=float(data.get("longitude", 5.0)),
            min_confidence=float(data.get("min_confidence", 0.25)),
            sensitivity=float(data.get("sensitivity", 1.0)),
            locale=data.get("locale", "nl"),
        )
        disk_manager.update_max_pct(float(data.get("max_disk_pct", 95.0)))
    # BirdWeather altijd bijwerken, ook als services niet draaien
    birdweather.configure(
        token=data.get("birdweather_token", ""),
        enabled=str(data.get("birdweather_enabled", "false")).lower() == "true",
    )


# ═══════════════════════════════════════════════════════════════════════════════
# Main
# ═══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    os.makedirs("logs", exist_ok=True)
    os.makedirs("data", exist_ok=True)
    db.init_db()

    settings = db.get_settings()

    # BirdWeather initialiseren
    birdweather.configure(
        token=settings.get("birdweather_token", ""),
        enabled=settings.get("birdweather_enabled", False),
    )

    if settings.get("auto_start", True):
        log.info("Auto-starting recording services...")
        _start_services()

    port = int(os.environ.get("PORT", 5000))
    log.info(f"BirdWatch dashboard running on http://0.0.0.0:{port}")
    socketio.run(app, host="0.0.0.0", port=port, debug=False, allow_unsafe_werkzeug=True)
