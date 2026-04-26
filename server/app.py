"""
Claudiac Flask backend — v5 (adds realtime DAQ scenario).

In addition to the 10 prerecorded scenarios, this version adds an
11th "scenario" called `realtime_daq` that reads from data/live_ecg.npz
(produced by daq.py when the user presses 's').

Real-time DAQ flow:
  1. User presses 's' in daq.py            -> writes data/live_ecg.npz
  2. User clicks "Real-time DAQ" in web UI  -> POST /api/scenario/select
  3. Server reads data/live_ecg.npz, runs Pan-Tompkins at 256 Hz
  4. Server runs quality check:
       - R-peaks detected >= 5
       - HRV SDNN <= 200 ms
     If either fails -> fallback to data/scenarios/realtime_daq_cached.npz
  5. Either way, the response includes a 'data_source' field telling
     the UI whether this was 'live', 'cached_fallback', or 'cached_only'

Run:
    python server\\app.py
"""

import os
import sys
import json
import time
import threading
import numpy as np
from flask import Flask, jsonify, Response, send_from_directory, \
    stream_with_context, request
from flask_cors import CORS

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from algorithms.heart_rate import pan_tompkins
from algorithms.classifier import classify, KNOWLEDGE_TABLE


# ---------------------------------------------------------------------------
# Paths and constants
# ---------------------------------------------------------------------------
FRONTEND_DIR = os.path.join(ROOT, "frontend")
SCENARIOS_DIR = os.path.join(ROOT, "data", "scenarios")
LIVE_ECG_PATH = os.path.join(ROOT, "data", "live_ecg.npz")
CACHED_DAQ_PATH = os.path.join(SCENARIOS_DIR, "realtime_daq_cached.npz")

REALTIME_DAQ_ID = "realtime_daq"
DAQ_SAMPLING_RATE = 256.0   # Arduino + EXG Pill default per daq.py

# Quality gate
MIN_R_PEAKS = 5
MAX_HRV_SDNN_MS = 200.0


# ---------------------------------------------------------------------------
# App setup
# ---------------------------------------------------------------------------
app = Flask(__name__, static_folder=FRONTEND_DIR, static_url_path="")
CORS(app)


# ---------------------------------------------------------------------------
# Manifest (with synthetic realtime_daq entry appended)
# ---------------------------------------------------------------------------
def _load_manifest():
    with open(os.path.join(SCENARIOS_DIR, "manifest.json")) as f:
        m = json.load(f)
    # Inject a virtual scenario for the realtime DAQ button.
    has_daq = any(s["id"] == REALTIME_DAQ_ID for s in m["scenarios"])
    if not has_daq:
        m["scenarios"].append({
            "id": REALTIME_DAQ_ID,
            "label": "Real-time DAQ",
            "color": "#C0C0C0",            # silver — clearly distinct
            "color_secondary": None,
            "flashing": False,
            "expected": {"hr_bpm": "live", "hrv_ms": "live", "amplitude": "live"},
            "physiological_why": "Live ECG capture from Arduino + EXG Pill (256 Hz).",
            "file": None,                  # no fixed file; resolved at runtime
            "is_realtime": True,
        })
    return m


MANIFEST = _load_manifest()
SCENARIO_INDEX = {sc["id"]: sc for sc in MANIFEST["scenarios"]}


# ---------------------------------------------------------------------------
# Global state
# ---------------------------------------------------------------------------
_state_lock = threading.Lock()
CURRENT_STATE = {
    "scenario_id": "envy",
    "selected_at_ms": int(time.time() * 1000),
    "cached_bundle": None,
}


def _set_current_scenario(scenario_id: str):
    with _state_lock:
        CURRENT_STATE["scenario_id"] = scenario_id
        CURRENT_STATE["selected_at_ms"] = int(time.time() * 1000)
        CURRENT_STATE["cached_bundle"] = None


