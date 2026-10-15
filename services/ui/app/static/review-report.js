const root = document.getElementById("review-workflow");
const versionId = document.body.dataset.versionId;
let panelOpen = false;

function element(tag, text, className) {
  const node = document.createElement(tag);
  if (text !== undefined && text !== null) node.textContent = text;
  if (className) node.className = className;
  return node;
}

async function request(path, options = {}) {
  const response = await fetch(path, options);
  const body = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(body.detail || `Request failed (${response.status})`);
  }
  return body;
}

function renderQuestion(question, answerInputs) {
  const resolution = question.resolution;
  const card = element(
    "div",
    null,
    `review-question${resolution?.status === "APPLIED" ? " applied" : ""}`
  );
  card.append(
    element("div", `${question.severity} · ${question.category}`, "severity"),
    element("h3", question.question),
    element("p", `Discrepancy: ${question.description}`),
    element("p", `Evidence: ${question.evidence}`, "evidence")
  );

  if (resolution?.status === "APPLIED") {
    card.append(
      element("p", `Your answer: ${resolution.answer}`),
      element("p", `Resolution applied: ${resolution.rationale}`, "success")
    );
    return card;
  }

  const answer = document.createElement("textarea");
  answer.placeholder = "Enter your answer for this inconsistency…";
  answer.value = resolution?.answer || "";
  answer.setAttribute("aria-label", question.question);
  answer.disabled = Boolean(resolution && !resolution.remaining_uncertainty);
  answerInputs.push({question, answer});
  card.appendChild(answer);

  if (resolution) {
    const proposal = element("div", null, "proposal");
    proposal.append(
      element("h4", "Proposed resolution"),
      element("p", resolution.rationale),
      element("pre", resolution.resolved_markdown)
    );
    if (resolution.remaining_uncertainty) {
      proposal.append(
        element(
          "p",
          resolution.follow_up_question || "More clarification is required.",
          "error"
        )
      );
    }
    card.appendChild(proposal);
  }
  return card;
}

async function resolveAll(answerInputs, button, message) {
  const unanswered = answerInputs.filter(
    ({question}) =>
      !question.resolution || question.resolution.remaining_uncertainty
  );
  if (!unanswered.length) return;
  if (unanswered.some(({answer}) => !answer.value.trim())) {
    message.textContent = "Answer every unresolved question before continuing.";
    message.className = "message error";
    return;
  }
  button.disabled = true;
  for (let index = 0; index < unanswered.length; index += 1) {
    const {question, answer} = unanswered[index];
    button.textContent = `Resolving ${index + 1} of ${unanswered.length}…`;
    message.textContent = "The resolver is checking each slide and answer.";
    try {
      await request(
        `/api/ingestion/versions/${versionId}/review-questions/${encodeURIComponent(question.id)}/resolve`,
        {
          method: "POST",
          headers: {"Content-Type": "application/json"},
          body: JSON.stringify({answer: answer.value.trim()}),
        }
      );
    } catch (error) {
      message.textContent = error.message;
      message.className = "message error";
      button.disabled = false;
      button.textContent = "Resolve all with AI";
      return;
    }
  }
  panelOpen = true;
  await loadWorkflow();
}

async function applyAll(questions, button, message) {
  button.disabled = true;
  const pending = questions.filter(
    (question) => question.resolution?.status !== "APPLIED"
  );
  for (let index = 0; index < pending.length; index += 1) {
    const question = pending[index];
    button.textContent = `Applying ${index + 1} of ${pending.length}…`;
    try {
      await request(
        `/api/ingestion/versions/${versionId}/review-questions/${encodeURIComponent(question.id)}/apply`,
        {method: "POST"}
      );
    } catch (error) {
      message.textContent = error.message;
      message.className = "message error";
      button.disabled = false;
      button.textContent = "Apply all resolutions";
      return;
    }
  }
  panelOpen = true;
  await loadWorkflow();
}

function renderWorkflow(data) {
  root.replaceChildren();
  root.appendChild(
    element(
      "div",
      `${data.applied_count} of ${data.question_count} inconsistencies resolved`,
      `review-progress${data.complete ? " success" : ""}`
    )
  );
  if (!data.groups.length) {
    root.appendChild(element("p", "No answerable inconsistencies were found."));
    return;
  }

  const openButton = element(
    "button",
    panelOpen ? "Hide inconsistency questions" : "Resolve inconsistencies",
    "primary"
  );
  const panel = element("div");
  panel.hidden = !panelOpen;
  openButton.addEventListener("click", () => {
    panelOpen = !panelOpen;
    panel.hidden = !panelOpen;
    openButton.textContent = panelOpen
      ? "Hide inconsistency questions"
      : "Resolve inconsistencies";
  });
  root.append(openButton, panel);

  const answerInputs = [];
  const allQuestions = [];
  for (const group of data.groups) {
    const section = element("section", null, "review-slide");
    section.appendChild(
      element("h2", `${group.locator.label}: ${group.title}`)
    );
    if (group.artifact_path) {
      const image = document.createElement("img");
      image.src = `/api/ingestion/versions/${versionId}/review-artifacts/${group.artifact_path}`;
      image.alt = `Rendered ${group.locator.label}`;
      section.appendChild(image);
    }
    for (const question of group.questions) {
      allQuestions.push(question);
      section.appendChild(renderQuestion(question, answerInputs));
    }
    panel.appendChild(section);
  }

  const message = element("div", "", "message");
  const unanswered = allQuestions.filter(
    (question) =>
      !question.resolution || question.resolution.remaining_uncertainty
  );
  if (unanswered.length) {
    const resolve = element("button", "Resolve all with AI", "primary");
    resolve.addEventListener("click", () =>
      resolveAll(answerInputs, resolve, message)
    );
    panel.append(resolve, message);
  } else {
    const uncertain = allQuestions.some(
      (question) => question.resolution?.remaining_uncertainty
    );
    if (uncertain) {
      panel.appendChild(
        element("p", "At least one answer needs further clarification.", "error")
      );
    } else if (!data.complete) {
      const apply = element("button", "Apply all resolutions", "primary");
      apply.addEventListener("click", () =>
        applyAll(allQuestions, apply, message)
      );
      panel.append(apply, message);
    }
  }

  if (data.complete) {
    panelOpen = true;
    panel.hidden = false;
    root.appendChild(
      element(
        "p",
        "All inconsistencies are resolved and applied. Return to the app, review the document status, and select Accept for final approval.",
        "success"
      )
    );
    const returnButton = element("button", "Return to app", "primary");
    returnButton.type = "button";
    returnButton.addEventListener("click", () => {
      window.location.assign("/");
    });
    root.appendChild(returnButton);
  }
}

async function loadWorkflow() {
  try {
    renderWorkflow(
      await request(`/api/ingestion/versions/${versionId}/review-workflow`)
    );
  } catch (error) {
    root.replaceChildren(element("p", error.message, "error"));
  }
}

loadWorkflow();
