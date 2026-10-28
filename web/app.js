/* StemLab — interface. Conversa com a API local e monta o player multipista. */
"use strict";

const $ = (id) => document.getElementById(id);

/* ============================================================ utilidades === */
const fmtTime = (s) => {
  if (!isFinite(s) || s < 0) s = 0;
  const m = Math.floor(s / 60);
  const sec = Math.floor(s % 60);
  return `${m}:${sec.toString().padStart(2, "0")}`;
};

const fmtBytes = (b) => {
  if (!b && b !== 0) return "";
  const u = ["B", "KB", "MB", "GB"];
  let i = 0, n = b;
  while (n >= 1024 && i < u.length - 1) { n /= 1024; i++; }
  return `${n.toFixed(n < 10 && i > 0 ? 1 : 0)} ${u[i]}`;
};

async function api(path, opts) {
  const res = await fetch(path, opts);
  const text = await res.text();
  let data = null;
  try { data = text ? JSON.parse(text) : null; } catch { data = text; }
  if (!res.ok) {
    const msg = (data && data.detail) || (typeof data === "string" ? data : res.statusText);
    throw new Error(msg);
  }
  return data;
}

const SPEED = { htdemucs: 1, htdemucs_ft: 4, htdemucs_6s: 1.15, hdemucs_mmi: 1,
                mdx_extra: .85, mdx_extra_q: .6, mdx_q: .45 };
const BAG = { htdemucs_ft: 4 };

function estimateSeconds(duration, model, shifts) {
  if (!duration) return null;
  const passes = (BAG[model] || 1) * (1 + (parseInt(shifts, 10) || 0));
  return Math.max(15, Math.round(duration * 0.45 * (SPEED[model] || 1) * passes));
}

/* ============================================================ estado ====== */
const state = {
  file: null,          // {file, name, bytes, duration}
  models: [],
  activeJobId: null,
  player: null,
  pollTimer: null,
  openedFromHash: false,
};

const jobIdFromHash = () => {
  const m = /#job=([A-Za-z0-9]+)/.exec(location.hash || "");
  return m ? m[1] : null;
};

const displayName = (job) => job.display_name || job.source_name;

/* ======================================================== sistema/modelos == */
async function loadSystem() {
  try {
    const s = await api("/api/system");
    const bits = [];
    bits.push(`${s.cpu_count} núcleos · ${s.device.toUpperCase()}`);
    bits.push(`torch ${s.torch || "?"}`);
    bits.push(s.ffmpeg ? "ffmpeg ok" : "⚠ ffmpeg ausente");
    $("sysinfo").innerHTML = bits.join("<br>");
  } catch (e) {
    $("sysinfo").textContent = "servidor indisponível";
  }
}

async function loadModels() {
  const data = await api("/api/models");
  state.models = data.models;
  const sel = $("model");
  sel.innerHTML = "";
  for (const m of state.models) {
    const opt = document.createElement("option");
    opt.value = m.id;
    opt.textContent = m.label + (m.tag ? ` — ${m.tag}` : "");
    sel.appendChild(opt);
  }
  sel.value = "htdemucs";
  updateModelNote();
}

function currentModel() {
  return state.models.find((m) => m.id === $("model").value);
}

function updateModelNote() {
  const m = currentModel();
  $("model-note").textContent = m ? m.note : "";
}

function updateEstimate() {
  const el = $("estimate");
  const m = currentModel();
  if (!state.file || !state.file.duration) { el.textContent = ""; return; }
  const secs = estimateSeconds(state.file.duration, $("model").value, $("shifts").value);
  if (!secs) { el.textContent = ""; return; }
  el.textContent = `estimativa: ~${fmtTime(secs)} (${state.file.duration ? fmtTime(state.file.duration) : "?"} de áudio)`;
}

