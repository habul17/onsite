// The page: pick a round, talk to the interviewer, then read your report.
// Everything the model writes is shown with textContent, never as HTML.
"use strict";

const $ = (id) => document.getElementById(id);
const HIRE = ["Strong Hire", "Hire", "Lean Hire"];

let options = null;
let round = null; // the latest round state from the server
let codeSent = ""; // the editor text the interviewer has already seen
let boardSent = ""; // the same for the whiteboard
let busy = false;
let graded = false;
let editor = null;
let board = null;

async function api(path, body) {
  const init = body === undefined ? {} : { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) };
  let res;
  try {
    res = await fetch(path, init);
  } catch {
    throw new Error("Can't reach onsite. Is `onsite web` still running?");
  }
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(typeof data.detail === "string" ? data.detail : `Something went wrong (${res.status}).`);
  return data;
}

function el(tag, text, className) {
  const node = document.createElement(tag);
  if (text !== undefined) node.textContent = text;
  if (className) node.className = className;
  return node;
}

function show(screen) {
  for (const id of ["setup", "round", "report"]) $(id).hidden = id !== screen;
}

function showError(id, message) {
  $(id).textContent = message;
  $(id).hidden = !message;
}

function roundName(track, level) {
  const name = options.tracks.find((t) => t.id === track).name;
  return `${name} round, ${level} level`;
}

// 1. Pick a round

function addChoice(box, name, value, text, checked) {
  const label = el("label");
  const input = el("input");
  Object.assign(input, { type: "radio", name, value, checked });
  label.append(input, text);
  box.append(label);
}

function picked(name) {
  return document.querySelector(`input[name="${name}"]:checked`).value;
}

function showPool() {
  const track = options.tracks.find((t) => t.id === picked("track"));
  const level = picked("level");
  let text = `${track.questions[level]} ${track.name.toLowerCase()} questions at ${level} level.`;
  if (track.id === "system_design" && level === "junior") text += " Design rounds are rare for juniors, but you can still practise one.";
  $("pool").textContent = text;
}

async function setup() {
  try {
    options = await api("/api/options");
  } catch (err) {
    showError("setup-error", err.message);
    return;
  }
  options.tracks.forEach((t, i) => addChoice($("tracks"), "track", t.id, t.name, i === 0));
  options.levels.forEach((lv) => addChoice($("levels"), "level", lv, lv[0].toUpperCase() + lv.slice(1), lv === "mid"));
  $("setup-form").addEventListener("change", showPool);
  showPool();
}

$("setup-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  if (!options || busy) return;
  busy = true;
  $("start").disabled = true;
  showError("setup-error", "");
  try {
    begin(await api("/api/rounds", { track: picked("track"), level: picked("level") }));
  } catch (err) {
    showError("setup-error", err.message);
  } finally {
    busy = false;
    $("start").disabled = false;
  }
});

// 2. The round

function begin(state) {
  round = state;
  codeSent = "";
  boardSent = board.signature();
  graded = false;
  $("log").replaceChildren();
  $("answer").value = "";
  showError("error", "");
  $("round-label").textContent = roundName(state.track, state.level);

  const coding = state.track === "coding";
  const design = state.track === "system_design";
  $("code-pane").hidden = !coding;
  $("board-pane").hidden = !design;
  $("round").querySelector(".round-grid").classList.toggle("with-side", coding || design);
  editor.value = "";
  board.clear();
  boardSent = board.signature();

  show("round");
  update(state);
  $("answer").focus();
}

function plural(n, word, many = `${word}s`) {
  return `${n} ${n === 1 ? word : many}`;
}

function addMessage(speaker, text, code, drawn) {
  const li = el("li", undefined, speaker);
  li.append(el("span", speaker === "interviewer" ? "Interviewer" : "You", "who"));
  if (text) li.append(text);
  if (code) li.append(el("span", `${text ? "\n" : ""}+ your code (${plural(code.split("\n").length, "line")})`, "muted"));
  if (drawn) {
    const what = `${plural(drawn.boxes, "box", "boxes")}, ${plural(drawn.arrows, "arrow")}`;
    li.append(el("span", `${text || code ? "\n" : ""}+ your whiteboard (${what})`, "muted"));
  }
  $("log").append(li);
  li.scrollIntoView({ block: "nearest" });
  return li;
}

function update(state) {
  round = state;
  addMessage("interviewer", state.message);
  $("progress").textContent = state.done ? "No more questions" : `Question ${state.question} of ${state.questions}`;

  // Keep the problem in view for coding and design rounds, where you work on one question for a long time.
  const pinned = state.track !== "behavioral";
  $("problem").hidden = !pinned;
  if (pinned) {
    $("problem-title").textContent = state.title;
    $("problem-text").textContent = state.prompt;
  }

  $("finish").classList.toggle("primary", state.done);
  $("send").classList.toggle("primary", !state.done);
  setBusy(false);
  if (state.done) $("finish").focus();
}

function setBusy(on, message = "Interviewer is thinking…") {
  busy = on;
  $("thinking").textContent = message;
  $("thinking").hidden = !on;
  const over = round && round.done;
  $("send").disabled = on || over;
  $("skip").disabled = on || over;
  $("finish").disabled = on;
}

function codeToSend() {
  if (round.track !== "coding") return null;
  return editor.value;
}

