"""
ECG -> Emotion / Anomaly classifier.

Architecture (chosen for hackathon reliability):
  1. Hard rule classifier maps extracted features (HR, HRV, peak amplitude)
     to one of 10 categories using deterministic if-else over the medical
     knowledge table. 100% reproducible classification.
  2. LLM explainer (Claude) takes the classified label + the raw features +
     the knowledge table, and produces a natural-language explanation of
     WHY this classification fits, in human-friendly prose.

This is "retrieval-augmented reasoning over structured medical knowledge"
— the rules are the retrieval layer, the LLM is the reasoning layer.

Categories (matching the spreadsheet, top-priority first):
  heart_attack > anger / anxiety / fear  (high-HR group)
              > joy / envy / disgust / embarrassment  (normal-HR group)
              > ennui / sadness  (low-HR group)
"""

import os
import json
from typing import Optional


# ============================================================================
# CONFIG
# ============================================================================
USE_CLAUDE_FOR_EXPLANATION = True
CLAUDE_MODEL = "claude-sonnet-4-5"
CLAUDE_TIMEOUT_S = 8.0
CLAUDE_MAX_TOKENS = 400


# ============================================================================
# Medical knowledge table (the "RAG corpus" — also embedded in LLM prompt)
# ============================================================================
KNOWLEDGE_TABLE = [
    {
        "emotion": "Heart Attack",
        "id": "heart_attack",
        "color": "#FF0000",
        "color_secondary": "#000000",
        "flashing": True,
        "hr_rule": "< 40 OR > 150 OR no organized QRS",
        "hrv_rule": "ignored",
        "amplitude_rule": "erratic / flatline",
        "physiological_why": (
            "Medical anomaly thresholds. Overrides all other classifications."
        ),
    },
    {"emotion": "Anger", "id": "anger", "color": "#FF0000",
     "hr_rule": "> 85", "hrv_rule": "< 30",
     "amplitude_rule": "high (pounding)",
     "physiological_why": "High energy, high stress, strong pounding chest."},
    {"emotion": "Anxiety", "id": "anxiety", "color": "#FF8C00",
     "hr_rule": "> 85", "hrv_rule": "< 30",
     "amplitude_rule": "low (shallow / noisy)",
     "physiological_why": "Fast heart rate, high stress, but shallow breathing/trembling."},
    {"emotion": "Fear", "id": "fear", "color": "#8A2BE2",
     "hr_rule": "> 85", "hrv_rule": ">= 30", "amplitude_rule": "any",
     "physiological_why": "Fight-or-flight triggered. Fast HR, but heart compensates well (HRV stays normal)."},
    {"emotion": "Joy", "id": "joy", "color": "#FFD700",
     "hr_rule": "65-85", "hrv_rule": "> 50", "amplitude_rule": "any",
     "physiological_why": "Resting heart rate, but highly relaxed/positive variance."},
    {"emotion": "Envy", "id": "envy", "color": "#00CED1",
     "hr_rule": "65-85", "hrv_rule": "30-50", "amplitude_rule": "any",
     "physiological_why": "The baseline. Awake, alert, but neutral vitals."},
    {"emotion": "Disgust", "id": "disgust", "color": "#32CD32",
     "hr_rule": "65-85", "hrv_rule": "< 30",
     "amplitude_rule": "high / norm",
     "physiological_why": "Unpleasant stimulus causing mild stress, but not enough to spike HR. Strong physical rejection reaction."},
    {"emotion": "Embarrassment", "id": "embarrassment", "color": "#FF69B4",
     "hr_rule": "65-85", "hrv_rule": "< 30",
     "amplitude_rule": "low (shallow)",
     "physiological_why": "Mild stress, but the body physically 'withdraws' or shrinks (shallow breathing/low amplitude)."},
    {"emotion": "Ennui", "id": "ennui", "color": "#3A3B5C",
     "hr_rule": "< 65", "hrv_rule": "> 50", "amplitude_rule": "any",
     "physiological_why": "Extremely relaxed, almost asleep. Low energy."},
    {"emotion": "Sadness", "id": "sadness", "color": "#4169E1",
     "hr_rule": "< 65", "hrv_rule": "<= 50", "amplitude_rule": "any",
     "physiological_why": "Low energy, but accompanied by withdrawal/stress (low variability)."},
]


# Personal baseline reference for "amplitude high/low" judgement.
# In a real product this would be calibrated per user.
AMPLITUDE_BASELINE_UV = 1400  # ~1.4 mV typical R-wave peak
AMPLITUDE_HIGH_THRESHOLD_UV = 1800
AMPLITUDE_LOW_THRESHOLD_UV = 1000