/* ============================================================== upload ==== */
function setupUpload() {
  const dz = $("dropzone");
  const input = $("fileinput");

  $("pick").addEventListener("click", (e) => { e.stopPropagation(); input.click(); });
  dz.addEventListener("click", () => input.click());
  input.addEventListener("change", () => {
    if (input.files && input.files[0]) uploadFile(input.files[0]);
  });

  ["dragenter", "dragover"].forEach((ev) =>
    dz.addEventListener(ev, (e) => { e.preventDefault(); dz.classList.add("drag"); }));
  ["dragleave", "drop"].forEach((ev) =>
    dz.addEventListener(ev, (e) => { e.preventDefault(); dz.classList.remove("drag"); }));
  dz.addEventListener("drop", (e) => {
    const f = e.dataTransfer.files && e.dataTransfer.files[0];
    if (f) uploadFile(f);
  });

  $("fi-clear").addEventListener("click", () => {
    state.file = null;
    $("fileinfo").classList.add("hidden");
    $("start").disabled = true;
    updateEstimate();
  });
}

async function uploadFile(file) {
  const dz = $("dropzone");
  dz.classList.add("drag");
  const label = dz.querySelector("p strong");
  const old = label.textContent;
  label.textContent = `Enviando ${file.name}…`;

  try {
    const form = new FormData();
    form.append("file", file);
    const res = await api("/api/upload", { method: "POST", body: form });
    state.file = res;
    $("fi-name").textContent = res.name;
    $("fi-meta").textContent = `${fmtBytes(res.bytes)}${res.duration ? " · " + fmtTime(res.duration) : ""}`;
    $("fileinfo").classList.remove("hidden");
    $("start").disabled = false;
    updateEstimate();
  } catch (e) {
    alert("Falha no envio: " + e.message);
  } finally {
    label.textContent = old;
    dz.classList.remove("drag");
  }
}

/* ================================================================ jobs ==== */
async function startJob() {
  if (!state.file) return;
  const btn = $("start");
  btn.disabled = true;
  try {
    const job = await api("/api/jobs", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        file: state.file.file,
        model: $("model").value,
        format: $("format").value,
        mp3_bitrate: parseInt($("bitrate").value, 10),
        shifts: parseInt($("shifts").value, 10),
        overlap: parseFloat($("overlap").value),
        karaoke: $("karaoke").checked,
      }),
    });
    state.activeJobId = job.id;
    showActive(job);
    ensurePolling();
  } catch (e) {
    alert("Não foi possível iniciar: " + e.message);
  } finally {
    btn.disabled = false;
  }
}

function showActive(job) {
  $("active-card").classList.remove("hidden");
  $("ac-name").textContent = displayName(job);
  $("ac-model").textContent = job.model_label;
  setProgress(job);
}

function setProgress(job) {
  $("ac-bar").style.width = `${job.progress || 0}%`;
  $("ac-msg").textContent = job.message || "";
  const bits = [`${job.progress || 0}%`];
  if (job.estimate) bits.push(`estimativa ~${fmtTime(job.estimate)}`);
  if (job.status === "queued") bits.push("na fila");
  $("ac-time").textContent = bits.join(" · ");
}

/* ============================================================= polling ==== */
function ensurePolling() {
  if (state.pollTimer) return;
  state.pollTimer = setInterval(poll, 1200);
  poll();
}

async function poll() {
  let data;
  try {
    data = await api("/api/jobs");
  } catch { return; }

  const jobs = data.jobs || [];
  renderJobList(jobs);

  if (state.activeJobId) {
    const job = jobs.find((j) => j.id === state.activeJobId);
    if (!job) { state.activeJobId = null; $("active-card").classList.add("hidden"); }
    else if (job.status === "running" || job.status === "queued") {
      setProgress(job);
    } else if (job.status === "done") {
      state.activeJobId = null;
      $("active-card").classList.add("hidden");
      openPlayer(job);
    } else {
      state.activeJobId = null;
      $("active-card").classList.add("hidden");
      alert("A separação falhou:\n\n" + (job.error || job.message));
    }
  }

  if (state.player) {
    const pj = jobs.find((j) => j.id === state.player.jobId);
    if (!pj) closePlayer();
  } else if (!state.openedFromHash) {
    const wanted = jobIdFromHash();
    if (wanted) {
      const j = jobs.find((x) => x.id === wanted && x.status === "done");
      if (j) { state.openedFromHash = true; openPlayer(j); }
    }
  }
}

