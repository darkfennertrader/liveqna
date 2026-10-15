// liveqna frontend: single WS to the ui backend. The ui backend relays
// transcript events from stt and brokers configure/start/stop calls to
// capture and stt.

const sourceSelect = document.getElementById("source-select");
const liveProjectSelect = document.getElementById("live-project-select");
const refreshLiveProjectsBtn = document.getElementById("refresh-live-projects");
const backendSelect = document.getElementById("backend-select");
const transcriptEl = document.getElementById("transcript");
const ingestionDotEl = document.getElementById("ingestion-dot");
const connDot = document.getElementById("conn-dot");
const statusEl = document.getElementById("status");
const groupLocal = document.getElementById("group-local");
const groupOnline = document.getElementById("group-online");
const levelDot = document.getElementById("level-dot");
const levelBarFill = document.getElementById("level-bar-fill");
const levelLabel = document.getElementById("level-label");
const startBtn = document.getElementById("start-btn");
const stopBtn = document.getElementById("stop-btn");
const startGateHint = document.getElementById("start-gate-hint");
const historyListEl = document.getElementById("history-list");
const historyEmptyEl = document.getElementById("history-empty");
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
const ingestionNewProjectEl = document.getElementById("ingestion-new-project");
const ingestionCreateProjectBtn = document.getElementById("ingestion-create-project-btn");
const ingestionDeleteProjectBtn = document.getElementById("ingestion-delete-project-btn");
const ingestionProjectGateEl = document.getElementById("ingestion-project-gate");
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
let projectDeletionPollTimer = null;
let projectMutationPending = false;
let ingestionRunning = false;
const ingestionDetails = new Map();
let activeLiveProject = null;

function liveSessionBlocksIngestion() {
  return captureRunning || startPending || liveSessionEnding || activeLiveProject !== null;
}

function updateOperationalIndicators() {
  const liveBusy = liveSessionBlocksIngestion();
  ingestionDotEl.classList.toggle("green", !liveBusy);
  ingestionDotEl.classList.toggle("red", liveBusy);
  ingestionDotEl.classList.toggle("working", ingestionRunning && !liveBusy);
  ingestionDotEl.title = liveBusy
    ? "Ingestion unavailable while Live Q&A is active"
    : ingestionRunning
      ? "Document ingestion is running"
      : "Ingestion ready";

  connDot.classList.toggle("green", !ingestionRunning);
  connDot.classList.toggle("red", ingestionRunning);
  connDot.classList.toggle("working", liveBusy && !ingestionRunning);
  connDot.title = ingestionRunning
    ? "Live Q&A unavailable while ingestion is running"
    : liveBusy
      ? "Live transcript is active"
      : "Live Q&A ready";
}

function setIngestionWorking(working) {
  ingestionRunning = working;
  updateOperationalIndicators();
  updateCaptureButtons();
  updateIngestionStartAvailability();
}

function updateIngestionStartAvailability() {
  const pending = ingestionSelections.some(
    (item) => item.status === "selected" || item.status === "error"
  );
  ingestionStartBtn.disabled = !pending || ingestionRunning || liveSessionBlocksIngestion();
}

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
let liveSessionEnding = false;
// Set the instant Start is clicked, cleared once the server actually
// confirms capture_started/capture_stopped or reports an error -- lets
// Start disable itself immediately on click (see conversation history)
// without prematurely flipping captureRunning/the "Live transcript" dot,
// which must only reflect a *confirmed* running pipeline.
let startPending = false;

// Browser-side history of the server's automatic READY / WAIT / IGNORE
// decisions. The server owns buffering and candidate boundaries.
let capturedItems = [];
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

function selectedIngestionProject() {
  const option = ingestionProjectSelect.selectedOptions[0];
  if (!option || !option.value) return null;
  return {
    id: option.value,
    name: option.dataset.projectName || option.textContent,
    deletionStatus: option.dataset.deletionStatus || "ACTIVE",
  };
}

function updateIngestionProjectChoice() {
  const project = selectedIngestionProject();
  const ready = project?.deletionStatus === "ACTIVE" && !projectMutationPending;
  ingestionSelectBtn.disabled = !ready;
  ingestionFileInputEl.disabled = !ready;
  ingestionDeleteProjectBtn.disabled = !project || projectMutationPending;
  ingestionCreateProjectBtn.disabled = projectMutationPending || !ingestionNewProjectEl.value.trim();
  ingestionProjectGateEl.textContent = project?.deletionStatus === "DELETE_FAILED"
    ? "Project deletion failed. Use Delete project to retry."
    : "Select or create a project before selecting source files.";
  ingestionProjectGateEl.style.display = ready ? "none" : "block";
  renderIngestionSelections();
}

