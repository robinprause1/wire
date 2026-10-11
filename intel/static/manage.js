/* Manage: desks, their sources, schedule and brief. One desk is shown at a time, set up in four steps.
   The map above follows the step: it moves to the stations the step is about. */
import WorldMap from "./map.js";
import Line from "./line.js";
import { STATIONS, api, costWord, deskFromUrl, escape, rememberDesk, stationLines, statusWord, when } from "./shared.js";

const params = new URLSearchParams(location.search);
const Stage = params.get("stage") === "line" ? Line : WorldMap;

const $ = (id) => document.getElementById(id);
const stage = $("stage");
const notice = $("notice");
const stationList = $("stations");

const STEPS_ORDER = ["desk", "sources", "brief", "schedule"];

/* Which part of the route each step is about: the map view and the stations it lights. */
const STEP_VIEW = { desk: "overview", sources: "west", brief: "east", schedule: "clock" };
const STEP_STATIONS = { desk: [], sources: ["fetch", "ingest"], brief: ["retrieve", "judge"], schedule: [] };
const CAPTIONS = {
  draft: ["New desk", "The route is printed but idle. It comes alive as soon as the desk exists."],
  running: ["Running", "Stories are moving through the four stations right now."],
  desk: ["01 · Desk", "One run, four stations: stories come in from the sources in the west and end at Jev in the east."],
  sources: ["02 · Sources", "Everything you add in this step is pulled at station 01 and written to the index at station 02."],
  brief: ["03 · Question", "Station 03 picks the candidates from the window; station 04 asks Jev your question about each of them."],
  schedule: ["04 · Schedule", "The marker is the clock. It travels the route once per cycle and starts a run when it reaches Jev."],
};

let options = { connectors: [], pickers: [], templates: [], steps: [] };
let desks = [];
let deskId = "";
let desk = null;
let board = null;
let boardTimer = 0;
let noticeTimer = 0;
let creating = false;
let step = "desk";

function tell(message, kind = "info") {
  notice.textContent = message;
  notice.dataset.kind = kind;
  notice.hidden = false;
  window.clearTimeout(noticeTimer);
  if (kind !== "error") noticeTimer = window.setTimeout(() => (notice.hidden = true), 6000);
}

function describe(response, fallback) {
  return response.data?.error || response.data?.message || fallback;
}

/* ---------- loading ---------- */

async function loadOptions() {
  const response = await api("GET", "/api/options");
  if (response.ok) options = response.data;
}

async function loadDesks() {
  const response = await api("GET", "/api/desks");
  desks = response.ok && Array.isArray(response.data.desks) ? response.data.desks : [];
  if (!desks.some((entry) => entry.id === deskId)) deskId = deskFromUrl(desks);
  paintTabs();
}

async function loadDesk() {
  if (!deskId) {
    desk = null;
    board = null;
    paintAll();
    if (!creating) startNew();
    return;
  }
  const [deskResponse, boardResponse] = await Promise.all([api("GET", `/api/desks/${deskId}`), api("GET", `/api/desks/${deskId}/board`)]);
  if (!deskResponse.ok) {
    tell(describe(deskResponse, "Desk not found."), "error");
    return;
  }
  desk = deskResponse.data.desk;
  board = boardResponse.ok ? boardResponse.data : null;
  rememberDesk(deskId);
  paintAll();
}

async function refreshBoard() {
  window.clearTimeout(boardTimer);
  if (deskId) {
    const response = await api("GET", `/api/desks/${deskId}/board`);
    if (response.ok) {
      board = response.data;
      paintBoard();
    }
  }
  boardTimer = window.setTimeout(refreshBoard, board?.running ? 4000 : 15_000);
}

async function selectDesk(id) {
  deskId = id;
  creating = false;
  Stage.setDraft?.(false);
  paintTabs();
  await loadDesk();
}

/* ---------- the step flow ---------- */

function goStep(next, { scroll = false } = {}) {
  if (!STEPS_ORDER.includes(next)) return;
  const changed = next !== step;
  step = next;
  paintSteps();
  paintStations();
  paintCaption();
  Stage.focus?.(STEP_VIEW[step]);
  if (changed) enterPanel();
  if (scroll) stage.scrollIntoView({ block: "start", behavior: "smooth" });
}

