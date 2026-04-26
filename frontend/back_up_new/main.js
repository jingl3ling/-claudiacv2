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

let analyzeTimer = null;
let waveFetchTimer = null;
let lastWaveMtime = 0;
let lastBpm = null;
let attackTimer = null;
let attackPhase = false;
const STALE_AFTER_MS = 4000;
let lastGoodAt = 0;

// Scope state
const WINDOW_S = 2.0;
let fullSamples = [];
let originalSampleRate = 512;
let displayStep = 1;
let displaySampleRate = 512;
let playheadStartedAt = 0;
let scopeRafId = null;

// ----------------------------------------------------------------------
// utils
// ----------------------------------------------------------------------
function setError(message) {
  if (!message) { errorBoxEl.hidden = true; errorBoxEl.textContent = ""; return; }
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
  return clamp(60 / bpm, 0.33, 1.6);
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
// emotions (now 11)
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
  { key: "realtime_daq",   label: "Real-time DAQ",  swatch: "#C0C0C0", mode: "realtime" },
];

function renderDemoButtons() {
  demoButtonsEl.innerHTML = "";
  for (const emo of EMOTIONS) {
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "pill";
    if (emo.mode === "attack") btn.classList.add("pillAttack");
    if (emo.mode === "realtime") btn.classList.add("pillRealtime");
    btn.dataset.key = emo.key;
    btn.innerHTML = `<span class="name">${emo.label}</span><span class="swatch" aria-hidden="true"></span>`;
    const sw = /** @type {HTMLElement} */ (btn.querySelector(".swatch"));
    if (emo.mode === "attack") {
      sw.style.background = "linear-gradient(90deg, #ff0000, #000000)";
    } else if (emo.mode === "realtime") {
      sw.style.background = "linear-gradient(135deg, #ffffff, #888888)";
    } else {
      sw.style.background = emo.swatch;
    }
    btn.addEventListener("click", () => selectScenario(emo));
    demoButtonsEl.appendChild(btn);
  }
}

// ----------------------------------------------------------------------
// data-source badge — shown only for realtime_daq
// ----------------------------------------------------------------------
function renderDataSourceBadge(scenarioId, dataSource, fallbackReason) {
  let badge = document.getElementById("dataSourceBadge");
  if (!badge) {
    badge = document.createElement("div");
    badge.id = "dataSourceBadge";
    badge.className = "dataSourceBadge";
    classCardEl.parentElement?.insertBefore(badge, classCardEl);
  }

  if (scenarioId !== "realtime_daq") {
    badge.hidden = true;
    return;
  }
  badge.hidden = false;

  let label = "—";
  let cls = "src-unknown";
  switch (dataSource) {
    case "live":
      label = "🟢 LIVE — your real ECG, just captured";
      cls = "src-live";
      break;
    case "cached_fallback":
      label = "🟡 CACHED FALLBACK — live signal failed quality gate";
      cls = "src-fallback";
      break;
    case "cached_only":
      label = "🔵 CACHED ONLY — no live capture yet";
      cls = "src-cached";
      break;
    case "live_low_quality":
      label = "🟠 LIVE (low quality) — no fallback available";
      cls = "src-bad";
      break;
    default:
      label = `data source: ${dataSource}`;
  }

  badge.className = `dataSourceBadge ${cls}`;
  badge.innerHTML = `<span class="srcLabel">${label}</span>` +
    (fallbackReason ? `<span class="srcReason">${fallbackReason}</span>` : "");
}

