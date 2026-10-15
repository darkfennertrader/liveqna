// liveqna frontend: single WS to the ui backend. The ui backend relays
// transcript events from stt and brokers configure/start/stop calls to
// capture and stt.

const sourceSelect = document.getElementById("source-select");
const liveProjectSelect = document.getElementById("live-project-select");
const refreshLiveProjectsBtn = document.getElementById("refresh-live-projects");
const backendSelect = document.getElementById("backend-select");
const transcriptEl = document.getElementById("transcript");
const connDot = document.getElementById("conn-dot");
const pttDot = document.getElementById("ptt-dot");
const statusEl = document.getElementById("status");
const groupLocal = document.getElementById("group-local");
const groupOnline = document.getElementById("group-online");
const levelDot = document.getElementById("level-dot");
const levelBarFill = document.getElementById("level-bar-fill");
const levelLabel = document.getElementById("level-label");
const startBtn = document.getElementById("start-btn");
const stopBtn = document.getElementById("stop-btn");
const startGateHint = document.getElementById("start-gate-hint");
const pttBtn = document.getElementById("ptt-btn");
const pttStatusEl = document.getElementById("ptt-status");
const capturedListEl = document.getElementById("captured-list");
const capturedEmptyEl = document.getElementById("captured-empty");
const historyListEl = document.getElementById("history-list");
const historyEmptyEl = document.getElementById("history-empty");
const modalOverlay = document.getElementById("modal-overlay");
const modalTitle = document.getElementById("modal-title");
const modalBody = document.getElementById("modal-body");
const modalFooter = document.getElementById("modal-footer");
const modalClose = document.getElementById("modal-close");
const questionsListEl = document.getElementById("questions-list");
const questionsEmptyEl = document.getElementById("questions-empty");
const questionsClearBtn = document.getElementById("questions-clear-btn");
const questionGenerationDotEl = document.getElementById("question-generation-dot");
const questionGenerationTimeEl = document.getElementById("question-generation-time");
const questionRetrievalDotEl = document.getElementById("question-retrieval-dot");
const questionRetrievalTimeEl = document.getElementById("question-retrieval-time");
const supervisorProviderEl = document.getElementById("supervisor-provider");
const supervisorModelEl = document.getElementById("supervisor-model");
const supervisorParametersEl = document.getElementById("supervisor-parameters");
const questionProviderEl = document.getElementById("question-provider");
const questionModelEl = document.getElementById("question-model");
const questionParametersEl = document.getElementById("question-parameters");
const ingestionProjectSelect = document.getElementById("ingestion-project-select");
const ingestionNewProjectGroup = document.getElementById("ingestion-new-project-group");
const ingestionNewProjectEl = document.getElementById("ingestion-new-project");
const ingestionFileInputEl = document.getElementById("ingestion-file-input");
const ingestionSelectBtn = document.getElementById("ingestion-select-btn");
const ingestionStartBtn = document.getElementById("ingestion-start-btn");
const ingestionRefreshBtn = document.getElementById("ingestion-refresh-btn");
const ingestionFileListEl = document.getElementById("ingestion-file-list");
const ingestionSelectionEmptyEl = document.getElementById("ingestion-selection-empty");
const ingestionLibraryEl = document.getElementById("ingestion-library");
const ingestionLibraryEmptyEl = document.getElementById("ingestion-library-empty");

let providerCatalog = [];
const agentParameterState = { supervisor: {}, question: {} };
let ingestionSelections = [];
let ingestionLibrary = [];
let ingestionLibraryLoading = false;
let ingestionPollTimer = null;
const ingestionDetails = new Map();
let activeLiveProject = null;

function agentConfigElements(kind) {
  return kind === "supervisor"
    ? {
        provider: supervisorProviderEl,
        model: supervisorModelEl,
        parameters: supervisorParametersEl,
      }
    : {
        provider: questionProviderEl,
        model: questionModelEl,
        parameters: questionParametersEl,
      };
}

function providerDefinition(name) {
  return providerCatalog.find((provider) => provider.name === name);
}

function modelFamilyMatches(model, family) {
  const normalizedModel = model.trim().toLowerCase();
  const normalizedFamily = family.trim().toLowerCase();
  return normalizedModel === normalizedFamily
    || normalizedModel.startsWith(`${normalizedFamily}-`);
}

function effectiveConstraint(provider, model) {
  return [...(provider?.model_constraints || [])]
    .filter((constraint) => modelFamilyMatches(model, constraint.model_family))
    .sort((left, right) => right.model_family.length - left.model_family.length)[0] || null;
}

function readAgentParameters(kind) {
  const { parameters } = agentConfigElements(kind);
  const values = {};
  for (const control of parameters.querySelectorAll("[data-parameter-name]")) {
    const name = control.dataset.parameterName;
    if (control.type === "checkbox") {
      values[name] = control.checked;
    } else if (control.value !== "") {
      if (control.type === "number") values[name] = Number(control.value);
      else if (control.tagName === "SELECT") values[name] = JSON.parse(control.value);
      else values[name] = control.value;
    }
  }
  return values;
}

function renderAgentParameters(kind, requestedValues = {}) {
  const { provider: providerEl, model, parameters: container } = agentConfigElements(kind);
  const provider = providerDefinition(providerEl.value);
  const constraint = effectiveConstraint(provider, model.value);
  container.innerHTML = "";
  if (!provider) return;

  for (const parameter of provider.parameters || []) {
    const label = document.createElement("label");
    const controlId = `${kind}-parameter-${parameter.name}`;
    label.htmlFor = controlId;
    label.textContent = parameter.label;
    container.appendChild(label);

    const allowed = constraint?.allowed_values?.[parameter.name] || null;
    const options = allowed
      ? (parameter.options || []).filter((option) => allowed.includes(option.value))
      : (parameter.options || []);
    let value = Object.hasOwn(requestedValues, parameter.name)
      ? requestedValues[parameter.name]
      : parameter.default;
    if (allowed && value !== null && value !== undefined && !allowed.includes(value)) {
      value = constraint?.defaults?.[parameter.name] ?? allowed[0];
    }

    let control;
    if (parameter.control === "select") {
      control = document.createElement("select");
      if (parameter.nullable) {
        const defaultOption = document.createElement("option");
        defaultOption.value = "";
        defaultOption.textContent = "Provider default";
        control.appendChild(defaultOption);
      }
      for (const option of options) {
        const optionEl = document.createElement("option");
        optionEl.value = JSON.stringify(option.value);
        optionEl.textContent = option.label;
        control.appendChild(optionEl);
      }
      control.value = value === null || value === undefined ? "" : JSON.stringify(value);
    } else if (parameter.control === "checkbox") {
      control = document.createElement("input");
      control.type = "checkbox";
      control.checked = Boolean(value);
    } else {
      control = document.createElement("input");
      control.type = parameter.control === "number" ? "number" : "text";
      if (parameter.minimum !== null) control.min = parameter.minimum;
      if (parameter.maximum !== null) control.max = parameter.maximum;
      if (parameter.step !== null) control.step = parameter.step;
      control.value = value === null || value === undefined ? "" : value;
    }
    control.id = controlId;
    control.dataset.parameterName = parameter.name;
    container.appendChild(control);

    if (parameter.help) {
      const help = document.createElement("p");
      help.className = "agent-hint";
      help.textContent = parameter.help;
      container.appendChild(help);
    }
  }
}

function selectionParameters(selection) {
  const parameters = { ...(selection.parameters || {}) };
  for (const name of ["temperature", "reasoning_effort", "service_tier"]) {
    if (!(name in parameters) && selection[name] !== null && selection[name] !== undefined) {
      parameters[name] = selection[name];
    }
  }
  return parameters;
}

function populateProviderSelect(select) {
  select.innerHTML = "";
  for (const provider of providerCatalog) {
    const option = document.createElement("option");
    option.value = provider.name;
    option.textContent = provider.label;
    select.appendChild(option);
  }
}

function handleAgentProviderChange(kind) {
  const { provider } = agentConfigElements(kind);
  const previousProvider = provider.dataset.renderedProvider;
  if (previousProvider) {
    agentParameterState[kind][previousProvider] = readAgentParameters(kind);
  }
  const values = agentParameterState[kind][provider.value] || {};
  renderAgentParameters(kind, values);
  provider.dataset.renderedProvider = provider.value;
}

let ws = null;
let levelResetTimer = null;
let wsConnected = false;
let captureRunning = false;
// Set the instant Start is clicked, cleared once the server actually
// confirms capture_started/capture_stopped or reports an error -- lets
// Start disable itself immediately on click (see conversation history)
// without prematurely flipping captureRunning/the "Live transcript" dot,
// which must only reflect a *confirmed* running pipeline.
let startPending = false;

// --- push-to-talk state --------------------------------------------------
// Manual alternative to speaker-based filtering (see conversation history):
// the user marks exactly which stretch of live transcript they care about
// by holding a Start/Stop toggle, independent of who is speaking.
let pttCapturing = false;
// Ordered list of segment_ids first seen while capturing, in arrival order.
let pttSegmentOrder = [];
// segment_id -> latest text seen for it (partial or final).
let pttSegmentText = new Map();
// segment_ids that have reached a final decode.
let pttSegmentFinal = new Set();
// After Stop is pressed, any segment that was already open (had a partial)
// keeps updating until it resolves to final, so the last word isn't cut
// off mid-utterance. This timer bounds how long we wait for that before
// finalizing with whatever text is available anyway.
let pttFinalizeTimer = null;