async function loadProjectSelectors(preferredIngestionProjectId = null) {
  const previousLive = liveProjectSelect.value;
  const previousIngestion = ingestionProjectSelect.value;
  refreshLiveProjectsBtn.disabled = true;
  try {
    const projects = await requestJson("/api/ingestion/projects");
    liveProjectSelect.innerHTML = "";
    ingestionProjectSelect.innerHTML = "";

    const activeProjects = projects.filter(
      (project) => (project.deletion_status || "ACTIVE") === "ACTIVE"
    );
    const livePlaceholder = document.createElement("option");
    livePlaceholder.value = "";
    livePlaceholder.textContent = activeProjects.length ? "Select project..." : "No projects available";
    liveProjectSelect.appendChild(livePlaceholder);
    const ingestionPlaceholder = document.createElement("option");
    ingestionPlaceholder.value = "";
    ingestionPlaceholder.textContent = "Select project...";
    ingestionProjectSelect.appendChild(ingestionPlaceholder);

    for (const project of activeProjects) {
      for (const select of [liveProjectSelect, ingestionProjectSelect]) {
        const option = document.createElement("option");
        option.value = project.id;
        option.dataset.projectName = project.name;
        option.dataset.deletionStatus = project.deletion_status || "ACTIVE";
        option.textContent = project.name;
        select.appendChild(option);
      }
    }
    for (const project of projects.filter(
      (candidate) => candidate.deletion_status === "DELETE_FAILED"
    )) {
      const option = document.createElement("option");
      option.value = project.id;
      option.dataset.projectName = project.name;
      option.dataset.deletionStatus = project.deletion_status;
      option.textContent = `${project.name} (delete failed)`;
      ingestionProjectSelect.appendChild(option);
    }
    if ([...liveProjectSelect.options].some((option) => option.value === previousLive)) {
      liveProjectSelect.value = previousLive;
    }
    const requestedIngestion = preferredIngestionProjectId || previousIngestion;
    if ([...ingestionProjectSelect.options].some((option) => option.value === requestedIngestion)) {
      ingestionProjectSelect.value = requestedIngestion;
    }
    return projects;
  } catch (error) {
    liveProjectSelect.innerHTML = '<option value="">Projects unavailable</option>';
    ingestionProjectSelect.innerHTML = '<option value="">Projects unavailable</option>';
    setStatus(`Could not load projects: ${error.message}`, false);
    return null;
  } finally {
    refreshLiveProjectsBtn.disabled = false;
    updateIngestionProjectChoice();
    updateCaptureButtons();
  }
}

function updateCaptureButtons() {
  const project = selectedLiveProject();
  startBtn.disabled = !wsConnected || !project || captureRunning || startPending || liveSessionEnding || ingestionRunning;
  stopBtn.disabled = !captureRunning || startPending || liveSessionEnding;
  liveProjectSelect.disabled = captureRunning || startPending || liveSessionEnding;
  refreshLiveProjectsBtn.disabled = captureRunning || startPending || liveSessionEnding;
  if (ingestionRunning) {
    startGateHint.textContent = "Wait for document ingestion to finish before starting Live Q&A.";
    startGateHint.style.display = "block";
  } else if (!project) {
    startGateHint.textContent = "Select a project before starting the live call.";
    startGateHint.style.display = "block";
  } else if (!wsConnected) {
    startGateHint.textContent = "Waiting for live transcript connection...";
    startGateHint.style.display = "block";
  } else {
    startGateHint.style.display = "none";
  }
  updateOperationalIndicators();
  updateIngestionStartAvailability();
}


