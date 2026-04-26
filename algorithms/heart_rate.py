"""
Heart Rate detection via Pan-Tompkins (1985) QRS algorithm.

Implemented from scratch using scipy primitives only — no NeuroKit2 wrapper
calls — so every step of the pipeline is visible and explainable.

Reference:
    Pan, J. & Tompkins, W.J. (1985). "A Real-Time QRS Detection Algorithm."
    IEEE Transactions on Biomedical Engineering, BME-32(3), 230-236.

Pipeline:
    Raw ECG -> Bandpass (5-15 Hz) -> Derivative -> Square ->
    Moving window integration -> Adaptive threshold -> R-peak indices
"""

import numpy as np
from scipy.signal import butter, filtfilt, find_peaks


# ---------------------------------------------------------------------------
# Stage 1 — Bandpass filter (5–15 Hz)
# ---------------------------------------------------------------------------
def bandpass_filter(signal: np.ndarray, fs: float,
                    low: float = 5.0, high: float = 15.0,
                    order: int = 2) -> np.ndarray:
    """
    QRS energy concentrates in 5-15 Hz. This filter removes:
      - baseline wander and P/T waves below 5 Hz
      - muscle noise and powerline interference above 15 Hz
    """
    nyq = 0.5 * fs
    b, a = butter(order, [low / nyq, high / nyq], btype="band")
    # filtfilt = zero-phase filtering, no time delay
    return filtfilt(b, a, signal)


# ---------------------------------------------------------------------------
# Stage 2 — Derivative
# ---------------------------------------------------------------------------
def derivative(signal: np.ndarray, fs: float) -> np.ndarray:
    """
    Pan-Tompkins original 5-point derivative kernel:
        y(n) = (1/8T) * [-x(n-2) - 2x(n-1) + 2x(n+1) + x(n+2)]
    Emphasizes the steep slopes of the QRS complex.
    """
    kernel = np.array([-1, -2, 0, 2, 1]) / 8.0
    return np.convolve(signal, kernel, mode="same") * fs


# ---------------------------------------------------------------------------
# Stage 3 — Square
# ---------------------------------------------------------------------------
def square(signal: np.ndarray) -> np.ndarray:
    """
    Pointwise squaring:
      - makes everything positive
      - amplifies large values (QRS) more than small ones (noise)
    """
    return signal ** 2


# ---------------------------------------------------------------------------
# Stage 4 — Moving window integration
# ---------------------------------------------------------------------------
def moving_window_integrate(signal: np.ndarray, fs: float,
                            window_ms: float = 150.0) -> np.ndarray:
    """
    150 ms is roughly the width of a QRS complex. Integrating over this window
    smooths the squared signal into a single bump per QRS.
    """
    window_size = int(window_ms / 1000.0 * fs)
    kernel = np.ones(window_size) / window_size
    return np.convolve(signal, kernel, mode="same")


# ---------------------------------------------------------------------------
# Stage 5 — Adaptive threshold + R-peak detection
# ---------------------------------------------------------------------------
def detect_r_peaks(integrated: np.ndarray, raw: np.ndarray, fs: float
                   ) -> np.ndarray:
    """
    Find peaks in the integrated signal that are:
      - above an adaptive threshold (mean + 0.5 * std of the integrated signal)
      - separated by at least 200 ms (physiological refractory period)
    Then refine each peak's location by searching a small window of the raw
    bandpassed signal for the true R-wave maximum.
    """
    threshold = np.mean(integrated) + 0.5 * np.std(integrated)
    min_distance = int(0.2 * fs)  # 200 ms refractory

    candidate_peaks, _ = find_peaks(integrated,
                                    height=threshold,
                                    distance=min_distance)

    # Refinement: integration shifts peaks ~75 ms; back-search for the true R
    refined = []
    search_radius = int(0.075 * fs)
    for p in candidate_peaks:
        lo = max(0, p - search_radius)
        hi = min(len(raw), p + search_radius)
        refined.append(lo + int(np.argmax(raw[lo:hi])))
    return np.array(refined, dtype=int)


# ---------------------------------------------------------------------------
# Top-level: run the whole pipeline + compute HR & HRV
# ---------------------------------------------------------------------------
def pan_tompkins(ecg: np.ndarray, fs: float) -> dict:
    """
    Run the full pipeline on a raw ECG signal.

    Returns a dict with:
      - r_peaks            : sample indices of detected R-peaks
      - rr_intervals_s     : array of RR intervals in seconds
      - heart_rate_bpm     : average HR (60 / mean RR)
      - hrv_sdnn_ms        : standard deviation of RR intervals (ms)
      - stages             : intermediate signals for visualization/debug
    """
    bp = bandpass_filter(ecg, fs)
    deriv = derivative(bp, fs)
    sq = square(deriv)
    integ = moving_window_integrate(sq, fs)
    r_peaks = detect_r_peaks(integ, bp, fs)

    if len(r_peaks) < 2:
        return {
            "r_peaks": r_peaks,
            "rr_intervals_s": np.array([]),
            "heart_rate_bpm": float("nan"),
            "hrv_sdnn_ms": float("nan"),
            "stages": {"bandpass": bp, "derivative": deriv,
                       "squared": sq, "integrated": integ},
        }

    rr_s = np.diff(r_peaks) / fs
    hr_bpm = 60.0 / np.mean(rr_s)
    sdnn_ms = float(np.std(rr_s) * 1000.0)

    return {
        "r_peaks": r_peaks,
        "rr_intervals_s": rr_s,
        "heart_rate_bpm": float(hr_bpm),
        "hrv_sdnn_ms": sdnn_ms,
        "stages": {"bandpass": bp, "derivative": deriv,
                   "squared": sq, "integrated": integ},
    }


# ---------------------------------------------------------------------------
# Quick self-test when run directly
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import os
    here = os.path.dirname(os.path.abspath(__file__))
    data_path = os.path.join(here, "..", "data", "apple_watch_ecg_api.npz")
    d = np.load(data_path)
    ecg = d["voltage_uV"].astype(float)
    fs = float(d["sampling_rate"])

    result = pan_tompkins(ecg, fs)
    print(f"Sampling rate     : {fs:.0f} Hz")
    print(f"Signal length     : {len(ecg) / fs:.1f} s ({len(ecg)} samples)")
    print(f"R-peaks detected  : {len(result['r_peaks'])}")
    print(f"Heart rate        : {result['heart_rate_bpm']:.1f} bpm")
    print(f"HRV SDNN          : {result['hrv_sdnn_ms']:.1f} ms")
    print(f"First 5 R-peaks   : {result['r_peaks'][:5]}")
