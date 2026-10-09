// liveqna frontend: single WS to the ui backend. The ui backend relays
// transcript events from stt and brokers configure/start/stop calls to
// capture and stt.

const sourceSelect = document.getElementById("source-select");
const backendSelect = document.getElementById("backend-select");
const transcriptEl = document.getElementById("transcript");
const connEl = document.getElementById("conn");
const statusEl = document.getElementById("status");
const groupLocal = document.getElementById("group-local");
const groupOnline = document.getElementById("group-online");
const levelDot = document.getElementById("level-dot");
const levelBarFill = document.getElementById("level-bar-fill");
const levelLabel = document.getElementById("level-label");

let ws = null;
let levelResetTimer = null;

// segment_id -> DOM text element for that utterance, so a later event for
// the same segment_id updates in place rather than appending a new line.
const utteranceEls = new Map();

function setStatus(message, ok = true) {
  const span = document.createElement("div");
  span.className = ok ? "ok" : "bad";
  span.textContent = message;
  statusEl.prepend(span);
  while (statusEl.childNodes.length > 6) statusEl.removeChild(statusEl.lastChild);
}

function updateBackendFields() {
  const backend = backendSelect.value;
  groupLocal.classList.toggle("active", backend === "local");
  groupOnline.classList.toggle("active", backend !== "local");
}
backendSelect.addEventListener("change", updateBackendFields);
updateBackendFields();

function renderSources(sources) {
  sourceSelect.innerHTML = "";
  for (const s of sources) {
    const opt = document.createElement("option");
    opt.value = JSON.stringify({ node: s.node_name, kind: s.kind });
    opt.textContent = `${s.description} (${s.kind})`;
    sourceSelect.appendChild(opt);
  }
}

function appendTranscriptEvent(ev) {
  const segId = ev.segment_id;
  let textEl = segId != null ? utteranceEls.get(segId) : null;

  if (!textEl) {
    textEl = document.createElement("div");
    textEl.className = "utterance-text";
    transcriptEl.appendChild(textEl);
    if (segId != null) utteranceEls.set(segId, textEl);
  }

  textEl.classList.remove("final", "partial");
  textEl.classList.add(ev.type === "partial" ? "partial" : "final");
  textEl.textContent = ev.text;

  transcriptEl.scrollTop = transcriptEl.scrollHeight;
}

function updateLevel(level) {
  const pct = Math.max(0, Math.min(1, level));
  levelBarFill.style.width = `${(pct * 100).toFixed(1)}%`;
  const isLive = pct > 0.02;
  levelDot.classList.toggle("live", isLive);
  levelLabel.textContent = isLive ? `${Math.round(pct * 100)}%` : "no signal";

  // If no level event arrives for a while (capture stopped / dropped),
  // fall back to showing "no signal" rather than a stuck reading.
  if (levelResetTimer) clearTimeout(levelResetTimer);
  levelResetTimer = setTimeout(() => {
    levelBarFill.style.width = "0%";
    levelDot.classList.remove("live");
    levelLabel.textContent = "no signal";
  }, 2000);
}

function connect() {
  const proto = location.protocol === "https:" ? "wss:" : "ws:";
  ws = new WebSocket(`${proto}//${location.host}/ws/client`);

  ws.onopen = () => {
    connEl.textContent = "connected";
    connEl.className = "connected";
  };
  ws.onclose = () => {
    connEl.textContent = "disconnected";
    connEl.className = "disconnected";
    setTimeout(connect, 1500);
  };
  ws.onerror = () => ws.close();

  ws.onmessage = (event) => {
    const msg = JSON.parse(event.data);
    switch (msg.kind) {
      case "sources":
        renderSources(msg.sources);
        break;
      case "transcript":
        appendTranscriptEvent(msg);
        break;
      case "level":
        updateLevel(msg.level ?? 0);
        break;
      case "config":
        applyConfigToForm(msg.config);
        break;
      case "capture_configured":
        setStatus("Capture configured");
        break;
      case "model_configured":
        setStatus(`Model configured: ${JSON.stringify(msg.result)}`);
        break;
      case "capture_started":
        setStatus("Capture started");
        break;
      case "capture_stopped":
        setStatus("Capture stopped");
        break;
      case "error":
        setStatus(msg.message, false);
        break;
      default:
        break;
    }
  };
}

function applyConfigToForm(config) {
  if (!config) return;
  if (config.capture) {
    document.getElementById("sample-rate").value = config.capture.sample_rate ?? 16000;
  }
  if (config.model) {
    backendSelect.value = config.model.backend ?? "local";
    document.getElementById("model-size").value = config.model.model_size ?? "small";
    document.getElementById("compute-type").value = config.model.compute_type ?? "int8_float32";
    document.getElementById("language").value = config.model.language ?? "en";
    document.getElementById("vad-aggr").value = config.model.vad_aggressiveness ?? 2;
    document.getElementById("silence-ms").value = config.model.silence_duration_ms ?? 700;
    document.getElementById("min-speech-ms").value = config.model.min_speech_ms ?? 250;
    document.getElementById("max-segment-ms").value = config.model.max_segment_ms ?? 12000;
    updateBackendFields();
  }
}

function send(action, extra = {}) {
  if (!ws || ws.readyState !== WebSocket.OPEN) {
    setStatus("Not connected to server", false);
    return;
  }
  ws.send(JSON.stringify({ action, ...extra }));
}

document.getElementById("refresh-sources").addEventListener("click", () => {
  send("refresh_sources");
});

document.getElementById("start-btn").addEventListener("click", () => {
  const selected = sourceSelect.value;
  if (!selected) {
    setStatus("No source selected", false);
    return;
  }
  const { node, kind } = JSON.parse(selected);
  send("update_capture_config", {
    config: {
      source_node: node,
      source_kind: kind,
      sample_rate: parseInt(document.getElementById("sample-rate").value, 10),
    },
  });
  setTimeout(() => send("start"), 300);
});

document.getElementById("stop-btn").addEventListener("click", () => {
  send("stop");
});

document.getElementById("clear-btn").addEventListener("click", () => {
  transcriptEl.innerHTML = "";
  utteranceEls.clear();
});

document.getElementById("apply-model").addEventListener("click", () => {
  const backend = backendSelect.value;
  const config = {
    backend,
    language: document.getElementById("language").value || null,
    sample_rate: parseInt(document.getElementById("sample-rate").value, 10),
  };
  if (backend === "local") {
    config.model_size = document.getElementById("model-size").value;
    config.compute_type = document.getElementById("compute-type").value;
    config.vad_aggressiveness = parseInt(document.getElementById("vad-aggr").value, 10);
    config.silence_duration_ms = parseInt(document.getElementById("silence-ms").value, 10);
    config.min_speech_ms = parseInt(document.getElementById("min-speech-ms").value, 10);
    config.max_segment_ms = parseInt(document.getElementById("max-segment-ms").value, 10);
  } else {
    config.api_key = document.getElementById("api-key").value || null;
    config.model = document.getElementById("model-name").value || null;
  }
  send("update_model_config", { config });
});

connect();
