/* Desk page: what one desk fetched, what Jev answered, and a search box over its index. */
import { api, costWord, deskFromUrl, escape, remain, rememberDesk, statusWord, verdictWord, when } from "./shared.js";

const $ = (id) => document.getElementById(id);
const schedule = $("schedule");
const tape = $("tape");
const pager = $("pager");
const filters = $("filters");
const verdict = $("verdict");
const question = $("question");
const next = $("next");
const docCount = $("doc-count");
const hits = $("hits");
const run = $("run");
const force = $("force");
const form = $("ask");
const field = $("q");
const findField = $("coin-q");
const onlyYesButton = $("only-yes");
const deskSelect = $("desk-select");
const ONLY_YES_KEY = "wire.onlyYes";

const HEADINGS = { coins: ["Coins", "Coin"], watchlist: ["Topics", "Term"], corpus: ["Verdict", "Topic"] };

let deskId = "";
const PAGE = 10;

let source = "all";
let page = 1;
let board = null;
let timer = 0;
let sortKey = "rank";
let sortDir = 1;
let query = "";
let onlyYes = localStorage.getItem(ONLY_YES_KEY) === "1";

function untilNext(stamp) {
  if (!stamp) return "—";
  const delta = new Date(stamp).getTime() - Date.now();
  return delta <= 0 ? "due" : remain(delta);
}

function labelOf(row) {
  const passage = Array.isArray(row.passages) ? row.passages[0] || {} : {};
  return String(passage.label || row.question || "");
}

function scoreOf(row) {
  return row.maxScore == null ? -1 : Number(row.maxScore);
}

function answerRank(row) {
  if (row.verdict === "yes") return 0;
  if (row.verdict === "no") return 1;
  return 2;
}

function sorted(rows) {
  const indexed = rows.map((row, index) => ({ row, index }));
  indexed.sort((left, right) => {
    if (sortKey === "token") return labelOf(left.row).localeCompare(labelOf(right.row), "en") * sortDir;
    if (sortKey === "answer") return (answerRank(left.row) - answerRank(right.row)) * sortDir;
    if (sortKey === "prob") return (scoreOf(left.row) - scoreOf(right.row)) * sortDir;
    return left.index - right.index;
  });
  return indexed.map((entry) => entry.row);
}

function sortMark(key) {
  if (sortKey !== key) return "";
  return sortDir < 0 ? " ↓" : " ↑";
}

function verdictRow(row) {
  const passage = Array.isArray(row.passages) ? row.passages[0] || {} : {};
  const label = labelOf(row);
  const token = passage.url
    ? `<a class="token" href="${escape(passage.url)}" target="_blank" rel="noreferrer">${escape(label)}</a>`
    : `<span class="token">${escape(label)}</span>`;
  const probability = row.maxScore == null ? "—" : `${Math.round(Number(row.maxScore) * 100)} %`;
  const answered = row.verdict === "yes" || row.verdict === "no";
  const stand = answered ? verdictWord(row.verdict) : statusWord(row.status);
  const pill = `<span class="pill ${escape(answered ? row.verdict : row.status)}">${escape(stand)}</span>`;
  return `<tr>
    <td>${token}<div class="meta">${escape(row.note || "")}</div></td>
    <td>${pill}</td>
    <td class="prob"><div>${escape(probability)}</div><div class="meta">${escape(stand)} · ${escape(when(row.askedAt))}</div></td>
  </tr>`;
}

