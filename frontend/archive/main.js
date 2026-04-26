const $ = (id) => /** @type {HTMLElement} */ (document.getElementById(id));

const baseUrlEl = /** @type {HTMLInputElement} */ ($("baseUrl"));
const connectBtn = /** @type {HTMLButtonElement} */ ($("connectBtn"));
const ecgSourceEl = /** @type {HTMLSelectElement} */ ($("ecgSource"));
const deviceIdEl = /** @type {HTMLInputElement} */ ($("deviceId"));

const bpmValueEl = $("bpmValue");
const statusTextEl = $("statusText");
const sourceTextEl = $("sourceText");
const errorBoxEl = $("errorBox");
const heartEl = $("heart");
const glowEl = $("glow");
const demoButtonsEl = $("demoButtons");
const waveCanvas = /** @type {HTMLCanvasElement} */ ($("waveCanvas"));
const waveRefreshBtn = /** @type {HTMLButtonElement} */ ($("waveRefreshBtn"));
const waveInfoTextEl = $("waveInfoText");

// New elements added to index.html for classification + explanation
const classCardEl = $("classCard");
const classLabelEl = $("classLabel");
const classRulesEl = $("classRules");
const classFeaturesEl = $("classFeatures");
const explCardEl = $("explCard");
const explTextEl = $("explText");
const explSourceEl = $("explSource");

const stop0 = /** @type {SVGStopElement} */ (document.getElementById("hgStop0"));
const stop1 = /** @type {SVGStopElement} */ (document.getElementById("hgStop1"));
const stop2 = /** @type {SVGStopElement} */ (document.getElementById("hgStop2"));

let timer = null;
let waveTimer = null;
let lastWaveMtime = 0;
let lastBpm = null;

let attackTimer = null;
let attackPhase = false;

const STALE_AFTER_MS = 4000;
let lastGoodAt = 0;

// ----------------------------------------------------------------------
// utils
// ----------------------------------------------------------------------
function setError(message) {
  if (!message) {
    errorBoxEl.hidden = true;
    errorBoxEl.textContent = "";
    return;
  }
  errorBoxEl.hidden = false;
  errorBoxEl.textContent = message;
}

function setStatus(text, kind = "muted") {
  statusTextEl.textContent = text;
  statusTextEl.style.color =
    kind === "ok" ? "var(--ok)" : kind === "warn" ? "var(--warn)" : "var(--muted)";
}

function setIdle(isIdle) {
  const v = isIdle ? "idle" : "live";
  heartEl.dataset.state = v;
  glowEl.dataset.state = v;
}

function clamp(n, a, b) { return Math.max(a, Math.min(b, n)); }

function bpmToBeatSeconds(bpm) {
  const seconds = 60 / bpm;
  return clamp(seconds, 0.33, 1.6);
}

function applyBpm(bpm, sourceLabel) {
  bpmValueEl.textContent = String(Math.round(bpm));
  sourceTextEl.textContent = sourceLabel || "—";
  document.documentElement.style.setProperty("--beat", `${bpmToBeatSeconds(bpm)}s`);
  setIdle(false);
}

function stopAttackMode() {
  document.documentElement.dataset.demo = "";
  if (attackTimer) window.clearInterval(attackTimer);
  attackTimer = null;
  attackPhase = false;
}

function setThemeAccent(hex) {
  document.documentElement.style.setProperty("--accent", hex);
  document.documentElement.style.setProperty("--accentSoft", `${hex}52`);
  stop0.setAttribute("stop-color", "#ffffff");
  stop1.setAttribute("stop-color", hex);
  stop2.setAttribute("stop-color", hex);
}

function startAttackMode() {
  stopAttackMode();
  document.documentElement.dataset.demo = "heart-attack";
  attackTimer = window.setInterval(() => {
    attackPhase = !attackPhase;
    const c = attackPhase ? "#000000" : "#ff0000";
    stop0.setAttribute("stop-color", c);
    stop1.setAttribute("stop-color", c);
    stop2.setAttribute("stop-color", c);
    document.documentElement.style.setProperty("--accent", "#ff0000");
    document.documentElement.style.setProperty("--accentSoft", "rgba(255,0,0,0.42)");
  }, 180);
}

