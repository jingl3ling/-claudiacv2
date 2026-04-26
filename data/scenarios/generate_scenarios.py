"""
Generate 10 ECG scenarios matching the emotion classification table.

Each scenario is a 30-second, 512 Hz ECG signal saved as .npz, with
parameters chosen so the downstream feature extractor (Pan-Tompkins)
will produce HR / HRV / amplitude values that fall into the rule
boxes for that emotion.

Categories (matching the spreadsheet):
  - heart_attack : V-fib (erratic, no organized QRS)
  - anger        : HR > 85, HRV < 30, high amplitude
  - anxiety      : HR > 85, HRV < 30, low/shallow amplitude
  - fear         : HR > 85, HRV >= 30 (compensating well)
  - joy          : HR 65-85, HRV > 50 (relaxed positive variance)
  - envy         : HR 65-85, HRV 30-50 (baseline)
  - disgust      : HR 65-85, HRV < 30, normal amplitude
  - embarrassment: HR 65-85, HRV < 30, low amplitude
  - ennui        : HR < 65, HRV > 50 (relaxed)
  - sadness      : HR < 65, HRV <= 50 (low energy + withdrawal)

Run:
    python data/scenarios/generate_scenarios.py
"""

import os
import numpy as np
import neurokit2 as nk

SAMPLING_RATE = 512
DURATION_S = 30
RNG = np.random.default_rng(42)

OUT_DIR = os.path.dirname(os.path.abspath(__file__))


def make_normal_ecg(hr_bpm: float, hr_std: float,
                    amplitude_mv: float, noise: float = 0.01,
                    seed: int = 0) -> np.ndarray:
    """
    Generate a synthetic ECG with target HR and HRV characteristics.

    NeuroKit2's heart_rate_std parameter controls beat-to-beat variability,
    which after Pan-Tompkins detection will roughly become HRV SDNN.
    Empirical mapping (rough):
       heart_rate_std = 2  -> SDNN ~15 ms (low HRV)
       heart_rate_std = 5  -> SDNN ~35 ms (normal)
       heart_rate_std = 8  -> SDNN ~55 ms (high)
    """
    ecg = nk.ecg_simulate(
        duration=DURATION_S,
        sampling_rate=SAMPLING_RATE,
        heart_rate=hr_bpm,
        heart_rate_std=hr_std,
        method="ecgsyn",
        noise=noise,
        random_state=seed,
    )
    # Scale to target amplitude (mV) then convert to uV
    ecg_normalized = ecg / np.max(np.abs(ecg))
    return ecg_normalized * amplitude_mv * 1000.0  # uV


def make_vfib_ecg(seed: int = 99) -> np.ndarray:
    """
    Synthesize a ventricular fibrillation pattern: chaotic oscillations
    with no organized QRS complexes. Looks dramatic on the screen and is
    what 'cardiac arrest' tends to render as on a monitor.

    Method: sum of several sinusoids at ~5-7 Hz with random amplitude
    and phase modulation, plus baseline noise. No P-QRS-T morphology.
    """
    rng = np.random.default_rng(seed)
    n = SAMPLING_RATE * DURATION_S
    t = np.arange(n) / SAMPLING_RATE

    signal = np.zeros(n)
    # Several chaotic frequency components in the V-fib band (4-8 Hz)
    for _ in range(5):
        freq = rng.uniform(4.0, 8.0)
        phase = rng.uniform(0, 2 * np.pi)
        amp = rng.uniform(0.4, 1.0)
        # slowly varying amplitude makes it look more chaotic
        envelope = 0.5 + 0.5 * np.sin(2 * np.pi * rng.uniform(0.2, 0.8) * t + rng.uniform(0, 2*np.pi))
        signal += amp * envelope * np.sin(2 * np.pi * freq * t + phase)

    # Add baseline drift and noise so it doesn't look too clean
    signal += 0.15 * np.sin(2 * np.pi * 0.3 * t)  # slow baseline wander
    signal += rng.normal(0, 0.08, n)              # high-frequency noise

    # Scale to ~1.5 mV peak chaotic excursions, convert to uV
    signal = signal / np.max(np.abs(signal)) * 1.5
    return signal * 1000.0


# ---------------------------------------------------------------------------
# Scenario specifications
# ---------------------------------------------------------------------------
# Tuned so that after Pan-Tompkins runs, the extracted features land in
# the right cell of the table. amplitude_mv is the R-wave peak target.

