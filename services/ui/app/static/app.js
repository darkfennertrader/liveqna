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
const ctxMenu = document.getElementById("ctx-menu");

let ws = null;
let levelResetTimer = null;

// --- speaker tracking state -------------------------------------------
// speaker_id -> { color, name }. Entries only exist for tracked speakers;
// untracked speakers render with no entry (default/white).
const trackedSpeakers = new Map();
// Display name overrides, independent of tracking state -- a speaker can
// be renamed whether or not they are currently tracked.
const speakerNames = new Map();
// segment_id -> DOM elements for that utterance (name line + text line),
// so a later event for the same segment_id updates in place rather than
// appending a new line.
const utteranceEls = new Map();

const COLOR_PALETTE = ["#5cd68a", "#e8a33d", "#c88cf0", "#4fc3f7", "#f06292", "#a8d85c"];

function nextAvailableColor() {
  const used = new Set([...trackedSpeakers.values()].map((v) => v.color));
  return COLOR_PALETTE.find((c) => !used.has(c)) || COLOR_PALETTE[trackedSpeakers.size % COLOR_PALETTE.length];
}

function displayName(speakerId) {
  if (!speakerId) return "Unknown speaker";
  return speakerNames.get(speakerId) || speakerId.replace("speaker_", "Speaker ");
}


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

function colorForSpeaker(speakerId) {
  const entry = trackedSpeakers.get(speakerId);
  return entry ? entry.color : null;
}

function applyUtteranceStyle(textEl, ev) {
  textEl.classList.toggle("overlap", !!ev.is_overlap);
  const color = colorForSpeaker(ev.speaker_id);
  textEl.style.color = ev.is_overlap ? "" : (color || "");
}

// Builds a brand-new labeled utterance block (speaker-name header + one
// text line) and appends it to the transcript.
function createUtteranceBlock(ev) {
  const wrapper = document.createElement("div");
  wrapper.className = "utterance";
  wrapper.dataset.speakerId = ev.speaker_id || "";

  const nameEl = document.createElement("div");
  nameEl.className = "speaker-name";
  nameEl.textContent = displayName(ev.speaker_id);

  const textEl = document.createElement("span");
  textEl.className = "speaker-text";

  wrapper.appendChild(nameEl);
  wrapper.appendChild(document.createElement("br"));
  wrapper.appendChild(textEl);
  transcriptEl.appendChild(wrapper);

  wrapper.addEventListener("click", () => onUtteranceClick(wrapper));
  wrapper.addEventListener("contextmenu", (e) => {
    e.preventDefault();
    onUtteranceContextMenu(e, wrapper);
  });

  return { wrapper, nameEl, textEl };
}

// Appends a new text line to an existing utterance block (same speaker
// continuing to talk), without repeating the speaker-name header.
function appendLineToBlock(wrapper) {
  const textEl = document.createElement("span");
  textEl.className = "speaker-text";
  wrapper.appendChild(document.createElement("br"));
  wrapper.appendChild(textEl);
  return textEl;
}