// ----------------------------------------------------------------------
// classification + explanation rendering
// ----------------------------------------------------------------------
function renderClassification(payload) {
  if (!payload) return;
  const cls = payload.classification;
  const ki  = payload.key_information;
  if (!cls) return;

  classCardEl.hidden = false;
  classLabelEl.textContent = cls.category_label || "—";
  classLabelEl.style.color = cls.color || "var(--text)";
  if (cls.is_critical) classCardEl.classList.add("critical");
  else classCardEl.classList.remove("critical");

  classRulesEl.innerHTML = "";
  const rules = cls.rules_matched || {};
  for (const [k, v] of [["HR rule", rules.hr], ["HRV rule", rules.hrv], ["Amp rule", rules.amplitude]]) {
    if (!v) continue;
    const row = document.createElement("div");
    row.className = "ruleRow";
    row.innerHTML = `<span class="ruleK">${k}</span><span class="ruleV">${v}</span>`;
    classRulesEl.appendChild(row);
  }

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
// SCROLLING ECG SCOPE
// ----------------------------------------------------------------------
function startScopeAnimation() {
  if (scopeRafId !== null) return;
  playheadStartedAt = performance.now();
  const tick = (now) => { drawScrollingScope(now); scopeRafId = requestAnimationFrame(tick); };
  scopeRafId = requestAnimationFrame(tick);
}
function stopScopeAnimation() {
  if (scopeRafId !== null) cancelAnimationFrame(scopeRafId);
  scopeRafId = null;
}
function drawScrollingScope(nowMs) {
  const ctx = waveCanvas.getContext("2d");
  if (!ctx) return;
  const dpr = window.devicePixelRatio || 1;
  const rect = waveCanvas.getBoundingClientRect();
  const w = Math.max(1, Math.floor(rect.width * dpr));
  const h = Math.max(1, Math.floor(rect.height * dpr));
  if (waveCanvas.width !== w) waveCanvas.width = w;
  if (waveCanvas.height !== h) waveCanvas.height = h;

  ctx.clearRect(0, 0, w, h);
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
  if (!fullSamples.length) return;

  const totalSeconds = fullSamples.length / displaySampleRate;
  const elapsedMs = nowMs - playheadStartedAt;
  const elapsedS = (elapsedMs / 1000) % totalSeconds;
  const playheadIdx = Math.floor(elapsedS * displaySampleRate);

  const windowSamples = Math.max(2, Math.floor(WINDOW_S * displaySampleRate));
  const startIdx = playheadIdx - windowSamples;

  const visible = new Array(windowSamples);
  for (let i = 0; i < windowSamples; i++) {
    let idx = startIdx + i;
    while (idx < 0) idx += fullSamples.length;
    while (idx >= fullSamples.length) idx -= fullSamples.length;
    visible[i] = fullSamples[idx];
  }

  let minV = Infinity, maxV = -Infinity;
  for (const v of visible) { if (v < minV) minV = v; if (v > maxV) maxV = v; }
  if (!Number.isFinite(minV) || !Number.isFinite(maxV)) { minV = -1; maxV = 1; }
  const span = Math.max(maxV - minV, 200);
  const center = (maxV + minV) / 2;
  minV = center - span / 2; maxV = center + span / 2;
  const pad = (maxV - minV) * 0.10;
  minV -= pad; maxV += pad;

  const accent = getComputedStyle(document.documentElement).getPropertyValue("--accent").trim() || "#ff2d55";
  ctx.strokeStyle = accent;
  ctx.lineWidth = Math.max(1.5, Math.round(1.8 * dpr));
  ctx.lineJoin = "round";
  ctx.lineCap = "round";

  const n = visible.length;
  const xScale = (w - 2) / Math.max(1, n - 1);
  const yScale = (h - 2) / (maxV - minV);

  ctx.beginPath();
  for (let i = 0; i < n; i++) {
    const x = 1 + i * xScale;
    const y = 1 + (maxV - visible[i]) * yScale;
    if (i === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y);
  }
  ctx.stroke();

  // REC marker + time tag
  ctx.fillStyle = "rgba(255, 68, 68, 0.85)";
  ctx.beginPath();
  ctx.arc(14 * dpr, 14 * dpr, 4 * dpr, 0, 2 * Math.PI);
  ctx.fill();
  ctx.fillStyle = "rgba(255,255,255,0.7)";
  ctx.font = `${11 * dpr}px ui-monospace, "SF Mono", Menlo, monospace`;
  ctx.fillText("LIVE", 24 * dpr, 18 * dpr);
  const tagText = `t = ${elapsedS.toFixed(1)}s / ${totalSeconds.toFixed(0)}s`;
  ctx.fillText(tagText, w - (12 * dpr) - ctx.measureText(tagText).width, 18 * dpr);
}

// ----------------------------------------------------------------------
// API: select scenario
// ----------------------------------------------------------------------
async function selectScenario(emo) {
  stopAttackMode();
  if (emo.mode === "attack") startAttackMode();
  else if (emo.mode === "realtime") setThemeAccent("#a0a0a0");
  else setThemeAccent(emo.swatch);

  const realtime = emo.mode === "realtime";
  setStatus(realtime ? "loading live capture…" : "loading scenario…", "warn");
  setExplanationLoading(realtime
    ? "🤖 Analyzing your live ECG with Claude…"
    : "🤖 Asking Claude for explanation…");

  const baseUrl = baseUrlEl.value.trim().replace(/\/+$/, "");
  if (!baseUrl) { setError("Please enter the backend base URL."); return; }

  try {
    const res = await fetch(`${baseUrl}/api/scenario/select`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ scenario_id: emo.key }),
    });
    if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
    const data = await res.json();

    if (!data.ok) {
      setError(data.error || "Selection failed");
      setStatus("error", "warn");
      renderDataSourceBadge(emo.key, data.data_source, data.fallback_reason);
      return;
    }

    setError("");
    setStatus(`scenario: ${emo.label}`, "ok");

    // For realtime DAQ, the theme accent comes from the actual classification
    // (which we don't know until the server returns). Apply it now.
    if (emo.mode === "realtime" && data?.classification?.color) {
      // is_critical means heart-attack; flash mode handles its own colors
      if (data.classification.is_critical) {
        startAttackMode();
      } else {
        setThemeAccent(data.classification.color);
      }
    }

    const bpm = data?.heart_rate?.bpm;
    if (Number.isFinite(bpm) && bpm > 0) {
      applyBpm(bpm, `scenario: ${emo.label}`);
      lastBpm = bpm;
    } else {
      bpmValueEl.textContent = "—";
    }

    renderClassification(data);
    renderExplanation(data);
    renderDataSourceBadge(emo.key, data.data_source, data.fallback_reason);

    if (data?.ecg && Array.isArray(data.ecg.samples_uV)) {
      adoptWaveform(data.ecg);
    } else {
      void fetchWaveformOnce(true);
    }
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
    const bpm = (rawBpm === null || rawBpm === undefined) ? Number.NaN : Number(rawBpm);

    setError("");

    if (Number.isFinite(bpm) && bpm > 0) {
      setStatus("connected", "ok");
      const label = data?.scenario?.label || `source=${source}`;
      applyBpm(bpm, label);
      lastBpm = bpm;
      lastGoodAt = Date.now();
      // For realtime DAQ, apply the classification's color as theme
      if (data?.scenario?.is_realtime && data?.classification?.color
          && !data.classification.is_critical) {
        setThemeAccent(data.classification.color);
      }
      renderClassification(data);
      renderExplanation(data);
      renderDataSourceBadge(data?.scenario?.id, data?.data_source, data?.fallback_reason);
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
function adoptWaveform(ecgPayload) {
  if (!ecgPayload || !Array.isArray(ecgPayload.samples_uV)) return;
  const samples = ecgPayload.samples_uV;
  const fs = Number(ecgPayload.sampling_rate);
  const step = Number(ecgPayload.display_step) || 1;
  const dur = Number(ecgPayload.duration_s);
  const n0 = Number(ecgPayload.n_original_samples);

  fullSamples = samples;
  originalSampleRate = Number.isFinite(fs) && fs > 0 ? fs : 512;
  displayStep = step > 0 ? step : 1;
  displaySampleRate = originalSampleRate / displayStep;

  playheadStartedAt = performance.now();

  waveInfoTextEl.textContent =
    `${Number.isFinite(dur) ? dur.toFixed(1) : "—"}s @ ${Number.isFinite(fs) ? fs.toFixed(0) : "—"}Hz` +
    (Number.isFinite(n0) ? ` • ${n0} samples` : "") +
    `  •  scope window ${WINDOW_S}s`;

  startScopeAnimation();
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
      if (res.status === 404 || res.status === 503) {
        waveInfoTextEl.textContent = "no live capture yet";
        fullSamples = [];
        return;
      }
      throw new Error(`${res.status} ${res.statusText}`);
    }
    const data = await res.json();
    const mtime = Number(data?.mtime ?? 0);
    if (!force && mtime && mtime === lastWaveMtime) return;
    lastWaveMtime = mtime || lastWaveMtime;

    adoptWaveform({
      samples_uV: Array.isArray(data?.samples) ? data.samples : [],
      sampling_rate: data?.fs,
      display_step: data?.display_step,
      duration_s: data?.duration_s,
      n_original_samples: data?.n_original_samples,
    });
  } catch {
    waveInfoTextEl.textContent = "waveform unavailable";
  }
}

// ----------------------------------------------------------------------
// connect / boot
// ----------------------------------------------------------------------
function connect() {
  if (analyzeTimer) window.clearInterval(analyzeTimer);
  analyzeTimer = null;
  if (waveFetchTimer) window.clearInterval(waveFetchTimer);
  waveFetchTimer = null;
  stopAttackMode();

  setStatus("connecting…", "muted");
  setError("");
  setIdle(true);
  lastGoodAt = 0;
  lastBpm = null;

  void fetchAnalyzeOnce();
  analyzeTimer = window.setInterval(fetchAnalyzeOnce, 1200);
  void fetchWaveformOnce(true);
  waveFetchTimer = window.setInterval(fetchWaveformOnce, 1500);
}

connectBtn.addEventListener("click", connect);
baseUrlEl.addEventListener("keydown", (e) => { if (e.key === "Enter") connect(); });
waveRefreshBtn.addEventListener("click", () => void fetchWaveformOnce(true));
for (const el of [ecgSourceEl, deviceIdEl]) {
  el.addEventListener("change", () => connect());
}

renderDemoButtons();
connect();