/* ============================================================ histórico === */
function renderJobList(jobs) {
  const box = $("joblist");
  if (!jobs.length) {
    box.innerHTML = '<p class="hint">Nenhum trabalho ainda.</p>';
    return;
  }
  box.innerHTML = "";
  for (const job of jobs) {
    const row = document.createElement("div");
    row.className = "jobrow";

    const statusLabel = {
      queued: "na fila", running: `${job.progress || 0}%`, done: "pronto",
      error: "erro", canceled: "cancelado",
    }[job.status] || job.status;

    row.innerHTML = `
      <div class="grow">
        <div class="name"></div>
        <div class="meta"></div>
      </div>
      <span class="status ${job.status}"></span>`;

    row.querySelector(".name").textContent = displayName(job);
    const when = new Date(job.created_at * 1000).toLocaleString("pt-BR");
    const bits = [job.model_label, when];
    if (job.duration) bits.push(fmtTime(job.duration));
    if (job.elapsed) bits.push(`levou ${fmtTime(job.elapsed)}`);
    row.querySelector(".meta").textContent = bits.join(" · ");
    row.querySelector(".status").textContent = statusLabel;

    const actions = document.createElement("div");
    actions.className = "actions";
    actions.style.margin = "0";

    if (job.status === "done") {
      actions.appendChild(btn("Ouvir", () => openPlayer(job), "primary"));
      actions.appendChild(link("Baixar .zip", `/api/jobs/${job.id}/zip`));
    }
    if (job.status === "running" || job.status === "queued") {
      actions.appendChild(btn("Cancelar", async () => {
        try { await api(`/api/jobs/${job.id}/cancel`, { method: "POST" }); } catch (e) { alert(e.message); }
      }, "danger"));
    }
    if (job.status === "error") {
      actions.appendChild(btn("Ver erro", () => alert(job.error || job.message)));
    }
    actions.appendChild(btn("Excluir", async () => {
      if (!confirm("Excluir este trabalho e todas as faixas geradas?")) return;
      try {
        await api(`/api/jobs/${job.id}`, { method: "DELETE" });
        if (state.player && state.player.jobId === job.id) closePlayer();
        poll();
      } catch (e) { alert(e.message); }
    }, "ghost"));

    row.appendChild(actions);
    box.appendChild(row);
  }
}

function btn(text, onClick, cls) {
  const b = document.createElement("button");
  b.type = "button";
  b.textContent = text;
  if (cls) b.className = cls;
  b.addEventListener("click", onClick);
  return b;
}

function link(text, href) {
  const a = document.createElement("a");
  a.className = "btn";
  a.href = href;
  a.textContent = text;
  return a;
}