function appendTranscriptEvent(ev) {
  const segId = ev.segment_id;
  let refs = segId != null ? utteranceEls.get(segId) : null;

  if (!refs) {
    // Partial events usually arrive before a speaker_id is resolved (see
    // local_whisper.py -- speaker-id only runs on the final decode), so
    // the common case here is "no speaker_id yet" and a fresh standalone
    // block is created; it is folded into the preceding block later, in
    // the regroup step below, once its speaker becomes known. The
    // speaker_id-already-known branch below only fires for finals that
    // arrive without a prior partial.
    const lastBlock = transcriptEl.lastElementChild;
    if (
      ev.speaker_id &&
      lastBlock &&
      lastBlock.classList.contains("utterance") &&
      lastBlock.dataset.speakerId === ev.speaker_id
    ) {
      refs = { wrapper: lastBlock, nameEl: lastBlock.querySelector(".speaker-name"), textEl: appendLineToBlock(lastBlock) };
    } else {
      refs = createUtteranceBlock(ev);
    }
    if (segId != null) utteranceEls.set(segId, refs);
  }

  if (ev.speaker_id && refs.wrapper.dataset.speakerId !== ev.speaker_id) {
    // Speaker just resolved (or changed) for a block that was created
    // before its speaker was known. If this block is still a lone,
    // unlabeled line (the common partial -> final transition) and the
    // block immediately preceding it in the transcript belongs to this
    // same now-known speaker, fold this line into that block instead of
    // leaving a redundant repeated speaker-name header. Only ever
    // regroups a still-unlabeled solo block -- an already-labeled block
    // (e.g. a genuine speaker change) is left exactly as before.
    const stillUnlabeledSolo = refs.wrapper.dataset.speakerId === "";
    const prevBlock = refs.wrapper.previousElementSibling;
    if (
      stillUnlabeledSolo &&
      prevBlock &&
      prevBlock.classList.contains("utterance") &&
      prevBlock.dataset.speakerId === ev.speaker_id
    ) {
      prevBlock.appendChild(document.createElement("br"));
      prevBlock.appendChild(refs.textEl);
      refs.wrapper.remove();
      refs.wrapper = prevBlock;
      refs.nameEl = prevBlock.querySelector(".speaker-name");
      utteranceEls.set(segId, refs);
    } else {
      refs.wrapper.dataset.speakerId = ev.speaker_id;
      refs.nameEl.textContent = displayName(ev.speaker_id);
    }
  }
  refs.textEl.classList.remove("final", "partial");
  refs.textEl.classList.add(ev.type === "partial" ? "partial" : "final");
  refs.textEl.textContent = ev.text;
  applyUtteranceStyle(refs.textEl, ev);

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
  if (config.speaker) {
    document.getElementById("embedding-backend").value = config.speaker.embedding_backend ?? "ecapa";
    document.getElementById("speaker-threshold").value = config.speaker.similarity_threshold ?? 0.40;
    document.getElementById("overlap-enabled").checked = config.speaker.overlap_detection_enabled ?? true;
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

document.getElementById("apply-speaker").addEventListener("click", () => {
  send("update_speaker_config", {
    config: {
      embedding_backend: document.getElementById("embedding-backend").value,
      similarity_threshold: parseFloat(document.getElementById("speaker-threshold").value),
      overlap_detection_enabled: document.getElementById("overlap-enabled").checked,
    },
  });
});

const EMBEDDING_DEFAULT_THRESHOLDS = { ecapa: 0.40, resemblyzer: 0.78 };
document.getElementById("embedding-backend").addEventListener("change", (e) => {
  document.getElementById("speaker-threshold").value = EMBEDDING_DEFAULT_THRESHOLDS[e.target.value] ?? 0.5;
});

// --- speaker tracking interactions --------------------------------------

function recolorAllForSpeaker(speakerId) {
  for (const refs of utteranceEls.values()) {
    if (refs.wrapper.dataset.speakerId === speakerId) {
      applyUtteranceStyle(refs.textEl, {
        is_overlap: refs.textEl.classList.contains("overlap"),
        speaker_id: speakerId,
      });
      refs.nameEl.textContent = displayName(speakerId);
    }
  }
}

function onUtteranceClick(wrapper) {
  const speakerId = wrapper.dataset.speakerId;
  if (!speakerId) return;

  if (trackedSpeakers.has(speakerId)) {
    trackedSpeakers.delete(speakerId);
  } else {
    trackedSpeakers.set(speakerId, { color: nextAvailableColor() });
  }
  recolorAllForSpeaker(speakerId);
}

function hideCtxMenu() {
  ctxMenu.style.display = "none";
}
document.addEventListener("click", (e) => {
  if (!ctxMenu.contains(e.target)) hideCtxMenu();
});

function onUtteranceContextMenu(e, wrapper) {
  const speakerId = wrapper.dataset.speakerId;
  if (!speakerId) return;

  ctxMenu.innerHTML = "";

  const renameBtn = document.createElement("button");
  renameBtn.textContent = "Rename...";
  renameBtn.addEventListener("click", () => {
    hideCtxMenu();
    const current = displayName(speakerId);
    const name = window.prompt("Speaker name:", current);
    if (name && name.trim()) {
      speakerNames.set(speakerId, name.trim());
      recolorAllForSpeaker(speakerId);
    }
  });
  ctxMenu.appendChild(renameBtn);

  if (trackedSpeakers.has(speakerId)) {
    const untrackBtn = document.createElement("button");
    untrackBtn.textContent = "Untrack";
    untrackBtn.addEventListener("click", () => {
      hideCtxMenu();
      trackedSpeakers.delete(speakerId);
      recolorAllForSpeaker(speakerId);
    });
    ctxMenu.appendChild(untrackBtn);
  } else if (trackedSpeakers.size > 0) {
    ctxMenu.appendChild(document.createElement("hr"));
    for (const [targetId] of trackedSpeakers) {
      if (targetId === speakerId) continue;
      const mergeBtn = document.createElement("button");
      mergeBtn.textContent = `Merge into: ${displayName(targetId)}`;
      mergeBtn.addEventListener("click", () => {
        hideCtxMenu();
        send("merge_speaker", { source_id: speakerId, target_id: targetId });
        // Reflect the merge locally right away so the UI doesn't wait on
        // a round trip before showing the color change.
        const info = trackedSpeakers.get(targetId);
        trackedSpeakers.set(speakerId, info);
        for (const refs of utteranceEls.values()) {
          if (refs.wrapper.dataset.speakerId === speakerId) {
            refs.wrapper.dataset.speakerId = targetId;
          }
        }
        recolorAllForSpeaker(targetId);
      });
      ctxMenu.appendChild(mergeBtn);
    }
  }

  ctxMenu.style.left = `${e.pageX}px`;
  ctxMenu.style.top = `${e.pageY}px`;
  ctxMenu.style.display = "block";
}

// --- speaker enrollment (upload once, enroll multiple people) -----------

const enrolledListEl = document.getElementById("enrolled-list");
const enrollFileEl = document.getElementById("enroll-file");
const enrollUploadStatusEl = document.getElementById("enroll-upload-status");
const enrollFieldsEl = document.getElementById("enroll-fields");

let currentUploadId = null;

async function refreshEnrolledList() {
  try {
    const resp = await fetch("/api/enrollment/speakers");
    const data = await resp.json();
    enrolledListEl.innerHTML = "";
    for (const speaker of data.speakers || []) {
      const chip = document.createElement("span");
      chip.className = "enrolled-chip";
      const label = document.createElement("span");
      label.textContent = speaker.name;
      chip.appendChild(label);
      const removeBtn = document.createElement("button");
      removeBtn.textContent = "✕";
      removeBtn.title = "Remove enrolled speaker";
      removeBtn.addEventListener("click", async () => {
        await fetch(`/api/enrollment/speakers/${encodeURIComponent(speaker.name)}`, { method: "DELETE" });
        refreshEnrolledList();
      });
      chip.appendChild(removeBtn);
      enrolledListEl.appendChild(chip);
    }
    if (!data.speakers || data.speakers.length === 0) {
      enrolledListEl.textContent = "No enrolled speakers yet.";
    }
  } catch (e) {
    enrolledListEl.textContent = "Failed to load enrolled speakers.";
  }
}

enrollFileEl.addEventListener("change", async () => {
  const file = enrollFileEl.files[0];
  if (!file) return;

  enrollUploadStatusEl.textContent = "Uploading...";
  enrollFieldsEl.style.display = "none";

  const formData = new FormData();
  formData.append("file", file);

  try {
    const resp = await fetch("/api/enrollment/upload", { method: "POST", body: formData });
    const data = await resp.json();
    if (data.error) {
      enrollUploadStatusEl.textContent = `Upload failed: ${data.error}`;
      return;
    }
    currentUploadId = data.upload_id;
    const mins = Math.floor(data.duration_sec / 60);
    const secs = Math.round(data.duration_sec % 60);
    enrollUploadStatusEl.textContent = `Uploaded: ${file.name} (${mins}:${String(secs).padStart(2, "0")})`;
    enrollFieldsEl.style.display = "block";
  } catch (e) {
    enrollUploadStatusEl.textContent = "Upload failed.";
  }
});

// Parses "min.sec" notation (e.g. "4.10" = 4 minutes 10 seconds, "4.5" =
// 4 minutes 05 seconds -- the digits after the dot are literal seconds
// digits, left-padded to 2, not a decimal fraction of a minute). Returns
// total seconds, or null if the input is empty/invalid (seconds >= 60).
function parseMinSec(value) {
  const trimmed = (value || "").trim();
  if (!trimmed) return null;
  const match = trimmed.match(/^(\d+)(?:\.(\d{1,2}))?$/);
  if (!match) return null;
  const minutes = parseInt(match[1], 10);
  const secondsDigits = (match[2] || "0").padStart(2, "0");
  const seconds = parseInt(secondsDigits, 10);
  if (seconds >= 60) return null;
  return minutes * 60 + seconds;
}

document.getElementById("enroll-add-btn").addEventListener("click", async () => {
  if (!currentUploadId) return;
  const name = document.getElementById("enroll-name").value.trim();
  const startRaw = document.getElementById("enroll-start").value;
  const endRaw = document.getElementById("enroll-end").value;
  const startSec = parseMinSec(startRaw);
  const endSec = parseMinSec(endRaw);
  if (!name) {
    setStatus("Enter a speaker name first", false);
    return;
  }
  if (startSec === null || endSec === null) {
    setStatus('Start/End must be in "min.sec" format, e.g. 4.10 for 4m10s (seconds part must be 00-59)', false);
    return;
  }
  if (!(endSec > startSec)) {
    setStatus("End time must be after start time", false);
    return;
  }

  const resp = await fetch("/api/enrollment/enroll", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ upload_id: currentUploadId, name, start_sec: startSec, end_sec: endSec }),
  });
  const data = await resp.json();
  if (data.error) {
    setStatus(`Enroll failed: ${data.error}`, false);
  } else {
    setStatus(`Enrolled "${name}"`);
    document.getElementById("enroll-name").value = "";
    refreshEnrolledList();
  }
});

document.getElementById("enroll-done-btn").addEventListener("click", async () => {
  if (currentUploadId) {
    await fetch(`/api/enrollment/finish/${currentUploadId}`, { method: "POST" });
  }
  currentUploadId = null;
  enrollFieldsEl.style.display = "none";
  enrollUploadStatusEl.textContent = "";
  enrollFileEl.value = "";
});

refreshEnrolledList();

connect();