// ----------------------------------------------------------------------
// emotions / button row
// ----------------------------------------------------------------------
const EMOTIONS = [
  { key: "heart_attack",   label: "Heart Attack",   swatch: "#ff0000", mode: "attack" },
  { key: "anger",          label: "Anger",          swatch: "#FF0000" },
  { key: "anxiety",        label: "Anxiety",        swatch: "#FF8C00" },
  { key: "fear",           label: "Fear",           swatch: "#8A2BE2" },
  { key: "joy",            label: "Joy",            swatch: "#FFD700" },
  { key: "envy",           label: "Envy",           swatch: "#00CED1" },
  { key: "disgust",        label: "Disgust",        swatch: "#32CD32" },
  { key: "embarrassment",  label: "Embarrassment",  swatch: "#FF69B4" },
  { key: "ennui",          label: "Ennui",          swatch: "#3A3B5C" },
  { key: "sadness",        label: "Sadness",        swatch: "#4169E1" },
];

function renderDemoButtons() {
  demoButtonsEl.innerHTML = "";
  for (const emo of EMOTIONS) {
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "pill";
    btn.dataset.key = emo.key;
    btn.innerHTML = `<span class="name">${emo.label}</span><span class="swatch" aria-hidden="true"></span>`;
    const sw = /** @type {HTMLElement} */ (btn.querySelector(".swatch"));
    sw.style.background = emo.mode === "attack"
      ? "linear-gradient(90deg, #ff0000, #000000)"
      : emo.swatch;
    btn.addEventListener("click", () => selectScenario(emo));
    demoButtonsEl.appendChild(btn);
  }
}

// ----------------------------------------------------------------------
// classification + explanation rendering
// ----------------------------------------------------------------------
function renderClassification(payload) {
  if (!payload) return;
  const cls = payload.classification;
  const ki  = payload.key_information;
  if (!cls) return;

  // Card visible
  classCardEl.hidden = false;
  classLabelEl.textContent = cls.category_label || "—";
  classLabelEl.style.color = cls.color || "var(--text)";

  // critical = heart attack
  if (cls.is_critical) {
    classCardEl.classList.add("critical");
  } else {
    classCardEl.classList.remove("critical");
  }

  // Rules matched
  classRulesEl.innerHTML = "";
  const rules = cls.rules_matched || {};
  const ruleRows = [
    ["HR rule",  rules.hr],
    ["HRV rule", rules.hrv],
    ["Amp rule", rules.amplitude],
  ];
  for (const [k, v] of ruleRows) {
    if (!v) continue;
    const row = document.createElement("div");
    row.className = "ruleRow";
    row.innerHTML = `<span class="ruleK">${k}</span><span class="ruleV">${v}</span>`;
    classRulesEl.appendChild(row);
  }

  // Features (HR / HRV / Amp with bands)
  classFeaturesEl.innerHTML = "";
  if (ki && cls.features) {
    const feats = [
      [`HR`, `${ki.heart_rate_bpm} bpm`, cls.features.hr_band],
      [`HRV`, `${ki.hrv_sdnn_ms} ms`, cls.features.hrv_band],
      [`Peak`, `${Math.round(ki.peak_amplitude_uV)} uV`, cls.features.amplitude_band],
      [`Beats`, `${ki.n_beats_detected}`, "30s"],
    ];
    for (const [k, v, band] of feats) {
      const chip = document.createElement("div");
      chip.className = `featChip band-${band}`;
      chip.innerHTML = `<span class="featK">${k}</span><span class="featV">${v}</span><span class="featBand">${band}</span>`;
      classFeaturesEl.appendChild(chip);
    }
  }
}

function renderExplanation(payload) {
  if (!payload) return;
  const expl = payload.explanation;
  if (!expl) return;
  explCardEl.hidden = false;
  explTextEl.textContent = expl.text || "";
  explSourceEl.textContent = expl.source || "";
}

function setExplanationLoading(label) {
  explCardEl.hidden = false;
  explTextEl.textContent = label || "🤖 Reasoning…";
  explSourceEl.textContent = "";
}

