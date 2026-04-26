"""
Claudiac Flask backend.

Exposes the algorithms layer as HTTP endpoints so the frontend
(HTML/JS, written by the teammate) can call them via fetch().

Endpoints:
  GET  /                  -> health check (returns {"status": "ok"})
  GET  /api/analyze       -> runs the full pipeline on the bundled ECG
                             and returns ECG samples + R-peaks + HR/HRV
                             + mood + risk
  POST /api/mood          -> re-runs only the mood inference with a
                             custom self-report (so the UI can let the
                             user change valence/arousal and re-infer)

Run from the project root:
    python server\app.py

Then open http://localhost:5000/api/analyze in a browser to see the JSON,
or have the frontend fetch from the same URL.
"""

import os
import sys
import numpy as np
from flask import Flask, jsonify, request, send_from_directory
from flask_cors import CORS

# Make `algorithms` importable when running from the project root
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from algorithms.heart_rate import pan_tompkins
from algorithms.mood import infer_mood, MOCK_SELF_REPORT, MOCK_CONTEXT
from algorithms.risk import compute_risk


# ---------------------------------------------------------------------------
# App setup
# ---------------------------------------------------------------------------
FRONTEND_DIR = os.path.join(ROOT, "frontend")
app = Flask(__name__, static_folder=FRONTEND_DIR, static_url_path="")
CORS(app)  # allow the frontend to call us from any origin


# ---------------------------------------------------------------------------
# Cache: load ECG once at startup, reuse on every request
# ---------------------------------------------------------------------------
def _load_ecg():
    data_path = os.path.join(ROOT, "data", "apple_watch_ecg_api.npz")
    d = np.load(data_path)
    return {
        "voltage_uV": d["voltage_uV"].astype(float),
        "sampling_rate": float(d["sampling_rate"]),
    }


ECG = _load_ecg()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _downsample_for_display(signal: np.ndarray, target_points: int = 1500
                            ) -> list:
    """
    The raw ECG has 15,360 samples. Sending all of them to the browser
    works but bloats payload. Downsample for plotting only — actual
    R-peak detection runs on the full signal server-side.
    """
    if len(signal) <= target_points:
        return signal.tolist()
    step = max(1, len(signal) // target_points)
    return signal[::step].tolist()


def _run_full_pipeline(self_report: dict = None,
                       context: dict = None) -> dict:
    """Run heart_rate -> mood -> risk and return everything in one bundle."""
    ecg = ECG["voltage_uV"]
    fs = ECG["sampling_rate"]

    hr_result = pan_tompkins(ecg, fs)
    mood = infer_mood(hr_result["heart_rate_bpm"],
                      hr_result["hrv_sdnn_ms"],
                      self_report=self_report,
                      context=context)
    risk = compute_risk(hr_result["heart_rate_bpm"],
                        hr_result["hrv_sdnn_ms"],
                        hr_result["rr_intervals_s"])

    # Convert ndarray -> list for JSON, downsample ECG for plotting
    ecg_display = _downsample_for_display(ecg, target_points=1500)
    display_step = max(1, len(ecg) // len(ecg_display))
    # R-peak indices need to map onto the downsampled x-axis
    r_peaks_display = (hr_result["r_peaks"] // display_step).tolist()

    return {
        "ecg": {
            "samples_uV": ecg_display,
            "sampling_rate": fs,
            "duration_s": len(ecg) / fs,
            "n_original_samples": int(len(ecg)),
            "display_step": int(display_step),
        },
        "r_peaks_display": r_peaks_display,
        "heart_rate": {
            "bpm": round(hr_result["heart_rate_bpm"], 1),
            "hrv_sdnn_ms": round(hr_result["hrv_sdnn_ms"], 1),
            "n_beats": int(len(hr_result["r_peaks"])),
        },
        "mood": mood,
        "risk": risk,
        "self_report": self_report or MOCK_SELF_REPORT,
        "context": context or MOCK_CONTEXT,
    }


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------
@app.route("/api/health", methods=["GET"])
def health():
    return jsonify({"status": "ok", "service": "claudiac-backend"})


@app.route("/api/analyze", methods=["GET"])
def analyze():
    """Full pipeline run with default mock self-report and context."""
    return jsonify(_run_full_pipeline())


@app.route("/api/mood", methods=["POST"])
def mood_only():
    """
    Re-infer mood with a custom self-report from the UI.
    Body (JSON): {"valence": "stressed", "arousal": "high", "note": "..."}
    """
    body = request.get_json(silent=True) or {}
    self_report = {
        "valence": body.get("valence", "calm"),
        "arousal": body.get("arousal", "low"),
        "note": body.get("note", ""),
    }
    return jsonify(_run_full_pipeline(self_report=self_report))


# Optional: serve the frontend on the same port (avoids CORS entirely)
@app.route("/")
def index():
    index_path = os.path.join(FRONTEND_DIR, "index.html")
    if os.path.exists(index_path):
        return send_from_directory(FRONTEND_DIR, "index.html")
    return jsonify({
        "status": "ok",
        "message": "Claudiac backend is running. Frontend not yet built.",
        "try": ["/api/health", "/api/analyze"],
    })


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    print("=" * 60)
    print("  Claudiac backend")
    print("=" * 60)
    print(f"  Project root : {ROOT}")
    print(f"  Frontend dir : {FRONTEND_DIR}")
    print(f"  ECG samples  : {len(ECG['voltage_uV']):,} @ "
          f"{ECG['sampling_rate']:.0f} Hz")
    print()
    print("  Endpoints:")
    print("    GET  http://localhost:5000/api/health")
    print("    GET  http://localhost:5000/api/analyze")
    print("    POST http://localhost:5000/api/mood")
    print("=" * 60)
    app.run(host="0.0.0.0", port=5000, debug=False)