// --- visual line-break synchronization ------------------------------------
// When Start is pressed while a VAD segment is still mid-flight (partial),
// faster-whisper keeps re-decoding and replacing that segment's full text
// in place, so a <br> simply appended at press-time would visually "wait"
// for the segment to finish before the break appears to take effect (see
// conversation history: the break landed later than the actual press).
// Instead we split that segment's rendering into a "before" span (frozen
// at the character offset the segment had reached at press-time) and an
// "after" span inserted right after the <br>, and re-slice every
// subsequent full-text update at that same offset so new words keep
// landing visually after the break, synchronized to the press moment.
// This is a rendering-only fix: the actual captured text sent downstream
// still includes the "before" portion too, per the documented
// utterance-granular-truncation limitation.
//
// segId -> { offset, afterEl }. Entries persist for the lifetime of the
// segment's DOM nodes (cleaned up alongside them in trimTranscriptIfNeeded)
// so later highlight/text updates for an already-split segment keep
// finding the same "after" element.
const segmentSplits = new Map();
// Most recently updated segment id, and the set of segment ids that have
// resolved to a final decode -- tracked for every segment (not just
// ptt-captured ones) so Start-press can tell whether a segment is
// currently mid-flight.
let lastRenderedSegId = null;
const allSegmentFinal = new Set();


// Single source of truth for every captured piece this session (see
// conversation history: History tab = full session log). Discarding
// never deletes an entry -- it marks it "discarded" so the History tab
// remains a true, permanent record; only the sidebar's active-queue view
// hides discarded entries.
// { id, text, status: "pending"|"sent"|"discarded", ts }
let capturedItems = [];
let capturedIdCounter = 0;
const HISTORY_STORAGE_KEY = "liveqna_captured_history";

// Durable orchestration runs are owned by the backend's LangGraph
// checkpointer. This array is only the current browser view of them.
let orchestrationRuns = [];
const activeGenerationRunIds = new Set();

const phaseIndicators = {
  generation: {
    dot: questionGenerationDotEl,
    time: questionGenerationTimeEl,
    startedAt: null,
    interval: null,
    activeCount: 0,
  },
  retrieval: {
    dot: questionRetrievalDotEl,
    time: questionRetrievalTimeEl,
    startedAt: null,
    interval: null,
    activeCount: 0,
  },
};

function formatDuration(milliseconds, emptyLabel = "not run") {
  if (milliseconds === null || milliseconds === undefined) return emptyLabel;
  return `${(milliseconds / 1000).toFixed(2)}s`;
}

function startPhaseIndicator(name) {
  const phase = phaseIndicators[name];
  phase.activeCount += 1;
  if (phase.activeCount > 1) return;
  phase.startedAt = performance.now();
  phase.dot.classList.add("green");
  phase.dot.classList.add("working");
  const update = () => {
    const prefix = phase.activeCount > 1 ? `${phase.activeCount} active · ` : "";
    phase.time.textContent = `${prefix}${formatDuration(performance.now() - phase.startedAt)}`;
  };
  update();
  phase.interval = setInterval(update, 100);
}

function finishPhaseIndicator(name, milliseconds = null) {
  const phase = phaseIndicators[name];
  phase.activeCount = Math.max(0, phase.activeCount - 1);
  if (phase.activeCount > 0) return;
  const elapsed = phase.startedAt === null ? null : performance.now() - phase.startedAt;
  if (phase.interval) clearInterval(phase.interval);
  phase.interval = null;
  phase.startedAt = null;
  phase.dot.classList.remove("working");
  phase.dot.classList.remove("yellow");
  phase.dot.classList.add("green");
  phase.time.textContent = formatDuration(milliseconds ?? elapsed, "ready");
}

function updatePhaseSummaryFromLatestRun() {
  const latest = orchestrationRuns[0];
  if (phaseIndicators.generation.startedAt === null) {
    finishPhaseIndicator("generation", latest?.question_generation_ms ?? null);
  }
  if (phaseIndicators.retrieval.startedAt === null) {
    finishPhaseIndicator("retrieval", latest?.question_retrieval_ms ?? null);
  }
}

// segment_id -> DOM span element for that utterance, so a later event for
// the same segment_id updates in place rather than appending a new node.
const utteranceEls = new Map();
// Cap on tracked segments kept in the live transcript DOM; oldest are
// trimmed once exceeded so a long session doesn't grow memory unbounded.
const MAX_TRANSCRIPT_SEGMENTS = 500;
let transcriptSegmentOrder = [];