function render() {
  if (!board) return;
  const paused = board.schedule && board.schedule.enabled === false;
  next.textContent = board.running ? "running" : board.busyElsewhere ? "waiting" : paused ? "paused" : untilNext(board.nextRunAt);
  const balance = Array.isArray(board.balances) ? board.balances[0] : null;
  $("balance").hidden = !balance;
  if (balance) {
    $("balance-label").textContent = balance.label;
    $("balance-value").textContent = balance.remaining == null ? `${balance.perPull} / pull` : `${Number(balance.remaining).toLocaleString("en-GB")} left`;
  }
  docCount.textContent = `${Number(board.documents).toLocaleString("en-GB")} documents`;
  const brief = board.brief || {};
  question.textContent = brief.question || "";
  const [heading, singular] = HEADINGS[brief.picker] || HEADINGS.corpus;
  $("verdict-heading").textContent = heading;
  findField.placeholder = `Filter by ${singular.toLowerCase()}`;
  run.disabled = board.running || board.busyElsewhere;
  force.disabled = board.running || board.busyElsewhere;

  const names = new Map(board.sources.map((item) => [item.id, item.name]));
  schedule.innerHTML = board.sources.length
    ? board.sources
        .map(
          (item) => `<article class="source${item.enabled === false ? " off" : ""}">
        <div>
          <div class="source-name">${escape(item.name)}</div>
          <p class="meta">${escape(costWord(item.cost))} · ${item.inserted} new · ${escape(when(item.lastRunAt))}</p>
        </div>
        <span class="pill ${item.enabled === false ? "skipped" : escape(item.status)}">${item.enabled === false ? "off" : escape(statusWord(item.status))}</span>
      </article>`,
        )
        .join("")
    : `<p class="empty">No sources yet. Add them under Manage.</p>`;

  const active = ["all", ...new Set(board.items.map((item) => item.source))];
  filters.innerHTML = active
    .map((id) => {
      const label = id === "all" ? "All" : names.get(id) || id;
      return `<button type="button" data-source="${escape(id)}" class="chip${id === source ? " on" : ""}">${escape(label)}</button>`;
    })
    .join("");

  const items = board.items.filter((item) => source === "all" || item.source === source);
  const pages = Math.max(1, Math.ceil(items.length / PAGE));
  if (page > pages) page = pages;
  const start = (page - 1) * PAGE;
  const slice = items.slice(start, start + PAGE);
  tape.innerHTML = slice.length
    ? slice
        .map((item) => {
          const title = escape(item.title);
          const link = item.url ? `<a href="${escape(item.url)}" target="_blank" rel="noreferrer">${title}</a>` : title;
          return `<article class="story">
            <span class="badge">${escape(names.get(item.source) || item.source)}</span>
            <h3>${link}</h3>
            <p class="snippet">${escape(item.snippet)}</p>
          </article>`;
        })
        .join("")
    : `<p class="empty">${board.running ? "Run in progress." : "No stories yet."}</p>`;
  if (pager instanceof HTMLElement) {
    pager.hidden = items.length <= PAGE;
    const from = slice.length ? start + 1 : 0;
    const to = start + slice.length;
    pager.innerHTML = `<button type="button" data-page="prev"${page <= 1 ? " disabled" : ""}>Back</button>
      <span>${from}–${to} of ${items.length}</span>
      <button type="button" data-page="next"${page >= pages ? " disabled" : ""}>Next</button>`;
  }

  const rows = Array.isArray(board.verdicts) ? board.verdicts : [];
  const visible = rows.filter((row) => {
    if (onlyYes && row.verdict !== "yes") return false;
    if (!query) return true;
    return `${labelOf(row)} ${row.note || ""}`.toLowerCase().includes(query);
  });
  if (!rows.length) {
    verdict.innerHTML = `<p class="empty">Jev answers after the first run.</p>`;
  } else if (!visible.length) {
    verdict.innerHTML = `<p class="empty">${onlyYes ? "No yes" : "Nothing"}${query ? ` for “${escape(query)}”` : ""}.</p>`;
  } else {
    verdict.innerHTML = `<table class="book">
      <thead><tr>
        <th><button type="button" class="sort${sortKey === "token" ? " on" : ""}" data-sort="token">${escape(singular)}${sortMark("token")}</button></th>
        <th><button type="button" class="sort${sortKey === "answer" ? " on" : ""}" data-sort="answer">Answer${sortMark("answer")}</button></th>
        <th><button type="button" class="sort${sortKey === "prob" ? " on" : ""}" data-sort="prob">Probability${sortMark("prob")}</button></th>
      </tr></thead>
      <tbody>${sorted(visible).map(verdictRow).join("")}</tbody>
    </table>`;
  }
}

