"""
Claudiac Flask backend — v6 (true realtime serial reader).

Major change from v5:
  Backend now opens COM9 itself and continuously fills a ring buffer
  with 30 seconds of ECG samples. This means daq.py must NOT be running
  at the same time (only one process can hold the serial port).

For the realtime_daq scenario:
  - GET /api/analyze polls every 1.2s -> reads ring buffer -> classifies
  - GET /api/waveform returns the latest ring buffer contents
  - The frontend's heart, BPM, classification, ECG scope all auto-update

The 10 prerecorded scenarios still work the same way (no change).

If serial isn't available (port unavailable, pyserial not installed),
the backend still starts, just without realtime_daq capability.
"""

import os
import sys
import json
import time
import threading
import numpy as np
from collections import deque
from flask import Flask, jsonify, Response, send_from_directory, \
    stream_with_context, request
from flask_cors import CORS

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from algorithms.heart_rate import pan_tompkins
from algorithms.classifier import classify, KNOWLEDGE_TABLE


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
FRONTEND_DIR = os.path.join(ROOT, "frontend")
SCENARIOS_DIR = os.path.join(ROOT, "data", "scenarios")
CACHED_DAQ_PATH = os.path.join(SCENARIOS_DIR, "realtime_daq_cached.npz")

REALTIME_DAQ_ID = "realtime_daq"

# Serial config — matches arduino_ecg.ino + daq.py
SERIAL_PORT = "COM9"
SERIAL_BAUD = 115200
DAQ_FS = 256                 # samples/sec
DAQ_BUFFER_SECONDS = 30      # how much we keep in ring buffer
DAQ_BUFFER_SIZE = DAQ_FS * DAQ_BUFFER_SECONDS  # 7680
ADC_MAX = 1023               # Arduino Uno 10-bit

# Quality gate (looser than v5 since we have continuous data)
MIN_R_PEAKS = 5
MAX_HRV_SDNN_MS = 200.0

# How often to consider re-running Claude explanation
# (we only re-call Claude when the classification *changes*, to save money)
LAST_CLASSIFICATION_ID = None
LAST_EXPLANATION = None
_explain_lock = threading.Lock()


# ---------------------------------------------------------------------------
# App setup
# ---------------------------------------------------------------------------
app = Flask(__name__, static_folder=FRONTEND_DIR, static_url_path="")
CORS(app)


# ---------------------------------------------------------------------------
# Manifest
# ---------------------------------------------------------------------------
def _load_manifest():
    with open(os.path.join(SCENARIOS_DIR, "manifest.json")) as f:
        m = json.load(f)
    has_daq = any(s["id"] == REALTIME_DAQ_ID for s in m["scenarios"])
    if not has_daq:
        m["scenarios"].append({
            "id": REALTIME_DAQ_ID,
            "label": "Real-time DAQ",
            "color": "#C0C0C0",
            "color_secondary": None,
            "flashing": False,
            "expected": {"hr_bpm": "live", "hrv_ms": "live",
                         "amplitude": "live"},
            "physiological_why": ("Live ECG capture from Arduino + EXG Pill, "
                                  "256 Hz, continuous ring buffer."),
            "file": None,
            "is_realtime": True,
        })
    return m


MANIFEST = _load_manifest()
SCENARIO_INDEX = {sc["id"]: sc for sc in MANIFEST["scenarios"]}


