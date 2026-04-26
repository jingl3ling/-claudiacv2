# Claudiac — Backend overview for the frontend teammate

Hey — this is everything you need to know before you start writing the
frontend. Read top to bottom once, then jump to `FRONTEND_API.md` for
the actual API spec.

## What we're building

Claudiac is a Claude-powered cardiac and emotional health companion.
The pitch is one sentence:

> Apple Watch tells you your HRV is 12 ms and you panic. Claudiac
> tells you why your body is bracing — and decides whether to nudge
> you, talk to you, or stay quiet.

The differentiator is **signal + self-report fusion**. We detect the
*mismatch* between physiology (what the body says) and self-report
(what the user says). That mismatch — and the contextual reasoning
about why it exists — is where Claude earns its keep.

## Architecture (4 blocks)

```
Block 1 — Apple Watch        (mocked: bundled ECG file)
   ↓
Block 2 — Cloud upload       (mocked: file on disk)
   ↓
Block 3 — Healthcare algos   ← Zack owns
   • Pan-Tompkins QRS detection (heart rate, HRV)
   • Mood inference (physiology + self-report + context fusion)
   • Risk scoring (single-window cardiac anomaly score)
   ↓
Block 4 — HCI                ← you own the frontend, Zack owns the API layer
   • Flask backend exposes Block 3 over HTTP
   • Frontend (you) calls the API and renders the UI
```

You don't touch Python. Zack doesn't touch CSS. We meet at the JSON.

## Repo layout

```
Claudiac/
├── data/                          ECG sample file (Apple Watch format)
├── algorithms/                    Block 3 — pure Python, no web
│   ├── heart_rate.py              Pan-Tompkins QRS, returns HR + HRV
│   ├── mood.py                    Rule-based now, Claude API later
│   └── risk.py                    Anomaly score with explainable findings
├── server/
│   └── app.py                     Flask backend — exposes algorithms over HTTP
├── frontend/                      ← THIS IS YOUR FOLDER
│   ├── index.html                 (you create)
│   ├── style.css                  (you create)
│   └── app.js                     (you create)
├── run_demo.py                    Terminal-only end-to-end test
├── FRONTEND_API.md                API contract — your detailed reference
└── README_FOR_TEAMMATE.md         this file
```

Anything outside `frontend/` is Zack's. Anything inside `frontend/` is yours.
There is no shared code, no shared state, no shared dependency tree.
The only shared thing is the API contract in `FRONTEND_API.md`.

## How the backend works (1-minute mental model)

The backend is a Flask server. Flask = a Python library that lets
Python code respond to HTTP requests. When you fetch a URL like
`http://localhost:5000/api/analyze`, this is what happens server-side:

```
1. Your fetch() hits Flask
2. Flask calls heart_rate.pan_tompkins(ecg)        → HR, HRV, R-peaks
3. Flask calls mood.infer_mood(hr, hrv, ...)       → mood interpretation
4. Flask calls risk.compute_risk(hr, hrv, rr)      → anomaly score + findings
5. Flask packages all of that as JSON
6. Your fetch() resolves with that JSON
```

The whole thing runs locally on Zack's laptop during demo. Nothing is
deployed anywhere — `localhost:5000` is the production URL for this
hackathon.

## Running the backend

Zack will start the backend before you start coding. Once running, you
can verify it from your browser:

- Health check: <http://localhost:5000/api/health>
  → Returns `{"status": "ok", ...}`

- Full analysis: <http://localhost:5000/api/analyze>
  → Returns the full JSON payload (ECG samples, R-peaks, HR, HRV,
    mood, risk).

If those URLs work in your browser, your `fetch()` calls will work too.

## Where to put your code

Two options:

### Option A — Same origin (simplest, no CORS issues)

Put `index.html`, `style.css`, `app.js` in the `frontend/` folder.
Flask serves them at `http://localhost:5000/`. Your fetch URLs become
relative: `fetch("/api/analyze")`. Open the browser at
`http://localhost:5000`.

### Option B — Separate (your own dev server)

Open `frontend/index.html` directly with a VS Code Live Server or any
local dev server you prefer. Your fetch URLs need to be absolute:
`fetch("http://localhost:5000/api/analyze")`. CORS is already enabled
on the backend so this works.

Either is fine. Option A is the path of least resistance.

## What the API gives you

Three endpoints, fully documented in `FRONTEND_API.md`. Quick summary:

- `GET /api/health` — sanity check
- `GET /api/analyze` — full pipeline run, returns everything in one bundle
- `POST /api/mood` — re-run the mood inference with a custom self-report

The `GET /api/analyze` response is the main one. It contains, in a
single JSON object:

- The ECG waveform (downsampled to ~1500 points for fast plotting)
- The R-peak indices (for marking dots on the waveform)
- HR and HRV numbers
- Mood inference (with a `mismatch` boolean — the demo's hero moment)
- Risk score with human-readable findings

Full schema with example response is in `FRONTEND_API.md`.

## What the demo should communicate visually

The story we want the judges to feel, in order:

1. **"We did real signal processing."** Show the ECG waveform with
   R-peak dots overlaid. That's the proof we're not just calling
   ChatGPT on a number.

2. **"We see something the user can't."** When `mood.mismatch === true`,
   make it loud. The user said "calm." The body said "strain." Claudiac
   noticed the gap. This is the headline.

3. **"And we explain why, with care."** Show `mood.message` in a chat
   bubble. Show `mood.hypothesis` smaller, as the reasoning behind the
   message. Tone: a thoughtful friend, not a clinical alarm.

4. **"And we don't medicalize what isn't medical."** Show
   `risk.level === "normal"` clearly. The findings list reassures: HR
   normal, rhythm normal, no ectopy. The one caveat (HRV suppressed)
   is acknowledged but not alarming. This contrast — body strained but
   heart medically fine — is the whole point.

## Suggested layout

```
+-----------------------------------------------------+
|  CLAUDIAC                                            |
+-----------------------------------------------------+
|                            |                        |
|  ECG waveform              |  72 bpm   |  12 ms     |
|  (with red R-peak dots)    |  Heart    |  HRV       |
|                            |  rate     |  (SDNN)    |
|                            |                        |
|                            +------------------------+
|                            |  ⚠ MISMATCH DETECTED   |
|                            |                        |
|                            |  Body: subclinical     |
|                            |        strain          |
|                            |  You:  calm            |
|                            |                        |
|                            |  "Your body's showing  |
|                            |   early signs of       |
|                            |   stress even though   |
|                            |   you feel calm.       |
|                            |   Worth a 60-second    |
|                            |   breathing reset      |
|                            |   before the meeting?" |
|                            |                        |
|                            |  why: upcoming advisor |
|                            |  meeting; under-slept  |
|                            +------------------------+
|                            |  RISK   normal  0.13   |
|                            |  ✓ HR healthy          |
|                            |  ✓ Rhythm regular      |
|                            |  ✓ No ectopic beats    |
|                            |  ⚠ HRV suppressed      |
+----------------------------+------------------------+
```

This is a suggestion. You have full freedom to redesign — but please
keep the mismatch card as the visual hero.

## Libraries we're cool with

Anything CDN-loaded. Some that fit naturally:

- **Chart.js** — easiest line chart for the ECG. CDN one-liner.
- **uPlot** — faster than Chart.js for ~1500 points; nicer if you
  end up animating.
- **Plain SVG** — also great. The ECG is a simple polyline, drawing it
  by hand is ~10 lines of JS.
- **No framework** — please don't pull in React/Vue/Svelte. Zero benefit
  at this scale, and the tooling overhead will eat the timeline.

## What's deliberately not built

We made some 10-hour-window sacrifices you should know about, in case a
judge asks:

- **Real Apple Watch app** — we use a bundled ECG file shaped exactly
  like the HealthKit `HKElectrocardiogram` API output. The pipeline is
  real; the device is mocked.
- **Real cloud upload** — same, mocked as a file on disk.
- **Multi-day trend analysis** — risk.py is intentionally a
  single-window anomaly score. Multi-day burnout detection is on the
  roadmap slide, not in the demo.
- **Multiple scenarios** — first scenario is the mismatch case
  (the demo's hero moment). Adding normal/anomaly scenarios is a
  stretch goal if we have time.

## Claude API status

`mood.py` runs in two modes:

- **Rule-based** (current default): deterministic, no API calls.
  Works offline. The mismatch detection logic is real; the wording is
  hard-coded.
- **Claude API** (toggle later): same input/output, but Claude
  generates the `message` and `hypothesis` fields, giving more
  natural and context-sensitive prose.

When we flip the toggle, the JSON schema doesn't change — so the
frontend doesn't need any updates. The only visible difference will be
that `mood.mode` flips from `"rule_based"` to `"claude_api"` and the
messages get warmer.

## Communication norms

- **Don't wait on me to start.** The API is stable. Build against the
  example response in `FRONTEND_API.md` and we'll catch any
  discrepancies in 2 minutes when we wire up.

- **Use the browser DevTools.** F12 → Network tab → click any failed
  request → look at the Response. 90% of frontend-backend bugs solve
  themselves there.

- **If a field is missing or weird, tell me before working around it.**
  I'd rather fix the API than have you write client-side patches that
  we have to undo later.

- **One thing I will need from you, eventually:** when you're ~60%
  done, send me a screenshot of the current state. It tells me whether
  the API is shaped right for what you're doing.

## Quickstart

1. Pull the repo (Zack will share).
2. Read `FRONTEND_API.md`.
3. Make sure Zack's backend is running:
   <http://localhost:5000/api/health> works in your browser.
4. Create `frontend/index.html` and start hacking.

That's it. Ping me on Slack/Discord when you have questions or get
stuck. I'm here and the backend is stable — no reason for you to be
blocked.

— Zack