/* The incoming panel slides up a little, so the change of step is visible. */
function enterPanel() {
  const panel = document.querySelector(`.panel[data-step="${step}"]`);
  if (!panel) return;
  panel.classList.remove("enter");
  void panel.offsetWidth;
  panel.classList.add("enter");
  panel.addEventListener("animationend", () => panel.classList.remove("enter"), { once: true });
}

function startNew() {
  creating = true;
  step = "desk";
  paintTabs();
  paintDeskForm();
  paintSteps();
  paintStations();
  paintCaption();
  Stage.setDraft?.(true);
  Stage.focus?.("overview");
  stage.classList.add("draft");
  $("run-now").disabled = true;
  $("desk-name").focus();
}

function stopNew() {
  creating = false;
  Stage.setDraft?.(false);
  stage.classList.remove("draft");
  paintAll();
}

function stepDone(id) {
  if (!desk || creating) return false;
  switch (id) {
    case "desk":
      return Boolean(desk.name);
    case "sources":
      return (desk.sources ?? []).length > 0;
    case "brief":
      return Boolean(desk.brief?.question);
    case "schedule":
      return true;
    default:
      return false;
  }
}

function stepSummary(id) {
  if (creating || !desk) return id === "desk" ? "Name and description" : "After the desk exists";
  const sources = desk.sources ?? [];
  const brief = desk.brief ?? {};
  const schedule = desk.schedule ?? {};
  switch (id) {
    case "desk":
      return desk.summary || desk.name;
    case "sources":
      return sources.length
        ? `${sources.length} ${sources.length === 1 ? "source" : "sources"}, ${sources.filter((source) => source.enabled).length} on`
        : "No sources yet";
    case "brief": {
      const picker = (options.pickers ?? []).find((entry) => entry.id === brief.picker)?.label ?? "";
      return brief.question ? `${picker} · ${brief.windowHours ?? 36} h window` : "No question yet";
    }
    case "schedule":
      if (!schedule.enabled) return "By hand only";
      return schedule.mode === "interval" ? `Every ${schedule.everyMinutes} minutes` : "Every full hour";
    default:
      return "";
  }
}

function paintSteps() {
  $("setup-kicker").textContent = creating ? "New desk" : "Setup";
  $("setup-title").textContent = creating ? "A new brief." : desk?.name ?? "Desk";
  $("setup-lede").textContent = creating
    ? "Name first, then sources, question and schedule. The desk is created after the first step."
    : "Four steps. Each step saves on its own; you can go back any time.";
  let selected = null;
  document.querySelectorAll("#steps [data-go]").forEach((button) => {
    const id = button.dataset.go;
    const locked = (creating || !desk) && id !== "desk";
    button.disabled = locked;
    button.setAttribute("aria-selected", id === step ? "true" : "false");
    button.classList.toggle("done", stepDone(id));
    button.querySelector("[data-sum]").textContent = stepSummary(id);
    if (id === step) selected = button;
  });
  document.querySelectorAll(".panel[data-step]").forEach((panel) => {
    panel.hidden = panel.dataset.step !== step;
  });
  /* The rail runs from the first dot to the last; the fill stops at the current one. */
  const buttons = document.querySelectorAll("#steps [data-go]");
  const last = buttons[buttons.length - 1];
  if (last) $("steps").style.setProperty("--rail", `${last.offsetTop}px`);
  if (selected) $("steps-fill").style.height = `${selected.offsetTop}px`;
}

function paintCaption() {
  const box = $("stage-caption");
  if (Stage !== WorldMap) {
    box.hidden = true;
    return;
  }
  const [kicker, text] = creating ? CAPTIONS.draft : board?.running ? CAPTIONS.running : CAPTIONS[step];
  $("caption-kicker").textContent = kicker;
  $("caption-text").textContent = text;
  box.hidden = false;
}

/* ---------- saving ---------- */