def _get_current_scenario_id() -> str:
    with _state_lock:
        return CURRENT_STATE["scenario_id"]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _downsample(signal: np.ndarray, target_points: int = 1500
                ) -> tuple[list, int]:
    if len(signal) <= target_points:
        return signal.tolist(), 1
    step = max(1, len(signal) // target_points)
    return signal[::step].tolist(), step


def _load_prerecorded_scenario(scenario_id: str) -> tuple[np.ndarray, float, dict]:
    """Load one of the 10 synthetic scenarios."""
    sc_meta = SCENARIO_INDEX[scenario_id]
    path = os.path.join(SCENARIOS_DIR, sc_meta["file"])
    d = np.load(path)
    return d["voltage_uV"].astype(float), float(d["sampling_rate"]), sc_meta


def _read_daq_npz(path: str) -> tuple[np.ndarray, float] | None:
    """
    Read a daq.py-style npz. Schema written by daq.py:
        ecg     : float32, normalized (zero-mean, unit-std)
        ecg_raw : float32, raw ADC values (0..1023)
        fs      : int, sampling rate
    Returns (samples_uV_like, fs) or None if file missing/bad.

    We treat the normalized 'ecg' field as our canonical signal, then
    rescale into a microvolt-like range so the Pan-Tompkins thresholds and
    amplitude bands behave like they do for the synthetic data.
    """
    if not os.path.exists(path):
        return None
    try:
        d = np.load(path)
    except Exception:
        return None

    if "ecg" in d:
        sig = np.asarray(d["ecg"], dtype=float)
    elif "ecg_raw" in d:
        # No normalized field — normalize on the fly
        raw = np.asarray(d["ecg_raw"], dtype=float)
        if raw.std() > 0:
            sig = (raw - raw.mean()) / raw.std()
        else:
            return None
    elif "voltage_uV" in d:
        # Already in our format
        return np.asarray(d["voltage_uV"], dtype=float), float(d.get("sampling_rate", DAQ_SAMPLING_RATE))
    else:
        return None

    fs = float(d["fs"]) if "fs" in d else DAQ_SAMPLING_RATE
    # Rescale normalized signal to ~ +/- 1500 uV peaks so amplitude bands
    # (which thresholds in microvolts) work consistently with synthetic data.
    sig = sig / max(np.max(np.abs(sig)), 1e-9) * 1500.0
    return sig, fs


def _quality_check(hr_result: dict) -> tuple[bool, str]:
    """
    Decide whether a captured signal is good enough to use.
    Returns (is_good, reason_if_bad).
    """
    n_peaks = len(hr_result["r_peaks"])
    hrv = hr_result["hrv_sdnn_ms"]

    if n_peaks < MIN_R_PEAKS:
        return False, (f"only {n_peaks} R-peaks detected "
                       f"(min {MIN_R_PEAKS})")
    if not np.isfinite(hrv) or hrv > MAX_HRV_SDNN_MS:
        return False, (f"HRV SDNN {hrv:.0f} ms exceeds {MAX_HRV_SDNN_MS:.0f} ms "
                       f"(noisy / unreliable RR detection)")
    return True, ""


# ---------------------------------------------------------------------------
# Bundle builder — handles all scenarios including realtime_daq
# ---------------------------------------------------------------------------
def _build_bundle(scenario_id: str) -> dict:
    """Run full pipeline and return the bundle. Cached per scenario.

    EXCEPTION: realtime_daq is NEVER cached because its underlying file
    (data/live_ecg.npz) changes whenever the user runs daq.py + presses
    's'. Caching it would mean a stale capture is served forever.
    """
    is_realtime = SCENARIO_INDEX.get(scenario_id, {}).get("is_realtime", False)
    if not is_realtime:
        with _state_lock:
            cached = CURRENT_STATE.get("cached_bundle")
            if cached and cached.get("scenario", {}).get("id") == scenario_id:
                return cached

    # ---- choose data source ----
    data_source = "synthetic"
    is_realtime = SCENARIO_INDEX.get(scenario_id, {}).get("is_realtime", False)
    fallback_reason = None

    if is_realtime:
        live = _read_daq_npz(LIVE_ECG_PATH)
        cached_data = _read_daq_npz(CACHED_DAQ_PATH)

        if live is not None:
            ecg, fs = live
            # Quick HR check to decide live vs fallback
            hr_check = pan_tompkins(ecg, fs)
            ok, why = _quality_check(hr_check)
            if ok:
                data_source = "live"
                hr_result = hr_check
            else:
                fallback_reason = why
                if cached_data is not None:
                    ecg, fs = cached_data
                    hr_result = pan_tompkins(ecg, fs)
                    data_source = "cached_fallback"
                else:
                    # No cached available — show the bad live data with a flag.
                    hr_result = hr_check
                    data_source = "live_low_quality"
        elif cached_data is not None:
            ecg, fs = cached_data
            hr_result = pan_tompkins(ecg, fs)
            data_source = "cached_only"
            fallback_reason = "no live capture (data/live_ecg.npz missing)"
        else:
            # Nothing available
            return {
                "scenario": {"id": scenario_id, "label": "Real-time DAQ",
                             "expected": {}},
                "error": ("No live capture and no cached fallback. "
                          "Run daq.py and press 's', or place a recording "
                          "at data/scenarios/realtime_daq_cached.npz"),
                "data_source": "none",
            }

        sc_meta = SCENARIO_INDEX[scenario_id]
    else:
        # Standard prerecorded scenario
        ecg, fs, sc_meta = _load_prerecorded_scenario(scenario_id)
        hr_result = pan_tompkins(ecg, fs)

    # ---- run classification ----
    peak_uv = float(np.max(np.abs(ecg)))
    classification = classify(
        hr_result["heart_rate_bpm"],
        hr_result["hrv_sdnn_ms"],
        peak_uv,
    )

    ecg_display, step = _downsample(ecg, 1500)
    r_peaks_display = (hr_result["r_peaks"] // step).tolist() \
        if step > 0 else hr_result["r_peaks"].tolist()

    # heart_attack BPM bump for drama (only on synthetic anomaly path)
    display_bpm = hr_result["heart_rate_bpm"]
    if scenario_id == "heart_attack":
        display_bpm = 165.0

    bundle = {
        "scenario": {
            "id": sc_meta["id"],
            "label": sc_meta["label"],
            "expected": sc_meta["expected"],
            "is_realtime": sc_meta.get("is_realtime", False),
        },
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
        "data_source": data_source,
    }
    if fallback_reason:
        bundle["fallback_reason"] = fallback_reason

    with _state_lock:
        if not is_realtime and CURRENT_STATE["scenario_id"] == scenario_id:
            CURRENT_STATE["cached_bundle"] = bundle
    return bundle


# ===========================================================================
# Teammate's UI endpoints
# ===========================================================================
@app.route("/api/analyze", methods=["GET"])
def api_analyze():
    scenario_id = _get_current_scenario_id()
    if scenario_id not in SCENARIO_INDEX:
        return jsonify({"heart_rate": {"bpm": None},
                        "error": f"Unknown scenario: {scenario_id}"}), 200

    bundle = _build_bundle(scenario_id)
    if "error" in bundle:
        return jsonify({"heart_rate": {"bpm": None}, **bundle}), 200

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
        "selected_at_ms": CURRENT_STATE["selected_at_ms"],
    })