function setStatus(message, ok = true) {
  const span = document.createElement("div");
  span.className = ok ? "ok" : "bad";
  span.textContent = `[${new Date().toLocaleString()}] ${message}`;
  statusEl.prepend(span);
  // Dedicated "Logging" tab (see conversation history) rather than a
  // cramped sidebar strip, so keep a longer scrollback than before.
  while (statusEl.childNodes.length > 200) statusEl.removeChild(statusEl.lastChild);
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

function selectedLiveProject() {
  const option = liveProjectSelect.selectedOptions[0];
  if (!option || !option.value) return null;
  return { id: option.value, name: option.dataset.projectName || option.textContent };
}

function selectedIngestionProjectName() {
  if (ingestionProjectSelect.value === "__new__") {
    return ingestionNewProjectEl.value.trim();
  }
  const option = ingestionProjectSelect.selectedOptions[0];
  return option?.dataset.projectName || "";
}

function updateIngestionProjectChoice() {
  const creating = ingestionProjectSelect.value === "__new__";
  ingestionNewProjectGroup.style.display = creating ? "block" : "none";
  renderIngestionSelections();
}

async function loadProjectSelectors() {
  const previousLive = liveProjectSelect.value;
  const previousIngestion = ingestionProjectSelect.value;
  refreshLiveProjectsBtn.disabled = true;
  try {
    const projects = await requestJson("/api/ingestion/projects");
    liveProjectSelect.innerHTML = "";
    ingestionProjectSelect.innerHTML = "";

    const livePlaceholder = document.createElement("option");
    livePlaceholder.value = "";
    livePlaceholder.textContent = projects.length ? "Select project..." : "No projects available";
    liveProjectSelect.appendChild(livePlaceholder);
    const ingestionPlaceholder = document.createElement("option");
    ingestionPlaceholder.value = "";
    ingestionPlaceholder.textContent = "Select project...";
    ingestionProjectSelect.appendChild(ingestionPlaceholder);

    for (const project of projects) {
      for (const select of [liveProjectSelect, ingestionProjectSelect]) {
        const option = document.createElement("option");
        option.value = project.id;
        option.dataset.projectName = project.name;
        option.textContent = project.name;
        select.appendChild(option);
      }
    }
    const createNew = document.createElement("option");
    createNew.value = "__new__";
    createNew.textContent = "+ Create new project";
    ingestionProjectSelect.appendChild(createNew);

    if ([...liveProjectSelect.options].some((option) => option.value === previousLive)) {
      liveProjectSelect.value = previousLive;
    }
    if ([...ingestionProjectSelect.options].some((option) => option.value === previousIngestion)) {
      ingestionProjectSelect.value = previousIngestion;
    }
  } catch (error) {
    liveProjectSelect.innerHTML = '<option value="">Projects unavailable</option>';
    ingestionProjectSelect.innerHTML = '<option value="">Projects unavailable</option><option value="__new__">+ Create new project</option>';
    setStatus(`Could not load projects: ${error.message}`, false);
  } finally {
    refreshLiveProjectsBtn.disabled = false;
    updateIngestionProjectChoice();
    updateCaptureButtons();
  }
}

// Button-enablement rules (see conversation history):
//   - Start (audio pipeline) is disabled unless the live-transcript
//     WebSocket is connected AND no capture session is already running
//     (no point re-starting what's already running) AND push-to-talk
//     isn't mid-capture.
//   - Stop (audio pipeline) is disabled unless a capture session is
//     actually running, and -- while a push-to-talk capture is in
//     progress -- is force-disabled too, so the audio pipeline can't be
//     torn out from under an in-progress push-to-talk capture. Once
//     push-to-talk Stop is pressed, only Stop re-enables (Start stays
//     disabled because the pipeline is still running).
function updateCaptureButtons() {
  const project = selectedLiveProject();
  startBtn.disabled = !wsConnected || !project || captureRunning || pttCapturing || startPending;
  stopBtn.disabled = !captureRunning || pttCapturing;
  liveProjectSelect.disabled = captureRunning || pttCapturing || startPending;
  refreshLiveProjectsBtn.disabled = captureRunning || pttCapturing || startPending;
  if (!project) {
    startGateHint.textContent = "Select a project before starting the live call.";
    startGateHint.style.display = "block";
  } else if (!wsConnected) {
    startGateHint.textContent = "Waiting for live transcript connection...";
    startGateHint.style.display = "block";
  } else {
    startGateHint.style.display = "none";
  }
}


// "Live transcript" means transcript is actually flowing right now, which
// requires BOTH the browser's WebSocket to be open AND the audio capture
// pipeline to be running -- just having the WS open (true from the moment
// the page loads) is not enough on its own (see conversation history: the
// dot was wrongly green before Start was ever pressed).
let liveTranscriptActive = false;
function updateConnDot() {
  const live = wsConnected && captureRunning;
  connDot.classList.toggle("green", live);
  connDot.classList.toggle("red", !live);
  // Log the actual edge (not every call -- this runs on every WS/status
  // message) so the Logging tab records exactly one "started"/"ended"
  // pair per real transition, with the real datetime it happened.
  if (live && !liveTranscriptActive) {
    setStatus("Live transcription started");
  } else if (!live && liveTranscriptActive) {
    setStatus("Live transcription ended");
  }
  liveTranscriptActive = live;
}

function setConnDot(connected) {
  wsConnected = connected;
  updateConnDot();
  updateCaptureButtons();
  if (!connected) setCaptureDot(false);
}

function setCaptureDot(running) {
  captureRunning = running;
  updateConnDot();
  updateCaptureButtons();
}

// "Capturing" dot tracks the push-to-talk Start/Stop toggle specifically
// (NOT the audio pipeline -- see conversation history: these are two
// different, easily-confused "capture" concepts, and the dot must follow
// the push-to-talk button it sits next to).
function updatePttDot() {
  pttDot.classList.toggle("green", pttCapturing);
  pttDot.classList.toggle("red", !pttCapturing);
}


function isScrolledToBottom(el, thresholdPx = 40) {
  return el.scrollHeight - el.scrollTop - el.clientHeight <= thresholdPx;
}

function trimTranscriptIfNeeded() {
  while (transcriptSegmentOrder.length > MAX_TRANSCRIPT_SEGMENTS) {
    const oldId = transcriptSegmentOrder.shift();
    const el = utteranceEls.get(oldId);
    if (el) {
      // Remove the span and the single leading space/break text node we
      // inserted before it, if present.
      const prev = el.previousSibling;
      if (prev && prev.nodeType === Node.TEXT_NODE) prev.remove();
      el.remove();
    }
    const split = segmentSplits.get(oldId);
    if (split) {
      const br = split.afterEl.previousSibling;
      if (br && br.nodeName === "BR") br.remove();
      split.afterEl.remove();
      segmentSplits.delete(oldId);
    }
    utteranceEls.delete(oldId);
    pttSegmentText.delete(oldId);
    pttSegmentFinal.delete(oldId);
    allSegmentFinal.delete(oldId);
  }
}

function appendTranscriptEvent(ev) {
  const segId = ev.segment_id;
  const wasAtBottom = isScrolledToBottom(transcriptEl);
  let textEl = segId != null ? utteranceEls.get(segId) : null;

  if (!textEl) {
    textEl = document.createElement("span");
    textEl.className = "utterance-text";
    // Continuous flowing text: segments are separated by a single space,
    // not a forced line break, so the transcript reads as one justified
    // paragraph rather than one line per VAD segment. splitSegmentForPushToTalk
    // below inserts an actual <br> only at the moment Start-capturing is
    // pressed, so that boundary is visually explicit.
    if (transcriptEl.lastElementChild) {
      transcriptEl.appendChild(document.createTextNode(" "));
    }
    transcriptEl.appendChild(textEl);
    if (segId != null) {
      utteranceEls.set(segId, textEl);
      transcriptSegmentOrder.push(segId);
      trimTranscriptIfNeeded();
    }
  }

  const isPartial = ev.type === "partial";
  const split = segId != null ? segmentSplits.get(segId) : null;
  if (split) {
    // This segment was split at Start-press time: keep slicing every
    // update at the same frozen offset so new words continue to land
    // visually after the <br>, synchronized to when the button was
    // pressed (see note above segmentSplits).
    const beforeText = ev.text.slice(0, split.offset);
    const afterText = ev.text.slice(split.offset);
    textEl.textContent = beforeText;
    split.afterEl.textContent = afterText;
    for (const el of [textEl, split.afterEl]) {
      el.classList.remove("final", "partial");
      el.classList.add(isPartial ? "partial" : "final");
    }
  } else {
    textEl.classList.remove("final", "partial");
    textEl.classList.add(isPartial ? "partial" : "final");
    textEl.textContent = ev.text;
  }

  if (segId != null) {
    lastRenderedSegId = segId;
    if (!isPartial) allSegmentFinal.add(segId);
  }

  if (wasAtBottom) transcriptEl.scrollTop = transcriptEl.scrollHeight;

  recordPushToTalkEvent(ev);
}

// --- push-to-talk logic ---------------------------------------------------

function setUtteranceCapturing(segId, on) {
  // If this segment was split at Start-press time, only the "after"
  // portion (words spoken from the press moment onward) represents what
  // is actually being captured -- the "before" portion is pre-press
  // speech and must never be highlighted, or the green marker would
  // misleadingly include words the user didn't intend to capture.
  const split = segmentSplits.get(segId);
  if (split) {
    split.afterEl.classList.toggle("capturing", on);
    return;
  }
  const el = utteranceEls.get(segId);
  if (el) el.classList.toggle("capturing", on);
}


function recordPushToTalkEvent(ev) {
  const segId = ev.segment_id;
  if (segId == null) return;

  const isFinal = ev.type !== "partial";

  if (pttCapturing) {
    if (!pttSegmentText.has(segId)) pttSegmentOrder.push(segId);
    pttSegmentText.set(segId, ev.text);
    if (isFinal) pttSegmentFinal.add(segId);
    setUtteranceCapturing(segId, true);
    return;
  }

  // Not currently capturing, but this segment was still open when Stop
  // was pressed (its id is already tracked) -- keep absorbing updates
  // (and keep it highlighted) until it resolves to final, so the sentence
  // isn't cut off mid-word.
  if (pttSegmentText.has(segId) && !pttSegmentFinal.has(segId)) {
    pttSegmentText.set(segId, ev.text);
    setUtteranceCapturing(segId, true);
    if (isFinal) {
      pttSegmentFinal.add(segId);
      maybeFinishPushToTalkCapture();
    }
  }
}

function allPushToTalkSegmentsFinal() {
  return pttSegmentOrder.every((id) => pttSegmentFinal.has(id));
}

function maybeFinishPushToTalkCapture() {
  if (pttCapturing) return;
  if (!allPushToTalkSegmentsFinal()) return;
  if (pttFinalizeTimer) {
    clearTimeout(pttFinalizeTimer);
    pttFinalizeTimer = null;
  }
  finalizePushToTalkCapture();
}

function finalizePushToTalkCapture() {
  const text = pttSegmentOrder
    .map((id) => (pttSegmentText.get(id) || "").trim())
    .filter((t) => t.length > 0)
    .join(" ");

  // The green highlight is a *permanent* visual record of what was
  // captured, per conversation history -- it must stay green after Stop
  // so a user can scan the live transcript later and see exactly which
  // stretches were queued, so we deliberately do NOT clear it here.

  if (text) {
    const project = activeLiveProject || selectedLiveProject();
    const item = {
      id: ++capturedIdCounter,
      text,
      status: "pending",
      ts: Date.now(),
      projectId: project?.id || null,
      projectName: project?.name || null,
    };
    capturedItems.push(item);
    renderCapturedList();
  }

  pttSegmentOrder = [];
  pttSegmentText = new Map();
  pttSegmentFinal = new Set();
}

function resetInProgressPushToTalkCapture() {
  // Discards the *in-progress* (not-yet-finalized) buffer only -- used
  // when toggling Start while nothing has been captured yet.
  for (const id of pttSegmentOrder) setUtteranceCapturing(id, false);
  pttSegmentOrder = [];
  pttSegmentText = new Map();
  pttSegmentFinal = new Set();
  if (pttFinalizeTimer) {
    clearTimeout(pttFinalizeTimer);
    pttFinalizeTimer = null;
  }
}

// Inserts a line break in the live transcript synchronized to *this exact
// moment* (Start-press or Stop-press), splitting the currently open/mid-
// flight segment's rendering at this instant so later updates to it keep
// landing after the break (see segmentSplits above for the full
// rationale). Shared by both Start and Stop so the same visual boundary
// behavior applies whichever button closes the capture window.
function insertSyncedLineBreak() {
  if (!transcriptEl.lastElementChild) return;
  const openSegId = lastRenderedSegId;
  const openEl = openSegId != null ? utteranceEls.get(openSegId) : null;
  const alreadySplit = openSegId != null && segmentSplits.has(openSegId);
  if (openEl && !alreadySplit && !allSegmentFinal.has(openSegId)) {
    const offset = openEl.textContent.length;
    const afterEl = document.createElement("span");
    afterEl.className = openEl.className;
    transcriptEl.appendChild(document.createElement("br"));
    transcriptEl.appendChild(afterEl);
    segmentSplits.set(openSegId, { offset, afterEl });
  } else {
    transcriptEl.appendChild(document.createElement("br"));
  }
}

pttBtn.addEventListener("click", () => {
  if (!pttCapturing) {
    resetInProgressPushToTalkCapture();
    pttCapturing = true;
    updatePttDot();
    setStatus("Capture started");
    // While push-to-talk is capturing, the audio pipeline's own Start
    // AND Stop buttons are both disabled (see conversation history) --
    // the pipeline can't be torn down or re-started out from under an
    // in-progress capture window. The push-to-talk button itself (this
    // one) stays enabled so it can be pressed again to stop.
    updateCaptureButtons();
    pttBtn.textContent = "Stop capturing";
    pttBtn.classList.add("recording");
    pttStatusEl.textContent = "Capturing... speak now.";
    // Mark the start of a new captured piece with an explicit line break
    // in the live transcript, synchronized to this exact press moment.
    insertSyncedLineBreak();
    return;
  }

  pttCapturing = false;
  updatePttDot();
  setStatus("Capture ended");
  pttBtn.textContent = "Start capturing";
  pttBtn.classList.remove("recording");
  // Mark the end of the captured piece with a line break too, so the
  // next bit of live transcript after Stop visually starts fresh on its
  // own line, matching the Start-press boundary.
  insertSyncedLineBreak();
  // Push-to-talk capture is over: re-evaluate the audio pipeline's
  // Start/Stop -- Stop re-enables (if the pipeline is running), while
  // Start stays disabled as long as the pipeline is still running.
  updateCaptureButtons();

  if (pttSegmentOrder.length === 0) {
    pttStatusEl.textContent = "Not capturing.";
    return;
  }

  if (allPushToTalkSegmentsFinal()) {
    pttStatusEl.textContent = "Not capturing.";
    finalizePushToTalkCapture();
  } else {
    // Give the still-open segment a bounded window to resolve to final
    // instead of using its last partial text as-is.
    pttStatusEl.textContent = "Finishing last utterance...";
    pttFinalizeTimer = setTimeout(() => {
      pttFinalizeTimer = null;
      pttStatusEl.textContent = "Not capturing.";
      finalizePushToTalkCapture();
    }, 3000);
  }
});


// --- captured items list (sidebar) + modal viewer -------------------------

function renderCapturedList() {
  capturedListEl.innerHTML = "";
  const active = capturedItems.filter((i) => i.status !== "discarded");
  capturedEmptyEl.style.display = active.length === 0 ? "block" : "none";

  for (const item of active) {
    const row = document.createElement("div");
    row.className = "captured-item";

    const preview = document.createElement("div");
    preview.className = "preview";
    preview.textContent = item.text;
    row.appendChild(preview);

    const badge = document.createElement("span");
    badge.className = `badge ${item.status}`;
    badge.textContent = item.status;
    row.appendChild(badge);

    const removeBtn = document.createElement("button");
    removeBtn.className = "remove-btn";
    removeBtn.textContent = "\u2715";
    removeBtn.title = "Discard";
    removeBtn.addEventListener("click", (e) => {
      e.stopPropagation();
      discardCapturedItem(item);
    });
    row.appendChild(removeBtn);

    row.addEventListener("click", () => openCapturedModal(item));
    capturedListEl.appendChild(row);
  }

  renderHistory();
  saveHistory();
}

function discardCapturedItem(item) {
  item.status = "discarded";
  renderCapturedList();
}


function closeModal() {
  modalOverlay.classList.remove("open");
  modalBody.textContent = "";
  modalFooter.innerHTML = "";
}
modalClose.addEventListener("click", closeModal);
modalOverlay.addEventListener("click", (e) => {
  if (e.target === modalOverlay) closeModal();
});

function openCapturedModal(item) {
  modalTitle.textContent = item.status === "sent" ? "Captured text (sent)" : "Captured text";
  modalBody.textContent = item.text;
  modalFooter.innerHTML = "";

  if (item.status !== "sent") {
    const sendBtn = document.createElement("button");
    sendBtn.className = "primary";
    sendBtn.textContent = "Send";
    sendBtn.addEventListener("click", () => sendCapturedItem(item, sendBtn));
    modalFooter.appendChild(sendBtn);
  }

  const discardBtn = document.createElement("button");
  discardBtn.className = item.status === "sent" ? "" : "danger";
  discardBtn.textContent = item.status === "sent" ? "Close" : "Discard";
  discardBtn.addEventListener("click", () => {
    if (item.status !== "sent") {
      discardCapturedItem(item);
    }
    closeModal();
  });
  modalFooter.appendChild(discardBtn);

  modalOverlay.classList.add("open");
}

async function sendCapturedItem(item, sendBtnEl) {
  if (sendBtnEl) sendBtnEl.disabled = true;
  const project = item.projectId
    ? { id: item.projectId, name: item.projectName }
    : selectedLiveProject();
  if (!project) {
    setStatus("This capture has no project. Select a project before sending it.", false);
    if (sendBtnEl) sendBtnEl.disabled = false;
    return;
  }
  startPhaseIndicator("generation");
  try {
    const data = await requestJson("/api/push_to_talk/send", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        text: item.text,
        project_id: project.id,
        project_name: project.name,
        supervisor_model: agentModelSelection("supervisor"),
        question_model: agentModelSelection("question"),
      }),
    });
    activeGenerationRunIds.add(data.run_id);
    upsertOrchestrationRun({
      run_id: data.run_id,
      source_text: item.text,
      project_id: project.id,
      project_name: project.name,
      status: "generating",
      questions: [],
      attempt: 0,
      supervisor_model: agentModelSelection("supervisor"),
      question_model: agentModelSelection("question"),
      supervisor_reason: "",
      retrieval_status: "not_started",
      question_generation_ms: null,
      question_retrieval_ms: null,
      response_return_ms: null,
      error: null,
      created_at: data.created_at,
      updated_at: data.created_at,
    });
    setStatus("Question generation started in the background.");
    item.status = "sent";
    item.runId = data.run_id;
    renderCapturedList();
    saveHistory();
    closeModal();
    activateTab("questions");
  } catch (e) {
    finishPhaseIndicator("generation");
    setStatus(`Send failed: ${e.message}`, false);
    if (sendBtnEl) sendBtnEl.disabled = false;
  }
}