async function saveDesk(next, done = "Saved.") {
  const response = await api("PUT", `/api/desks/${deskId}`, next);
  if (!response.ok) {
    tell(describe(response, "Could not save."), "error");
    return false;
  }
  desk = response.data.desk;
  await loadDesks();
  await refreshBoard();
  paintAll();
  tell(done);
  return true;
}

function patched(changes) {
  return { ...desk, ...changes };
}

/* Steps live as {id: bool} on the desk; the stages want a list with summaries. */
function stepList() {
  const summaries = new Map((options.steps ?? []).map((entry) => [entry.id, entry]));
  return STATIONS.map((station) => ({
    id: station.id,
    name: summaries.get(station.id)?.name ?? station.name,
    summary: summaries.get(station.id)?.summary ?? "",
    enabled: desk ? desk.steps?.[station.id] !== false : true,
  }));
}

function withStep(id, enabled) {
  return patched({ steps: { ...desk.steps, [id]: enabled } });
}

/* ---------- painting ---------- */

function paintAll() {
  paintTabs();
  paintStations();
  paintDeskForm();
  paintSchedule();
  paintBrief();
  paintSources();
  paintBoard();
  paintSteps();
  paintCaption();
  const link = $("desk-link");
  link.href = deskId ? `/?desk=${encodeURIComponent(deskId)}` : "/";
  $("stage-link").href = Stage === Line ? `/manage?desk=${encodeURIComponent(deskId)}` : `/manage?desk=${encodeURIComponent(deskId)}&stage=line`;
  $("stage-link").textContent = Stage === Line ? "Show as map" : "Show as line";
  stage.classList.toggle("draft", creating);
  stage.classList.toggle("no-desk", !desk);
}

function paintBoard() {
  Stage.update(board);
  Stage.sync(stepList());
  paintStations();
  paintSourceStatus();
  paintCaption();
  const runButton = $("run-now");
  runButton.disabled = creating || !desk || Boolean(board?.running) || Boolean(board?.busyElsewhere);
  runButton.textContent = board?.running ? "Running …" : board?.busyElsewhere ? "Another desk is running …" : "Run now";
}

function paintTabs() {
  const tabs = $("desk-tabs");
  tabs.innerHTML = desks
    .map(
      (entry) => `
      <button type="button" role="tab" aria-selected="${!creating && entry.id === deskId}" data-desk="${escape(entry.id)}">
        <strong>${escape(entry.name)}</strong>
        <small>${entry.sources} ${entry.sources === 1 ? "source" : "sources"} · ${escape(entry.schedule)}${entry.running ? " · running" : ""}</small>
      </button>`,
    )
    .join("");
  if (!desks.length) tabs.innerHTML = `<p class="empty">No desk yet. Create the first one.</p>`;
}

function paintStations() {
  const steps = stepList();
  const lines = stationLines(board);
  const lit = creating ? [] : STEP_STATIONS[step];
  stationList.innerHTML = STATIONS.map((station, index) => {
    const { enabled, ...meta } = steps[index];
    const classes = [enabled ? "" : "off", lit.includes(station.id) ? "lit" : ""].filter(Boolean).join(" ");
    return `
      <li>
        <button type="button" role="switch" aria-checked="${enabled}" class="${classes}" data-step="${station.id}" title="${escape(meta.summary)}" ${desk ? "" : "disabled"}>
          <i class="dot" aria-hidden="true">${String(index + 1).padStart(2, "0")}</i>
          <span class="station-copy">
            <strong>${escape(meta.name)}</strong>
            <em>${escape(creating ? "—" : enabled ? lines[station.id] || "—" : "Skipped")}</em>
          </span>
          <em class="station-state">${enabled ? "on" : "off"}</em>
        </button>
      </li>`;
  }).join("");
}

