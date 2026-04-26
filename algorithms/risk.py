"""
Risk module — single-window cardiac anomaly score.

Per the project's option-C scope: this is NOT a multi-day trend module.
It scores how anomalous *this particular 30s ECG window* looks, based on
classical signal features computed from the R-peak series.

Four sub-scores combine into a 0-1 anomaly score (higher = more concerning):
  1. HR out of healthy sinus range (60-100 bpm)
  2. RR coefficient of variation (rhythm regularity)
  3. Ectopic beat ratio (sudden short or long intervals)
  4. HRV vs personal baseline

Each sub-score is in [0, 1]; the final score is a weighted average. The
module also returns human-readable "findings" so the UI / Claude downstream
can explain *why* the score is what it is — explainability over black-box.
"""

import numpy as np
from typing import Optional


# Healthy adult resting sinus range
HR_LOW_BPM = 60
HR_HIGH_BPM = 100

# Personal HRV baseline (in production: rolling 7-day from HealthKit)
HRV_BASELINE_MS = 45

# Sub-score weights (must sum to 1.0)
WEIGHTS = {
    "hr_range": 0.25,
    "rhythm_cv": 0.30,
    "ectopic": 0.30,
    "hrv_deviation": 0.15,
}


# ---------------------------------------------------------------------------
# Sub-score 1 — HR out of normal range
# ---------------------------------------------------------------------------
def score_hr_range(hr_bpm: float) -> tuple[float, str]:
    """0 inside healthy sinus range; ramps to 1 at clinical thresholds."""
    if HR_LOW_BPM <= hr_bpm <= HR_HIGH_BPM:
        return 0.0, f"HR {hr_bpm:.0f} bpm in healthy range"
    if hr_bpm < HR_LOW_BPM:
        # bradycardia — full alarm by 40 bpm
        score = min(1.0, (HR_LOW_BPM - hr_bpm) / (HR_LOW_BPM - 40))
        return score, f"HR {hr_bpm:.0f} bpm below normal (bradycardia range)"
    # tachycardia — full alarm by 130 bpm
    score = min(1.0, (hr_bpm - HR_HIGH_BPM) / (130 - HR_HIGH_BPM))
    return score, f"HR {hr_bpm:.0f} bpm above normal (tachycardia range)"


# ---------------------------------------------------------------------------
# Sub-score 2 — RR coefficient of variation (rhythm regularity)
# ---------------------------------------------------------------------------
def score_rhythm_cv(rr_intervals_s: np.ndarray) -> tuple[float, str]:
    """
    CV = std(RR) / mean(RR). Healthy sinus rhythm: CV ~0.02-0.06.
    CV > 0.15 suggests irregular rhythm (e.g. atrial fibrillation pattern).
    """
    if len(rr_intervals_s) < 2:
        return 0.5, "Insufficient beats to assess rhythm"
    cv = np.std(rr_intervals_s) / np.mean(rr_intervals_s)
    # Map CV: 0.06 -> 0, 0.20 -> 1
    score = float(np.clip((cv - 0.06) / (0.20 - 0.06), 0.0, 1.0))
    if cv < 0.06:
        finding = f"Rhythm regular (CV {cv:.3f})"
    elif cv < 0.15:
        finding = f"Mild rhythm irregularity (CV {cv:.3f})"
    else:
        finding = f"Marked rhythm irregularity (CV {cv:.3f})"
    return score, finding


# ---------------------------------------------------------------------------
# Sub-score 3 — Ectopic / premature beat detection
# ---------------------------------------------------------------------------
def score_ectopic(rr_intervals_s: np.ndarray) -> tuple[float, str]:
    """
    Ectopic heuristic: an RR interval >20% shorter than the surrounding
    median, often followed by a compensatory pause, suggests a premature
    beat. We just count intervals deviating >20% from the median.
    """
    if len(rr_intervals_s) < 3:
        return 0.0, "Too few beats to assess ectopy"
    median_rr = float(np.median(rr_intervals_s))
    deviations = np.abs(rr_intervals_s - median_rr) / median_rr
    ectopic_count = int(np.sum(deviations > 0.20))
    ectopic_ratio = ectopic_count / len(rr_intervals_s)
    # Map: 0 -> 0, 0.15 (>15% beats) -> 1
    score = float(np.clip(ectopic_ratio / 0.15, 0.0, 1.0))
    if ectopic_count == 0:
        finding = "No ectopic beats detected"
    else:
        finding = (f"{ectopic_count} ectopic-like beat(s) detected "
                   f"({ectopic_ratio*100:.1f}% of intervals)")
    return score, finding