function agentModelSelection(kind) {
  const { provider, model } = agentConfigElements(kind);
  renderAgentParameters(kind, readAgentParameters(kind));
  return {
    provider: provider.value,
    model: model.value.trim(),
    parameters: readAgentParameters(kind),
  };
}

async function requestJson(url, options = {}) {
  const response = await fetch(url, options);
  let body = {};
  try {
    body = await response.json();
  } catch (e) {
    // The status below remains the most useful fallback for a non-JSON error.
  }
  if (!response.ok) {
    throw new Error(body.detail || body.error || `${response.status} ${response.statusText}`);
  }
  return body;
}

// --- asynchronous document ingestion -------------------------------------

function humanFileSize(bytes) {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

function parseRetrievalMetadata(value) {
  const text = value.trim();
  if (!text) return {};
  if (text.startsWith("{")) {
    const parsed = JSON.parse(text);
    if (!parsed || Array.isArray(parsed) || typeof parsed !== "object") {
      throw new Error("Metadata JSON must be an object.");
    }
    return parsed;
  }
  const result = {};
  for (const [index, rawLine] of text.split("\n").entries()) {
    const line = rawLine.trim();
    if (!line) continue;
    const separator = line.indexOf("=");
    if (separator <= 0) {
      throw new Error(`Metadata line ${index + 1} must use key=value.`);
    }
    const key = line.slice(0, separator).trim();
    const rawValue = line.slice(separator + 1).trim();
    if (!key || !rawValue) {
      throw new Error(`Metadata line ${index + 1} needs both a key and value.`);
    }
    try {
      result[key] = JSON.parse(rawValue);
    } catch (_) {
      result[key] = rawValue;
    }
  }
  return result;
}

function ingestionStatusNode(selection) {
  const wrapper = document.createElement("span");
  wrapper.className = `ingestion-status ${selection.status}`;
  if (selection.status === "uploading" || selection.status === "queued") {
    const spinner = document.createElement("span");
    spinner.className = "ingestion-spinner";
    spinner.title = "Working in the background";
    wrapper.appendChild(spinner);
  } else {
    wrapper.textContent = selection.status.replaceAll("_", " ");
  }
  return wrapper;
}

function renderIngestionSelections() {
  ingestionFileListEl.innerHTML = "";
  ingestionSelectionEmptyEl.style.display = ingestionSelections.length ? "none" : "block";
  ingestionStartBtn.disabled = !selectedIngestionProjectName() || !ingestionSelections.some(
    (item) => item.status === "selected" || item.status === "error"
  );

  for (const selection of ingestionSelections) {
    const card = document.createElement("div");
    card.className = "ingestion-file";
    const head = document.createElement("div");
    head.className = "ingestion-file-head";
    const nameWrap = document.createElement("div");
    const name = document.createElement("div");
    name.className = "ingestion-filename";
    name.textContent = selection.file.name;
    const size = document.createElement("div");
    size.className = "ingestion-file-meta";
    size.textContent = humanFileSize(selection.file.size);
    nameWrap.append(name, size);
    head.append(nameWrap, ingestionStatusNode(selection));
    card.appendChild(head);

    const profileLabel = document.createElement("label");
    profileLabel.textContent = "Content profile (extraction hint and retrieval filter)";
    const profile = document.createElement("input");
    profile.type = "text";
    profile.value = selection.contentProfile;
    profile.placeholder = "general_document";
    profile.disabled = selection.status === "uploading" || selection.status === "queued";
    profile.addEventListener("input", () => { selection.contentProfile = profile.value; });

    const metadataLabel = document.createElement("label");
    metadataLabel.textContent = "User retrieval metadata";
    const metadata = document.createElement("textarea");
    metadata.value = selection.metadataText;
    metadata.placeholder = "country=Italy\nproduct=Life\nyear=2026";
    metadata.disabled = profile.disabled;
    metadata.addEventListener("input", () => { selection.metadataText = metadata.value; });
    const hint = document.createElement("p");
    hint.className = "ingestion-help";
    hint.textContent = "One key=value per line, or a JSON object. These values stay separate from future AI-generated metadata.";
    card.append(profileLabel, profile, metadataLabel, metadata, hint);

    if (selection.error) {
      const error = document.createElement("div");
      error.className = "ingestion-error";
      error.textContent = selection.error;
      card.appendChild(error);
    }
    if (selection.status === "selected" || selection.status === "error") {
      const actions = document.createElement("div");
      actions.className = "ingestion-actions";
      const remove = document.createElement("button");
      remove.className = "danger";
      remove.textContent = "Remove";
      remove.addEventListener("click", () => {
        ingestionSelections = ingestionSelections.filter((item) => item.id !== selection.id);
        renderIngestionSelections();
      });
      actions.appendChild(remove);
      card.appendChild(actions);
    }
    ingestionFileListEl.appendChild(card);
  }
}

function addIngestionFiles(files) {
  for (const file of files) {
    ingestionSelections.push({
      id: crypto.randomUUID(),
      file,
      contentProfile: file.name.toLowerCase().endsWith(".pptx")
        ? "general_presentation"
        : "general_document",
      metadataText: "",
      status: "selected",
      error: "",
    });
  }
  ingestionFileInputEl.value = "";
  renderIngestionSelections();
}

async function uploadIngestionSelection(selection) {
  selection.status = "uploading";
  selection.error = "";
  renderIngestionSelections();
  try {
    const metadata = parseRetrievalMetadata(selection.metadataText);
    const profile = selection.contentProfile.trim().toLowerCase();
    if (!/^[a-z][a-z0-9_]{0,99}$/.test(profile)) {
      throw new Error("Content profile must use lower-case letters, numbers, and underscores.");
    }
    const form = new FormData();
    form.append("file", selection.file);
    const projectName = selectedIngestionProjectName();
    if (!projectName) throw new Error("Select an existing project or enter a new project name.");
    form.append("project_name", projectName);
    form.append("content_profile", profile);
    form.append("retrieval_metadata", JSON.stringify(metadata));
    const accepted = await requestJson("/api/ingestion/upload", {
      method: "POST",
      body: form,
    });
    selection.status = "queued";
    selection.versionId = accepted.version.id;
  } catch (error) {
    selection.status = "error";
    selection.error = error.message;
  }
  renderIngestionSelections();
}

async function startIngestion() {
  const pending = ingestionSelections.filter(
    (item) => item.status === "selected" || item.status === "error"
  );
  if (!pending.length) return;
  ingestionStartBtn.disabled = true;
  try {
    const runtime = await requestJson("/api/runtime/start-ingestion", { method: "POST" });
    const detail = runtime.mode === "parallel"
      ? " Remote-only ingestion is running in parallel with Live Q&A."
      : " Live Q&A models were unloaded so ingestion can use the GPU.";
    setStatus(`Started ${pending.length} document ingestion job${pending.length === 1 ? "" : "s"} in the background.${detail}`);
  } catch (error) {
    setStatus(`Could not start ingestion: ${error.message}`, false);
    renderIngestionSelections();
    return;
  }
  await Promise.allSettled(pending.map(uploadIngestionSelection));
  await Promise.all([loadIngestionLibrary(), loadProjectSelectors()]);
}

function metadataTags(metadata, prefix = "") {
  return Object.entries(metadata || {}).map(([key, value]) => {
    const tag = document.createElement("span");
    tag.className = "ingestion-tag";
    tag.textContent = `${prefix}${key}=${Array.isArray(value) ? value.join(", ") : value}`;
    return tag;
  });
}

function downloadNormalized(versionId, filename) {
  requestJson(`/api/ingestion/versions/${versionId}/normalized`)
    .then((payload) => {
      const blob = new Blob([JSON.stringify(payload, null, 2)], { type: "application/json" });
      const url = URL.createObjectURL(blob);
      const anchor = document.createElement("a");
      anchor.href = url;
      anchor.download = `${filename}.normalized.json`;
      document.body.appendChild(anchor);
      anchor.click();
      anchor.remove();
      URL.revokeObjectURL(url);
    })
    .catch((error) => setStatus(`Normalized output download failed: ${error.message}`, false));
}

async function approveIngestionVersion(versionId) {
  if (!window.confirm("Approve this normalized version? Any previously approved version of the same file will be archived.")) return;
  try {
    await requestJson(`/api/ingestion/versions/${versionId}/approve`, { method: "POST" });
    setStatus("Document version approved. Its predecessor, if any, was archived.");
    ingestionDetails.delete(versionId);
    await loadIngestionLibrary(true);
  } catch (error) {
    setStatus(`Approval failed: ${error.message}`, false);
  }
}

async function rejectIngestionVersion(versionId) {
  if (!window.confirm("Reject and delete this draft version and its normalized artifacts?")) return;
  try {
    await requestJson(`/api/ingestion/versions/${versionId}`, { method: "DELETE" });
    ingestionDetails.delete(versionId);
    setStatus("Draft document version rejected and removed.");
    await loadIngestionLibrary(true);
  } catch (error) {
    setStatus(`Rejection failed: ${error.message}`, false);
  }
}

async function acceptReviewedVersion(versionId) {
  if (!window.confirm("Accept this reviewed presentation and approve its normalized version?")) return;
  try {
    await requestJson(`/api/ingestion/versions/${versionId}/approve`, { method: "POST" });
    ingestionDetails.delete(versionId);
    setStatus("Presentation accepted. Gate 2 is now PASS.");
    await loadIngestionLibrary(true);
  } catch (error) {
    setStatus(`Accept failed: ${error.message}`, false);
  }
}

async function reingestVersion(versionId, sourcePath) {
  if (!window.confirm(`Edit and save the source file first:\n\n${sourcePath}\n\nReingest it now from this same location?`)) return;
  try {
    await requestJson(`/api/ingestion/versions/${versionId}/reingest`, {
      method: "POST",
    });
    ingestionDetails.delete(versionId);
    setStatus("Presentation reingestion started in the background.");
    await loadIngestionLibrary(true);
  } catch (error) {
    setStatus(`Reingestion failed: ${error.message}`, false);
  }
}

async function deleteIngestionDocument(documentRecord, versionCount) {
  const retry = documentRecord.deletion_status === "DELETE_FAILED";
  const prompt = retry
    ? `Retry deletion of "${documentRecord.title}" and all ${versionCount} version${versionCount === 1 ? "" : "s"}?`
    : `Permanently delete "${documentRecord.title}" and all ${versionCount} version${versionCount === 1 ? "" : "s"}?\n\nThis removes its database records, stored files, normalized outputs, and any Qdrant points.`;
  if (!window.confirm(prompt)) return;

  documentRecord.deletion_status = "DELETING";
  documentRecord.deletion_error = null;
  renderIngestionLibrary();
  try {
    await requestJson(`/api/ingestion/documents/${documentRecord.id}`, {
      method: "DELETE",
    });
    setStatus(`Deleting ${documentRecord.title} and its complete version history in the background.`);
    if (ingestionPollTimer) clearTimeout(ingestionPollTimer);
    ingestionPollTimer = setTimeout(() => loadIngestionLibrary(true), 500);
  } catch (error) {
    documentRecord.deletion_status = "DELETE_FAILED";
    documentRecord.deletion_error = error.message;
    renderIngestionLibrary();
    setStatus(`Document deletion failed: ${error.message}`, false);
  }
}

function renderIngestionVersion(documentRecord, version) {
  const detail = ingestionDetails.get(version.id);
  const extraction = detail?.extraction;
  const processing = (detail?.version?.processing_status || version.processing_status).toLowerCase();
  const lifecycle = (detail?.version?.status || version.status).toLowerCase();
  const active = processing === "queued" || processing === "running";
  const isPresentation = extraction?.source_format === "pptx"
    || documentRecord.title.toLowerCase().endsWith(".pptx");
  const reviewRequired = isPresentation && processing === "needs_review";
  const passed = isPresentation && processing === "ready";
  const card = document.createElement("div");
  card.className = "ingestion-version";
  const head = document.createElement("div");
  head.className = "ingestion-version-head";
  const titleWrap = document.createElement("div");
  const title = document.createElement("div");
  title.className = "ingestion-filename";
  title.textContent = version.version_label;
  const meta = document.createElement("div");
  meta.className = "ingestion-file-meta";
  meta.textContent = version.content_profile;
  titleWrap.append(title, meta);
  const status = document.createElement("span");
  status.className = `ingestion-status ${lifecycle === "approved" || passed ? "approved" : processing}`;
  if (active) {
    const spinner = document.createElement("span");
    spinner.className = "ingestion-spinner";
    status.appendChild(spinner);
  } else {
    if (reviewRequired) status.textContent = "NEEDS REVIEW";
    else if (passed) status.textContent = "PASS";
    else status.textContent = lifecycle === "approved" ? "approved" : processing.replaceAll("_", " ");
  }
  head.append(titleWrap, status);
  card.appendChild(head);

  const tags = document.createElement("div");
  tags.className = "ingestion-tags";
  tags.append(...metadataTags(version.retrieval_metadata));
  if (Object.keys(version.llm_metadata || {}).length) {
    tags.append(...metadataTags(version.llm_metadata, "AI:"));
  }
  if (tags.childElementCount) card.appendChild(tags);

  if (reviewRequired) {
    const path = document.createElement("div");
    path.className = "ingestion-review-path";
    path.textContent = `See ${extraction.source_full_path || version.file_path}`;
    card.appendChild(path);
  } else if (extraction?.quality_report) {
    const quality = document.createElement("div");
    quality.className = "ingestion-file-meta";
    quality.style.marginTop = "8px";
    quality.textContent = `${extraction.quality_report.block_count ?? 0} normalized blocks · ${extraction.quality_report.text_character_count ?? 0} characters`;
    card.appendChild(quality);
  }
  if (!reviewRequired && extraction?.error && extraction.error_code !== "DOCUMENT_DELETING") {
    const error = document.createElement("div");
    error.className = "ingestion-error";
    error.textContent = extraction.error;
    card.appendChild(error);
  }

  const deleting = documentRecord.deletion_status === "DELETING";
  const actions = document.createElement("div");
  actions.className = "ingestion-actions";
  if (!deleting && reviewRequired && lifecycle === "draft") {
    const accept = document.createElement("button");
    accept.className = "primary";
    accept.textContent = "Accept";
    accept.addEventListener("click", () => acceptReviewedVersion(version.id));
    const reingest = document.createElement("button");
    reingest.textContent = "Reingest";
    reingest.addEventListener("click", () =>
      reingestVersion(
        version.id,
        extraction.source_full_path || version.file_path
      )
    );
    const reject = document.createElement("button");
    reject.className = "danger";
    reject.textContent = "Reject";
    reject.addEventListener("click", () => rejectIngestionVersion(version.id));
    actions.append(accept, reingest, reject);
  } else if (!deleting && ["ready", "needs_review"].includes(processing)) {
    const normalized = document.createElement("button");
    normalized.textContent = "Download normalized JSON";
    normalized.addEventListener("click", () => downloadNormalized(version.id, documentRecord.title));
    actions.appendChild(normalized);
  }
  if (!deleting && !reviewRequired && lifecycle === "draft" && processing === "ready") {
    const approve = document.createElement("button");
    approve.className = "primary";
    approve.textContent = "Approve";
    approve.addEventListener("click", () => approveIngestionVersion(version.id));
    actions.appendChild(approve);
  }
  if (!deleting && !reviewRequired && lifecycle === "draft" && !active) {
    const reject = document.createElement("button");
    reject.className = "danger";
    reject.textContent = "Reject";
    reject.addEventListener("click", () => rejectIngestionVersion(version.id));
    actions.appendChild(reject);
  }
  if (actions.childElementCount) card.appendChild(actions);
  return card;
}

function renderIngestionLibrary() {
  ingestionLibraryEl.innerHTML = "";
  const groups = [...ingestionLibrary].sort((left, right) =>
    `${left.project.name}/${left.document.title}`.localeCompare(
      `${right.project.name}/${right.document.title}`
    )
  );
  ingestionLibraryEmptyEl.style.display = groups.length ? "none" : "block";

  for (const group of groups) {
    const { project, document: documentRecord } = group;
    const versions = [...group.versions].sort((left, right) =>
      right.created_at.localeCompare(left.created_at)
    );
    const wrapper = document.createElement("section");
    wrapper.className = "ingestion-document";
    const head = document.createElement("div");
    head.className = "ingestion-document-head";
    const titleWrap = document.createElement("div");
    const title = document.createElement("div");
    title.className = "ingestion-filename";
    title.textContent = documentRecord.title;
    const meta = document.createElement("div");
    meta.className = "ingestion-file-meta";
    meta.textContent = `${project.name} · ${versions.length} version${versions.length === 1 ? "" : "s"}`;
    titleWrap.append(title, meta);

    const controls = document.createElement("div");
    controls.className = "ingestion-document-controls";
    if (documentRecord.deletion_status !== "ACTIVE") {
      const deletionStatus = document.createElement("span");
      deletionStatus.className = `ingestion-status ${documentRecord.deletion_status.toLowerCase()}`;
      if (documentRecord.deletion_status === "DELETING") {
        const spinner = document.createElement("span");
        spinner.className = "ingestion-spinner";
        deletionStatus.appendChild(spinner);
      } else {
        deletionStatus.textContent = "delete failed";
      }
      controls.appendChild(deletionStatus);
    }
    const remove = document.createElement("button");
    remove.className = "ingestion-document-delete";
    remove.type = "button";
    remove.textContent = "×";
    remove.title = documentRecord.deletion_status === "DELETE_FAILED"
      ? "Retry deleting this document"
      : "Delete this document and all versions";
    remove.setAttribute("aria-label", remove.title);
    remove.disabled = documentRecord.deletion_status === "DELETING";
    remove.addEventListener("click", () =>
      deleteIngestionDocument(documentRecord, versions.length)
    );
    controls.appendChild(remove);
    head.append(titleWrap, controls);
    wrapper.appendChild(head);

    if (documentRecord.deletion_error) {
      const error = document.createElement("div");
      error.className = "ingestion-error";
      error.textContent = documentRecord.deletion_error;
      wrapper.appendChild(error);
    }
    if (!versions.length) {
      const empty = document.createElement("div");
      empty.className = "ingestion-file-meta";
      empty.textContent = "No versions registered.";
      wrapper.appendChild(empty);
    }
    for (const version of versions) {
      wrapper.appendChild(renderIngestionVersion(documentRecord, version));
    }
    ingestionLibraryEl.appendChild(wrapper);
  }
}

async function loadIngestionLibrary(force = false) {
  if (ingestionLibraryLoading && !force) return;
  ingestionLibraryLoading = true;
  if (ingestionPollTimer) clearTimeout(ingestionPollTimer);
  try {
    ingestionLibrary = await requestJson("/api/ingestion/library");
    const versions = ingestionLibrary.flatMap((group) => group.versions);
    const versionById = new Map(versions.map((version) => [version.id, version]));
    for (const selection of ingestionSelections) {
      if (!selection.versionId) continue;
      const current = versionById.get(selection.versionId);
      if (current) selection.status = current.processing_status.toLowerCase();
    }
    renderIngestionSelections();
    const detailCandidates = versions.filter((version) =>
      ["QUEUED", "RUNNING"].includes(version.processing_status)
      || ["QUEUED", "RUNNING"].includes(
        ingestionDetails.get(version.id)?.version?.processing_status
      )
      || !ingestionDetails.has(version.id)
    );
    await Promise.all(detailCandidates.map(async (version) => {
      try {
        ingestionDetails.set(
          version.id,
          await requestJson(`/api/ingestion/versions/${version.id}`)
        );
      } catch (_) {
        // The library still provides enough information to render this version.
      }
    }));
    renderIngestionLibrary();
    const stillActive = ingestionLibrary.some(
      (group) => group.document.deletion_status === "DELETING"
    ) || versions.some((version) =>
      ["QUEUED", "RUNNING"].includes(
        ingestionDetails.get(version.id)?.version?.processing_status || version.processing_status
      )
    );
    if (stillActive) {
      ingestionPollTimer = setTimeout(() => loadIngestionLibrary(), 1500);
    }
  } catch (error) {
    ingestionLibraryEl.innerHTML = "";
    ingestionLibraryEmptyEl.style.display = "block";
    ingestionLibraryEmptyEl.textContent = `Ingestion service unavailable: ${error.message}`;
  } finally {
    ingestionLibraryLoading = false;
  }
}

ingestionSelectBtn.addEventListener("click", () => ingestionFileInputEl.click());
ingestionFileInputEl.addEventListener("change", () => addIngestionFiles(ingestionFileInputEl.files));
ingestionStartBtn.addEventListener("click", startIngestion);
ingestionRefreshBtn.addEventListener("click", () => {
  loadIngestionLibrary(true);
  loadProjectSelectors();
});
ingestionProjectSelect.addEventListener("change", updateIngestionProjectChoice);
ingestionNewProjectEl.addEventListener("input", renderIngestionSelections);

function upsertOrchestrationRun(run) {
  const index = orchestrationRuns.findIndex((item) => item.run_id === run.run_id);
  if (index >= 0) orchestrationRuns[index] = run;
  else orchestrationRuns.unshift(run);
  orchestrationRuns.sort((a, b) => b.created_at.localeCompare(a.created_at));
  renderQuestions();
}

function questionDraftsFromCard(card) {
  return [...card.querySelectorAll(".question-draft")].map((row) => ({
    id: row.dataset.questionId,
    question: row.querySelector("textarea").value.trim(),
    source_excerpt: row.dataset.sourceExcerpt || "",
    review_status: row.dataset.reviewStatus || "pending",
  })).filter((item) => item.question);
}

async function submitQuestionReview(run, card, row, action, buttons) {
  const questions = questionDraftsFromCard(card);
  const questionId = row.dataset.questionId;
  if (!questions.some((item) => item.id === questionId)) {
    setStatus("Enter question text before reviewing it.", false);
    return;
  }
  for (const button of buttons) button.disabled = true;
  const feedback = row.querySelector(".question-feedback").value.trim();
  if (action === "regenerate") startPhaseIndicator("generation");
  try {
    const updated = await requestJson(`/api/orchestrator/runs/${run.run_id}/review`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ action, question_id: questionId, questions, feedback }),
    });
    if (action === "regenerate" && updated.accepted) {
      activeGenerationRunIds.add(run.run_id);
      upsertOrchestrationRun({ ...run, status: "generating" });
      setStatus("Question regeneration started in the background.");
      return;
    }
    if (action === "regenerate") {
      finishPhaseIndicator("generation", updated.question_generation_ms);
    }
    upsertOrchestrationRun(updated);
    const messages = {
      approve: "Question validated.",
      reject: "Question rejected.",
      regenerate: "Question regenerated and ready for review.",
    };
    setStatus(messages[action]);
  } catch (e) {
    if (action === "regenerate") finishPhaseIndicator("generation");
    setStatus(`Review failed: ${e.message}`, false);
    for (const button of buttons) button.disabled = false;
  }
}