function paintDeskForm() {
  $("desk-name").value = creating ? "" : desk?.name ?? "";
  $("desk-summary").value = creating ? "" : desk?.summary ?? "";
  $("templates-field").hidden = !creating;
  $("creator-close").hidden = !creating || !desks.length;
  $("desk-delete").hidden = creating || !desk;
  $("desk-next").textContent = creating ? "Create and continue" : "Next";
  if (creating && !$("templates").children.length) {
    $("templates").innerHTML = (options.templates ?? [])
      .map(
        (template, index) => `
      <label class="picker ${index === 0 ? "on" : ""}">
        <input type="radio" name="template" value="${escape(template.id)}" ${index === 0 ? "checked" : ""} />
        <strong>${escape(template.name)}</strong>
        <small>${escape(template.summary)}</small>
      </label>`,
      )
      .join("");
  }
}

function paintSchedule() {
  const schedule = desk?.schedule ?? { enabled: false, mode: "hourly", everyMinutes: 60 };
  $("schedule-on").checked = Boolean(schedule.enabled);
  $("schedule-mode").value = schedule.mode;
  $("schedule-minutes").value = String(schedule.everyMinutes);
  $("schedule").classList.toggle("interval", schedule.mode === "interval");
  const cadence = board?.desk?.schedule ?? "";
  const next = board?.nextRunAt ? `Next run ${when(board.nextRunAt)}.` : schedule.enabled ? "" : "No automatic run.";
  $("schedule-note").textContent = [cadence && cadence[0].toUpperCase() + cadence.slice(1), next].filter(Boolean).join(" · ") || "When the route runs on its own.";
}

function paintBrief() {
  const brief = desk?.brief ?? {};
  const pickers = options.pickers ?? [];
  $("pickers").innerHTML = pickers
    .map(
      (picker) => `
      <label class="picker ${picker.id === brief.picker ? "on" : ""}">
        <input type="radio" name="picker" value="${escape(picker.id)}" ${picker.id === brief.picker ? "checked" : ""} />
        <strong>${escape(picker.label)}</strong>
        <small>${escape(picker.summary)}</small>
      </label>`,
    )
    .join("");
  $("watchlist-field").hidden = brief.picker !== "watchlist";
  $("brief-watchlist").value = (brief.watchlist ?? []).join("\n");
  $("brief-question").value = brief.question ?? "";
  $("brief-true").value = brief.whenTrue ?? "";
  $("brief-false").value = brief.whenFalse ?? "";
  $("brief-window").value = String(brief.windowHours ?? 36);
  $("brief-yes").value = String(brief.yesAt ?? 0.4);
}

function connectorOf(kind) {
  return (options.connectors ?? []).find((entry) => entry.kind === kind);
}

function paintSources() {
  const sources = desk?.sources ?? [];
  $("source-count").textContent = sources.length
    ? `${sources.length} ${sources.length === 1 ? "source" : "sources"}, ${sources.filter((source) => source.enabled).length} on.`
    : "No sources yet. Add one below.";
  $("sources").innerHTML = sources
    .map((source) => {
      const meta = connectorOf(source.kind);
      const detail = Object.entries(source.params ?? {})
        .map(([key, value]) => {
          const choices = meta?.fields?.find((field) => field.key === key)?.options ?? [];
          return escape(choices.find((choice) => choice.value === value)?.label ?? value ?? key);
        })
        .join(" · ");
      return `
      <article class="source-row ${source.enabled ? "" : "off"}" data-source="${escape(source.id)}">
        <label class="toggle" title="${source.enabled ? "Pause source" : "Turn source on"}">
          <input type="checkbox" data-toggle ${source.enabled ? "checked" : ""} />
          <i class="knob" aria-hidden="true"></i>
        </label>
        <div class="source-body">
          <strong>${escape(source.name)}</strong>
          <small>${escape(meta?.label ?? source.kind)}${detail ? ` · ${detail}` : ""}</small>
          <small class="source-run" data-run></small>
        </div>
        <span class="cost ${source.cost === "free" ? "" : "credits"}">${escape(costWord(source.cost))}</span>
        <button type="button" class="ghost" data-remove title="Remove source">Remove</button>
      </article>`;
    })
    .join("");
  paintSourceStatus();
  paintAdder();
}

