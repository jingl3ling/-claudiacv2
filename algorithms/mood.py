"""
Mood inference module.

Fuses three sources to produce a mood interpretation:
  1. Physiological signals from ECG (HR, HRV)  -- objective
  2. Self-report from the user                 -- subjective
  3. Context (time, activity, calendar)        -- situational

Two modes, switchable with USE_CLAUDE flag:
  - rule_based : deterministic logic, no API needed (fallback)
  - claude_api : sends fused context to Claude for narrative reasoning

The interesting cases are MISMATCHES between physiology and self-report —
those are where AI adds value over a simple HRV-to-stress mapping.
"""

import os
import json
from typing import Optional


# ============================================================================
# CONFIG — flip this to True after Claude API key + credits are ready
# ============================================================================
USE_CLAUDE = False
CLAUDE_MODEL = "claude-sonnet-4-5"


# ============================================================================
# Mock self-report and context (in production these come from the iPhone app)
# ============================================================================
MOCK_SELF_REPORT = {
    "valence": "calm",      # calm | stressed | anxious | happy | sad | tired
    "arousal": "low",       # low | medium | high
    "note": "preparing for advisor meeting in 30 min",
}

MOCK_CONTEXT = {
    "time_of_day": "morning",
    "day_of_week": "Monday",
    "activity": "sedentary",
    "next_calendar_event": "1:1 with advisor at 10:00 (in 30 min)",
    "sleep_last_night_hours": 6.2,  # below the 7-9 healthy range
}

# Personal baselines (in production: rolling 7-day average from HealthKit)
BASELINES = {
    "resting_hr_bpm": 65,
    "hrv_sdnn_ms_typical": 45,   # healthy adult resting baseline
    "hrv_sdnn_ms_low_threshold": 25,
}


# ============================================================================
# Rule-based mode (works without API)
# ============================================================================
def rule_based_mood(hr_bpm: float, hrv_sdnn_ms: float,
                    self_report: dict, context: dict,
                    baselines: dict) -> dict:
    """
    Deterministic fusion logic. Three diagnostic checks:
      1. Is physiology elevated vs personal baseline?
      2. Does it agree with self-report?
      3. Does context offer a plausible explanation?
    """
    # --- physiology layer
    hr_elevated = hr_bpm > baselines["resting_hr_bpm"] + 10
    hrv_low = hrv_sdnn_ms < baselines["hrv_sdnn_ms_low_threshold"]

    if hrv_low and hr_elevated:
        physio_state = "stressed"
    elif hrv_low:
        physio_state = "subclinical_strain"
    elif hr_elevated:
        physio_state = "aroused"
    else:
        physio_state = "calm"

    # --- self-report layer (normalize)
    reported = self_report.get("valence", "unknown")
    reported_calm = reported in ("calm", "happy")

    # --- mismatch detection
    physio_calm = physio_state == "calm"
    mismatch = physio_calm != reported_calm

    # --- contextual hypothesis
    hypothesis_parts = []
    if context.get("next_calendar_event") and "advisor" in str(
            context["next_calendar_event"]).lower():
        hypothesis_parts.append("upcoming advisor meeting may be a stressor")
    if context.get("sleep_last_night_hours", 8) < 7:
        hypothesis_parts.append(
            f"under-slept ({context['sleep_last_night_hours']}h)")
    hypothesis = "; ".join(hypothesis_parts) or "no obvious contextual driver"

    # --- action recommendation
    if mismatch and physio_state == "subclinical_strain":
        action = "nudge"
        message = ("Your body's showing early signs of stress even though "
                   "you feel calm. Worth a 60-second breathing reset before "
                   "the meeting?")
    elif mismatch and physio_state == "stressed":
        action = "converse"
        message = ("Heart rate elevated and HRV is suppressed — looks like "
                   "your body is bracing for something. What's on your mind?")
    elif physio_state == "calm" and reported_calm:
        action = "silent"
        message = ""
    else:
        action = "acknowledge"
        message = "Noted — your physiology matches how you're feeling."

    return {
        "mode": "rule_based",
        "physiological_state": physio_state,
        "reported_state": reported,
        "mismatch": mismatch,
        "hypothesis": hypothesis,
        "action": action,
        "message": message,
        "metrics": {
            "hr_bpm": round(hr_bpm, 1),
            "hr_baseline": baselines["resting_hr_bpm"],
            "hrv_sdnn_ms": round(hrv_sdnn_ms, 1),
            "hrv_baseline": baselines["hvr_sdnn_ms_typical"]
            if "hvr_sdnn_ms_typical" in baselines
            else baselines["hrv_sdnn_ms_typical"],
        },
    }