SCENARIOS = [
    # --- Heart attack: special V-fib generator ---
    {
        "id": "heart_attack",
        "label": "Heart Attack",
        "color": "#FF0000",
        "color_secondary": "#000000",
        "flashing": True,
        "ecg_method": "vfib",
        "expected": {
            "hr_bpm": "ignored", "hrv_ms": "ignored",
            "amplitude": "erratic",
        },
        "physiological_why": (
            "Medical anomaly thresholds. Overrides all other classifications."
        ),
    },

    # --- 9 emotions ---
    {
        "id": "anger",
        "label": "Anger",
        "color": "#FF0000",
        "ecg_method": "normal",
        "params": {"hr_bpm": 105, "hr_std": 1.5, "amplitude_mv": 2.2},
        "expected": {"hr_bpm": ">85", "hrv_ms": "<30", "amplitude": "high"},
        "physiological_why": (
            "High energy, high stress, strong pounding chest."
        ),
    },
    {
        "id": "anxiety",
        "label": "Anxiety",
        "color": "#FF8C00",
        "ecg_method": "normal",
        "params": {"hr_bpm": 100, "hr_std": 1.5, "amplitude_mv": 0.7,
                   "noise": 0.04},
        "expected": {"hr_bpm": ">85", "hrv_ms": "<30", "amplitude": "low/noisy"},
        "physiological_why": (
            "Fast heart rate, high stress, but shallow breathing/trembling."
        ),
    },
    {
        "id": "fear",
        "label": "Fear",
        "color": "#8A2BE2",
        "ecg_method": "normal",
        "params": {"hr_bpm": 95, "hr_std": 5.0, "amplitude_mv": 1.5},
        "expected": {"hr_bpm": ">85", "hrv_ms": ">=30", "amplitude": "any"},
        "physiological_why": (
            "Fight-or-flight triggered. Fast HR, but heart compensates "
            "well (HRV stays normal)."
        ),
    },
    {
        "id": "joy",
        "label": "Joy",
        "color": "#FFD700",
        "ecg_method": "normal",
        "params": {"hr_bpm": 75, "hr_std": 7.5, "amplitude_mv": 1.4},
        "expected": {"hr_bpm": "65-85", "hrv_ms": ">50", "amplitude": "any"},
        "physiological_why": (
            "Resting heart rate, but highly relaxed/positive variance."
        ),
    },
    {
        "id": "envy",
        "label": "Envy",
        "color": "#00CED1",
        "ecg_method": "normal",
        "params": {"hr_bpm": 75, "hr_std": 4.0, "amplitude_mv": 1.4},
        "expected": {"hr_bpm": "65-85", "hrv_ms": "30-50", "amplitude": "any"},
        "physiological_why": (
            "The baseline. Awake, alert, but neutral vitals."
        ),
    },
    {
        "id": "disgust",
        "label": "Disgust",
        "color": "#32CD32",
        "ecg_method": "normal",
        "params": {"hr_bpm": 78, "hr_std": 1.5, "amplitude_mv": 1.6},
        "expected": {"hr_bpm": "65-85", "hrv_ms": "<30", "amplitude": "high/norm"},
        "physiological_why": (
            "Unpleasant stimulus causing mild stress, but not enough to "
            "spike HR. Strong physical rejection reaction."
        ),
    },
    {
        "id": "embarrassment",
        "label": "Embarrassment",
        "color": "#FF69B4",
        "ecg_method": "normal",
        "params": {"hr_bpm": 78, "hr_std": 1.5, "amplitude_mv": 0.7},
        "expected": {"hr_bpm": "65-85", "hrv_ms": "<30", "amplitude": "low"},
        "physiological_why": (
            "Mild stress, body physically 'withdraws' (shallow breathing/"
            "low amplitude)."
        ),
    },
    {
        "id": "ennui",
        "label": "Ennui",
        "color": "#3A3B5C",
        "ecg_method": "normal",
        "params": {"hr_bpm": 58, "hr_std": 7.5, "amplitude_mv": 1.3},
        "expected": {"hr_bpm": "<65", "hrv_ms": ">50", "amplitude": "any"},
        "physiological_why": (
            "Extremely relaxed, almost asleep. Low energy."
        ),
    },
    {
        "id": "sadness",
        "label": "Sadness",
        "color": "#4169E1",
        "ecg_method": "normal",
        "params": {"hr_bpm": 58, "hr_std": 2.5, "amplitude_mv": 1.0},
        "expected": {"hr_bpm": "<65", "hrv_ms": "<=50", "amplitude": "any"},
        "physiological_why": (
            "Low energy, but accompanied by withdrawal/stress (low variability)."
        ),
    },
]


# ---------------------------------------------------------------------------
# Generate
# ---------------------------------------------------------------------------
def main():
    print("=" * 68)
    print("  Generating 10 ECG scenarios for Claudiac Demo 2")
    print("=" * 68)

    metadata = []
    for i, sc in enumerate(SCENARIOS):
        if sc["ecg_method"] == "vfib":
            ecg_uv = make_vfib_ecg(seed=99)
        else:
            p = sc["params"]
            ecg_uv = make_normal_ecg(
                hr_bpm=p["hr_bpm"],
                hr_std=p["hr_std"],
                amplitude_mv=p["amplitude_mv"],
                noise=p.get("noise", 0.01),
                seed=i,
            )

        out_path = os.path.join(OUT_DIR, f"{sc['id']}.npz")
        np.savez(
            out_path,
            voltage_uV=ecg_uv.astype(np.float32),
            sampling_rate=SAMPLING_RATE,
            scenario_id=sc["id"],
        )

        peak_uv = float(np.max(np.abs(ecg_uv)))
        print(f"  [{i+1:2d}/10] {sc['id']:15s}  -> {out_path}")
        print(f"          peak |V| = {peak_uv:7.0f} uV   "
              f"len = {len(ecg_uv):,} samples")

        metadata.append({
            "id": sc["id"],
            "label": sc["label"],
            "color": sc["color"],
            "color_secondary": sc.get("color_secondary"),
            "flashing": sc.get("flashing", False),
            "expected": sc["expected"],
            "physiological_why": sc["physiological_why"],
            "file": f"{sc['id']}.npz",
        })

    # Save manifest so the server can list all scenarios
    import json
    manifest_path = os.path.join(OUT_DIR, "manifest.json")
    with open(manifest_path, "w") as f:
        json.dump({
            "n_scenarios": len(SCENARIOS),
            "duration_s": DURATION_S,
            "sampling_rate": SAMPLING_RATE,
            "scenarios": metadata,
        }, f, indent=2)

    print()
    print(f"Manifest -> {manifest_path}")
    print("Done.")


if __name__ == "__main__":
    main()
