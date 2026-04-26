# Claudiac — Frontend API spec

This is everything you need to build the frontend. The backend is a
Flask server that exposes three endpoints. You can ignore Python entirely —
just call these URLs from your JS with `fetch()`.

## Running the backend

The backend runs on `http://localhost:5000`. Zack starts it with:

```cmd
cd C:\Users\zhipe\Downloads\Claudiac
venv\Scripts\activate
python server\app.py
```

Once it's running, you can open `http://localhost:5000/api/analyze` in
your browser to see the JSON response.

## Endpoints

### `GET /api/health`

Sanity check. Returns:

```json
{"status": "ok", "service": "claudiac-backend"}
```

Use this to verify the backend is up before doing anything else.

---

### `GET /api/analyze`

The main endpoint. Runs the full pipeline (Pan-Tompkins QRS detection →
mood inference → risk scoring) on the bundled ECG and returns a single
JSON bundle with everything you need to render the UI.

**Response shape:**

```json
{
  "ecg": {
    "samples_uV": [1634.1, 1612.2, 1555.7, ... ],
    "sampling_rate": 512.0,
    "duration_s": 30.0,
    "n_original_samples": 15360,
    "display_step": 10
  },
  "r_peaks_display": [42, 85, 128, 171, 214, ... ],

  "heart_rate": {
    "bpm": 71.9,
    "hrv_sdnn_ms": 11.6,
    "n_beats": 35
  },

  "mood": {
    "mode": "rule_based",
    "physiological_state": "subclinical_strain",
    "reported_state": "calm",
    "mismatch": true,
    "hypothesis": "upcoming advisor meeting may be a stressor; under-slept (6.2h)",
    "action": "nudge",
    "message": "Your body's showing early signs of stress even though you feel calm. Worth a 60-second breathing reset before the meeting?",
    "metrics": {
      "hr_bpm": 71.9,
      "hr_baseline": 65,
      "hrv_sdnn_ms": 11.6,
      "hrv_baseline": 45
    }
  },

  "risk": {
    "anomaly_score": 0.129,
    "level": "normal",
    "sub_scores": {
      "hr_range": 0.0,
      "rhythm_cv": 0.0,
      "ectopic": 0.0,
      "hrv_deviation": 0.857
    },
    "weights": {
      "hr_range": 0.25,
      "rhythm_cv": 0.3,
      "ectopic": 0.3,
      "hrv_deviation": 0.15
    },
    "findings": [
      "HR 72 bpm in healthy range",
      "Rhythm regular (CV 0.014)",
      "No ectopic beats detected",
      "HRV 11.6 ms suppressed vs baseline 45 ms (caveat: 30 s window is short)"
    ]
  },

  "self_report": {
    "valence": "calm",
    "arousal": "low",
    "note": "preparing for advisor meeting in 30 min"
  },

  "context": {
    "time_of_day": "morning",
    "day_of_week": "Monday",
    "activity": "sedentary",
    "next_calendar_event": "1:1 with advisor at 10:00 (in 30 min)",
    "sleep_last_night_hours": 6.2
  }
}
```

**Field-by-field guide:**

- `ecg.samples_uV` — array of ~1500 floats, the ECG voltage in microvolts.
  This is downsampled from 15,360 raw samples for fast browser rendering.
  Plot it directly with Chart.js or any line chart library.

- `ecg.sampling_rate` / `ecg.duration_s` — for axis labels (x = time in s).

- `r_peaks_display` — indices into `ecg.samples_uV` where R-peaks were
  detected. Use these to draw red dots on top of the ECG line. Already
  mapped to the downsampled axis, so just plot points at
  `(samples_uV[i], r_peaks_display[i])`.

- `heart_rate.bpm` / `heart_rate.hrv_sdnn_ms` — big number displays.

- `mood.mismatch` — boolean. **This is the headline insight of the demo.**
  When `true`, the body and the user disagree — that's where Claudiac
  earns its keep. Make this visually loud.

- `mood.action` — one of `"silent" | "nudge" | "converse" | "acknowledge"`.
  Use this to decide whether to show the message at all and how
  prominently.

- `mood.message` — the user-facing nudge text. Render in a chat-bubble
  style card.

- `mood.hypothesis` — internal reasoning. Show as a smaller caption under
  the message, like a "why we said this" tooltip.

- `risk.level` — one of `"normal" | "watch" | "concern"`. Color the risk
  card accordingly (green / amber / red).

- `risk.findings` — array of 4 short strings. Render as a bulleted list
  inside the risk card.