// "Live transcript" means transcript is actually flowing right now, which
// requires BOTH the browser's WebSocket to be open AND the audio capture
// pipeline to be running -- just having the WS open (true from the moment
// the page loads) is not enough on its own (see conversation history: the
// dot was wrongly green before Start was ever pressed).
let liveTranscriptActive = false;
function updateConnDot() {
  const live = wsConnected && captureRunning;
  updateOperationalIndicators();
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


function isScrolledToBottom(el, thresholdPx = 40) {
  return el.scrollHeight - el.scrollTop - el.clientHeight <= thresholdPx;
}

function trimTranscriptIfNeeded() {
  while (transcriptSegmentOrder.length > MAX_TRANSCRIPT_SEGMENTS) {
    const oldId = transcriptSegmentOrder.shift();
    const el = utteranceEls.get(oldId);
    if (el) {
      const prev = el.previousSibling;
      if (prev && prev.nodeType === Node.TEXT_NODE) prev.remove();
      el.remove();
    }
    utteranceEls.delete(oldId);
  }
}

function appendTranscriptEvent(ev) {
  const segId = ev.segment_id;
  const wasAtBottom = isScrolledToBottom(transcriptEl);
  let textEl = segId != null ? utteranceEls.get(segId) : null;

  if (!textEl) {
    textEl = document.createElement("span");
    textEl.className = "utterance-text";
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
  textEl.classList.remove("final", "partial");
  textEl.classList.add(isPartial ? "partial" : "final");
  textEl.textContent = ev.text;

  if (wasAtBottom) transcriptEl.scrollTop = transcriptEl.scrollHeight;
}

function renderCapturedList() {
  renderHistory();
  saveHistory();
}

function recordAutomaticCandidate(message) {
  const id = `${message.session_id}:${message.sequence}`;
  if (capturedItems.some((item) => item.id === id)) return;
  const previouslyWait = capturedItems.some(
    (item) => item.sessionId === message.session_id && item.status === "wait"
  );
  if (message.decision !== "wait") {
    capturedItems = capturedItems.filter(
      (item) => !(item.sessionId === message.session_id && item.status === "wait")
    );
  }
  const result = {
    id,
    sessionId: message.session_id,
    text: message.source_text,
    status: message.decision,
    reason: message.reason || "",
    ts: Date.now(),
    runId: message.run_id || null,
    projectId: activeLiveProject?.id || null,
    projectName: activeLiveProject?.name || null,
    previouslyWait: message.decision === "ready" && previouslyWait,
  };
  capturedItems.push(result);
  renderCapturedList();
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
  updateIngestionStartAvailability();

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
    size.textContent = `${humanFileSize(selection.file.size)} · Project: ${selection.projectName}`;
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
  const project = selectedIngestionProject();
  if (!project || project.deletionStatus !== "ACTIVE") {
    ingestionFileInputEl.value = "";
    setStatus("Select or create a project before selecting source files.", false);
    updateIngestionProjectChoice();
    return;
  }
  for (const file of files) {
    ingestionSelections.push({
      id: crypto.randomUUID(),
      file,
      projectId: project.id,
      projectName: project.name,
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
    form.append("project_id", selection.projectId);
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
  setIngestionWorking(true);
  ingestionStartBtn.disabled = true;
  try {
    const runtime = await requestJson("/api/runtime/start-ingestion", { method: "POST" });
    const detail = runtime.mode === "remote_ingestion"
      ? " Live Q&A is unavailable until ingestion finishes."
      : " Live Q&A models were unloaded so ingestion can use the GPU.";
    setStatus(`Started ${pending.length} document ingestion job${pending.length === 1 ? "" : "s"} in the background.${detail}`);
  } catch (error) {
    setIngestionWorking(false);
    setStatus(`Could not start ingestion: ${error.message}`, false);
    renderIngestionSelections();
    return;
  }
  await Promise.allSettled(pending.map(uploadIngestionSelection));
  await Promise.all([loadIngestionLibrary(), loadProjectSelectors()]);
}

async function createIngestionProject() {
  const name = ingestionNewProjectEl.value.trim();
  if (!name || projectMutationPending) return;
  projectMutationPending = true;
  updateIngestionProjectChoice();
  try {
    const project = await requestJson("/api/ingestion/projects", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name }),
    });
    ingestionNewProjectEl.value = "";
    await loadProjectSelectors(project.id);
    setStatus(`Project "${project.name}" created. You can now select source files.`);
  } catch (error) {
    setStatus(`Project creation failed: ${error.message}`, false);
  } finally {
    projectMutationPending = false;
    updateIngestionProjectChoice();
  }
}

function scheduleProjectDeletionPoll(projectId, projectName) {
  if (projectDeletionPollTimer) clearTimeout(projectDeletionPollTimer);
  projectDeletionPollTimer = setTimeout(async () => {
    const projects = await loadProjectSelectors();
    await loadIngestionLibrary(true);
    if (!projects) {
      scheduleProjectDeletionPoll(projectId, projectName);
      return;
    }
    const project = projects.find((candidate) => candidate.id === projectId);
    if (!project) {
      projectDeletionPollTimer = null;
      setStatus(`Project "${projectName}" and all linked data were deleted.`);
      return;
    }
    if (project.deletion_status === "DELETE_FAILED") {
      projectDeletionPollTimer = null;
      setStatus(`Project deletion failed: ${project.deletion_error || "unknown error"}`, false);
      return;
    }
    scheduleProjectDeletionPoll(projectId, projectName);
  }, 1000);
}

async function deleteSelectedIngestionProject() {
  const project = selectedIngestionProject();
  if (!project || projectMutationPending) return;
  const warning = `Permanently delete project "${project.name}"?\n\nThis deletes every linked document and version, stored source and normalized files, ingestion database records, and Qdrant points.`;
  if (!window.confirm(warning)) return;

  projectMutationPending = true;
  updateIngestionProjectChoice();
  try {
    const result = await requestJson(`/api/ingestion/projects/${project.id}`, {
      method: "DELETE",
    });
    ingestionSelections = ingestionSelections.filter(
      (selection) => selection.projectId !== project.id
    );
    ingestionProjectSelect.value = "";
    renderIngestionSelections();
    setStatus(
      `Deleting project "${project.name}" in the background: ${result.document_count} document${result.document_count === 1 ? "" : "s"}, ${result.version_count} version${result.version_count === 1 ? "" : "s"}.`
    );
    scheduleProjectDeletionPoll(project.id, project.name);
  } catch (error) {
    setStatus(`Project deletion failed: ${error.message}`, false);
  } finally {
    projectMutationPending = false;
    updateIngestionProjectChoice();
  }
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

async function reingestVersion(versionId, sourcePath) {
  if (!window.confirm(`Edit and save the source file first:\n\n${sourcePath}\n\nReingest it now from this same location?`)) return;
  setIngestionWorking(true);
  try {
    await requestJson("/api/runtime/start-ingestion", { method: "POST" });
    await requestJson(`/api/ingestion/versions/${versionId}/reingest`, {
      method: "POST",
    });
    ingestionDetails.delete(versionId);
    setStatus("Presentation reingestion started in the background.");
    await loadIngestionLibrary(true);
  } catch (error) {
    setIngestionWorking(false);
    setStatus(`Reingestion failed: ${error.message}`, false);
  }
}

async function deleteIngestionDocument(documentRecord, versionCount) {
  const retry = documentRecord.deletion_status === "DELETE_FAILED";
  const versionScope = versionCount === 1
    ? "including its only registered version"
    : `including the current version and all ${versionCount - 1} previous versions (${versionCount} versions total)`;
  const prompt = retry
    ? `Retry deletion of "${documentRecord.title}", ${versionScope}?`
    : `Permanently delete "${documentRecord.title}", ${versionScope}?\n\nThis removes its database records, stored files, normalized outputs, and any Qdrant points.`;
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

function createIngestionDeleteButton(documentRecord, versionCount) {
  const remove = document.createElement("button");
  remove.className = "action-delete";
  remove.type = "button";
  remove.textContent = "Delete";
  remove.title = "Delete the entire document, all versions, stored files, database records, normalized outputs, and Qdrant data.";
  remove.addEventListener("click", () =>
    deleteIngestionDocument(documentRecord, versionCount)
  );
  return remove;
}

function renderIngestionVersion(documentRecord, version, versionCount, showDocumentDelete) {
  const detail = ingestionDetails.get(version.id);
  const extraction = detail?.extraction;
  const processing = (detail?.version?.processing_status || version.processing_status).toLowerCase();
  const lifecycle = (detail?.version?.status || version.status).toLowerCase();
  const active = processing === "queued" || processing === "running";
  const isPresentation = extraction?.source_format === "pptx"
    || documentRecord.title.toLowerCase().endsWith(".pptx");
  const reviewRequired = processing === "needs_review";
  const passed = processing === "ready";
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
    const sourcePath = extraction.source_full_path || version.file_path;
    path.textContent = `Source: ${sourcePath.replace(/^\/home\/ray(?=\/|$)/, "~")}`;
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
    accept.className = "action-accept";
    accept.textContent = "Accept";
    accept.disabled = true;
    accept.title = "Resolve every inconsistency before accepting this document.";
    const resolve = document.createElement("button");
    resolve.className = "action-resolve";
    resolve.textContent = "Resolve";
    resolve.addEventListener("click", () => {
      window.open(
        `/api/ingestion/versions/${version.id}/review-report`,
        "_blank",
        "noopener,noreferrer"
      );
    });
    const reingest = document.createElement("button");
    reingest.className = "action-reingest";
    reingest.textContent = "Reingest";
    reingest.addEventListener("click", () =>
      reingestVersion(
        version.id,
        extraction.source_full_path || version.file_path
      )
    );
    actions.append(accept, resolve, reingest);
  } else if (!deleting && ["ready", "needs_review"].includes(processing)) {
    if (isPresentation) {
      const report = document.createElement("button");
      report.textContent = "Open review report";
      report.addEventListener("click", () => {
        window.open(
          `/api/ingestion/versions/${version.id}/review-report`,
          "_blank",
          "noopener,noreferrer"
        );
      });
      actions.appendChild(report);
    }
    const normalized = document.createElement("button");
    normalized.textContent = "Download normalized JSON";
    normalized.addEventListener("click", () => downloadNormalized(version.id, documentRecord.title));
    actions.appendChild(normalized);
  }
  if (!deleting && !reviewRequired && lifecycle === "draft" && processing === "ready") {
    const approve = document.createElement("button");
    approve.className = "action-accept";
    approve.textContent = "Accept";
    approve.addEventListener("click", () => approveIngestionVersion(version.id));
    actions.appendChild(approve);
  }
  if (!deleting && !active && showDocumentDelete) {
    actions.appendChild(createIngestionDeleteButton(documentRecord, versionCount));
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
    for (const [index, version] of versions.entries()) {
      wrapper.appendChild(renderIngestionVersion(
        documentRecord,
        version,
        versions.length,
        index === 0
      ));
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
    const ingestionActive = versions.some((version) =>
      ["QUEUED", "RUNNING"].includes(
        ingestionDetails.get(version.id)?.version?.processing_status || version.processing_status
      )
    ) || ingestionSelections.some((selection) =>
      ["uploading", "queued"].includes(selection.status)
    );
    setIngestionWorking(ingestionActive);
    const stillActive = ingestionLibrary.some(
      (group) => group.document.deletion_status === "DELETING"
    ) || ingestionActive;
    if (stillActive) {
      ingestionPollTimer = setTimeout(() => loadIngestionLibrary(), 1500);
    }
  } catch (error) {
    const localWorkActive = ingestionSelections.some((selection) =>
      ["uploading", "queued"].includes(selection.status)
    );
    setIngestionWorking(localWorkActive);
    ingestionLibraryEl.innerHTML = "";
    ingestionLibraryEmptyEl.style.display = "block";
    ingestionLibraryEmptyEl.textContent = `Ingestion service unavailable: ${error.message}`;
  } finally {
    ingestionLibraryLoading = false;
  }
}

ingestionSelectBtn.addEventListener("click", () => {
  if (selectedIngestionProject()?.deletionStatus !== "ACTIVE") {
    setStatus("Select or create a project before selecting source files.", false);
    return;
  }
  ingestionFileInputEl.click();
});
ingestionFileInputEl.addEventListener("change", () => addIngestionFiles(ingestionFileInputEl.files));
ingestionStartBtn.addEventListener("click", startIngestion);
ingestionCreateProjectBtn.addEventListener("click", createIngestionProject);
ingestionDeleteProjectBtn.addEventListener("click", deleteSelectedIngestionProject);
ingestionRefreshBtn.addEventListener("click", () => {
  loadIngestionLibrary(true);
  loadProjectSelectors();
});
ingestionProjectSelect.addEventListener("change", updateIngestionProjectChoice);
ingestionNewProjectEl.addEventListener("input", updateIngestionProjectChoice);
ingestionNewProjectEl.addEventListener("keydown", (event) => {
  if (event.key === "Enter") {
    event.preventDefault();
    createIngestionProject();
  }
});

function upsertOrchestrationRun(run) {
  const index = orchestrationRuns.findIndex((item) => item.run_id === run.run_id);
  if (index >= 0) orchestrationRuns[index] = run;
  else orchestrationRuns.unshift(run);
  orchestrationRuns.sort((a, b) => b.created_at.localeCompare(a.created_at));
  renderQuestions();
}

function questionDraftsFromCard(card, run) {
  const visibleDrafts = [...card.querySelectorAll(".question-draft")].map((row) => ({
    id: row.dataset.questionId,
    question: row.querySelector("textarea").value.trim(),
    source_excerpt: row.dataset.sourceExcerpt || "",
    review_status: row.dataset.reviewStatus || "pending",
  })).filter((item) => item.question);
  const visibleIds = new Set(visibleDrafts.map((item) => item.id));
  const hiddenRejected = run.questions.filter(
    (item) => item.review_status === "rejected" && !visibleIds.has(item.id)
  );
  return [...visibleDrafts, ...hiddenRejected];
}

async function submitQuestionReview(run, card, row, action, buttons) {
  const questions = questionDraftsFromCard(card, run);
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
  const hiddenRunStatuses = new Set(["ignored", "failed", "rejected"]);
  const visibleRuns = orchestrationRuns.filter(
    (run) => !hiddenRunStatuses.has(run.status)
  );
  questionsEmptyEl.style.display = visibleRuns.length === 0 ? "block" : "none";
  questionsClearBtn.disabled = orchestrationRuns.length === 0;

  for (const run of visibleRuns) {
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

    const editable = run.status === "awaiting_review";
    const visibleQuestions = run.questions.filter((draft) => {
      const reviewStatus = draft.review_status || "pending";
      if (run.status === "validated") return reviewStatus === "validated";
      return reviewStatus !== "rejected";
    });
    visibleQuestions.forEach((draft, index) => {
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
        : run.status === "failed"
          ? "Question generation failed. No question was produced."
          : "No responsible question was generated. Add one manually for review.";
      card.appendChild(noDraft);
    }

    if (editable) {
      const add = document.createElement("button");
      add.className = "question-add";
      add.type = "button";
      add.textContent = "+ Add question";
      add.addEventListener("click", () => {
        run.questions = questionDraftsFromCard(card, run);
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

// --- History tab: automatic transcript candidate decisions ----------------

function formatTimestamp(ts) {
  const d = new Date(ts);
  return d.toLocaleString(undefined, { hour: "2-digit", minute: "2-digit", second: "2-digit", month: "short", day: "numeric" });
}

// Row ids currently expanded in the History tab (not persisted -- resets
// on reload, which is fine since it's just a view-state toggle).
const expandedHistoryIds = new Set();

function renderHistory() {
  historyListEl.innerHTML = "";
  const sorted = capturedItems
    .filter((item) => item.status !== "ignore")
    .sort((a, b) => b.ts - a.ts);
  historyEmptyEl.style.display = sorted.length === 0 ? "block" : "none";

  for (const item of sorted) {
    const row = document.createElement("div");
    row.className = `history-item ${item.status}`;
    const isExpanded = expandedHistoryIds.has(item.id);
    row.classList.toggle("expanded", isExpanded);

    const meta = document.createElement("div");
    meta.className = "meta";
    const metaLeft = document.createElement("span");
    metaLeft.textContent = formatTimestamp(item.ts);
    meta.appendChild(metaLeft);

    const metaRight = document.createElement("div");
    metaRight.className = "meta-right";

    const badge = document.createElement("span");
    badge.className = `badge ${item.status}`;
    badge.textContent = item.status;
    metaRight.appendChild(badge);
    if (item.status === "ready" && item.previouslyWait) {
      const transitionBadge = document.createElement("span");
      transitionBadge.className = "badge previously-wait";
      transitionBadge.textContent = "Previously WAIT";
      metaRight.appendChild(transitionBadge);
    }
    if (item.questionGenerationFailed) {
      const failureBadge = document.createElement("span");
      failureBadge.className = "badge generation-failed";
      failureBadge.textContent = "Question generation failed";
      metaRight.appendChild(failureBadge);
    }
    meta.appendChild(metaRight);
    row.appendChild(meta);

    const snippet = document.createElement("div");
    snippet.className = "snippet";
    snippet.textContent = item.text;
    row.appendChild(snippet);

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

document.getElementById("history-logs-btn").addEventListener("click", async () => {
  try {
    const response = await fetch("/api/live-logs/pdf");
    if (!response.ok) {
      const body = await response.json().catch(() => ({}));
      throw new Error(body.detail || `Log download failed (${response.status})`);
    }
    const blob = await response.blob();
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    const disposition = response.headers.get("Content-Disposition") || "";
    const filenameMatch = disposition.match(/filename="?([^";]+)"?/i);
    a.download = filenameMatch?.[1] || "LIVE-QA-LOGS.pdf";
    document.body.appendChild(a);
    a.click();
    a.remove();
    URL.revokeObjectURL(url);
    setStatus("Live Q&A logs downloaded as PDF.");
  } catch (error) {
    setStatus(`Could not download logs: ${error.message}`, false);
  }
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
      case "session_assessment_started":
        startPhaseIndicator("generation");
        setStatus(`Automatic candidate ${msg.sequence} is being assessed.`);
        break;
      case "session_candidate":
        recordAutomaticCandidate(msg);
        if (msg.decision === "ready" && msg.run_id) {
          activeGenerationRunIds.add(msg.run_id);
          setStatus("Transcript candidate is ready; questions are being generated.");
        } else {
          finishPhaseIndicator("generation", msg.assessment_ms);
          const detail = msg.decision === "wait"
            ? "Supervisor is waiting for more context."
            : "Transcript candidate did not require a question.";
          setStatus(detail);
        }
        break;
      case "session_candidate_failed":
        finishPhaseIndicator("generation");
        setStatus(`Automatic transcript assessment failed: ${msg.error}`, false);
        break;
      case "live_session_status":
        if (msg.status === "ended") {
          liveSessionEnding = false;
          activeLiveProject = null;
          updateCaptureButtons();
          setStatus("Live Q&A session ended and its remaining transcript was flushed.");
        }
        break;
      case "orchestration_run":
        if (activeGenerationRunIds.delete(msg.run.run_id)) {
          finishPhaseIndicator("generation", msg.run.question_generation_ms);
        }
        upsertOrchestrationRun(msg.run);
        setStatus(msg.run.status === "ignored"
          ? "No responsible question was generated for this candidate."
          : "Questions generated and ready for independent review.");
        break;
      case "orchestration_run_failed": {
        if (activeGenerationRunIds.delete(msg.run_id)) {
          finishPhaseIndicator("generation");
        }
        const existing = orchestrationRuns.find((run) => run.run_id === msg.run_id);
        const failedRun = msg.run || existing;
        if (failedRun) {
          upsertOrchestrationRun({ ...failedRun, status: "failed", error: msg.error });
        }
        const historyItem = capturedItems.find((item) => item.runId === msg.run_id);
        if (historyItem) {
          historyItem.questionGenerationFailed = true;
          renderCapturedList();
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
    await requestJson("/api/runtime/start-live", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        project_id: project.id,
        project_name: project.name,
        supervisor_model: agentModelSelection("supervisor"),
        question_model: agentModelSelection("question"),
      }),
    });
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


document.getElementById("stop-btn").addEventListener("click", async () => {
  liveSessionEnding = true;
  stopBtn.disabled = true;
  try {
    await requestJson("/api/runtime/end-live", { method: "POST" });
    setStatus("Ending Live Q&A; remaining transcript will be assessed asynchronously.");
  } catch (error) {
    liveSessionEnding = false;
    stopBtn.disabled = false;
    updateCaptureButtons();
    setStatus(`Could not end Live Q&A: ${error.message}`, false);
  }
});

function clearTranscript() {
  transcriptEl.innerHTML = "";
  utteranceEls.clear();
  transcriptSegmentOrder = [];
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
  const warning = [
    "Clear all will permanently delete:",
    "",
    "- the transcript currently displayed in this browser",
    "- captured History stored by this browser",
    "- all generated-question runs and their human review state",
    "- pending file selections that have not been ingested",
    "- messages in the Logging tab",
    "- every Gate 2 inspection run in .cache/ingestion/runs",
    "- every persistent Live Q&A log in .data/live-session-logs",
    "",
    "It will NOT delete projects, ingested documents or versions, database records, Qdrant vectors, or downloaded model caches.",
    "",
    "This cannot be undone. Continue?",
  ].join("\n");
  if (!window.confirm(warning)) return;
  try {
    await requestJson("/api/clear-all", { method: "DELETE" });
    for (const runId of activeGenerationRunIds) {
      finishPhaseIndicator("generation");
    }
    activeGenerationRunIds.clear();
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
renderQuestions();
renderIngestionSelections();
loadIngestionLibrary();
loadProjectSelectors();
loadAgentDefaults();
loadOrchestrationRuns();
connect();