/* =============================================================== player === */
async function openPlayer(job) {
  closePlayer();
  history.replaceState(null, "", `#job=${job.id}`);
  const card = $("player-card");
  card.classList.remove("hidden");
  $("pl-title").textContent = `${displayName(job).replace(/\.[^.]+$/, "")} — ${job.model_label}`;
  $("pl-zip").href = `/api/jobs/${job.id}/zip`;

  const stems = job.stems.map((s) => ({
    ...s,
    vol: 1,
    mute: false,
    solo: false,
    audio: null,
  }));

  const player = {
    jobId: job.id,
    stems,
    master: null,
    playing: false,
    syncTimer: null,
    metaReady: 0,
  };
  state.player = player;

  const box = $("stems");
  box.innerHTML = "";

  stems.forEach((s, i) => {
    const audio = new Audio(s.url);
    audio.preload = "auto";
    audio.volume = 1;
    s.audio = audio;
    s.audio.addEventListener("loadedmetadata", () => {
      player.metaReady++;
      if (i === 0) {
        $("pl-dur").textContent = fmtTime(audio.duration);
        player.duration = audio.duration;
      }
    });

    const row = document.createElement("div");
    row.className = "stem";

    const name = document.createElement("span");
    name.className = "stem-name";
    name.textContent = s.label || s.stem;

    const mBtn = document.createElement("button");
    mBtn.type = "button"; mBtn.className = "toggle"; mBtn.textContent = "M";
    mBtn.title = "Silenciar";

    const sBtn = document.createElement("button");
    sBtn.type = "button"; sBtn.className = "toggle solo"; sBtn.textContent = "S";
    sBtn.title = "Isolar (solo)";

    const vol = document.createElement("input");
    vol.type = "range"; vol.min = "0"; vol.max = "1"; vol.step = "0.01"; vol.value = "1";

    const dl = document.createElement("a");
    dl.className = "toggle"; dl.textContent = "⬇"; dl.title = "Baixar faixa";
    dl.href = `${s.url}?download=1`;

    const tag = document.createElement("span");
    tag.className = "tag";
    tag.textContent = fmtBytes(s.bytes);

    mBtn.addEventListener("click", () => {
      s.mute = !s.mute;
      mBtn.classList.toggle("on", s.mute);
      applyGains();
    });
    sBtn.addEventListener("click", () => {
      s.solo = !s.solo;
      sBtn.classList.toggle("on", s.solo);
      box.querySelectorAll(".stem").forEach((el, idx) => {
        el.classList.toggle("active", !!player.stems[idx].solo);
      });
      applyGains();
    });
    vol.addEventListener("input", () => {
      s.vol = parseFloat(vol.value);
      applyGains();
    });

    row.append(name, mBtn, sBtn, vol, tag, dl);
    box.appendChild(row);
  });

  player.master = stems[0].audio;
  player.master.addEventListener("timeupdate", () => {
    if (!player.duration) player.duration = player.master.duration || 0;
    $("pl-cur").textContent = fmtTime(player.master.currentTime);
    if (player.duration) {
      $("pl-seek").value = Math.round((player.master.currentTime / player.duration) * 1000);
    }
  });

  $("pl-play").textContent = "▶";
  $("pl-seek").value = 0;
  $("pl-cur").textContent = "0:00";

  drawWaveform(stems[0].url, $("wave"));
  card.scrollIntoView({ behavior: "smooth", block: "start" });
}

function applyGains() {
  const p = state.player;
  if (!p) return;
  const anySolo = p.stems.some((s) => s.solo);
  for (const s of p.stems) {
    if (!s.audio) continue;
    const audible = !s.mute && (!anySolo || s.solo);
    s.audio.volume = audible ? s.vol * parseFloat($("pl-vol").value) : 0;
  }
}

async function togglePlay() {
  const p = state.player;
  if (!p) return;
  if (p.playing) {
    p.stems.forEach((s) => s.audio.pause());
    p.playing = false;
    $("pl-play").textContent = "▶";
    if (p.syncTimer) { clearInterval(p.syncTimer); p.syncTimer = null; }
    return;
  }
  applyGains();
  const t = p.master.currentTime;
  p.stems.forEach((s) => { try { s.audio.currentTime = t; } catch {} });
  const results = await Promise.allSettled(p.stems.map((s) => s.audio.play()));
  if (results.every((r) => r.status === "rejected")) {
    alert("O navegador bloqueou a reprodução. Clique na página e tente de novo.");
    return;
  }
  p.playing = true;
  $("pl-play").textContent = "❚❚";

  p.syncTimer = setInterval(() => {
    if (!p.playing) return;
    const ref = p.master.currentTime;
    for (const s of p.stems) {
      if (s.audio === p.master) continue;
      if (Math.abs(s.audio.currentTime - ref) > 0.12) {
        try { s.audio.currentTime = ref; } catch {}
      }
    }
  }, 500);
}