function paintSourceStatus() {
  const runs = new Map((board?.sources ?? []).map((row) => [row.id, row]));
  document.querySelectorAll("[data-source]").forEach((node) => {
    const row = runs.get(node.dataset.source);
    const slot = node.querySelector("[data-run]");
    if (!row || row.status === "idle") {
      slot.textContent = "Not run yet.";
      return;
    }
    const parts = [`${statusWord(row.status)} ${when(row.lastRunAt)}`, `${row.fetched} fetched, ${row.inserted} new`];
    if (row.note) parts.push(row.note);
    slot.textContent = parts.join(" · ");
    slot.classList.toggle("error", row.status === "error");
  });
}

function paintAdder() {
  const select = $("adder-kind");
  const current = select.value;
  const groups = new Map();
  for (const entry of options.connectors ?? []) {
    if (!groups.has(entry.group)) groups.set(entry.group, []);
    groups.get(entry.group).push(entry);
  }
  select.innerHTML = [...groups]
    .map(
      ([group, list]) =>
        `<optgroup label="${escape(group)}">${list.map((entry) => `<option value="${escape(entry.kind)}">${escape(entry.label)}</option>`).join("")}</optgroup>`,
    )
    .join("");
  if ([...select.options].some((option) => option.value === current)) select.value = current;
  paintAdderFields();
}

function paintAdderFields() {
  const meta = connectorOf($("adder-kind").value);
  const fields = $("adder-fields");
  fields.innerHTML = (meta?.fields ?? [])
    .map((field) => {
      const choices = field.options ?? [];
      const control = choices.length
        ? `<select name="${escape(field.key)}">${choices.map((choice) => `<option value="${escape(choice.value)}">${escape(choice.label)}</option>`).join("")}</select>`
        : `<input name="${escape(field.key)}" type="text" placeholder="${escape(field.placeholder)}" autocomplete="off" required />`;
      return `<label class="text-field grow"><span>${escape(field.label)}</span>${control}</label>`;
    })
    .join("");
  const summary = [meta?.summary ?? ""];
  if (meta?.cost && meta.cost !== "free") summary.push(`Costs ${meta.cost} per pull.`);
  if (meta?.secret) summary.push(`Needs the key “${meta.secret}” under Credentials.`);
  $("adder-summary").textContent = summary.filter(Boolean).join(" ");
}

const STATUS = { pending: "Waiting for approval", active: "Active", blocked: "Blocked" };

async function paintPeople() {
  const response = await api("GET", "/api/users");
  const users = response.ok && Array.isArray(response.data.users) ? response.data.users : [];
  $("people").innerHTML = users.length
    ? users.map((user) => {
      const admin = user.role === "admin";
      const actions = admin
        ? `<span class="muted">Admin</span>`
        : `${user.status !== "active" ? `<button type="button" data-user="${escape(user.id)}" data-status="active">Approve</button>` : `<button type="button" class="secondary" data-user="${escape(user.id)}" data-status="blocked">Block</button>`}
           <button type="button" class="danger" data-user="${escape(user.id)}" data-op="delete">Remove</button>`;
      return `<article class="person">
        <div>
          <strong>${escape(user.name)}</strong>
          <span class="muted">${escape(user.email)} · ${escape(STATUS[user.status] || user.status)} · ${when(user.createdAt)}</span>
        </div>
        <div class="person-actions">${actions}</div>
      </article>`;
    }).join("")
    : `<p class="muted">Nobody registered yet.</p>`;
}

async function paintSecrets() {
  const response = await api("GET", "/api/settings");
  if (!response.ok) return;
  const { credentials = [], model = "" } = response.data;
  $("secrets").innerHTML = `
    ${credentials
      .map(
        (entry) => `
      <form class="key" data-secret="${escape(entry.key)}">
        <span>
          <strong>${escape(entry.label)}</strong>
          <em>${entry.set ? `Stored, ends in ${escape(entry.hint)}` : "Not stored"}${entry.usedBy?.length ? ` · for ${escape(entry.usedBy.join(", "))}` : ""}</em>
        </span>
        <input type="password" autocomplete="off" placeholder="${entry.set ? "Enter a new value" : "Enter the key"}" />
        <button type="submit" class="secondary">Save</button>
      </form>`,
      )
      .join("")}
    <form class="key" data-model>
      <span>
        <strong>Jev model</strong>
        <em>Empty means default.</em>
      </span>
      <input type="text" autocomplete="off" value="${escape(model)}" placeholder="jev-latest" />
      <button type="submit" class="secondary">Save</button>
    </form>`;
}