function renderQuestions() {
  questionsListEl.innerHTML = "";
  questionsEmptyEl.style.display = orchestrationRuns.length === 0 ? "block" : "none";
  questionsClearBtn.disabled = orchestrationRuns.length === 0;

  for (const run of orchestrationRuns) {
    const card = document.createElement("section");
    card.className = "question-run";

    const header = document.createElement("div");
    header.className = "question-run-header";
    const meta = document.createElement("span");
    meta.className = "question-run-meta";
    meta.textContent = `${run.project_name ? `${run.project_name} · ` : ""}${formatTimestamp(Date.parse(run.created_at))} · attempt ${run.attempt + 1}`;
    const status = document.createElement("span");
    status.className = `run-status ${run.status}`;
    status.textContent = run.status.replaceAll("_", " ");
    header.append(meta, status);
    card.appendChild(header);

    const timings = document.createElement("div");
    timings.className = "question-timings";
    timings.innerHTML = [
      `<span>Generation: ${formatDuration(run.question_generation_ms)}</span>`,
      `<span>Retrieval: ${formatDuration(run.question_retrieval_ms)}</span>`,
      `<span>Response returned: ${formatDuration(run.response_return_ms, "not returned")}</span>`,
    ].join("");
    card.appendChild(timings);

    const source = document.createElement("div");
    source.className = "question-source";
    source.textContent = run.source_text;
    card.appendChild(source);

    const editable = run.status === "awaiting_review";
    run.questions.forEach((draft, index) => {
      const reviewStatus = draft.review_status || "pending";
      const row = document.createElement("div");
      row.className = `question-draft ${reviewStatus}`;
      row.dataset.questionId = draft.id;
      row.dataset.sourceExcerpt = draft.source_excerpt || "";
      row.dataset.reviewStatus = reviewStatus;

      const rowHead = document.createElement("div");
      rowHead.className = "question-draft-head";
      const number = document.createElement("span");
      number.className = "question-number";
      number.textContent = `Question ${index + 1}`;
      const questionStatus = document.createElement("span");
      questionStatus.className = `question-review-status ${reviewStatus}`;
      questionStatus.textContent = reviewStatus;
      rowHead.append(number, questionStatus);
      row.appendChild(rowHead);

      const text = document.createElement("textarea");
      text.value = draft.question;
      text.disabled = !editable || reviewStatus !== "pending";
      text.setAttribute("aria-label", `Question ${index + 1}`);
      row.appendChild(text);

      if (draft.source_excerpt) {
        const excerpt = document.createElement("p");
        excerpt.className = "question-excerpt";
        excerpt.textContent = `Source: “${draft.source_excerpt}”`;
        row.appendChild(excerpt);
      }

      if (editable && reviewStatus === "pending") {
        const feedback = document.createElement("textarea");
        feedback.className = "question-feedback";
        feedback.placeholder = "Optional guidance for regenerating this question";
        feedback.setAttribute("aria-label", `Regeneration guidance for question ${index + 1}`);
        row.appendChild(feedback);

        const actions = document.createElement("div");
        actions.className = "question-actions";
        const approve = document.createElement("button");
        approve.className = "primary";
        approve.textContent = "Validate";
        const regenerate = document.createElement("button");
        regenerate.textContent = "Regenerate";
        const reject = document.createElement("button");
        reject.className = "danger";
        reject.textContent = "Reject";
        const buttons = [approve, regenerate, reject];
        approve.addEventListener("click", () => submitQuestionReview(run, card, row, "approve", buttons));
        regenerate.addEventListener("click", () => submitQuestionReview(run, card, row, "regenerate", buttons));
        reject.addEventListener("click", () => submitQuestionReview(run, card, row, "reject", buttons));
        actions.append(approve, regenerate, reject);
        row.appendChild(actions);
      }
      card.appendChild(row);
    });

    if (run.questions.length === 0) {
      const noDraft = document.createElement("p");
      noDraft.className = "question-excerpt";
      noDraft.textContent = run.status === "generating"
        ? "Question generation is running in the background."
        : "No responsible question was generated. Add one manually for review.";
      card.appendChild(noDraft);
    }

    if (editable) {
      const add = document.createElement("button");
      add.className = "question-add";
      add.type = "button";
      add.textContent = "+ Add question";
      add.addEventListener("click", () => {
        run.questions = questionDraftsFromCard(card);
        run.questions.push({
          id: `manual-${Date.now()}`,
          question: "",
          source_excerpt: "",
          review_status: "pending",
        });
        renderQuestions();
      });
      card.appendChild(add);
    }

    if (run.error) {
      const error = document.createElement("p");
      error.className = "question-excerpt";
      error.textContent = run.error;
      card.appendChild(error);
    }
    questionsListEl.appendChild(card);
  }
  updatePhaseSummaryFromLatestRun();
}