// ----------------------------------------------------------------------
// API: select scenario (immediate render + cache invalidation)
// ----------------------------------------------------------------------
async function selectScenario(emo) {
  // Stop the attack flicker before re-applying (will restart if needed)
  stopAttackMode();

  // Theme swatch + heart-attack visual
  if (emo.mode === "attack") {
    startAttackMode();
  } else {
    setThemeAccent(emo.swatch);
  }
  setStatus("loading scenario…", "warn");
  setExplanationLoading("🤖 Asking Claude for explanation…");

  const baseUrl = baseUrlEl.value.trim().replace(/\/+$/, "");
  if (!baseUrl) {
    setError("Please enter the backend base URL.");
    return;
  }

  try {
    const res = await fetch(`${baseUrl}/api/scenario/select`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ scenario_id: emo.key }),
    });
    if (!res.ok) {
      throw new Error(`${res.status} ${res.statusText}`);
    }
    const data = await res.json();

    setError("");
    setStatus(`scenario: ${emo.label}`, "ok");

    const bpm = data?.heart_rate?.bpm;
    if (Number.isFinite(bpm) && bpm > 0) {
      applyBpm(bpm, `scenario: ${emo.label}`);
      lastBpm = bpm;
    } else {
      bpmValueEl.textContent = "—";
    }

    renderClassification(data);
    renderExplanation(data);

    // Force-refresh the waveform now (don't wait for poll)
    void fetchWaveformOnce(true);
    lastGoodAt = Date.now();
  } catch (err) {
    setError(`Selection failed: ${err.message}`);
    setStatus("error", "warn");
  }
}

// ----------------------------------------------------------------------
// API: poll /api/analyze
// ----------------------------------------------------------------------
async function fetchAnalyzeOnce() {
  const baseUrl = baseUrlEl.value.trim().replace(/\/+$/, "");
  if (!baseUrl) {
    setStatus("missing config", "warn");
    setError("Please enter the backend base URL (e.g. http://localhost:5000).");
    setIdle(true);
    return;
  }
  const source = ecgSourceEl.value;
  const deviceId = deviceIdEl.value.trim();

  const url = new URL(`${baseUrl}/api/analyze`);
  url.searchParams.set("source", source);
  if (source === "upload") url.searchParams.set("deviceId", deviceId);

  try {
    const res = await fetch(url.toString(), { cache: "no-store" });
    if (!res.ok) {
      const bodyText = await res.text().catch(() => "");
      throw new Error(`${res.status} ${res.statusText}${bodyText ? ` — ${bodyText}` : ""}`);
    }
    const data = await res.json();
    const rawBpm = data?.heart_rate?.bpm;
    const bpm = (rawBpm === null || rawBpm === undefined)
      ? Number.NaN : Number(rawBpm);

    setError("");

    if (Number.isFinite(bpm) && bpm > 0) {
      setStatus("connected", "ok");
      const label = data?.scenario?.label || `source=${source}`;
      applyBpm(bpm, label);
      lastBpm = bpm;
      lastGoodAt = Date.now();

      // Render classification + explanation from polled data.
      renderClassification(data);
      renderExplanation(data);
      return;
    }

    bpmValueEl.textContent = "—";
    sourceTextEl.textContent = "connected (no bpm yet)";
    setStatus("connected (analyzing…)", "warn");
    setIdle(true);
    lastBpm = null;
    lastGoodAt = Date.now();
  } catch (err) {
    const msg = err instanceof Error ? err.message : String(err);
    const now = Date.now();
    const isStale = !lastGoodAt || now - lastGoodAt > STALE_AFTER_MS;
    setStatus("reconnecting…", "warn");
    setError(`Backend unavailable.\n${msg}`);
    if (isStale && typeof lastBpm !== "number") {
      setIdle(true);
      sourceTextEl.textContent = "—";
      return;
    }
    if (typeof lastBpm === "number") {
      document.documentElement.style.setProperty("--beat", `${bpmToBeatSeconds(lastBpm)}s`);
      setIdle(false);
    } else {
      setIdle(true);
    }
  }
}