# ============================================================================
# Feature classification helpers
# ============================================================================
def _hr_band(hr: float) -> str:
    if hr < 65:
        return "low"
    if hr <= 85:
        return "normal"
    return "high"


def _hrv_band(hrv: float) -> str:
    if hrv < 30:
        return "low"
    if hrv <= 50:
        return "normal"
    return "high"


def _amplitude_band(peak_uv: float) -> str:
    if peak_uv >= AMPLITUDE_HIGH_THRESHOLD_UV:
        return "high"
    if peak_uv <= AMPLITUDE_LOW_THRESHOLD_UV:
        return "low"
    return "normal"


# ============================================================================
# Hard rule classifier — top-priority anomaly first, then emotion bands
# ============================================================================
def hard_rule_classify(hr_bpm: float, hrv_sdnn_ms: float,
                       peak_amplitude_uV: float) -> str:
    """
    Returns one of the 10 category IDs. Heart attack overrides everything.
    """
    # --- Override: heart attack detection ---
    # V-fib produces wildly varying RR intervals -> SDNN explodes (>200 ms).
    # Bradycardia (<40) and severe tachycardia (>150) are also red flags.
    if hr_bpm < 40 or hr_bpm > 150 or hrv_sdnn_ms > 200:
        return "heart_attack"

    hr = _hr_band(hr_bpm)
    hrv = _hrv_band(hrv_sdnn_ms)
    amp = _amplitude_band(peak_amplitude_uV)

    # --- High HR group (> 85) ---
    if hr == "high":
        if hrv == "low":
            # Anger vs Anxiety differ by amplitude
            return "anger" if amp == "high" else "anxiety"
        # HRV >= 30: fear (heart compensating well)
        return "fear"

    # --- Normal HR group (65-85) ---
    if hr == "normal":
        if hrv == "high":
            return "joy"
        if hrv == "normal":
            return "envy"
        # HRV < 30: disgust vs embarrassment, differ by amplitude
        return "embarrassment" if amp == "low" else "disgust"

    # --- Low HR group (< 65) ---
    # hr == "low"
    if hrv == "high":
        return "ennui"
    return "sadness"


def get_knowledge_entry(category_id: str) -> dict:
    """Look up the table row for a given category."""
    for entry in KNOWLEDGE_TABLE:
        if entry["id"] == category_id:
            return entry
    raise ValueError(f"Unknown category: {category_id}")


# ============================================================================
# LLM explanation layer
# ============================================================================
def _build_explanation_prompt(category_id: str, knowledge: dict,
                              hr_bpm: float, hrv_sdnn_ms: float,
                              peak_amplitude_uV: float,
                              hr_band: str, hrv_band: str,
                              amplitude_band: str) -> str:
    return f"""A patient's 30-second ECG was analyzed by Pan-Tompkins QRS
detection. The extracted features and the category match are below.

EXTRACTED FEATURES:
  Heart rate (HR)   : {hr_bpm:.1f} bpm   (band: {hr_band})
  HRV (SDNN)        : {hrv_sdnn_ms:.1f} ms   (band: {hrv_band})
  R-wave peak       : {peak_amplitude_uV:.0f} uV   (band: {amplitude_band})

CATEGORY MATCHED:
  {knowledge["emotion"]}
  HR rule        : {knowledge["hr_rule"]}
  HRV rule       : {knowledge["hrv_rule"]}
  Amplitude rule : {knowledge["amplitude_rule"]}
  Physiological "why": {knowledge["physiological_why"]}

Write 2-3 short sentences for a clinician dashboard explaining WHY this
ECG matches this category. Reference at least one specific extracted
number. Do not hedge with "may" or "could" — the rules already matched.
Tone: precise, clinical, concise. No greetings, no preamble.

Output only the explanation text, no JSON, no markdown."""


