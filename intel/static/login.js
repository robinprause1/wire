/* Login, first-admin setup, registration, and the waiting screens. */
import { api } from "./shared.js";

const $ = (id) => document.getElementById(id);
const root = document.documentElement;
const params = new URLSearchParams(location.search);
const next = params.get("next") || "/";
const note = $("note");
const theme = $("theme");
const forms = {
  setup: $("setup"),
  login: $("login"),
  register: $("register"),
};
const themes = ["light", "dark"];
const themeKey = "wire-theme";

const COPY = {
  setup: {
    kicker: "First access",
    title: "Create admin",
    lead: "This account approves the others. After that the desk is open.",
    document: "Wire · Create admin",
    num: "00",
    stop: "Access",
    plate: "Admin",
  },
  login: {
    kicker: "Sign in",
    title: "Sign in",
    lead: "Sign in. The answers are on the desk.",
    document: "Wire · Sign in",
    num: "01",
    stop: "Access",
    plate: "Sign in",
  },
  register: {
    kicker: "Registration",
    title: "Register",
    lead: "The admin approves you. Until then the desk stays closed.",
    document: "Wire · Register",
    num: "02",
    stop: "Register",
    plate: "Member",
  },
  pending: {
    kicker: "Approval",
    title: "Waiting for approval",
    lead: "As soon as the admin approves your access, the answers are on the desk.",
    document: "Wire · Approval",
    num: "03",
    stop: "Approval",
    plate: "Waiting",
  },
  blocked: {
    kicker: "Blocked",
    title: "Access blocked",
    lead: "This account is blocked. Write to the admin if that is a mistake.",
    document: "Wire · Blocked",
    num: "04",
    stop: "Blocked",
    plate: "Stop",
  },
};

const SUBMIT = {
  setup: { label: "Create admin", busy: "Creating" },
  login: { label: "Sign in", busy: "Signing in" },
  register: { label: "Register", busy: "Registering" },
};

function currentTheme() {
  return root.dataset.theme === "dark" ? "dark" : "light";
}

function paintTheme() {
  const current = currentTheme();
  if (!(theme instanceof HTMLElement)) return;
  for (const button of theme.querySelectorAll("[data-theme-set]")) {
    button.setAttribute("aria-checked", button.dataset.themeSet === current ? "true" : "false");
  }
  const meta = document.querySelector('meta[name="theme-color"]');
  if (meta) meta.setAttribute("content", current === "dark" ? "#0f0e0d" : "#f3efe6");
}

function setTheme(value) {
  if (value !== "light" && value !== "dark") return;
  root.dataset.theme = value;
  try {
    localStorage.setItem(themeKey, value);
  } catch {
    /* storage can be off */
  }
  paintTheme();
}

function fieldNames(message) {
  if (message === "Email or password is wrong.") return ["email", "password"];
  if (message.toLowerCase().includes("password")) return ["password"];
  if (message.toLowerCase().includes("email")) return ["email"];
  if (message.toLowerCase().includes("name")) return ["name"];
  return [];
}

function show(message) {
  if (!(note instanceof HTMLElement)) return;
  note.hidden = true;
  const text = $("note-text");
  if (text) text.textContent = message || "";
  if (message) note.hidden = false;
  const names = fieldNames(message);
  for (const input of document.querySelectorAll("form:not([hidden]) input")) {
    if (names.includes(input.name)) input.setAttribute("aria-invalid", "true");
    else input.removeAttribute("aria-invalid");
  }
}

function fitTitle() {
  const title = $("title");
  if (!(title instanceof HTMLElement)) return;
  title.style.fontSize = "";
  const available = title.clientWidth;
  if (!available) return;
  const current = parseFloat(getComputedStyle(title).fontSize);
  title.style.width = "max-content";
  const width = title.offsetWidth;
  title.style.width = "";
  if (!width || !current) return;
  title.style.fontSize = `${Math.floor(current * (available / width) * 0.985)}px`;
}