@app.route("/api/waveform", methods=["GET"])
def api_waveform():
    scenario_id = _get_current_scenario_id()
    if scenario_id not in SCENARIO_INDEX:
        return jsonify({"error": f"Unknown scenario: {scenario_id}"}), 404

    bundle = _build_bundle(scenario_id)
    if "error" in bundle:
        return jsonify({"error": bundle["error"]}), 503
    ecg = bundle["ecg"]
    return jsonify({
        "samples": ecg["samples_uV"],
        "fs": ecg["sampling_rate"],
        "duration_s": ecg["duration_s"],
        "n_original_samples": ecg["n_original_samples"],
        "display_step": ecg["display_step"],
        "mtime": CURRENT_STATE["selected_at_ms"],
        "scenario_id": scenario_id,
        "data_source": bundle.get("data_source", "synthetic"),
    })


@app.route("/api/scenario/select", methods=["POST", "GET"])
def api_scenario_select():
    data = request.get_json(silent=True) or {}
    scenario_id = (data.get("scenario_id")
                   or request.args.get("scenario_id")
                   or "")
    if scenario_id not in SCENARIO_INDEX:
        return jsonify({"error": f"Unknown scenario: {scenario_id}",
                        "valid_ids": list(SCENARIO_INDEX.keys())}), 400

    _set_current_scenario(scenario_id)
    bundle = _build_bundle(scenario_id)
    if "error" in bundle:
        return jsonify({
            "ok": False,
            "scenario_id": scenario_id,
            "error": bundle["error"],
            "data_source": bundle.get("data_source", "none"),
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


# ===========================================================================
# Misc
# ===========================================================================
@app.route("/api/health", methods=["GET"])
def health():
    has_live = os.path.exists(LIVE_ECG_PATH)
    has_cached = os.path.exists(CACHED_DAQ_PATH)
    return jsonify({
        "status": "ok",
        "service": "claudiac-backend",
        "version": "v5-realtime-daq",
        "n_scenarios": len(SCENARIO_INDEX),
        "current_scenario": _get_current_scenario_id(),
        "realtime_daq": {
            "live_npz_exists": has_live,
            "cached_npz_exists": has_cached,
            "live_path": LIVE_ECG_PATH if has_live else None,
            "cached_path": CACHED_DAQ_PATH if has_cached else None,
        },
    })


@app.route("/api/scenarios", methods=["GET"])
def list_scenarios():
    return jsonify({
        "scenarios": MANIFEST["scenarios"],
        "knowledge_table": KNOWLEDGE_TABLE,
    })


@app.route("/api/classify/<scenario_id>", methods=["GET"])
def classify_scenario(scenario_id: str):
    if scenario_id not in SCENARIO_INDEX:
        return jsonify({"error": f"Unknown scenario: {scenario_id}",
                        "valid_ids": list(SCENARIO_INDEX.keys())}), 404
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
    return jsonify({
        "status": "ok",
        "message": "Claudiac backend up. Frontend not yet built.",
    })


# ---------------------------------------------------------------------------
# Entry
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    has_live = os.path.exists(LIVE_ECG_PATH)
    has_cached = os.path.exists(CACHED_DAQ_PATH)
    print("=" * 64)
    print("  Claudiac backend v5 — adds realtime DAQ scenario")
    print("=" * 64)
    print(f"  Project root      : {ROOT}")
    print(f"  Scenarios loaded  : {len(SCENARIO_INDEX)}")
    print(f"  Live ECG file     : "
          f"{'PRESENT' if has_live else 'missing'}  ({LIVE_ECG_PATH})")
    print(f"  Cached fallback   : "
          f"{'PRESENT' if has_cached else 'missing'}  ({CACHED_DAQ_PATH})")
    print()
    print("  Endpoints (key):")
    print("    GET  /api/health")
    print("    GET  /api/scenarios")
    print("    POST /api/scenario/select  body: {scenario_id}")
    print("    GET  /api/analyze")
    print("    GET  /api/waveform")
    print("    GET  /api/classify/realtime_daq")
    print()
    print("  Real-time DAQ workflow:")
    print("    1. Run `python daq.py` in another window")
    print("    2. Wear electrodes, press 's' to save 30s -> data/live_ecg.npz")
    print("    3. Click 'Real-time DAQ' button in web UI")
    print("=" * 64)
    app.run(host="0.0.0.0", port=5000, debug=False, threaded=True)