async function loadDesks() {
  const me = await api("GET", "/api/me");
  if (!me.ok || !me.data.user || me.data.user.status !== "active") {
    location.href = "/login?next=" + encodeURIComponent(location.pathname + location.search);
    return;
  }
  const admin = me.data.user.role === "admin";
  $("who").textContent = me.data.user.name || me.data.user.email;
  $("run").hidden = !admin;
  $("force").hidden = !admin;
  $("manage-link").hidden = !admin;
  const response = await api("GET", "/api/desks");
  const desks = response.ok && Array.isArray(response.data.desks) ? response.data.desks : [];
  deskId = deskFromUrl(desks);
  deskSelect.innerHTML = desks.length
    ? desks.map((desk) => `<option value="${escape(desk.id)}" ${desk.id === deskId ? "selected" : ""}>${escape(desk.name)}</option>`).join("")
    : `<option value="">No desk</option>`;
  deskSelect.disabled = desks.length < 2;
  $("manage-link").href = deskId ? `/manage?desk=${encodeURIComponent(deskId)}` : "/manage";
}

async function load() {
  window.clearTimeout(timer);
  if (!deskId) {
    verdict.innerHTML = `<p class="empty">No desk yet. Create one under Manage.</p>`;
    return;
  }
  const response = await api("GET", `/api/desks/${deskId}/board`);
  if (response.ok) {
    board = response.data;
    rememberDesk(deskId);
    render();
  }
  timer = window.setTimeout(load, board?.running ? 3000 : 12000);
}

function paintOnlyYes() {
  onlyYesButton.classList.toggle("on", onlyYes);
  onlyYesButton.setAttribute("aria-pressed", onlyYes ? "true" : "false");
}

deskSelect.addEventListener("change", async () => {
  deskId = deskSelect.value;
  source = "all";
  page = 1;
  hits.innerHTML = "";
  $("manage-link").href = `/manage?desk=${encodeURIComponent(deskId)}`;
  await load();
});

onlyYesButton.addEventListener("click", () => {
  onlyYes = !onlyYes;
  localStorage.setItem(ONLY_YES_KEY, onlyYes ? "1" : "0");
  paintOnlyYes();
  render();
  verdict.scrollTop = 0;
});

findField.addEventListener("input", () => {
  query = findField.value.trim().toLowerCase();
  render();
});

verdict.addEventListener("click", (event) => {
  const button = event.target.closest("button[data-sort]");
  if (!button) return;
  const key = button.dataset.sort;
  if (sortKey === key) sortDir *= -1;
  else {
    sortKey = key;
    sortDir = key === "prob" ? -1 : 1;
  }
  render();
  verdict.scrollTop = 0;
});

filters.addEventListener("click", (event) => {
  const button = event.target.closest("button");
  if (!button) return;
  source = button.dataset.source || "all";
  page = 1;
  render();
});

pager?.addEventListener("click", (event) => {
  const button = event.target instanceof Element ? event.target.closest("button") : null;
  if (!(button instanceof HTMLButtonElement) || button.disabled) return;
  page += button.dataset.page === "prev" ? -1 : 1;
  render();
  document.querySelector(".feed")?.scrollIntoView({ block: "start" });
});

async function postRun(body) {
  if (!deskId) return;
  run.disabled = true;
  force.disabled = true;
  await api("POST", `/api/desks/${deskId}/run`, body);
  load();
}

$("logout").addEventListener("click", async () => {
  await api("DELETE", "/api/session");
  location.href = "/login";
});

run.addEventListener("click", () => postRun({}));
force.addEventListener("click", () => postRun({ forceJev: true, fetch: false }));

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  const text = field.value.trim();
  if (!text || !deskId) return;
  const response = await api("POST", `/api/desks/${deskId}/ask`, { question: text });
  const found = Array.isArray(response.data.hits) ? response.data.hits : [];
  hits.innerHTML = found.length
    ? found
        .map(
          (hit) => `<article class="hit">
            <span class="badge">${escape(hit.source)} · ${Number(hit.score).toFixed(3)}</span>
            <h3>${hit.url ? `<a href="${escape(hit.url)}" target="_blank" rel="noreferrer">${escape(hit.title)}</a>` : escape(hit.title)}</h3>
            <p class="snippet">${escape(hit.snippet)}</p>
          </article>`,
        )
        .join("")
    : `<p class="hit meta">Nothing in the index.</p>`;
});

paintOnlyYes();
loadDesks().then(load);
