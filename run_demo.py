"""
End-to-end Claudiac demo — wires Block 3 algorithms together.

Pipeline:
  1. Load ECG (the 'Apple Watch -> Cloud' product)
  2. heart_rate.py  — Pan-Tompkins for HR + HRV
  3. mood.py        — fuse physiology + self-report + context
  4. risk.py        — single-window cardiac anomaly score
  5. Print a unified dashboard view
"""

import os
import sys
import json
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from algorithms.heart_rate import pan_tompkins
from algorithms.mood import infer_mood, MOCK_SELF_REPORT, MOCK_CONTEXT
from algorithms.risk import compute_risk


def main():
    # 1. Load ECG
    data_path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                             "data", "apple_watch_ecg_api.npz")
    d = np.load(data_path)
    ecg = d["voltage_uV"].astype(float)
    fs = float(d["sampling_rate"])

    # 2. Heart rate + HRV via Pan-Tompkins
    hr_result = pan_tompkins(ecg, fs)
    hr = hr_result["heart_rate_bpm"]
    hrv = hr_result["hrv_sdnn_ms"]
    rr = hr_result["rr_intervals_s"]

    # 3. Mood inference
    mood = infer_mood(hr, hrv)

    # 4. Risk assessment
    risk = compute_risk(hr, hrv, rr)

    # 5. Unified dashboard
    print("=" * 64)
    print("  CLAUDIAC — end-to-end inference")
    print("=" * 64)
    print()
    print(f"  Source         : Apple Watch ECG, {len(ecg)/fs:.0f} s @ {fs:.0f} Hz")
    print(f"  Beats detected : {len(hr_result['r_peaks'])}")
    print()
    print("  HEART RATE")
    print("  " + "-" * 60)
    print(f"  HR              : {hr:.1f} bpm")
    print(f"  HRV (SDNN)      : {hrv:.1f} ms")
    print()
    print("  MOOD")
    print("  " + "-" * 60)
    print(f"  Physiological   : {mood['physiological_state']}")
    print(f"  Self-reported   : {mood['reported_state']}")
    print(f"  Mismatch        : {mood['mismatch']}")
    print(f"  Hypothesis      : {mood['hypothesis']}")
    print(f"  Action          : {mood['action']}")
    if mood.get("message"):
        print(f"  Message         : {mood['message']}")
    print()
    print("  RISK")
    print("  " + "-" * 60)
    print(f"  Anomaly score   : {risk['anomaly_score']:.3f}  [{risk['level']}]")
    for finding in risk["findings"]:
        print(f"    - {finding}")
    print()
    print("=" * 64)


if __name__ == "__main__":
    main()
