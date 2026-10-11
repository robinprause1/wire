/* Helpers both pages and both stages use. Formatting is English; the data is not. */

export const STATIONS = [
  { id: "fetch", name: "Fetch sources", short: "Sources" },
  { id: "ingest", name: "Write index", short: "Index" },
  { id: "retrieve", name: "Pick candidates", short: "Candidates" },
  { id: "judge", name: "Ask Jev", short: "Jev" },
];

const LOCALE = "en-GB";

export function escape(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

export function when(stamp) {
  if (!stamp) return "—";
  const date = new Date(stamp);
  if (Number.isNaN(date.getTime())) return "—";
  return date.toLocaleString(LOCALE, { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" });
}

export function clockStamp(stamp) {
  const date = new Date(stamp);
  if (Number.isNaN(date.getTime())) return "—";
  return date.toLocaleTimeString(LOCALE, { hour: "2-digit", minute: "2-digit" });
}

export function remain(ms) {
  const total = Math.max(0, Math.ceil(ms / 1000));
  const hours = Math.floor(total / 3600);
  const minutes = Math.floor((total % 3600) / 60);
  const seconds = total % 60;
  if (hours > 0) return `in ${hours} h ${String(minutes).padStart(2, "0")} min`;
  if (minutes > 0) return `in ${minutes} min ${String(seconds).padStart(2, "0")} s`;
  return `in ${seconds} s`;
}

export function statusWord(status) {
  switch (status) {
    case "ok":
      return "ok";
    case "error":
      return "error";
    case "skipped":
      return "skipped";
    case "idle":
      return "waiting";
    case "empty":
      return "empty";
    default:
      return String(status ?? "");
  }
}

export function verdictWord(value) {
  switch (value) {
    case "yes":
      return "Yes";
    case "no":
      return "No";
    case "pass":
      return "Pass";
    default:
      return String(value ?? "");
  }
}

/* Connector cost is "free" or a short phrase like "125 credits". */
export function costWord(cost) {
  return String(cost ?? "free");
}

export function count(value, singular, plural) {
  return `${Number(value).toLocaleString(LOCALE)} ${Number(value) === 1 ? singular : plural}`;
}

/* Numbers behind the four stations, from one board payload. */
export function tallies(board) {
  const sources = Array.isArray(board?.sources) ? board.sources : [];
  const verdicts = Array.isArray(board?.verdicts) ? board.verdicts : [];
  const judged = verdicts.filter((row) => row.verdict === "yes" || row.verdict === "no");
  return {
    fetched: sources.reduce((sum, source) => sum + (Number(source.fetched) || 0), 0),
    inserted: sources.reduce((sum, source) => sum + (Number(source.inserted) || 0), 0),
    live: sources.filter((source) => source.enabled).length,
    documents: Number(board?.documents) || 0,
    candidates: verdicts.filter((row) => row.status !== "empty").length,
    yes: judged.filter((row) => row.verdict === "yes").length,
    no: judged.filter((row) => row.verdict === "no").length,
  };
}

/* One line per station. `short` fits a label on the map; the long form fits a card. */
export function stationLines(board, { short = false } = {}) {
  if (!board) return { fetch: "", ingest: "", retrieve: "", judge: "" };
  const t = tallies(board);
  if (short) {
    return {
      fetch: count(t.fetched, "story", "stories"),
      ingest: count(t.documents, "document", "documents"),
      retrieve: count(t.candidates, "candidate", "candidates"),
      judge: t.yes + t.no ? `${t.yes} yes · ${t.no} no` : "",
    };
  }
  return {
    fetch: `Last run ${count(t.fetched, "story", "stories")} from ${count(t.live, "source", "sources")}`,
    ingest: `Last run ${t.inserted} new · ${count(t.documents, "document", "documents")} in the index`,
    retrieve: `Last run ${count(t.candidates, "candidate", "candidates")}`,
    judge: t.yes + t.no ? `Last run ${t.yes} yes, ${t.no} no` : "No verdicts yet",
  };
}

/* Where the hour stands for a desk: progress 0..1 and a phase word. */
export function journey(board, now = Date.now()) {
  if (!board) return { progress: 0, phase: "paused" };
  if (board.running) return { progress: 1, phase: "running" };
  const schedule = board.schedule || {};
  if (schedule.enabled === false || !board.nextRunAt) return { progress: 0, phase: "paused" };
  const end = Date.parse(board.nextRunAt);
  if (!Number.isFinite(end)) return { progress: 0, phase: "paused" };
  const span = (schedule.mode === "interval" ? Number(schedule.everyMinutes) || 60 : 60) * 60_000;
  const remaining = end - now;
  if (remaining <= 0) return { progress: 1, phase: "due" };
  return { progress: Math.min(1, Math.max(0, (now - (end - span)) / span)), phase: "enroute" };
}

export async function api(method, path, body) {
  const response = await fetch(path, {
    method,
    headers: body === undefined ? {} : { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  let data = {};
  try {
    data = await response.json();
  } catch {
    data = {};
  }
  return { ok: response.ok, status: response.status, data };
}

export function deskFromUrl(desks) {
  const wanted = new URLSearchParams(location.search).get("desk");
  if (wanted && desks.some((desk) => desk.id === wanted)) return wanted;
  const stored = localStorage.getItem("wire.desk");
  if (stored && desks.some((desk) => desk.id === stored)) return stored;
  return desks[0]?.id ?? "";
}

export function rememberDesk(id) {
  localStorage.setItem("wire.desk", id);
  const url = new URL(location.href);
  url.searchParams.set("desk", id);
  history.replaceState(null, "", url);
}
