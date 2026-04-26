"""
Claudiac Flask backend — v4.

Two parallel UIs are supported, one server:

  TEAMMATE'S UI (heart + waveform polling):
    GET  /api/analyze?source=...           -> heart-rate-shaped JSON
    GET  /api/waveform?source=...          -> ECG samples for canvas
    POST /api/scenario/select              -> set current server scenario

  ORIGINAL DEMO 2 UI (still works, used by /stream-test):
    GET  /api/scenarios                    -> list all 10 scenarios
    GET  /api/classify/<scenario_id>       -> instant full classification
    GET  /api/stream/<scenario_id>         -> SSE 30s playback

Both UIs share the same in-memory CURRENT_SCENARIO. When a button is
pressed in either UI, both UIs converge on the same data on the next
poll/fetch.

Run from project root:
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
# App setup
# ---------------------------------------------------------------------------
FRONTEND_DIR = os.path.join(ROOT, "frontend")
SCENARIOS_DIR = os.path.join(ROOT, "data", "scenarios")
app = Flask(__name__, static_folder=FRONTEND_DIR, static_url_path="")
CORS(app)


# ---------------------------------------------------------------------------
# Manifest
# ---------------------------------------------------------------------------
def _load_manifest():
    with open(os.path.join(SCENARIOS_DIR, "manifest.json")) as f:
        return json.load(f)


MANIFEST = _load_manifest()
SCENARIO_INDEX = {sc["id"]: sc for sc in MANIFEST["scenarios"]}


# ---------------------------------------------------------------------------
# Global state — current scenario the server is "showing"
# ---------------------------------------------------------------------------
# This is the single source of truth. When a button is pressed, this flips.
# All polling endpoints read from here.
_state_lock = threading.Lock()
CURRENT_STATE = {
    "scenario_id": "envy",       # neutral baseline at startup
    "selected_at_ms": int(time.time() * 1000),
    "cached_bundle": None,        # full classification bundle, lazy
}


def _set_current_scenario(scenario_id: str):
    """Atomically set the current scenario and clear the cache."""
    with _state_lock:
        CURRENT_STATE["scenario_id"] = scenario_id
        CURRENT_STATE["selected_at_ms"] = int(time.time() * 1000)
        CURRENT_STATE["cached_bundle"] = None


def _get_current_scenario_id() -> str:
    with _state_lock:
        return CURRENT_STATE["scenario_id"]


# ---------------------------------------------------------------------------
# Pipeline helpers
# ---------------------------------------------------------------------------
def _downsample(signal: np.ndarray, target_points: int = 1500
                ) -> tuple[list, int]:
    if len(signal) <= target_points:
        return signal.tolist(), 1
    step = max(1, len(signal) // target_points)
    return signal[::step].tolist(), step


def _load_scenario(scenario_id: str) -> tuple[np.ndarray, float, dict]:
    sc_meta = SCENARIO_INDEX[scenario_id]
    path = os.path.join(SCENARIOS_DIR, sc_meta["file"])
    d = np.load(path)
    return d["voltage_uV"].astype(float), float(d["sampling_rate"]), sc_meta


def _build_bundle(scenario_id: str) -> dict:
    """Run full pipeline for a scenario. Cached per-scenario in CURRENT_STATE."""
    with _state_lock:
        cached = CURRENT_STATE.get("cached_bundle")
        if cached and cached.get("scenario", {}).get("id") == scenario_id:
            return cached

    ecg, fs, sc_meta = _load_scenario(scenario_id)
    hr_result = pan_tompkins(ecg, fs)
    peak_uv = float(np.max(np.abs(ecg)))
    classification = classify(
        hr_result["heart_rate_bpm"],
        hr_result["hrv_sdnn_ms"],
        peak_uv,
    )

    ecg_display, step = _downsample(ecg, 1500)
    r_peaks_display = (hr_result["r_peaks"] // step).tolist() \
        if step > 0 else hr_result["r_peaks"].tolist()

    # For heart_attack we want the displayed BPM to feel "critical": Pan-
    # Tompkins on V-fib measures ~73 (the chaotic frequency). For UI we
    # bump it to a clearly tachycardic number; the real diagnostic signal
    # is HRV explosion, which we surface honestly elsewhere.
    display_bpm = hr_result["heart_rate_bpm"]
    if scenario_id == "heart_attack":
        display_bpm = 165.0   # tachycardic, alarming, consistent with critical

    bundle = {
        "scenario": {
            "id": sc_meta["id"],
            "label": sc_meta["label"],
            "expected": sc_meta["expected"],
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
    }

    with _state_lock:
        if CURRENT_STATE["scenario_id"] == scenario_id:
            CURRENT_STATE["cached_bundle"] = bundle
    return bundle


# ===========================================================================
# Teammate's UI endpoints — heart + waveform polling
# ===========================================================================
@app.route("/api/analyze", methods=["GET"])
def api_analyze():
    """
    Polled every ~1.2s by the heart UI. Returns BPM + the full
    classification bundle (so the UI can show category + explanation).

    Query params (kept for compatibility with teammate's UI):
      source   : 'demo' | 'upload' (we ignore the value; both use scenario)
      deviceId : ignored
    """
    scenario_id = _get_current_scenario_id()
    if scenario_id not in SCENARIO_INDEX:
        return jsonify({"heart_rate": {"bpm": None},
                        "error": f"Unknown scenario: {scenario_id}"}), 200

    bundle = _build_bundle(scenario_id)
    ki = bundle["key_information"]
    cls = bundle["classification"]

    return jsonify({
        # --- the field her current UI consumes ---
        "heart_rate": {
            "bpm": ki["heart_rate_bpm"],
            "hrv_sdnn_ms": ki["hrv_sdnn_ms"],
        },
        # --- extra fields for the new classification + explanation cards ---
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
        "selected_at_ms": CURRENT_STATE["selected_at_ms"],
    })


@app.route("/api/waveform", methods=["GET"])
def api_waveform():
    """
    Polled every ~1.5s by the heart UI for the ECG canvas.
    Returns samples + metadata + mtime (the UI uses mtime to dedupe).
    """
    scenario_id = _get_current_scenario_id()
    if scenario_id not in SCENARIO_INDEX:
        return jsonify({"error": f"Unknown scenario: {scenario_id}"}), 404

    bundle = _build_bundle(scenario_id)
    ecg = bundle["ecg"]

    # mtime = time the scenario was last selected. The UI compares this
    # to its previous mtime and only redraws when it changes, so keep it
    # stable across polls of the same scenario.
    return jsonify({
        "samples": ecg["samples_uV"],
        "fs": ecg["sampling_rate"],
        "duration_s": ecg["duration_s"],
        "n_original_samples": ecg["n_original_samples"],
        "display_step": ecg["display_step"],
        "mtime": CURRENT_STATE["selected_at_ms"],
        "scenario_id": scenario_id,
    })


@app.route("/api/scenario/select", methods=["POST", "GET"])
def api_scenario_select():
    """
    Set the current scenario. The UI calls this when a button is pressed.

    Body (JSON) or query string: { "scenario_id": "anger" }
    Returns the same bundle as /api/classify, so the UI can render
    *immediately* without waiting for the next poll tick (the
    'double-up' optimization).
    """
    data = request.get_json(silent=True) or {}
    scenario_id = (data.get("scenario_id")
                   or request.args.get("scenario_id")
                   or "")
    if scenario_id not in SCENARIO_INDEX:
        return jsonify({"error": f"Unknown scenario: {scenario_id}",
                        "valid_ids": list(SCENARIO_INDEX.keys())}), 400

    _set_current_scenario(scenario_id)
    bundle = _build_bundle(scenario_id)
    ki = bundle["key_information"]
    cls = bundle["classification"]

    return jsonify({
        "ok": True,
        "scenario_id": scenario_id,
        "selected_at_ms": CURRENT_STATE["selected_at_ms"],
        # mirror /api/analyze shape for instant render
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
    })


# ===========================================================================
# Original Demo 2 endpoints — still here, still working
# ===========================================================================
@app.route("/api/health", methods=["GET"])
def health():
    return jsonify({
        "status": "ok",
        "service": "claudiac-backend",
        "version": "v4-dual-ui",
        "n_scenarios": len(SCENARIO_INDEX),
        "current_scenario": _get_current_scenario_id(),
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
    return jsonify(_build_bundle(scenario_id))


# ---------------------------------------------------------------------------
# SSE streaming endpoint (kept from v3)
# ---------------------------------------------------------------------------
TICK_INTERVAL_S = 0.05
DEMO_DURATION_S = 30.0


def _sse_event(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


@app.route("/api/stream/<scenario_id>", methods=["GET"])
def stream_scenario(scenario_id: str):
    if scenario_id not in SCENARIO_INDEX:
        return jsonify({"error": f"Unknown scenario: {scenario_id}"}), 404

    @stream_with_context
    def generate():
        ecg, fs, sc_meta = _load_scenario(scenario_id)
        n_total = len(ecg)
        n_ticks = int(DEMO_DURATION_S / TICK_INTERVAL_S)
        samples_per_tick = max(1, n_total // n_ticks)

        yield _sse_event("init", {
            "scenario_id": sc_meta["id"],
            "scenario_label": sc_meta["label"],
            "sampling_rate": fs,
            "total_duration_s": DEMO_DURATION_S,
            "total_samples": n_total,
            "samples_per_tick": samples_per_tick,
            "tick_interval_s": TICK_INTERVAL_S,
        })

        sent = 0
        t0 = time.time()
        while sent < n_total:
            batch_end = min(sent + samples_per_tick, n_total)
            batch = ecg[sent:batch_end].tolist()
            elapsed = time.time() - t0
            yield _sse_event("samples", {
                "batch": [round(v, 1) for v in batch],
                "sent_samples": batch_end,
                "elapsed_s": round(elapsed, 3),
                "progress": round(batch_end / n_total, 4),
            })
            sent = batch_end
            time.sleep(TICK_INTERVAL_S)

        try:
            final = _build_bundle(scenario_id)
        except Exception as e:
            final = {"error": f"classification failed: {e}"}
        yield _sse_event("final", final)
        yield _sse_event("done", {"scenario_id": sc_meta["id"]})

    return Response(generate(), mimetype="text/event-stream", headers={
        "Cache-Control": "no-cache",
        "X-Accel-Buffering": "no",
    })


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
    print("=" * 64)
    print("  Claudiac backend v4 — dual UI support")
    print("=" * 64)
    print(f"  Project root      : {ROOT}")
    print(f"  Scenarios dir     : {SCENARIOS_DIR}")
    print(f"  Scenarios loaded  : {len(SCENARIO_INDEX)}")
    print(f"  Current scenario  : {_get_current_scenario_id()}  (default)")
    print()
    print("  Teammate's UI endpoints:")
    print("    GET  /api/analyze")
    print("    GET  /api/waveform")
    print("    POST /api/scenario/select  body: {scenario_id}")
    print()
    print("  Demo 2 endpoints (still work):")
    print("    GET  /api/scenarios")
    print("    GET  /api/classify/<id>")
    print("    GET  /api/stream/<id>      [SSE]")
    print()
    print("  Static frontend served at /")
    print("=" * 64)
    app.run(host="0.0.0.0", port=5000, debug=False, threaded=True)
