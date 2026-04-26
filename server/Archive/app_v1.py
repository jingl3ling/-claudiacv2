"""
Claudiac Flask backend — Demo 2 (classification version).

Endpoints:
  GET  /api/health                  -> sanity check
  GET  /api/scenarios               -> list of all 10 emotion / anomaly cards
  GET  /api/classify/<scenario_id>  -> run pipeline on that scenario,
                                       return ECG samples + features +
                                       classification + LLM explanation

Demo 1 (live DAQ) endpoint to be added when ESP32 ingestion is wired up.

Run from project root:
    python server\\app.py
"""

import os
import sys
import json
import numpy as np
from flask import Flask, jsonify, send_from_directory
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
# Manifest cache
# ---------------------------------------------------------------------------
def _load_manifest():
    with open(os.path.join(SCENARIOS_DIR, "manifest.json")) as f:
        return json.load(f)


MANIFEST = _load_manifest()
SCENARIO_INDEX = {sc["id"]: sc for sc in MANIFEST["scenarios"]}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _downsample_for_display(signal: np.ndarray, target_points: int = 1500
                            ) -> tuple[list, int]:
    if len(signal) <= target_points:
        return signal.tolist(), 1
    step = max(1, len(signal) // target_points)
    return signal[::step].tolist(), step


def _run_classification_pipeline(scenario_id: str) -> dict:
    """Load ECG -> Pan-Tompkins -> classifier -> bundle for frontend."""
    if scenario_id not in SCENARIO_INDEX:
        return {"error": f"Unknown scenario: {scenario_id}",
                "valid_ids": list(SCENARIO_INDEX.keys())}

    sc_meta = SCENARIO_INDEX[scenario_id]
    path = os.path.join(SCENARIOS_DIR, sc_meta["file"])
    d = np.load(path)
    ecg = d["voltage_uV"].astype(float)
    fs = float(d["sampling_rate"])

    # Run feature extraction (Demo 1 logic)
    hr_result = pan_tompkins(ecg, fs)
    peak_uv = float(np.max(np.abs(ecg)))

    # Run classification (Demo 2 logic)
    classification = classify(
        hr_result["heart_rate_bpm"],
        hr_result["hrv_sdnn_ms"],
        peak_uv,
    )

    # Downsample ECG for browser plotting
    ecg_display, step = _downsample_for_display(ecg, 1500)
    r_peaks_display = (hr_result["r_peaks"] // step).tolist() \
        if step > 0 else hr_result["r_peaks"].tolist()

    return {
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
            "heart_rate_bpm": round(hr_result["heart_rate_bpm"], 1),
            "hrv_sdnn_ms": round(hr_result["hrv_sdnn_ms"], 1),
            "peak_amplitude_uV": round(peak_uv, 0),
            "n_beats_detected": int(len(hr_result["r_peaks"])),
        },
        "classification": classification,
    }


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------
@app.route("/api/health", methods=["GET"])
def health():
    return jsonify({
        "status": "ok",
        "service": "claudiac-backend",
        "version": "demo2-classification",
        "n_scenarios": len(SCENARIO_INDEX),
    })


@app.route("/api/scenarios", methods=["GET"])
def list_scenarios():
    """
    Returns the full list of available scenarios with display metadata
    (label, color, etc.) so the frontend can render the 10 buttons.
    """
    return jsonify({
        "scenarios": MANIFEST["scenarios"],
        "knowledge_table": KNOWLEDGE_TABLE,
    })


@app.route("/api/classify/<scenario_id>", methods=["GET"])
def classify_scenario(scenario_id: str):
    """
    Run the full pipeline on a chosen scenario and return ECG +
    extracted features + classification + LLM explanation.
    """
    result = _run_classification_pipeline(scenario_id)
    if "error" in result:
        return jsonify(result), 404
    return jsonify(result)


@app.route("/")
def index():
    index_path = os.path.join(FRONTEND_DIR, "index.html")
    if os.path.exists(index_path):
        return send_from_directory(FRONTEND_DIR, "index.html")
    return jsonify({
        "status": "ok",
        "message": "Claudiac backend (Demo 2). Frontend not yet built.",
        "try": [
            "/api/health",
            "/api/scenarios",
            "/api/classify/anger",
            "/api/classify/heart_attack",
        ],
    })


# ---------------------------------------------------------------------------
# Entry
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    print("=" * 64)
    print("  Claudiac backend — Demo 2 (classification)")
    print("=" * 64)
    print(f"  Project root  : {ROOT}")
    print(f"  Scenarios dir : {SCENARIOS_DIR}")
    print(f"  Scenarios     : {len(SCENARIO_INDEX)} loaded")
    for sid in SCENARIO_INDEX:
        print(f"                  - {sid}")
    print()
    print("  Endpoints:")
    print("    GET  http://localhost:5000/api/health")
    print("    GET  http://localhost:5000/api/scenarios")
    print("    GET  http://localhost:5000/api/classify/<scenario_id>")
    print()
    print("  Try in browser:")
    print("    http://localhost:5000/api/classify/anger")
    print("    http://localhost:5000/api/classify/heart_attack")
    print("=" * 64)
    app.run(host="0.0.0.0", port=5000, debug=False)