async function send() {
  if (busy || !round || round.done) return;
  const text = $("answer").value.trim();
  const code = codeToSend();
  const newCode = code !== null && code.trim() !== "" && code !== codeSent;
  const drawing = round.track === "system_design" ? board.toJSON() : null;
  const signature = board.signature();
  const newBoard = drawing !== null && board.size.boxes > 0 && signature !== boardSent;
  if (!text && !newCode && !newBoard) {
    showError("error", "Write an answer first.");
    return;
  }

  showError("error", "");
  const li = addMessage("candidate", text, newCode ? code : null, newBoard ? board.size : null);
  $("answer").value = "";
  setBusy(true);
  try {
    const state = await api(`/api/rounds/${round.id}/answer`, { text, code, board: drawing });
    if (newCode) codeSent = code;
    if (newBoard) boardSent = signature;
    update(state);
  } catch (err) {
    // Nothing was recorded, so put the answer back to try again.
    li.remove();
    $("answer").value = text;
    showError("error", err.message);
    setBusy(false);
  }
}

$("answer-form").addEventListener("submit", (e) => {
  e.preventDefault();
  send();
});

$("answer").addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey && !e.isComposing) {
    e.preventDefault();
    send();
  }
});

$("skip").addEventListener("click", async () => {
  if (busy || !round) return;
  showError("error", "");
  setBusy(true);
  try {
    const state = await api(`/api/rounds/${round.id}/skip`, {});
    addMessage("candidate", "(skipped)");
    update(state);
  } catch (err) {
    showError("error", err.message);
    setBusy(false);
  }
});

$("finish").addEventListener("click", async () => {
  if (busy || !round) return;
  if (!round.done && !confirm("Finish now? You'll be graded on what you've answered so far.")) return;
  showError("error", "");
  setBusy(true, "The hiring committee is grading your round…");
  try {
    showReport(await api(`/api/rounds/${round.id}/finish`, {}));
  } catch (err) {
    showError("error", err.message);
    setBusy(false);
  }
});

window.addEventListener("beforeunload", (e) => {
  if (round && !graded && $("log").children.length > 1) e.preventDefault();
});

// 3. The report

function showReport({ report, saved_to }) {
  graded = true;
  setBusy(false);
  $("report-kind").textContent = roundName(report.track, report.level);
  $("decision").textContent = report.recommendation;
  $("decision").className = HIRE.includes(report.recommendation) ? "hire" : "no-hire";
  $("mean").textContent = `Average ${report.mean.toFixed(2)} out of 4`;

  const table = $("scores");
  table.replaceChildren();
  const head = el("tr");
  head.append(el("th", "Rubric item"));
  report.questions.forEach((_, i) => head.append(el("th", `Q${i + 1}`)));
  head.append(el("th", "Average", "avg"));
  table.append(head);

  let capped = false;
  for (const criterion of report.questions[0].scores) {
    const row = el("tr");
    row.append(el("td", criterion.name));
    for (const q of report.questions) {
      const s = q.scores.find((x) => x.criterion_id === criterion.criterion_id);
      capped ||= s.capped;
      row.append(el("td", s.capped ? `${s.score}*` : String(s.score)));
    }
    row.append(el("td", report.criterion_means[criterion.criterion_id].toFixed(1), "avg"));
    table.append(row);
  }
  $("capped-note").hidden = !capped;

  const feedback = $("feedback");
  feedback.replaceChildren();
  report.questions.forEach((q, i) => {
    const box = el("div", undefined, "question");
    box.append(el("h3", `Q${i + 1}. ${q.title}${q.skipped ? " (skipped)" : ""}`));
    if (q.based_on) box.append(el("p", `From your resume: "${q.based_on}"`, "muted"));
    if (!q.skipped) {
      list(box, "What went well", q.strengths);
      list(box, "What to work on", q.gaps);
      box.append(el("h4", "A stronger answer"), el("p", q.stronger_answer));
      box.append(evidence(q));
    }
    feedback.append(box);
  });

  $("saved-to").textContent = saved_to;
  $("round-label").textContent = "";
  show("report");
  $("decision").focus();
  window.scrollTo(0, 0);
}

function list(box, title, items) {
  if (!items.length) return;
  const ul = el("ul");
  for (const item of items) ul.append(el("li", item));
  box.append(el("h4", title), ul);
}

function evidence(q) {
  const details = el("details");
  details.append(el("summary", "Why each score"));
  for (const s of q.scores) {
    details.append(el("h4", `${s.name}: ${s.score}${s.capped ? " (no real quote backed a higher score)" : ""}`));
    details.append(el("p", s.reasoning));
    for (const quote of s.evidence) details.append(el("blockquote", quote));
  }
  return details;
}

$("again").addEventListener("click", () => {
  round = null;
  show("setup");
  $("start").focus();
});

editor = codeEditor($("code"), $("gutter"), { onSubmit: send });
board = whiteboard({
  svg: $("board"),
  kind: $("board-kind"),
  newLabel: $("board-new-label"),
  addForm: $("board-add-form"),
  from: $("board-from"),
  to: $("board-to"),
  arrowLabel: $("board-arrow-label"),
  connect: $("board-connect"),
  connectForm: $("board-connect-form"),
  label: $("board-label"),
  remove: $("board-delete"),
});
setup();