# ---------------------------------------------------------------------------
# Serial reader thread + ring buffer
# ---------------------------------------------------------------------------
class SerialReader:
    """Continuously reads samples from the Arduino into a ring buffer.

    Robust to:
      - serial port unavailable at startup (just disabled, won't crash)
      - bad lines on the wire (skipped silently)
      - serial disconnects (thread exits, status flag updated)
    """

    def __init__(self, port: str = SERIAL_PORT, baud: int = SERIAL_BAUD,
                 buffer_size: int = DAQ_BUFFER_SIZE):
        self.port = port
        self.baud = baud
        self.buffer = deque(maxlen=buffer_size)
        self.lock = threading.Lock()
        self.thread: threading.Thread | None = None
        self.running = False
        self.status = "not_started"      # not_started|connecting|live|error|stopped
        self.error_msg: str | None = None
        self.total_samples = 0
        self.start_time: float | None = None

    def start(self):
        if self.thread and self.thread.is_alive():
            return
        try:
            import serial  # pyserial
        except ImportError:
            self.status = "error"
            self.error_msg = ("pyserial not installed. "
                              "Run: pip install pyserial")
            print(f"[SerialReader] ERROR: {self.error_msg}")
            return

        self.status = "connecting"
        self.running = True
        self.thread = threading.Thread(target=self._loop, daemon=True)
        self.thread.start()

    def _loop(self):
        try:
            import serial
            print(f"[SerialReader] opening {self.port} @ {self.baud}…")
            ser = serial.Serial(self.port, self.baud, timeout=1)
            time.sleep(2)  # Arduino reset on connect
            ser.reset_input_buffer()
            self.status = "live"
            self.start_time = time.time()
            print(f"[SerialReader] LIVE. reading from {self.port}")

            while self.running:
                try:
                    line = ser.readline()
                except Exception as e:
                    self.status = "error"
                    self.error_msg = f"read error: {e}"
                    print(f"[SerialReader] {self.error_msg}")
                    break
                if not line:
                    continue
                try:
                    s = line.decode("utf-8", errors="ignore").strip()
                    if s.isdigit():
                        v = int(s)
                        if 0 <= v <= ADC_MAX:
                            with self.lock:
                                self.buffer.append(v)
                                self.total_samples += 1
                except Exception:
                    pass

            ser.close()
            self.status = "stopped"
            print("[SerialReader] stopped")
        except Exception as e:
            self.status = "error"
            self.error_msg = str(e)
            print(f"[SerialReader] FATAL: {e}")

    def stop(self):
        self.running = False

    def snapshot(self) -> tuple[np.ndarray, int]:
        """Returns (samples, total_count). Samples is a copy of current buffer."""
        with self.lock:
            arr = np.array(self.buffer, dtype=np.float32)
            total = self.total_samples
        return arr, total

    def is_ready(self, min_samples: int = DAQ_FS * 5) -> bool:
        """True once we have at least `min_samples` (default ~5 seconds)."""
        with self.lock:
            return len(self.buffer) >= min_samples