**Example fetch:**

```js
async function loadAnalysis() {
  const res = await fetch("http://localhost:5000/api/analyze");
  const data = await res.json();

  // Render ECG line chart
  drawEcgChart(data.ecg.samples_uV, data.ecg.sampling_rate, data.ecg.display_step);

  // Mark R-peaks
  drawRPeaks(data.r_peaks_display, data.ecg.samples_uV);

  // Big numbers
  document.getElementById("hr").textContent = data.heart_rate.bpm + " bpm";
  document.getElementById("hrv").textContent = data.heart_rate.hrv_sdnn_ms + " ms";

  // Mood card
  if (data.mood.mismatch) {
    document.getElementById("mood-card").classList.add("mismatch");
  }
  document.getElementById("mood-message").textContent = data.mood.message;

  // Risk card
  const riskCard = document.getElementById("risk-card");
  riskCard.className = "risk-" + data.risk.level;
  // ... render findings list
}
```

---

### `POST /api/mood`

Re-runs the pipeline with a custom self-report. Use this if you want to
let the demo viewer change the user's reported mood and watch the
mismatch detection update live.

**Request body (JSON):**

```json
{
  "valence": "stressed",
  "arousal": "high",
  "note": "anxious about the meeting"
}
```

`valence` is one of: `calm | stressed | anxious | happy | sad | tired`.
`arousal` is one of: `low | medium | high`.

**Response:** same shape as `GET /api/analyze`, but with the mood
section recomputed against the new self-report.

**Example:**

```js
async function reInferMood(valence, arousal, note) {
  const res = await fetch("http://localhost:5000/api/mood", {
    method: "POST",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify({ valence, arousal, note })
  });
  return res.json();
}
```

## Suggested layout

This is a suggestion — you have full freedom to redesign.

```
+---------------------------------------------------+
|  CLAUDIAC                              [scenario v]|
+---------------------------------------------------+
|                          |                        |
|  ECG waveform            |  HR    72 bpm          |
|  (with R-peak markers)   |  HRV   12 ms           |
|                          |                        |
|                          +------------------------+
|                          |  MOOD                  |
|                          |  ⚠ Mismatch detected   |
|                          |  Body says strain,     |
|                          |  you say calm          |
|                          |                        |
|                          |  "Your body's showing  |
|                          |   early signs of       |
|                          |   stress even though   |
|                          |   you feel calm. Worth |
|                          |   a 60-second          |
|                          |   breathing reset      |
|                          |   before the meeting?" |
|                          +------------------------+
|                          |  RISK   normal  (0.13) |
|                          |  ✓ HR healthy          |
|                          |  ✓ Rhythm regular      |
|                          |  ✓ No ectopic beats    |
|                          |  ⚠ HRV suppressed      |
+--------------------------+------------------------+
```

**Key visual moves to consider:**

1. **The mismatch card is the hero** — when `mood.mismatch === true`,
   make it visually stand out (border glow, slight pulse animation,
   contrasting color).

2. **R-peak dots over the ECG line** — small red dots on top of the
   blue ECG line. This is the visual that says "we did real signal
   processing" to the judges.

3. **Risk findings as a checklist** — green checks for clean findings,
   amber/red for the suppressed HRV one. The user feels reassured but
   sees the one caveat.

4. **Don't put everything on one screen** — if it gets crowded, the
   mood card can be the main view and the metrics/risk can collapse.

## Libraries

You can use any CDN-loaded library. Some that play well here:

- **Chart.js** — easiest line chart for the ECG.
  `<script src="https://cdn.jsdelivr.net/npm/chart.js"></script>`
- **uPlot** — faster than Chart.js for long signals (1500+ points).
- **Plain SVG** — also a totally valid choice if you prefer hand-coded.

Avoid React for this hackathon — adds tooling overhead with no benefit
at this scale. Plain HTML/CSS/JS is the right choice.

## Tips

- The Flask server has CORS enabled, so opening `index.html` directly
  with `file://` should work, but the cleanest way is to put your
  `index.html`, `style.css`, `app.js` in `Claudiac/frontend/` — Flask
  will then serve them at `http://localhost:5000/`.

- The `mood.message` field can be empty when `action === "silent"`. Hide
  the chat bubble when that happens.

- The Anthropic API integration is going to flip on later — when it
  does, `mood.mode` will change from `"rule_based"` to `"claude_api"`
  and the messages will get more natural. The rest of the schema stays
  identical, so no frontend changes needed.