def llm_explain(category_id: str,
                hr_bpm: float, hrv_sdnn_ms: float,
                peak_amplitude_uV: float) -> dict:
    """
    Generate natural-language explanation. Falls back to the table's
    physiological_why on any error.
    """
    knowledge = get_knowledge_entry(category_id)
    hr_band = _hr_band(hr_bpm)
    hrv_band = _hrv_band(hrv_sdnn_ms)
    amp_band = _amplitude_band(peak_amplitude_uV)

    fallback_text = knowledge["physiological_why"]

    if not USE_CLAUDE_FOR_EXPLANATION:
        return {"text": fallback_text, "source": "knowledge_table"}

    try:
        from anthropic import Anthropic
    except ImportError:
        return {"text": fallback_text,
                "source": "knowledge_table (anthropic not installed)"}

    if not os.getenv("ANTHROPIC_API_KEY"):
        return {"text": fallback_text,
                "source": "knowledge_table (no API key)"}

    try:
        client = Anthropic(timeout=CLAUDE_TIMEOUT_S)
        response = client.messages.create(
            model=CLAUDE_MODEL,
            max_tokens=CLAUDE_MAX_TOKENS,
            messages=[{
                "role": "user",
                "content": _build_explanation_prompt(
                    category_id, knowledge, hr_bpm, hrv_sdnn_ms,
                    peak_amplitude_uV, hr_band, hrv_band, amp_band),
            }],
        )
    except Exception as e:
        return {"text": fallback_text,
                "source": f"knowledge_table (Claude error: "
                          f"{type(e).__name__})"}

    text = response.content[0].text.strip()
    out = {"text": text, "source": "claude_api"}
    if hasattr(response, "usage"):
        out["_usage"] = {
            "input_tokens": response.usage.input_tokens,
            "output_tokens": response.usage.output_tokens,
        }
    return out


# ============================================================================
# Top-level
# ============================================================================
def classify(hr_bpm: float, hrv_sdnn_ms: float,
             peak_amplitude_uV: float) -> dict:
    """
    Full classification: hard rules + LLM explanation + display metadata.
    """
    category_id = hard_rule_classify(hr_bpm, hrv_sdnn_ms, peak_amplitude_uV)
    knowledge = get_knowledge_entry(category_id)
    explanation = llm_explain(category_id, hr_bpm, hrv_sdnn_ms,
                              peak_amplitude_uV)

    return {
        "category_id": category_id,
        "category_label": knowledge["emotion"],
        "color": knowledge["color"],
        "color_secondary": knowledge.get("color_secondary"),
        "flashing": knowledge.get("flashing", False),
        "is_critical": category_id == "heart_attack",
        "features": {
            "hr_bpm": round(hr_bpm, 1),
            "hr_band": _hr_band(hr_bpm),
            "hrv_sdnn_ms": round(hrv_sdnn_ms, 1),
            "hrv_band": _hrv_band(hrv_sdnn_ms),
            "peak_amplitude_uV": round(peak_amplitude_uV, 0),
            "amplitude_band": _amplitude_band(peak_amplitude_uV),
        },
        "rules_matched": {
            "hr": knowledge["hr_rule"],
            "hrv": knowledge["hrv_rule"],
            "amplitude": knowledge["amplitude_rule"],
        },
        "knowledge_table_why": knowledge["physiological_why"],
        "explanation": explanation,
    }


# ============================================================================
# Quick self-test on all 10 scenarios
# ============================================================================
if __name__ == "__main__":
    import sys
    sys.path.insert(0, os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))))
    import numpy as np
    from algorithms.heart_rate import pan_tompkins

    here = os.path.dirname(os.path.abspath(__file__))
    scenarios_dir = os.path.join(here, "..", "data", "scenarios")
    manifest_path = os.path.join(scenarios_dir, "manifest.json")
    with open(manifest_path) as f:
        manifest = json.load(f)

    print("=" * 78)
    print("  Claudiac classifier self-test on all 10 scenarios")
    print("=" * 78)
    print(f"  USE_CLAUDE_FOR_EXPLANATION = {USE_CLAUDE_FOR_EXPLANATION}")
    print()

    n_correct = 0
    for sc in manifest["scenarios"]:
        path = os.path.join(scenarios_dir, sc["file"])
        d = np.load(path)
        ecg = d["voltage_uV"].astype(float)
        fs = float(d["sampling_rate"])
        hr = pan_tompkins(ecg, fs)
        peak = float(np.max(np.abs(ecg)))

        result = classify(hr["heart_rate_bpm"], hr["hrv_sdnn_ms"], peak)
        ok = result["category_id"] == sc["id"]
        n_correct += int(ok)
        mark = "OK" if ok else "MISMATCH"
        print(f"  [{mark}] {sc['id']:<15} -> classified as "
              f"{result['category_id']:<15} "
              f"(HR={result['features']['hr_bpm']:.0f}, "
              f"HRV={result['features']['hrv_sdnn_ms']:.0f}, "
              f"peak={result['features']['peak_amplitude_uV']:.0f})")
        if ok and USE_CLAUDE_FOR_EXPLANATION:
            print(f"         explanation: "
                  f"{result['explanation']['text'][:120]}...")

    print()
    print(f"  ACCURACY: {n_correct}/10")
    print("=" * 78)