/* ---------- events ---------- */

$("desk-tabs").addEventListener("click", (event) => {
  const button = event.target.closest("[data-desk]");
  if (button) selectDesk(button.dataset.desk);
});

$("new-desk").addEventListener("click", () => {
  startNew();
  goStep("desk", { scroll: true });
});

$("creator-close").addEventListener("click", stopNew);

$("templates").addEventListener("change", () => {
  document.querySelectorAll("#templates .picker").forEach((node) => node.classList.toggle("on", node.querySelector("input").checked));
});

$("setup").addEventListener("click", (event) => {
  const button = event.target.closest("[data-go]");
  if (!(button instanceof HTMLButtonElement) || button.disabled) return;
  goStep(button.dataset.go, { scroll: true });
});

async function createDesk() {
  const template = document.querySelector("input[name=template]:checked")?.value ?? "blank";
  const name = $("desk-name").value.trim();
  const summary = $("desk-summary").value.trim();
  const response = await api("POST", "/api/desks", { template, name });
  if (!response.ok) {
    tell(describe(response, "Could not create the desk."), "error");
    return;
  }
  const created = response.data.desk;
  if (summary) await api("PUT", `/api/desks/${created.id}`, { ...created, summary });
  creating = false;
  stage.classList.remove("draft");
  await loadDesks();
  await selectDesk(created.id);
  tell(`Desk “${created.name}” created. Now the sources.`);
  goStep("sources", { scroll: true });
}

stationList.addEventListener("click", async (event) => {
  const button = event.target.closest("[data-step]");
  if (!button || !desk) return;
  await saveDesk(withStep(button.dataset.step, desk.steps?.[button.dataset.step] === false), "Stations updated.");
});
Stage.whenToggle?.(async (id, enabled) => {
  if (desk) await saveDesk(withStep(id, enabled), "Stations updated.");
});

$("desk-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  if (creating) {
    await createDesk();
    return;
  }
  if (await saveDesk(patched({ name: $("desk-name").value.trim(), summary: $("desk-summary").value.trim() }))) goStep("sources", { scroll: true });
});

$("desk-delete").addEventListener("click", async () => {
  if (!desk) return;
  if (!window.confirm(`Delete desk “${desk.name}” with all its runs and verdicts? The indexed documents are kept.`)) return;
  const response = await api("DELETE", `/api/desks/${deskId}`);
  if (!response.ok) {
    tell(describe(response, response.status === 409 ? "The desk is running right now." : "Could not delete."), "error");
    return;
  }
  deskId = "";
  await loadDesks();
  await loadDesk();
  tell("Desk deleted.");
});

$("schedule-mode").addEventListener("change", () => $("schedule").classList.toggle("interval", $("schedule-mode").value === "interval"));
$("schedule").addEventListener("submit", async (event) => {
  event.preventDefault();
  const enabled = $("schedule-on").checked;
  const saved = await saveDesk(
    patched({
      schedule: {
        enabled,
        mode: $("schedule-mode").value,
        everyMinutes: Number($("schedule-minutes").value) || 60,
      },
    }),
    enabled ? "Set up. The desk runs on schedule." : "Set up. The desk runs when you start it by hand.",
  );
  if (saved) stage.scrollIntoView({ block: "start", behavior: "smooth" });
});

$("pickers").addEventListener("change", (event) => {
  document.querySelectorAll("#pickers .picker").forEach((node) => node.classList.toggle("on", node.querySelector("input").checked));
  $("watchlist-field").hidden = event.target.value !== "watchlist";
});
$("brief").addEventListener("submit", async (event) => {
  event.preventDefault();
  const saved = await saveDesk(
    patched({
      brief: {
        picker: document.querySelector("input[name=picker]:checked")?.value ?? "corpus",
        watchlist: $("brief-watchlist").value,
        question: $("brief-question").value.trim(),
        whenTrue: $("brief-true").value.trim(),
        whenFalse: $("brief-false").value.trim(),
        windowHours: Number($("brief-window").value) || 36,
        yesAt: Number($("brief-yes").value) || 0.4,
      },
    }),
    "Question for Jev saved.",
  );
  if (saved) goStep("schedule", { scroll: true });
});