// ----------------------------------------------------------------------
// API: waveform
// ----------------------------------------------------------------------
function drawWaveform(samples) {
  const ctx = waveCanvas.getContext("2d");
  if (!ctx) return;
  const dpr = window.devicePixelRatio || 1;
  const rect = waveCanvas.getBoundingClientRect();
  const w = Math.max(1, Math.floor(rect.width * dpr));
  const h = Math.max(1, Math.floor(rect.height * dpr));
  if (waveCanvas.width !== w) waveCanvas.width = w;
  if (waveCanvas.height !== h) waveCanvas.height = h;

  ctx.clearRect(0, 0, w, h);
  ctx.globalAlpha = 1;
  ctx.strokeStyle = "rgba(255,255,255,0.08)";
  ctx.lineWidth = 1;
  for (let i = 1; i < 10; i++) {
    const x = Math.round((w * i) / 10) + 0.5;
    ctx.beginPath(); ctx.moveTo(x, 0); ctx.lineTo(x, h); ctx.stroke();
  }
  for (let i = 1; i < 6; i++) {
    const y = Math.round((h * i) / 6) + 0.5;
    ctx.beginPath(); ctx.moveTo(0, y); ctx.lineTo(w, y); ctx.stroke();
  }
  if (!samples?.length) return;

  let min = Infinity, max = -Infinity;
  for (const v of samples) { if (v < min) min = v; if (v > max) max = v; }
  if (!Number.isFinite(min) || !Number.isFinite(max) || min === max) { min = -1; max = 1; }
  const pad = (max - min) * 0.1;
  min -= pad; max += pad;

  const accent = getComputedStyle(document.documentElement).getPropertyValue("--accent").trim() || "#ff2d55";
  ctx.strokeStyle = accent;
  ctx.lineWidth = Math.max(1, Math.round(1.5 * dpr));
  ctx.lineJoin = "round";
  ctx.lineCap = "round";

  const n = samples.length;
  const xScale = (w - 2) / Math.max(1, n - 1);
  const yScale = (h - 2) / (max - min);

  ctx.beginPath();
  for (let i = 0; i < n; i++) {
    const x = 1 + i * xScale;
    const y = 1 + (max - samples[i]) * yScale;
    if (i === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y);
  }
  ctx.stroke();
}

async function fetchWaveformOnce(force = false) {
  const baseUrl = baseUrlEl.value.trim().replace(/\/+$/, "");
  if (!baseUrl) return;
  const source = ecgSourceEl.value;
  const deviceId = deviceIdEl.value.trim();

  try {
    const waveUrl = new URL(`${baseUrl}/api/waveform`);
    waveUrl.searchParams.set("source", source);
    if (source === "upload") waveUrl.searchParams.set("deviceId", deviceId);

    const res = await fetch(waveUrl.toString(), { cache: "no-store" });
    if (!res.ok) {
      if (res.status === 404) {
        waveInfoTextEl.textContent = "no live capture yet";
        drawWaveform([]);
        return;
      }
      throw new Error(`${res.status} ${res.statusText}`);
    }
    const data = await res.json();
    const mtime = Number(data?.mtime ?? 0);
    if (!force && mtime && mtime === lastWaveMtime) return;
    lastWaveMtime = mtime || lastWaveMtime;

    const fs = Number(data?.fs);
    const n0 = Number(data?.n_original_samples);
    const dur = Number(data?.duration_s);
    waveInfoTextEl.textContent =
      `${Number.isFinite(dur) ? dur.toFixed(1) : "—"}s @ ${Number.isFinite(fs) ? fs.toFixed(0) : "—"}Hz` +
      (Number.isFinite(n0) ? ` • ${n0} samples` : "");

    drawWaveform(Array.isArray(data?.samples) ? data.samples : []);
  } catch {
    waveInfoTextEl.textContent = "waveform unavailable";
  }
}

// ----------------------------------------------------------------------
// connect / boot
// ----------------------------------------------------------------------
function connect() {
  if (timer) window.clearInterval(timer);
  timer = null;
  if (waveTimer) window.clearInterval(waveTimer);
  waveTimer = null;
  stopAttackMode();

  setStatus("connecting…", "muted");
  setError("");
  setIdle(true);
  lastGoodAt = 0;
  lastBpm = null;

  void fetchAnalyzeOnce();
  timer = window.setInterval(fetchAnalyzeOnce, 1200);

  void fetchWaveformOnce(true);
  waveTimer = window.setInterval(fetchWaveformOnce, 1500);
}

connectBtn.addEventListener("click", connect);
baseUrlEl.addEventListener("keydown", (e) => { if (e.key === "Enter") connect(); });
waveRefreshBtn.addEventListener("click", () => void fetchWaveformOnce(true));
for (const el of [ecgSourceEl, deviceIdEl]) {
  el.addEventListener("change", () => connect());
}

renderDemoButtons();
connect();