# ---------------------------------------------------------------------------
# Sub-score 4 — HRV deviation from personal baseline
# ---------------------------------------------------------------------------
def score_hrv_deviation(hrv_sdnn_ms: float, baseline_ms: float
                        ) -> tuple[float, str]:
    """
    NOTE: 30 s is a short window for HRV — values are noisy and tend to
    underestimate true SDNN. This sub-score is included for completeness
    but weighted lightly. Critically suppressed HRV (<15 ms) still scores.
    """
    if hrv_sdnn_ms >= baseline_ms * 0.6:
        return 0.0, f"HRV {hrv_sdnn_ms:.1f} ms within expected range"
    # Map: 60% baseline -> 0, 20% baseline -> 1
    drop_ratio = (baseline_ms * 0.6 - hrv_sdnn_ms) / (baseline_ms * 0.4)
    score = float(np.clip(drop_ratio, 0.0, 1.0))
    finding = (f"HRV {hrv_sdnn_ms:.1f} ms suppressed vs baseline "
               f"{baseline_ms} ms (caveat: 30 s window is short)")
    return score, finding


# ---------------------------------------------------------------------------
# Top-level
# ---------------------------------------------------------------------------
def compute_risk(hr_bpm: float, hrv_sdnn_ms: float,
                 rr_intervals_s: np.ndarray,
                 hrv_baseline_ms: Optional[float] = None) -> dict:
    """
    Combine four sub-scores into a single anomaly score.

    Returns:
      {
        "anomaly_score": float in [0, 1],
        "level": "normal" | "watch" | "concern",
        "sub_scores": {...},
        "findings": [str, str, ...]   # human-readable summary
      }
    """
    baseline = hrv_baseline_ms or HRV_BASELINE_MS

    s1, f1 = score_hr_range(hr_bpm)
    s2, f2 = score_rhythm_cv(rr_intervals_s)
    s3, f3 = score_ectopic(rr_intervals_s)
    s4, f4 = score_hrv_deviation(hrv_sdnn_ms, baseline)

    sub_scores = {
        "hr_range": round(s1, 3),
        "rhythm_cv": round(s2, 3),
        "ectopic": round(s3, 3),
        "hrv_deviation": round(s4, 3),
    }
    total = (s1 * WEIGHTS["hr_range"]
             + s2 * WEIGHTS["rhythm_cv"]
             + s3 * WEIGHTS["ectopic"]
             + s4 * WEIGHTS["hrv_deviation"])

    if total < 0.25:
        level = "normal"
    elif total < 0.55:
        level = "watch"
    else:
        level = "concern"

    return {
        "anomaly_score": round(float(total), 3),
        "level": level,
        "sub_scores": sub_scores,
        "weights": WEIGHTS,
        "findings": [f1, f2, f3, f4],
    }


# ---------------------------------------------------------------------------
# Quick self-test
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import os, sys, json
    sys.path.insert(0, os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))))
    from algorithms.heart_rate import pan_tompkins

    here = os.path.dirname(os.path.abspath(__file__))
    data_path = os.path.join(here, "..", "data", "apple_watch_ecg_api.npz")
    d = np.load(data_path)
    res = pan_tompkins(d["voltage_uV"].astype(float),
                       float(d["sampling_rate"]))

    print(f"HR  : {res['heart_rate_bpm']:.1f} bpm")
    print(f"HRV : {res['hrv_sdnn_ms']:.1f} ms  (baseline {HRV_BASELINE_MS} ms)")
    print(f"RR intervals (n) : {len(res['rr_intervals_s'])}")
    print()
    print("Risk assessment:")
    print("-" * 60)
    risk = compute_risk(res["heart_rate_bpm"], res["hrv_sdnn_ms"],
                        res["rr_intervals_s"])
    print(json.dumps(risk, indent=2))