function paintTitle(text) {
  const words = text.split(" ").filter(Boolean);
  const first = $("title-a");
  const last = $("title-b");
  if (!(first instanceof HTMLElement) || !(last instanceof HTMLElement)) return;
  if (words.length < 2) {
    first.textContent = words[0] || "";
    first.classList.add("solo");
    last.hidden = true;
    last.textContent = "";
  } else {
    const end = words.pop() || "";
    first.textContent = words.join(" ");
    first.classList.remove("solo");
    last.hidden = false;
    last.textContent = end;
  }
  fitTitle();
}

function destination(user) {
  if (user.role === "admin" && next.startsWith("/") && !next.startsWith("//")) return next;
  return "/";
}

function resetPeeks() {
  for (const button of document.querySelectorAll("[data-peek]")) {
    if (!(button instanceof HTMLButtonElement)) continue;
    const input = $(button.dataset.peek || "");
    if (input instanceof HTMLInputElement) input.type = "password";
    button.setAttribute("aria-pressed", "false");
    button.setAttribute("aria-label", "Show password");
    button.textContent = "Show";
  }
}

function showScreen(which, user) {
  const copy = COPY[which];
  if (!copy) return;
  root.dataset.gate = "ready";
  const kicker = $("kicker-label");
  const lead = $("lead");
  if (kicker) kicker.textContent = copy.kicker;
  paintTitle(copy.title);
  const stopNum = $("stop-num");
  const stopLabel = $("stop-label");
  const plateNum = $("plate-num");
  const plateLabel = $("plate-label");
  if (stopNum) stopNum.textContent = copy.num;
  if (stopLabel) stopLabel.textContent = copy.stop;
  if (plateNum) plateNum.textContent = copy.num;
  if (plateLabel) plateLabel.textContent = copy.plate;
  document.title = copy.document;
  if (lead) {
    lead.textContent = which === "pending" && user?.name ? `${user.name} is registered. ${copy.lead}` : copy.lead;
  }
  show("");
  resetPeeks();
  for (const [name, form] of Object.entries(forms)) {
    if (form instanceof HTMLElement) form.hidden = name !== which;
  }
  const waiting = which === "pending" || which === "blocked";
  const switcher = $("switch");
  const actions = $("actions");
  const showRegister = $("show-register");
  const showLogin = $("show-login");
  const logout = $("logout");
  const back = $("back-login");
  if (switcher instanceof HTMLElement) switcher.hidden = which === "setup" || waiting;
  if (showRegister instanceof HTMLElement) showRegister.hidden = which !== "login";
  if (showLogin instanceof HTMLElement) showLogin.hidden = which !== "register";
  if (actions instanceof HTMLElement) actions.hidden = !waiting;
  if (logout instanceof HTMLElement) logout.hidden = !(waiting && user);
  if (back instanceof HTMLElement) back.hidden = !(which === "pending" && !user);
  const field = document.querySelector("form:not([hidden]) input");
  if (field instanceof HTMLElement) {
    field.focus();
  } else if (logout instanceof HTMLButtonElement && !logout.hidden) {
    logout.focus();
  } else if (back instanceof HTMLButtonElement && !back.hidden) {
    back.focus();
  }
}

async function submit(form, path, kind) {
  if (form.dataset.busy === "1") return;
  const copy = SUBMIT[kind];
  const button = form.querySelector("[type=submit]");
  if (!(button instanceof HTMLButtonElement)) return;
  const label = button.querySelector("span");
  form.dataset.busy = "1";
  button.disabled = true;
  if (label) label.textContent = copy.busy;
  show("");
  let done = false;
  try {
    const response = await api("POST", path, Object.fromEntries(new FormData(form)));
    if (!response.ok) {
      show(response.data?.error || "That did not work.");
      const field = form.querySelector("[aria-invalid='true']") || form.querySelector("input");
      if (field instanceof HTMLElement) field.focus();
      return;
    }
    done = true;
    const user = response.data.user || {};
    if (user.status === "pending") {
      location.href = "/login?pending=1";
      return;
    }
    location.href = destination(user);
  } catch {
    show("The desk is not reachable right now.");
  } finally {
    if (!done) {
      form.dataset.busy = "";
      button.disabled = false;
      const label = button.querySelector("span");
      if (label) label.textContent = copy.label;
    }
  }
}