$("sources").addEventListener("change", async (event) => {
  const row = event.target.closest("[data-source]");
  if (!row || !event.target.matches("[data-toggle]")) return;
  const sources = desk.sources.map((source) => (source.id === row.dataset.source ? { ...source, enabled: event.target.checked } : source));
  await saveDesk(patched({ sources }), event.target.checked ? "Source turned on." : "Source paused.");
});
$("sources").addEventListener("click", async (event) => {
  const row = event.target.closest("[data-source]");
  if (!row || !event.target.matches("[data-remove]")) return;
  const sources = desk.sources.filter((source) => source.id !== row.dataset.source);
  await saveDesk(patched({ sources }), "Source removed.");
});

$("adder-kind").addEventListener("change", paintAdderFields);
$("adder").addEventListener("submit", async (event) => {
  event.preventDefault();
  if (!desk) return;
  const kind = $("adder-kind").value;
  const fields = {};
  $("adder-fields")
    .querySelectorAll("[name]")
    .forEach((control) => (fields[control.name] = control.value.trim()));
  const meta = connectorOf(kind);
  const name = $("adder-name").value.trim() || fields.url || fields.channel || meta?.label || kind;
  const source = { kind, name, params: fields, enabled: true };
  if (await saveDesk(patched({ sources: [...desk.sources, source] }), `“${name}” added.`)) {
    $("adder-name").value = "";
    paintAdderFields();
  }
});

$("people").addEventListener("click", async (event) => {
  const button = event.target.closest("button[data-user]");
  if (!(button instanceof HTMLButtonElement)) return;
  button.disabled = true;
  const body = button.dataset.op === "delete"
    ? { id: button.dataset.user, op: "delete" }
    : { id: button.dataset.user, status: button.dataset.status };
  const response = await api("POST", "/api/users", body);
  if (!response.ok) tell(describe(response, "Could not change the user."), "error");
  await paintPeople();
});

$("logout").addEventListener("click", async () => {
  await api("DELETE", "/api/session");
  location.href = "/login";
});

$("secrets").addEventListener("submit", async (event) => {
  event.preventDefault();
  const form = event.target.closest("form");
  const input = form.querySelector("input");
  const body = form.hasAttribute("data-model") ? { op: "model", value: input.value.trim() } : { op: "secret", key: form.dataset.secret, value: input.value };
  const response = await api("POST", "/api/settings", body);
  if (!response.ok) {
    tell(describe(response, "Could not save."), "error");
    return;
  }
  await paintSecrets();
  tell("Saved.");
});

$("run-now").addEventListener("click", async () => {
  if (!desk) return;
  const response = await api("POST", `/api/desks/${deskId}/run`, { forceJev: true });
  if (response.status === 202) {
    tell("Run started.");
    await refreshBoard();
    return;
  }
  tell(describe(response, response.status === 409 ? "A run is already in progress." : "Could not start."), "error");
});

Stage.whenDue(() => refreshBoard());
window.addEventListener("resize", paintSteps);

/* ---------- boot ---------- */

(async function boot() {
  stage.classList.toggle("map", Stage === WorldMap);
  $("map").hidden = Stage !== WorldMap;
  stationList.hidden = Stage !== WorldMap;
  Stage.mount($("map"), $("viewport"));
  $("stage-hint").hidden = Stage !== WorldMap;
  await loadOptions();
  await loadDesks();
  const me = await api("GET", "/api/me");
  if (me.ok && me.data.user) $("who").textContent = me.data.user.name || me.data.user.email;
  await Promise.all([loadDesk(), paintSecrets(), paintPeople()]);
  boardTimer = window.setTimeout(refreshBoard, 15_000);
})();