async function loadAgentDefaults() {
  try {
    const options = await requestJson("/api/orchestrator/model-options");
    providerCatalog = options.provider_catalog || [];
    populateProviderSelect(supervisorProviderEl);
    populateProviderSelect(questionProviderEl);

    supervisorProviderEl.value = options.supervisor_default.provider;
    supervisorModelEl.value = options.supervisor_default.model;
    questionProviderEl.value = options.question_default.provider;
    questionModelEl.value = options.question_default.model;

    const supervisorParameters = selectionParameters(options.supervisor_default);
    const questionParameters = selectionParameters(options.question_default);
    agentParameterState.supervisor[supervisorProviderEl.value] = supervisorParameters;
    agentParameterState.question[questionProviderEl.value] = questionParameters;
    renderAgentParameters("supervisor", supervisorParameters);
    renderAgentParameters("question", questionParameters);
    supervisorProviderEl.dataset.renderedProvider = supervisorProviderEl.value;
    questionProviderEl.dataset.renderedProvider = questionProviderEl.value;
  } catch (e) {
    setStatus(`Agent configuration unavailable: ${e.message}`, false);
  }
}

async function loadOrchestrationRuns() {
  try {
    orchestrationRuns = await requestJson("/api/orchestrator/runs");
    renderQuestions();
  } catch (e) {
    setStatus(`Question history unavailable: ${e.message}`, false);
  }
}