# ============================================================================
# Claude API mode
# ============================================================================
def claude_mood(hr_bpm: float, hrv_sdnn_ms: float,
                self_report: dict, context: dict,
                baselines: dict) -> dict:
    """
    Sends the fused signal bundle to Claude for narrative-grade reasoning.
    Falls back to rule_based on any error, so the demo never breaks.
    """
    try:
        from anthropic import Anthropic
    except ImportError:
        return {**rule_based_mood(hr_bpm, hrv_sdnn_ms, self_report,
                                  context, baselines),
                "mode": "rule_based_fallback (anthropic not installed)"}

    if not os.getenv("ANTHROPIC_API_KEY"):
        return {**rule_based_mood(hr_bpm, hrv_sdnn_ms, self_report,
                                  context, baselines),
                "mode": "rule_based_fallback (no API key)"}

    prompt = f"""You are Claudiac, a health companion fusing physiological
signals with self-reported mood to detect mismatches and offer brief,
respectful insight.

Physiological signals (last 30 seconds, from Apple Watch ECG):
  Heart rate          : {hr_bpm:.1f} bpm  (personal baseline: {baselines["resting_hr_bpm"]} bpm)
  HRV (SDNN)          : {hrv_sdnn_ms:.1f} ms  (personal baseline: {baselines["hrv_sdnn_ms_typical"]} ms)

User's self-report:
  Valence : {self_report.get("valence")}
  Arousal : {self_report.get("arousal")}
  Note    : {self_report.get("note", "(none)")}

Context:
  Time          : {context.get("time_of_day")}, {context.get("day_of_week")}
  Activity      : {context.get("activity")}
  Next event    : {context.get("next_calendar_event", "none")}
  Sleep         : {context.get("sleep_last_night_hours", "unknown")} hours

Tasks (respond ONLY with valid JSON, no markdown fences):
  1. Determine if there is a mismatch between physiology and self-report
  2. Form one short causal hypothesis grounded in context
  3. Decide action: "silent", "nudge", or "converse"
  4. Compose a single message (<= 2 sentences) — only if action != silent

JSON schema:
{{
  "mismatch": boolean,
  "physiological_state": "calm" | "aroused" | "subclinical_strain" | "stressed",
  "hypothesis": "...",
  "action": "silent" | "nudge" | "converse",
  "message": "..."
}}"""

    client = Anthropic()
    response = client.messages.create(
        model=CLAUDE_MODEL,
        max_tokens=500,
        messages=[{"role": "user", "content": prompt}],
    )
    raw = response.content[0].text.strip()

    # Strip markdown fences if Claude added them despite the instruction
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
        raw = raw.strip()

    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return {**rule_based_mood(hr_bpm, hrv_sdnn_ms, self_report,
                                  context, baselines),
                "mode": "rule_based_fallback (Claude returned non-JSON)",
                "raw_response": raw}

    parsed["mode"] = "claude_api"
    parsed["metrics"] = {
        "hr_bpm": round(hr_bpm, 1),
        "hr_baseline": baselines["resting_hr_bpm"],
        "hrv_sdnn_ms": round(hrv_sdnn_ms, 1),
        "hrv_baseline": baselines["hrv_sdnn_ms_typical"],
    }
    return parsed


# ============================================================================
# Top-level entry
# ============================================================================
def infer_mood(hr_bpm: float, hrv_sdnn_ms: float,
               self_report: Optional[dict] = None,
               context: Optional[dict] = None,
               baselines: Optional[dict] = None) -> dict:
    self_report = self_report or MOCK_SELF_REPORT
    context = context or MOCK_CONTEXT
    baselines = baselines or BASELINES

    if USE_CLAUDE:
        return claude_mood(hr_bpm, hrv_sdnn_ms, self_report, context, baselines)
    return rule_based_mood(hr_bpm, hrv_sdnn_ms, self_report, context, baselines)


# ============================================================================
# Quick self-test when run directly
# ============================================================================
if __name__ == "__main__":
    import sys
    sys.path.insert(0, os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))))
    import numpy as np
    from algorithms.heart_rate import pan_tompkins

    here = os.path.dirname(os.path.abspath(__file__))
    data_path = os.path.join(here, "..", "data", "apple_watch_ecg_api.npz")
    d = np.load(data_path)
    hr_result = pan_tompkins(d["voltage_uV"].astype(float),
                             float(d["sampling_rate"]))

    print(f"Mode             : {'Claude API' if USE_CLAUDE else 'rule-based'}")
    print(f"HR from ECG      : {hr_result['heart_rate_bpm']:.1f} bpm")
    print(f"HRV from ECG     : {hr_result['hrv_sdnn_ms']:.1f} ms")
    print(f"Self-report      : {MOCK_SELF_REPORT['valence']}")
    print(f"Context          : {MOCK_CONTEXT['next_calendar_event']}")
    print()
    print("Mood inference:")
    print("-" * 60)
    result = infer_mood(hr_result["heart_rate_bpm"],
                        hr_result["hrv_sdnn_ms"])
    print(json.dumps(result, indent=2, ensure_ascii=False))
