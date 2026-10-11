/* Line stage for the Steuerung. Four stations as switches, one clock line toward the next run. */
import { STATIONS, clockStamp, journey, remain, stationLines, when } from "./shared.js";

const WireLine = (() => {
  let root;
  let fill;
  let mark;
  let from;
  let until;
  let headline;
  let subline;
  let stationNodes = new Map();
  let onDue = null;
  let onToggle = null;
  let dueNotified = false;
  let timer = 0;
  let steps = STATIONS.map((station) => ({ id: station.id, summary: "", enabled: true }));
  let board = null;

  function sync(list) {
    if (!Array.isArray(list)) return;
    steps = STATIONS.map((station) => list.find((step) => step.id === station.id) ?? { id: station.id, summary: "", enabled: true });
    paintStations();
  }

  function update(payload) {
    if (!payload) return;
    board = payload;
    const end = Date.parse(payload.nextRunAt || "");
    if (Number.isFinite(end) && end > Date.now()) dueNotified = false;
    paintStations();
    paintClock();
  }

  function whenDue(callback) {
    onDue = callback;
  }

  function whenToggle(callback) {
    onToggle = callback;
  }

  function mount(_canvas, frame) {
    build(frame);
    paintStations();
    paintClock();
    if (!timer) timer = window.setInterval(paintClock, 1000);
  }

  function build(frame) {
    root = document.createElement("div");
    root.className = "line-stage";
    root.innerHTML = `
      <div class="line-head">
        <div>
          <p class="line-title" id="line-title">One run, four stations.</p>
          <p class="line-sub" id="line-sub"></p>
        </div>
      </div>
      <ol class="line-stations" aria-label="Stations of a run"></ol>
      <div class="line-clock" aria-live="off">
        <span class="line-from" id="line-from">—</span>
        <div class="line-rail" role="progressbar" aria-valuemin="0" aria-valuemax="100" aria-valuenow="0" aria-label="Time until the next run">
          <i class="line-fill" id="line-fill"></i>
          <b class="line-mark" id="line-mark">—</b>
        </div>
        <span class="line-until" id="line-until">—</span>
      </div>`;
    frame.append(root);
    headline = root.querySelector("#line-title");
    subline = root.querySelector("#line-sub");
    fill = root.querySelector("#line-fill");
    mark = root.querySelector("#line-mark");
    from = root.querySelector("#line-from");
    until = root.querySelector("#line-until");

    const list = root.querySelector(".line-stations");
    stationNodes = new Map();
    STATIONS.forEach((station, index) => {
      const item = document.createElement("li");
      item.innerHTML = `
        <button type="button" role="switch" aria-checked="true" data-station="${station.id}">
          <i class="line-dot" aria-hidden="true"><span>${index + 1}</span></i>
          <strong>${station.short}</strong>
          <em class="line-count">—</em>
          <small class="line-why"></small>
        </button>`;
      list.append(item);
      stationNodes.set(station.id, item.querySelector("button"));
    });
    list.addEventListener("click", (event) => {
      const button = event.target.closest("[data-station]");
      if (!button || !onToggle) return;
      const step = steps.find((entry) => entry.id === button.dataset.station);
      if (!step) return;
      button.setAttribute("aria-busy", "true");
      Promise.resolve(onToggle(step.id, !step.enabled)).finally(() => button.removeAttribute("aria-busy"));
    });
  }

  function paintStations() {
    if (!root) return;
    const lines = stationLines(board);
    for (const step of steps) {
      const button = stationNodes.get(step.id);
      if (!button) continue;
      button.setAttribute("aria-checked", step.enabled ? "true" : "false");
      button.classList.toggle("off", !step.enabled);
      button.querySelector(".line-why").textContent = step.summary || "";
      button.querySelector(".line-count").textContent = step.enabled ? lines[step.id] || "—" : "Skipped";
    }
    const active = steps.filter((step) => step.enabled).length;
    headline.textContent =
      active === steps.length ? "One run, four stations." : active === 0 ? "No station active." : `One run, ${active} of ${steps.length} stations.`;
  }

  function paintClock() {
    if (!root) return;
    const trip = journey(board);
    const end = Date.parse(board?.nextRunAt || "");
    const schedule = board?.schedule || {};
    const span = (schedule.mode === "interval" ? Number(schedule.everyMinutes) || 60 : 60) * 60_000;
    root.classList.toggle("is-paused", trip.phase === "paused");
    root.classList.toggle("is-running", trip.phase === "running");
    root.classList.toggle("is-due", trip.phase === "due");
    const percent = Math.round(trip.progress * 100);
    fill.style.width = `${percent}%`;
    mark.style.left = `${Math.min(96, Math.max(4, percent))}%`;
    mark.textContent = label(trip, end);
    from.textContent = trip.phase === "running" && board?.runningSince ? clockStamp(board.runningSince) : Number.isFinite(end) ? clockStamp(end - span) : "—";
    until.textContent = Number.isFinite(end) && trip.phase !== "running" ? clockStamp(end) : "—";
    root.querySelector(".line-rail").setAttribute("aria-valuenow", String(percent));
    subline.textContent = sub(trip);
    if (trip.phase === "due" && !dueNotified) {
      dueNotified = true;
      if (onDue) onDue();
    }
  }

  function label(trip, end) {
    switch (trip.phase) {
      case "running":
        return "running";
      case "paused":
        return "paused";
      case "due":
        return "now";
      case "enroute":
        return remain(end - Date.now());
      default:
        return "—";
    }
  }

  function sub(trip) {
    const last = board?.lastCycleAt ? `Last run ${when(board.lastCycleAt)}.` : "No run yet.";
    if (trip.phase === "running") return `Running${board?.runningSince ? ` since ${clockStamp(board.runningSince)}` : ""}. ${last}`;
    if (trip.phase === "paused") return `Schedule off. Runs start by hand only. ${last}`;
    if (trip.phase === "due") return `The next run is due. ${last}`;
    const schedule = board?.schedule || {};
    const cadence = schedule.mode === "interval" ? `every ${schedule.everyMinutes} minutes` : "every full hour";
    return `Automatic, ${cadence}. ${last}`;
  }

  return { mount, sync, update, whenDue, whenToggle };
})();

export default WireLine;