// --- History tab: full session log of every capture, pending/sent/discarded

function formatTimestamp(ts) {
  const d = new Date(ts);
  return d.toLocaleString(undefined, { hour: "2-digit", minute: "2-digit", second: "2-digit", month: "short", day: "numeric" });
}

// Row ids currently expanded in the History tab (not persisted -- resets
// on reload, which is fine since it's just a view-state toggle).
const expandedHistoryIds = new Set();

function renderHistory() {
  historyListEl.innerHTML = "";
  const sorted = [...capturedItems].sort((a, b) => b.ts - a.ts);
  historyEmptyEl.style.display = sorted.length === 0 ? "block" : "none";

  for (const item of sorted) {
    const row = document.createElement("div");
    row.className = "history-item";
    const isExpanded = expandedHistoryIds.has(item.id);
    row.classList.toggle("expanded", isExpanded);

    const meta = document.createElement("div");
    meta.className = "meta";
    const metaLeft = document.createElement("span");
    metaLeft.textContent = formatTimestamp(item.ts);
    meta.appendChild(metaLeft);

    const metaRight = document.createElement("div");
    metaRight.className = "meta-right";

    // Send/Discard live in the expanded preview's top-right, next to the
    // status badge, per conversation history -- shown only when expanded
    // and only while the item is still pending (matches the modal's
    // existing rules: sent items get no Send button, and there is
    // nothing meaningful to discard once sent).
    if (item.status === "pending") {
      const actions = document.createElement("div");
      actions.className = "actions";

      const sendBtn = document.createElement("button");
      sendBtn.className = "primary";
      sendBtn.textContent = "Send";
      sendBtn.addEventListener("click", (e) => {
        e.stopPropagation();
        sendCapturedItem(item, sendBtn);
      });
      actions.appendChild(sendBtn);

      const discardBtn = document.createElement("button");
      discardBtn.className = "danger";
      discardBtn.textContent = "Discard";
      discardBtn.addEventListener("click", (e) => {
        e.stopPropagation();
        discardCapturedItem(item);
      });
      actions.appendChild(discardBtn);

      metaRight.appendChild(actions);
    }

    const badge = document.createElement("span");
    badge.className = `badge ${item.status}`;
    badge.textContent = item.status;
    metaRight.appendChild(badge);
    meta.appendChild(metaRight);
    row.appendChild(meta);

    const snippet = document.createElement("div");
    snippet.className = "snippet";
    snippet.textContent = item.text;
    row.appendChild(snippet);

    // Clicking the row expands/collapses it in place instead of opening
    // the modal (History-tab-only behavior; the sidebar queue still uses
    // the modal via openCapturedModal).
    row.addEventListener("click", () => {
      if (expandedHistoryIds.has(item.id)) {
        expandedHistoryIds.delete(item.id);
      } else {
        expandedHistoryIds.add(item.id);
      }
      renderHistory();
    });
    historyListEl.appendChild(row);
  }
}