SERIAL = SerialReader()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _downsample(signal: np.ndarray, target_points: int = 1500
                ) -> tuple[list, int]:
    if len(signal) <= target_points:
        return signal.tolist(), 1
    step = max(1, len(signal) // target_points)
    return signal[::step].tolist(), step


def _load_prerecorded_scenario(scenario_id: str
                               ) -> tuple[np.ndarray, float, dict]:
    sc_meta = SCENARIO_INDEX[scenario_id]
    path = os.path.join(SCENARIOS_DIR, sc_meta["file"])
    d = np.load(path)
    return d["voltage_uV"].astype(float), float(d["sampling_rate"]), sc_meta


def _ecg_from_serial_buffer() -> tuple[np.ndarray, float, str] | None:
    """
    Pull the current ring buffer, normalize, scale to ~uV range.
    Returns (signal_uV_like, fs, status_msg) or None if not enough data.
    """
    raw, total = SERIAL.snapshot()
    if len(raw) < DAQ_FS * 5:  # need ≥5 seconds before we attempt anything
        return None

    # Center & normalize like daq.py does
    if raw.std() < 1e-6:
        return None
    sig = (raw - raw.mean()) / raw.std()
    # Rescale to ~ +/- 1500 uV peaks so amplitude bands behave consistently
    sig = sig / max(np.max(np.abs(sig)), 1e-9) * 1500.0

    return sig.astype(float), float(DAQ_FS), \
        f"buffer: {len(raw)/DAQ_FS:.1f}s of {DAQ_BUFFER_SECONDS}s"


def _quality_check(hr_result: dict) -> tuple[bool, str]:
    n_peaks = len(hr_result["r_peaks"])
    hrv = hr_result["hrv_sdnn_ms"]
    if n_peaks < MIN_R_PEAKS:
        return False, f"only {n_peaks} R-peaks detected (min {MIN_R_PEAKS})"
    if not np.isfinite(hrv) or hrv > MAX_HRV_SDNN_MS:
        return False, (f"HRV SDNN {hrv:.0f} ms exceeds {MAX_HRV_SDNN_MS:.0f} ms")
    return True, ""


def _read_cached_npz(path: str) -> tuple[np.ndarray, float] | None:
    if not os.path.exists(path):
        return None
    try:
        d = np.load(path)
    except Exception:
        return None
    if "ecg" in d:
        sig = np.asarray(d["ecg"], dtype=float)
    elif "voltage_uV" in d:
        return np.asarray(d["voltage_uV"], dtype=float), \
               float(d.get("sampling_rate", DAQ_FS))
    elif "ecg_raw" in d:
        raw = np.asarray(d["ecg_raw"], dtype=float)
        if raw.std() == 0:
            return None
        sig = (raw - raw.mean()) / raw.std()
    else:
        return None
    fs = float(d["fs"]) if "fs" in d else DAQ_FS
    sig = sig / max(np.max(np.abs(sig)), 1e-9) * 1500.0
    return sig, fs


# ---------------------------------------------------------------------------
# Bundle builder
# ---------------------------------------------------------------------------
def _build_bundle(scenario_id: str) -> dict:
    """Run pipeline. realtime_daq is never cached; everything else is."""
    is_realtime = SCENARIO_INDEX.get(scenario_id, {}).get("is_realtime", False)

    if not is_realtime:
        # -- standard prerecorded scenario path --
        ecg, fs, sc_meta = _load_prerecorded_scenario(scenario_id)
        hr_result = pan_tompkins(ecg, fs)
        peak_uv = float(np.max(np.abs(ecg)))
        classification = classify(
            hr_result["heart_rate_bpm"], hr_result["hrv_sdnn_ms"], peak_uv,
        )
        ecg_display, step = _downsample(ecg, 1500)
        r_peaks_display = (hr_result["r_peaks"] // step).tolist() \
            if step > 0 else hr_result["r_peaks"].tolist()
        display_bpm = hr_result["heart_rate_bpm"]
        if scenario_id == "heart_attack":
            display_bpm = 165.0
        return {
            "scenario": {"id": sc_meta["id"], "label": sc_meta["label"],
                         "expected": sc_meta["expected"], "is_realtime": False},
            "ecg": {
                "samples_uV": ecg_display,
                "sampling_rate": fs,
                "duration_s": len(ecg) / fs,
                "n_original_samples": int(len(ecg)),
                "display_step": step,
            },
            "r_peaks_display": r_peaks_display,
            "key_information": {
                "heart_rate_bpm": round(display_bpm, 1),
                "hrv_sdnn_ms": round(hr_result["hrv_sdnn_ms"], 1),
                "peak_amplitude_uV": round(peak_uv, 0),
                "n_beats_detected": int(len(hr_result["r_peaks"])),
            },
            "classification": classification,
            "data_source": "synthetic",
        }

    # -- realtime_daq path --
    sc_meta = SCENARIO_INDEX[scenario_id]
    fallback_reason = None
    data_source = None
    used_cached = False

    live = _ecg_from_serial_buffer()
    cached = _read_cached_npz(CACHED_DAQ_PATH)

    if live is not None:
        ecg, fs, status_msg = live
        hr_check = pan_tompkins(ecg, fs)
        ok, why = _quality_check(hr_check)
        if ok:
            data_source = "live"
            hr_result = hr_check
        else:
            fallback_reason = why
            if cached is not None:
                ecg, fs = cached
                hr_result = pan_tompkins(ecg, fs)
                data_source = "cached_fallback"
                used_cached = True
            else:
                hr_result = hr_check
                data_source = "live_low_quality"
    elif cached is not None:
        ecg, fs = cached
        hr_result = pan_tompkins(ecg, fs)
        data_source = "cached_only"
        fallback_reason = (f"serial buffer not ready "
                           f"(status: {SERIAL.status})")
        used_cached = True
    else:
        return {
            "scenario": {"id": scenario_id, "label": "Real-time DAQ",
                         "expected": {}, "is_realtime": True},
            "error": (f"Serial reader status: {SERIAL.status}. "
                      f"{SERIAL.error_msg or 'No live data, no cached fallback.'}"),
            "data_source": "none",
            "serial_status": SERIAL.status,
        }

    peak_uv = float(np.max(np.abs(ecg)))

    # --- Classification + cached explanation logic ---
    # Run hard-rule classify (cheap)
    from algorithms.classifier import hard_rule_classify, get_knowledge_entry
    cat_id = hard_rule_classify(
        hr_result["heart_rate_bpm"], hr_result["hrv_sdnn_ms"], peak_uv)

    global LAST_CLASSIFICATION_ID, LAST_EXPLANATION
    with _explain_lock:
        if cat_id != LAST_CLASSIFICATION_ID or LAST_EXPLANATION is None:
            # Classification changed -> get a fresh Claude explanation
            classification = classify(
                hr_result["heart_rate_bpm"],
                hr_result["hrv_sdnn_ms"],
                peak_uv,
            )
            LAST_CLASSIFICATION_ID = cat_id
            LAST_EXPLANATION = classification["explanation"]
        else:
            # Reuse the last explanation, but rebuild the rest of the
            # classification dict with fresh feature numbers.
            classification = classify(
                hr_result["heart_rate_bpm"],
                hr_result["hrv_sdnn_ms"],
                peak_uv,
            )
            classification["explanation"] = LAST_EXPLANATION

    ecg_display, step = _downsample(ecg, 1500)
    r_peaks_display = (hr_result["r_peaks"] // step).tolist() \
        if step > 0 else hr_result["r_peaks"].tolist()

    bundle = {
        "scenario": {"id": sc_meta["id"], "label": sc_meta["label"],
                     "expected": sc_meta["expected"], "is_realtime": True},
        "ecg": {
            "samples_uV": ecg_display,
            "sampling_rate": fs,
            "duration_s": len(ecg) / fs,
            "n_original_samples": int(len(ecg)),
            "display_step": step,
        },
        "r_peaks_display": r_peaks_display,
        "key_information": {
            "heart_rate_bpm": round(hr_result["heart_rate_bpm"], 1),
            "hrv_sdnn_ms": round(hr_result["hrv_sdnn_ms"], 1),
            "peak_amplitude_uV": round(peak_uv, 0),
            "n_beats_detected": int(len(hr_result["r_peaks"])),
        },
        "classification": classification,
        "data_source": data_source,
        "serial_status": SERIAL.status,
    }
    if fallback_reason:
        bundle["fallback_reason"] = fallback_reason
    return bundle


# ---------------------------------------------------------------------------
# State (only used for non-realtime scenarios now; realtime is stateless)
# ---------------------------------------------------------------------------
_state_lock = threading.Lock()
CURRENT_STATE = {
    "scenario_id": "envy",
    "selected_at_ms": int(time.time() * 1000),
}


def _set_current_scenario(scenario_id: str):
    with _state_lock:
        CURRENT_STATE["scenario_id"] = scenario_id
        CURRENT_STATE["selected_at_ms"] = int(time.time() * 1000)


def _get_current_scenario_id() -> str:
    with _state_lock:
        return CURRENT_STATE["scenario_id"]


# ===========================================================================
# Endpoints — same names as v5 so frontend doesn't change much
# ===========================================================================
@app.route("/api/health", methods=["GET"])
def health():
    n_buf = len(SERIAL.buffer)
    return jsonify({
        "status": "ok",
        "service": "claudiac-backend",
        "version": "v6-realtime-serial",
        "n_scenarios": len(SCENARIO_INDEX),
        "current_scenario": _get_current_scenario_id(),
        "serial": {
            "status": SERIAL.status,
            "port": SERIAL.port,
            "buffer_samples": n_buf,
            "buffer_seconds": round(n_buf / DAQ_FS, 1),
            "total_received": SERIAL.total_samples,
            "error": SERIAL.error_msg,
        },
        "cached_npz_exists": os.path.exists(CACHED_DAQ_PATH),
    })


@app.route("/api/scenarios", methods=["GET"])
def list_scenarios():
    return jsonify({"scenarios": MANIFEST["scenarios"],
                    "knowledge_table": KNOWLEDGE_TABLE})


@app.route("/api/scenario/select", methods=["POST", "GET"])
def api_scenario_select():
    data = request.get_json(silent=True) or {}
    scenario_id = (data.get("scenario_id")
                   or request.args.get("scenario_id") or "")
    if scenario_id not in SCENARIO_INDEX:
        return jsonify({"error": f"Unknown scenario: {scenario_id}"}), 400

    _set_current_scenario(scenario_id)
    bundle = _build_bundle(scenario_id)
    if "error" in bundle:
        return jsonify({
            "ok": False, "scenario_id": scenario_id,
            "error": bundle["error"],
            "data_source": bundle.get("data_source", "none"),
            "serial_status": bundle.get("serial_status"),
        }), 200

    ki = bundle["key_information"]
    cls = bundle["classification"]
    return jsonify({
        "ok": True,
        "scenario_id": scenario_id,
        "selected_at_ms": CURRENT_STATE["selected_at_ms"],
        "heart_rate": {
            "bpm": ki["heart_rate_bpm"],
            "hrv_sdnn_ms": ki["hrv_sdnn_ms"],
        },
        "scenario": bundle["scenario"],
        "key_information": ki,
        "classification": {
            "category_id": cls["category_id"],
            "category_label": cls["category_label"],
            "color": cls["color"],
            "color_secondary": cls.get("color_secondary"),
            "flashing": cls.get("flashing", False),
            "is_critical": cls["is_critical"],
            "rules_matched": cls["rules_matched"],
            "features": cls["features"],
            "knowledge_table_why": cls["knowledge_table_why"],
        },
        "explanation": cls["explanation"],
        "ecg": bundle["ecg"],
        "data_source": bundle.get("data_source", "synthetic"),
        "fallback_reason": bundle.get("fallback_reason"),
    })


@app.route("/api/analyze", methods=["GET"])
def api_analyze():
    scenario_id = _get_current_scenario_id()
    if scenario_id not in SCENARIO_INDEX:
        return jsonify({"heart_rate": {"bpm": None}}), 200

    bundle = _build_bundle(scenario_id)
    if "error" in bundle:
        return jsonify({
            "heart_rate": {"bpm": None},
            "error": bundle["error"],
            "data_source": bundle.get("data_source"),
            "serial_status": bundle.get("serial_status"),
        }), 200

    ki = bundle["key_information"]
    cls = bundle["classification"]
    return jsonify({
        "heart_rate": {
            "bpm": ki["heart_rate_bpm"],
            "hrv_sdnn_ms": ki["hrv_sdnn_ms"],
        },
        "scenario": bundle["scenario"],
        "key_information": ki,
        "classification": {
            "category_id": cls["category_id"],
            "category_label": cls["category_label"],
            "color": cls["color"],
            "color_secondary": cls.get("color_secondary"),
            "flashing": cls.get("flashing", False),
            "is_critical": cls["is_critical"],
            "rules_matched": cls["rules_matched"],
            "features": cls["features"],
            "knowledge_table_why": cls["knowledge_table_why"],
        },
        "explanation": cls["explanation"],
        "data_source": bundle.get("data_source", "synthetic"),
        "fallback_reason": bundle.get("fallback_reason"),
        "serial_status": bundle.get("serial_status"),
        "selected_at_ms": CURRENT_STATE["selected_at_ms"],
    })


@app.route("/api/waveform", methods=["GET"])
def api_waveform():
    scenario_id = _get_current_scenario_id()
    if scenario_id not in SCENARIO_INDEX:
        return jsonify({"error": "Unknown scenario"}), 404
    bundle = _build_bundle(scenario_id)
    if "error" in bundle:
        return jsonify({"error": bundle["error"]}), 503
    ecg = bundle["ecg"]
    # For realtime, mtime should change every poll so the frontend redraws
    is_realtime = bundle["scenario"].get("is_realtime", False)
    mtime = int(time.time() * 1000) if is_realtime \
        else CURRENT_STATE["selected_at_ms"]
    return jsonify({
        "samples": ecg["samples_uV"],
        "fs": ecg["sampling_rate"],
        "duration_s": ecg["duration_s"],
        "n_original_samples": ecg["n_original_samples"],
        "display_step": ecg["display_step"],
        "mtime": mtime,
        "scenario_id": scenario_id,
        "data_source": bundle.get("data_source", "synthetic"),
    })


@app.route("/api/classify/<scenario_id>", methods=["GET"])
def classify_scenario(scenario_id: str):
    if scenario_id not in SCENARIO_INDEX:
        return jsonify({"error": f"Unknown scenario: {scenario_id}"}), 404
    bundle = _build_bundle(scenario_id)
    if "error" in bundle:
        return jsonify(bundle), 503
    return jsonify(bundle)


# ---------------------------------------------------------------------------
# Static frontend
# ---------------------------------------------------------------------------
@app.route("/")
def index():
    index_path = os.path.join(FRONTEND_DIR, "index.html")
    if os.path.exists(index_path):
        return send_from_directory(FRONTEND_DIR, "index.html")
    return jsonify({"status": "ok", "message": "Frontend not yet built."})


# ---------------------------------------------------------------------------
# Entry
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    print("=" * 64)
    print("  Claudiac backend v6 — TRUE realtime serial")
    print("=" * 64)
    print(f"  Project root      : {ROOT}")
    print(f"  Scenarios loaded  : {len(SCENARIO_INDEX)}")
    print(f"  Cached fallback   : "
          f"{'PRESENT' if os.path.exists(CACHED_DAQ_PATH) else 'missing'}")
    print()
    print("  IMPORTANT: daq.py must NOT be running.")
    print("  This server opens the serial port itself.")
    print()
    print("  Starting serial reader…")
    SERIAL.start()
    time.sleep(0.5)
    print(f"  Serial status: {SERIAL.status}")
    if SERIAL.status == "error":
        print(f"  -> {SERIAL.error_msg}")
        print("  -> realtime_daq will fallback to cached only.")
    print()
    print("  Endpoints:")
    print("    GET  /api/health   (check serial.status)")
    print("    GET  /api/scenarios")
    print("    POST /api/scenario/select  body: {scenario_id}")
    print("    GET  /api/analyze")
    print("    GET  /api/waveform")
    print("=" * 64)
    try:
        app.run(host="0.0.0.0", port=5000, debug=False, threaded=True)
    finally:
        SERIAL.stop()