paintTheme();

if (theme instanceof HTMLElement) {
  theme.addEventListener("click", (event) => {
    const button = event.target instanceof Element ? event.target.closest("[data-theme-set]") : null;
    if (button instanceof HTMLElement && button.dataset.themeSet) setTheme(button.dataset.themeSet);
  });
  theme.addEventListener("keydown", (event) => {
    if (event.key !== "ArrowLeft" && event.key !== "ArrowRight") return;
    const current = themes.indexOf(currentTheme());
    const step = event.key === "ArrowRight" ? 1 : -1;
    const value = themes[(current + step + themes.length) % themes.length];
    event.preventDefault();
    setTheme(value);
    const target = theme.querySelector(`[data-theme-set="${value}"]`);
    if (target instanceof HTMLElement) target.focus();
  });
}

document.addEventListener("click", (event) => {
  const button = event.target instanceof Element ? event.target.closest("[data-peek]") : null;
  if (!(button instanceof HTMLButtonElement)) return;
  const input = $(button.dataset.peek || "");
  if (!(input instanceof HTMLInputElement)) return;
  const showing = input.type === "password";
  input.type = showing ? "text" : "password";
  button.setAttribute("aria-pressed", showing ? "true" : "false");
  button.setAttribute("aria-label", showing ? "Hide password" : "Show password");
  button.textContent = showing ? "Hide" : "Show";
});

for (const form of Object.values(forms)) {
  if (!(form instanceof HTMLFormElement)) continue;
  form.addEventListener("input", () => {
    show("");
  });
}

forms.login?.addEventListener("submit", (event) => {
  event.preventDefault();
  if (forms.login instanceof HTMLFormElement) submit(forms.login, "/api/session", "login");
});
forms.register?.addEventListener("submit", (event) => {
  event.preventDefault();
  if (forms.register instanceof HTMLFormElement) submit(forms.register, "/api/register", "register");
});
forms.setup?.addEventListener("submit", (event) => {
  event.preventDefault();
  if (forms.setup instanceof HTMLFormElement) submit(forms.setup, "/api/setup", "setup");
});

$("show-register")?.addEventListener("click", () => {
  showScreen("register");
});
$("show-login")?.addEventListener("click", () => {
  showScreen("login");
});
$("back-login")?.addEventListener("click", () => {
  history.replaceState(null, "", "/login");
  showScreen("login");
});
$("logout")?.addEventListener("click", async () => {
  const logout = $("logout");
  if (logout instanceof HTMLButtonElement) logout.disabled = true;
  try {
    await api("DELETE", "/api/session");
  } catch {
    /* the cookie clear is best-effort; the redirect still leaves this screen */
  }
  location.href = "/login";
});

async function boot() {
  try {
    const me = await api("GET", "/api/me");
    const user = me.data.user;
    if (me.ok && user?.status === "active") {
      location.href = destination(user);
      return;
    }
    if (me.ok && user?.status === "pending") {
      showScreen("pending", user);
      return;
    }
    if (me.ok && user?.status === "blocked") {
      showScreen("blocked", user);
      return;
    }
    if (params.get("pending") === "1") {
      showScreen("pending");
      return;
    }
    if (me.status === 401 && me.data.setup) {
      showScreen("setup");
      return;
    }
    showScreen("login");
  } catch {
    showScreen("login");
    show("The desk is not reachable right now.");
  }
}

window.addEventListener("resize", fitTitle);
document.fonts?.ready.then(fitTitle).catch(() => {});

boot();