function saveHistory() {
  try {
    localStorage.setItem(HISTORY_STORAGE_KEY, JSON.stringify(capturedItems));
  } catch (e) {
    // localStorage can throw in private-browsing/quota-exceeded cases;
    // history persistence is a convenience, not required for the app
    // to function, so fail silently.
  }
}

// loadHistory() intentionally removed (see conversation history): History
// now always starts empty on page load/reload rather than restoring a
// prior session's localStorage contents. saveHistory() below still
// persists on every change, purely as an incidental safety net against
// an accidental tab crash mid-session -- nothing reads it back at
// bootstrap anymore.

document.getElementById("history-export-btn").addEventListener("click", () => {
  const lines = [...capturedItems]
    .sort((a, b) => a.ts - b.ts)
    .map((i) => `[${formatTimestamp(i.ts)}] (${i.status})\n${i.text}\n`);
  const blob = new Blob([lines.join("\n")], { type: "text/plain" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = `liveqna-captures-${new Date().toISOString().slice(0, 19).replace(/[:T]/g, "-")}.txt`;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
});

document.getElementById("history-clear-btn").addEventListener("click", () => {
  if (capturedItems.length === 0) return;
  if (!window.confirm("Clear all captured history? This cannot be undone.")) return;
  capturedItems = [];
  renderCapturedList();
});

async function clearQuestionsFromServer() {
  await requestJson("/api/orchestrator/runs", { method: "DELETE" });
  for (const runId of activeGenerationRunIds) {
    finishPhaseIndicator("generation");
  }
  activeGenerationRunIds.clear();
  orchestrationRuns = [];
  renderQuestions();
}

questionsClearBtn.addEventListener("click", async () => {
  if (orchestrationRuns.length === 0) return;
  if (!window.confirm("Clear all generated questions? This cannot be undone.")) return;
  questionsClearBtn.disabled = true;
  try {
    await clearQuestionsFromServer();
    setStatus("Generated questions cleared.");
  } catch (e) {
    questionsClearBtn.disabled = false;
    setStatus(`Clear questions failed: ${e.message}`, false);
  }
});

// --- tab switching ---------------------------------------------------------

function activateTab(name) {
  for (const b of document.querySelectorAll(".tab-btn")) {
    b.classList.toggle("active", b.dataset.tab === name);
  }
  for (const p of document.querySelectorAll(".tab-panel")) p.classList.remove("active");
  document.getElementById(`tab-${name}`).classList.add("active");
}

for (const btn of document.querySelectorAll(".tab-btn")) {
  btn.addEventListener("click", () => activateTab(btn.dataset.tab));
}

function updateLevel(level) {
  const pct = Math.max(0, Math.min(1, level));
  levelBarFill.style.width = `${(pct * 100).toFixed(1)}%`;
  const isLive = pct > 0.02;
  levelDot.classList.toggle("green", isLive);
  levelDot.classList.toggle("red", false);
  levelLabel.textContent = isLive ? `${Math.round(pct * 100)}%` : "no signal";

  if (levelResetTimer) clearTimeout(levelResetTimer);
  levelResetTimer = setTimeout(() => {
    levelBarFill.style.width = "0%";
    levelDot.classList.remove("green");
    levelLabel.textContent = "no signal";
  }, 2000);
}

function connect() {
  const proto = location.protocol === "https:" ? "wss:" : "ws:";
  ws = new WebSocket(`${proto}//${location.host}/ws/client`);

  ws.onopen = () => {
    setConnDot(true);
    send("status");
  };
  ws.onclose = () => {
    setConnDot(false);
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
        setStatus("Audio capture pipeline started");
        startPending = false;
        setCaptureDot(true);
        break;
      case "capture_stopped":
        setStatus("Audio capture pipeline stopped");
        startPending = false;
        setCaptureDot(false);
        break;
      case "status":
        setCaptureDot(!!(msg.capture && msg.capture.running));
        break;
      case "orchestration_run":
        if (activeGenerationRunIds.delete(msg.run.run_id)) {
          finishPhaseIndicator("generation", msg.run.question_generation_ms);
        }
        upsertOrchestrationRun(msg.run);
        setStatus("Questions generated and ready for independent review.");
        break;
      case "orchestration_run_failed": {
        if (activeGenerationRunIds.delete(msg.run_id)) {
          finishPhaseIndicator("generation");
        }
        const existing = orchestrationRuns.find((run) => run.run_id === msg.run_id);
        if (existing) {
          upsertOrchestrationRun({ ...existing, status: "failed", error: msg.error });
        }
        setStatus(`Question generation failed: ${msg.error}`, false);
        break;
      }
      case "error":
        setStatus(msg.message, false);
        startPending = false;
        updateCaptureButtons();
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

liveProjectSelect.addEventListener("change", updateCaptureButtons);
refreshLiveProjectsBtn.addEventListener("click", loadProjectSelectors);

startBtn.addEventListener("click", async () => {
  if (!wsConnected) {
    setStatus("Cannot start capture: live transcript is not connected.", false);
    return;
  }
  const project = selectedLiveProject();
  if (!project) {
    setStatus("Select a project before starting the live call.", false);
    return;
  }
  const selected = sourceSelect.value;
  if (!selected) {
    setStatus("No source selected", false);
    return;
  }
  // Optimistically disable Start the moment it's clicked (see
  // conversation history: the user wants it disabled immediately, not
  // only once the server round-trip confirms capture_started).
  // startPending is cleared by the capture_started/capture_stopped/error
  // WS handlers below, whichever comes back.
  startPending = true;
  activeLiveProject = project;
  updateCaptureButtons();
  try {
    await requestJson("/api/runtime/start-live", { method: "POST" });
  } catch (error) {
    startPending = false;
    activeLiveProject = null;
    updateCaptureButtons();
    setStatus(`Could not start Live Q&A: ${error.message}`, false);
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

// Fully resets the live transcript view and all per-segment tracking
// state tied to it (DOM nodes, split/highlight bookkeeping, push-to-talk
// in-progress buffer) -- shared by "Clear transcript" and "Clear all" so
// neither leaves stale segment_id references behind that could resurface
// oddly if a segment_id happened to repeat.
function clearTranscript() {
  if (pttCapturing) {
    // Don't silently drop an in-progress capture -- end it the same way
    // Stop would, just without the trailing synced line break (there is
    // no transcript left for it to apply to).
    pttCapturing = false;
    updatePttDot();
    pttBtn.textContent = "Start capturing";
    pttBtn.classList.remove("recording");
    updateCaptureButtons();
  }
  resetInProgressPushToTalkCapture();
  transcriptEl.innerHTML = "";
  utteranceEls.clear();
  transcriptSegmentOrder = [];
  segmentSplits.clear();
  allSegmentFinal.clear();
  lastRenderedSegId = null;
}

document.getElementById("clear-btn").addEventListener("click", clearTranscript);

// Clears the browser-side views in one shot. The user-triggered Clear all
// handler deletes durable question runs first; bootstrap uses this local
// reset without deleting backend history before it reloads that history.
function clearAllState() {
  clearTranscript();
  capturedItems = [];
  orchestrationRuns = [];
  ingestionSelections = [];
  renderCapturedList();
  renderQuestions();
  renderIngestionSelections();
  saveHistory();
  statusEl.innerHTML = "";
}

document.getElementById("clear-all-btn").addEventListener("click", async () => {
  if (!window.confirm("Clear everything -- transcript, history, questions, and logging? This cannot be undone.")) return;
  try {
    await clearQuestionsFromServer();
    clearAllState();
  } catch (e) {
    setStatus(`Clear all failed: ${e.message}`, false);
  }
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

supervisorProviderEl.addEventListener("change", () => handleAgentProviderChange("supervisor"));
supervisorModelEl.addEventListener("input", () => {
  renderAgentParameters("supervisor", readAgentParameters("supervisor"));
});
questionProviderEl.addEventListener("change", () => handleAgentProviderChange("question"));
questionModelEl.addEventListener("input", () => {
  renderAgentParameters("question", readAgentParameters("question"));
});

// History auto-clears on every fresh page load/reload (see conversation
// history: a "new instance" means a new browser session, not a new
// capture run) -- so, unlike before, we deliberately do NOT restore
// capturedItems from localStorage on bootstrap. The persisted value is
// still written on every change (saveHistory) only so an accidental tab
// crash/reload mid-session isn't instantly catastrophic, but a normal
// reload always starts from an empty History.
clearAllState();
renderCapturedList();
updateCaptureButtons();
updatePttDot();
renderQuestions();
renderIngestionSelections();
loadIngestionLibrary();
loadProjectSelectors();
loadAgentDefaults();
loadOrchestrationRuns();
connect();