function closePlayer() {
  const p = state.player;
  if (p) {
    p.stems.forEach((s) => { try { s.audio.pause(); s.audio.src = ""; } catch {} });
    if (p.syncTimer) clearInterval(p.syncTimer);
  }
  state.player = null;
  $("player-card").classList.add("hidden");
  if (jobIdFromHash()) history.replaceState(null, "", location.pathname);
}

/* ============================================================= waveform === */
async function drawWaveform(url, canvas) {
  const ctx2d = canvas.getContext("2d");
  const w = canvas.clientWidth || 900;
  const dpr = window.devicePixelRatio || 1;
  canvas.width = w * dpr;
  canvas.height = 90 * dpr;
  ctx2d.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx2d.clearRect(0, 0, w, 90);

  try {
    const res = await fetch(url);
    const buf = await res.arrayBuffer();
    const AC = window.AudioContext || window.webkitAudioContext;
    const ac = new AC();
    const decoded = await ac.decodeAudioData(buf);
    const data = decoded.getChannelData(0);
    const mid = 45, barW = 2, gap = 1;
    const cols = Math.floor(w / (barW + gap));
    const step = Math.max(1, Math.floor(data.length / cols));

    ctx2d.fillStyle = "rgba(74,222,128,.55)";
    for (let c = 0; c < cols; c++) {
      let peak = 0;
      const start = c * step;
      for (let i = 0; i < step; i += 8) {
        const v = Math.abs(data[start + i] || 0);
        if (v > peak) peak = v;
      }
      const h = Math.max(2, peak * 42);
      ctx2d.fillRect(c * (barW + gap), mid - h, barW, h * 2);
    }
    await ac.close();
  } catch (e) {
    ctx2d.fillStyle = "#93a1b5";
    ctx2d.font = "12px Segoe UI";
    ctx2d.fillText("não foi possível desenhar a forma de onda", 10, 48);
  }
}

/* ================================================================= init === */
function setupControls() {
  $("model").addEventListener("change", () => { updateModelNote(); updateEstimate(); });
  $("shifts").addEventListener("change", updateEstimate);
  $("format").addEventListener("change", () => {
    $("bitrate-field").style.display = $("format").value === "mp3" ? "" : "none";
  });
  $("start").addEventListener("click", startJob);

  $("ac-cancel").addEventListener("click", async () => {
    if (!state.activeJobId) return;
    try { await api(`/api/jobs/${state.activeJobId}/cancel`, { method: "POST" }); }
    catch (e) { alert(e.message); }
  });

  $("pl-play").addEventListener("click", togglePlay);
  $("pl-seek").addEventListener("input", () => {
    const p = state.player;
    if (!p || !p.duration) return;
    const t = (parseInt($("pl-seek").value, 10) / 1000) * p.duration;
    p.stems.forEach((s) => { try { s.audio.currentTime = t; } catch {} });
  });
  $("pl-vol").addEventListener("input", applyGains);
  $("pl-close").addEventListener("click", closePlayer);
  $("pl-loop").addEventListener("change", () => {
    const p = state.player;
    if (p) p.stems.forEach((s) => { s.audio.loop = $("pl-loop").checked; });
  });
  $("pl-folder").addEventListener("click", async () => {
    const p = state.player;
    if (!p) return;
    try { await api(`/api/jobs/${p.jobId}/reveal`, { method: "POST" }); }
    catch (e) { alert(e.message); }
  });

  $("clear-finished").addEventListener("click", async () => {
    try { await api("/api/jobs/clear-finished", { method: "POST" }); poll(); }
    catch (e) { alert(e.message); }
  });

  document.addEventListener("keydown", (e) => {
    if (e.target.tagName === "INPUT" || e.target.tagName === "SELECT") return;
    if (e.code === "Space" && state.player) { e.preventDefault(); togglePlay(); }
  });
}

(async function init() {
  setupUpload();
  setupControls();
  try {
    await loadModels();
    await loadSystem();
  } catch (e) {
    alert("Não foi possível falar com o servidor local:\n" + e.message);
  }
  ensurePolling();
})();