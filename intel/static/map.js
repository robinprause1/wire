/* Map stage for the Steuerung. A printed world map, one route from the sources to Jev.
   The hour travels along the route; small dots are messages on their way through the stations. */
import LAND from "./land.js";
import { STATIONS, journey, remain, stationLines } from "./shared.js";

/* Where the four stations sit: the news comes in from the west and ends at the desk near Frankfurt. */
const PLACES = {
  fetch: { lat: 37.77, lon: -122.42, label: [-16, 18], align: "right" },
  ingest: { lat: 40.71, lon: -74.0, label: [14, 30], align: "left" },
  retrieve: { lat: 51.51, lon: -0.13, label: [-34, -36], align: "right" },
  judge: { lat: 50.11, lon: 8.68, label: [18, 34], align: "left" },
};

/* The plate is a cut-out of the world: the map is drawn `zoom` times wider than the frame and centred on lat/lon.
   Each view belongs to one step of the setup; `lit` names the stations that step is about. */
const VIEWS = {
  overview: { lat: 35, lon: -52, zoom: 1.6, lit: [], clock: false },
  west: { lat: 41, lon: -97, zoom: 2.6, lit: ["fetch", "ingest"], clock: false },
  east: { lat: 49, lon: 2, zoom: 3.2, lit: ["retrieve", "judge"], clock: false },
  clock: { lat: 35, lon: -52, zoom: 1.6, lit: [], clock: true },
};
const MOVE_MS = 900;
const ARC_STEPS = 64;
const PACKETS = 7;
const RAD = Math.PI / 180;

/* Equal Earth projection (Šavrič, Patterson, Jenny 2018). Unit sphere; x spans about ±2.71, y about ±1.31. */
const A1 = 1.340264;
const A2 = -0.081106;
const A3 = 0.000893;
const A4 = 0.003796;
const M = Math.sqrt(3) / 2;

function equalEarth(lon, lat) {
  const phi = lat * RAD;
  const lambda = lon * RAD;
  const theta = Math.asin(M * Math.sin(phi));
  const t2 = theta * theta;
  const t6 = t2 * t2 * t2;
  return [
    (lambda * Math.cos(theta)) / (M * (A1 + 3 * A2 * t2 + t6 * (7 * A3 + 9 * A4 * t2))),
    theta * (A1 + A2 * t2 + t6 * (A3 + A4 * t2)),
  ];
}

const WORLD_HALF_WIDTH = equalEarth(180, 0)[0];

function toVector(lat, lon) {
  const phi = lat * RAD;
  const lambda = lon * RAD;
  return [Math.cos(phi) * Math.cos(lambda), Math.cos(phi) * Math.sin(lambda), Math.sin(phi)];
}

function toLatLon([x, y, z]) {
  return [Math.atan2(z, Math.hypot(x, y)) / RAD, Math.atan2(y, x) / RAD];
}

/* Point at fraction t of the great circle between two places. */
function greatCircle(from, to, t) {
  const a = toVector(from.lat, from.lon);
  const b = toVector(to.lat, to.lon);
  const dot = Math.min(1, Math.max(-1, a[0] * b[0] + a[1] * b[1] + a[2] * b[2]));
  const omega = Math.acos(dot);
  if (omega < 1e-6) return [from.lat, from.lon];
  const sine = Math.sin(omega);
  const wa = Math.sin((1 - t) * omega) / sine;
  const wb = Math.sin(t * omega) / sine;
  return toLatLon([a[0] * wa + b[0] * wb, a[1] * wa + b[1] * wb, a[2] * wa + b[2] * wb]);
}

function hexToRgb(hex) {
  const clean = hex.trim().replace("#", "");
  const full = clean.length === 3 ? clean.split("").map((c) => c + c).join("") : clean;
  const n = Number.parseInt(full, 16);
  return [(n >> 16) & 255, (n >> 8) & 255, n & 255];
}

function tint(hex, alpha) {
  const [r, g, b] = hexToRgb(hex);
  return `rgba(${r}, ${g}, ${b}, ${alpha})`;
}

const WireMap = (() => {
  const SEGMENTS = STATIONS.length - 1;
  const stations = STATIONS.map((station, index) => ({ ...station, ...PLACES[station.id], index }));

  let canvas;
  let viewport;
  let ctx;
  let ground;
  let width = 0;
  let height = 0;
  let dpr = 1;
  let scale = 1;
  let originX = 0;
  let originY = 0;
  let frame = 0;
  let board = null;
  let onDue = null;
  let onToggle = null;
  let dueNotified = false;
  let hover = null;
  let colors = null;
  let groundDirty = true;
  let reduced = false;
  let viewName = "overview";
  let draft = false;
  const view = { lat: VIEWS.overview.lat, lon: VIEWS.overview.lon, zoom: VIEWS.overview.zoom };
  let move = null;
  let lastTick = 0;
  const born = performance.now();
  const open = {};
  const target = {};
  const pulses = {};
  const packets = [];
  const routePoints = [];
  const lines = {};

  for (const station of stations) {
    open[station.id] = 1;
    target[station.id] = 1;
    pulses[station.id] = -1e9;
    lines[station.id] = "";
  }
  for (let index = 0; index < PACKETS; index += 1) packets.push({ offset: index / PACKETS, segment: -1 });

  /* ---------- public ---------- */

  function sync(steps) {
    if (!Array.isArray(steps)) return;
    for (const step of steps) {
      if (step.id in target) target[step.id] = step.enabled ? 1 : 0;
    }
    relabel();
  }

  function update(payload) {
    if (!payload) return;
    board = payload;
    const end = Date.parse(payload.nextRunAt || "");
    if (Number.isFinite(end) && end > Date.now()) dueNotified = false;
    relabel();
  }

  function whenDue(callback) {
    onDue = callback;
  }

  function whenToggle(callback) {
    onToggle = callback;
  }

  /* Move the plate to the part of the route a setup step is about. */
  function focus(name) {
    if (!(name in VIEWS) || name === viewName) return;
    viewName = name;
    groundDirty = true;
    const want = VIEWS[name];
    if (reduced) {
      move = null;
      Object.assign(view, { lat: want.lat, lon: want.lon, zoom: want.zoom });
      layout();
      return;
    }
    move = { from: { ...view }, to: { lat: want.lat, lon: want.lon, zoom: want.zoom }, start: performance.now() };
  }

  /* A desk that does not exist yet: the route is printed, nothing moves. */
  function setDraft(flag) {
    if (draft === Boolean(flag)) return;
    draft = Boolean(flag);
    groundDirty = true;
  }

  function mount(node, frameNode) {
    canvas = node;
    viewport = frameNode;
    ctx = canvas.getContext("2d");
    ground = document.createElement("canvas");
    reduced = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    readColors();
    resize();
    new ResizeObserver(resize).observe(viewport);
    new MutationObserver(() => {
      readColors();
      groundDirty = true;
    }).observe(document.documentElement, { attributes: true, attributeFilter: ["data-theme"] });
    document.fonts?.load('600 12px "Schibsted Grotesk"').then(() => (groundDirty = true));
    canvas.addEventListener("pointermove", onMove);
    canvas.addEventListener("pointerleave", () => setHover(null));
    canvas.addEventListener("click", onClick);
    document.addEventListener("visibilitychange", () => {
      if (document.hidden) stop();
      else start();
    });
    start();
  }

  /* ---------- geometry ---------- */

  function project(lat, lon) {
    const [x, y] = equalEarth(lon, lat);
    return [originX + x * scale, originY - y * scale];
  }

  function along(t) {
    const scaled = Math.min(1, Math.max(0, t)) * SEGMENTS;
    const index = Math.min(SEGMENTS - 1, Math.floor(scaled));
    const [lat, lon] = greatCircle(stations[index], stations[index + 1], scaled - index);
    return project(lat, lon);
  }

  function resize() {
    const box = viewport.getBoundingClientRect();
    if (!box.width || !box.height) return;
    width = box.width;
    height = box.height;
    dpr = Math.min(2, window.devicePixelRatio || 1);
    canvas.width = Math.round(width * dpr);
    canvas.height = Math.round(height * dpr);
    canvas.style.width = `${width}px`;
    canvas.style.height = `${height}px`;
    ground.width = canvas.width;
    ground.height = canvas.height;
    layout();
  }

  function layout() {
    scale = (width / (2 * WORLD_HALF_WIDTH)) * (width < 600 ? view.zoom * 1.35 : view.zoom);
    const [fx, fy] = equalEarth(view.lon, view.lat);
    originX = width / 2 - fx * scale;
    originY = height / 2 + fy * scale;
    routePoints.length = 0;
    for (let step = 0; step <= ARC_STEPS * SEGMENTS; step += 1) routePoints.push(along(step / (ARC_STEPS * SEGMENTS)));
    for (const station of stations) station.xy = project(station.lat, station.lon);
    groundDirty = true;
  }

  function readColors() {
    const style = getComputedStyle(viewport);
    const read = (name, fallback) => style.getPropertyValue(name).trim() || fallback;
    colors = {
      paper: read("--paper", "#faf8f2"),
      ink: read("--ink", "#141311"),
      muted: read("--muted", "#6e685d"),
      accent: read("--accent", "#1e5a3c"),
      dark: document.documentElement.dataset.theme === "dark",
    };
  }

  function relabel() {
    const next = stationLines(board, { short: true });
    for (const station of stations) lines[station.id] = next[station.id] || "";
    groundDirty = true;
  }

  /* ---------- the printed layer: paper, graticule, land, dashed route, labels ---------- */

  function paintGround() {
    groundDirty = false;
    const g = ground.getContext("2d");
    g.setTransform(dpr, 0, 0, dpr, 0, 0);
    g.clearRect(0, 0, width, height);
    g.fillStyle = colors.paper;
    g.fillRect(0, 0, width, height);

    /* Graticule every 15 degrees. */
    g.strokeStyle = tint(colors.ink, colors.dark ? 0.1 : 0.07);
    g.lineWidth = 1;
    g.beginPath();
    for (let lon = -180; lon <= 180; lon += 15) {
      for (let lat = -90; lat <= 90; lat += 2) {
        const [x, y] = project(lat, lon);
        if (lat === -90) g.moveTo(x, y);
        else g.lineTo(x, y);
      }
    }
    for (let lat = -75; lat <= 75; lat += 15) {
      for (let lon = -180; lon <= 180; lon += 3) {
        const [x, y] = project(lat, lon);
        if (lon === -180) g.moveTo(x, y);
        else g.lineTo(x, y);
      }
    }
    g.stroke();

    /* Land, flat, like a tint plate. */
    g.beginPath();
    for (const ring of LAND) {
      for (let index = 0; index < ring.length; index += 2) {
        const [x, y] = project(ring[index + 1], ring[index]);
        if (index === 0) g.moveTo(x, y);
        else g.lineTo(x, y);
      }
      g.closePath();
    }
    g.fillStyle = tint(colors.ink, colors.dark ? 0.16 : 0.11);
    g.fill("evenodd");

    /* Edge of the world. */
    g.beginPath();
    for (let lat = -90; lat <= 90; lat += 2) {
      const [x, y] = project(lat, -180);
      if (lat === -90) g.moveTo(x, y);
      else g.lineTo(x, y);
    }
    for (let lat = 90; lat >= -90; lat -= 2) {
      const [x, y] = project(lat, 180);
      g.lineTo(x, y);
    }
    g.closePath();
    g.strokeStyle = tint(colors.ink, 0.22);
    g.stroke();

    /* The route, dashed, as on a cable map. */
    g.beginPath();
    routePoints.forEach(([x, y], index) => (index === 0 ? g.moveTo(x, y) : g.lineTo(x, y)));
    g.setLineDash([2, 4]);
    g.lineWidth = 1;
    g.strokeStyle = tint(colors.ink, 0.6);
    g.stroke();
    g.setLineDash([]);

    for (const station of stations) paintLabel(g, station);
  }

  function paintLabel(g, station) {
    const [x, y] = station.xy;
    if (x < -20 || x > width + 20 || y < -20 || y > height + 20) return;
    const [dx, dy] = station.label;
    const enabled = target[station.id] === 1;
    const lit = VIEWS[viewName].lit.includes(station.id);
    const ax = x + dx;
    const ay = y + dy;
    const right = station.align === "right";
    const title = `${String(station.index + 1).padStart(2, "0")}  ${station.short}`;
    const detail = draft ? "—" : enabled ? lines[station.id] || "—" : "off";

    g.strokeStyle = tint(colors.ink, 0.45);
    g.lineWidth = 1;
    g.beginPath();
    g.moveTo(x, y);
    g.lineTo(ax, ay);
    g.stroke();

    g.textAlign = right ? "right" : "left";
    g.textBaseline = "middle";
    const tx = ax + (right ? -5 : 5);
    g.font = '600 12px "Schibsted Grotesk", "Helvetica Neue", Arial, sans-serif';
    g.lineJoin = "round";
    g.lineWidth = 4;
    g.strokeStyle = colors.paper;
    g.strokeText(title, tx, ay - 7);
    g.fillStyle = !enabled ? colors.muted : lit ? colors.accent : colors.ink;
    g.fillText(title, tx, ay - 7);
    if (!enabled) {
      const span = g.measureText(title).width;
      g.strokeStyle = colors.muted;
      g.lineWidth = 1;
      g.beginPath();
      g.moveTo(tx, ay - 7);
      g.lineTo(tx + (right ? -span : span), ay - 7);
      g.stroke();
    }
    g.font = '400 11px "Schibsted Grotesk", "Helvetica Neue", Arial, sans-serif';
    g.lineWidth = 4;
    g.strokeStyle = colors.paper;
    g.strokeText(detail, tx, ay + 8);
    g.fillStyle = colors.muted;
    g.fillText(detail, tx, ay + 8);
  }

  /* ---------- the moving layer ---------- */

  function start() {
    if (!frame) frame = requestAnimationFrame(tick);
  }

  function stop() {
    cancelAnimationFrame(frame);
    frame = 0;
  }

  function tick(now) {
    frame = requestAnimationFrame(tick);
    if (!width || !colors) return;
    moveCamera(now);
    if (groundDirty) paintGround();
    const seconds = (now - born) / 1000;
    const blend = Math.min(1, (now - lastTick) / 160);
    lastTick = now;
    for (const station of stations) open[station.id] += ((draft ? 0 : target[station.id]) - open[station.id]) * blend;
    const trip = journey(board);
    if (trip.phase === "due" && !dueNotified && typeof onDue === "function") {
      dueNotified = true;
      onDue();
    }

    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, width, height);
    ctx.drawImage(ground, 0, 0, width, height);
    if (!draft) {
      paintProgress(trip);
      paintPackets(trip, seconds, now);
    }
    paintStations(now);
    if (!draft) paintMarker(trip, seconds);
  }

  /* Tween the plate toward the current view on the clock, so a slow frame rate only drops frames. */
  function moveCamera(now) {
    if (!move) return;
    const t = Math.min(1, (now - move.start) / MOVE_MS);
    const eased = 1 - Math.pow(1 - t, 3);
    view.lat = move.from.lat + (move.to.lat - move.from.lat) * eased;
    view.lon = move.from.lon + (move.to.lon - move.from.lon) * eased;
    view.zoom = move.from.zoom + (move.to.zoom - move.from.zoom) * eased;
    layout();
    if (t >= 1) move = null;
  }

  function paintProgress(trip) {
    const progress = trip.phase === "paused" ? 0 : trip.progress;
    if (progress <= 0) return;
    const last = Math.floor(progress * (routePoints.length - 1));
    ctx.beginPath();
    for (let index = 0; index <= last; index += 1) {
      const [x, y] = routePoints[index];
      if (index === 0) ctx.moveTo(x, y);
      else ctx.lineTo(x, y);
    }
    const [tx, ty] = along(progress);
    ctx.lineTo(tx, ty);
    ctx.lineWidth = VIEWS[viewName].clock ? 2.5 : 1.5;
    ctx.lineCap = "round";
    ctx.strokeStyle = colors.accent;
    ctx.stroke();
  }

  function paintPackets(trip, seconds, now) {
    if (reduced) return;
    const period = trip.phase === "running" ? 2.4 : trip.phase === "paused" ? 28 : 9;
    const dim = trip.phase === "paused" ? 0.3 : 1;
    for (const packet of packets) {
      const t = (seconds / period + packet.offset) % 1;
      const segment = Math.min(SEGMENTS - 1, Math.floor(t * SEGMENTS));
      const gate = Math.min(open[stations[segment].id], open[stations[segment + 1].id]);
      if (segment !== packet.segment) {
        if (packet.segment !== -1 && segment > packet.segment) pulses[stations[segment].id] = now;
        if (packet.segment === SEGMENTS - 1 && segment === 0) pulses[stations[SEGMENTS].id] = now;
        packet.segment = segment;
      }
      const fade = Math.pow(Math.sin(Math.PI * t), 0.4);
      const alpha = fade * gate * dim;
      if (alpha < 0.02) continue;
      const [x, y] = along(t);
      ctx.beginPath();
      ctx.arc(x, y, 2.2, 0, Math.PI * 2);
      ctx.fillStyle = tint(colors.ink, alpha);
      ctx.fill();
    }
  }

  function paintStations(now) {
    const lit = VIEWS[viewName].lit;
    for (const station of stations) {
      const [x, y] = station.xy;
      const amount = open[station.id];
      const age = (now - pulses[station.id]) / 900;
      if (lit.includes(station.id)) {
        ctx.beginPath();
        ctx.arc(x, y, 10, 0, Math.PI * 2);
        ctx.lineWidth = 1;
        ctx.strokeStyle = colors.accent;
        ctx.stroke();
      }
      if (!reduced && age >= 0 && age <= 1 && amount > 0.5) {
        ctx.beginPath();
        ctx.arc(x, y, 5 + age * 16, 0, Math.PI * 2);
        ctx.lineWidth = 1;
        ctx.strokeStyle = tint(colors.ink, (1 - age) * 0.6);
        ctx.stroke();
      }
      const radius = hover === station.id ? 6 : 4.5;
      ctx.beginPath();
      ctx.arc(x, y, radius, 0, Math.PI * 2);
      ctx.fillStyle = colors.paper;
      ctx.fill();
      ctx.beginPath();
      ctx.arc(x, y, radius, 0, Math.PI * 2);
      ctx.lineWidth = 1.25;
      ctx.strokeStyle = colors.ink;
      ctx.stroke();
      if (amount > 0.02) {
        ctx.beginPath();
        ctx.arc(x, y, Math.max(0, (radius - 1.5) * amount), 0, Math.PI * 2);
        ctx.fillStyle = colors.ink;
        ctx.fill();
      }
    }
  }

  function paintMarker(trip, seconds) {
    const progress = trip.phase === "paused" ? 0 : trip.progress;
    const [x, y] = along(progress);
    const live = trip.phase === "running" || trip.phase === "due";
    const breathe = live && !reduced ? 1 + Math.sin(seconds * 4) * 0.12 : 1;
    const alpha = trip.phase === "paused" ? 0.45 : 1;

    if (VIEWS[viewName].clock) {
      ctx.beginPath();
      ctx.arc(x, y, 16, 0, Math.PI * 2);
      ctx.fillStyle = tint(colors.accent, 0.12);
      ctx.fill();
    }
    ctx.beginPath();
    ctx.arc(x, y, 7.5 * breathe, 0, Math.PI * 2);
    ctx.fillStyle = colors.paper;
    ctx.fill();
    ctx.beginPath();
    ctx.arc(x, y, 5 * breathe, 0, Math.PI * 2);
    ctx.fillStyle = tint(colors.accent, alpha);
    ctx.fill();

    const text = markerText(trip);
    if (!text) return;
    /* The time sits on the side of the marker that the nearest station label leaves free. */
    let nearest = stations[0];
    for (const station of stations) {
      if (Math.hypot(station.xy[0] - x, station.xy[1] - y) < Math.hypot(nearest.xy[0] - x, nearest.xy[1] - y)) nearest = station;
    }
    const below = nearest.label[1] < 0;
    ctx.font = '600 11px "Schibsted Grotesk", "Helvetica Neue", Arial, sans-serif';
    ctx.textAlign = "center";
    ctx.textBaseline = below ? "top" : "bottom";
    ctx.lineJoin = "round";
    ctx.lineWidth = 4;
    ctx.strokeStyle = colors.paper;
    const ty = below ? y + 11 : y - 11;
    ctx.strokeText(text, x, ty);
    ctx.fillStyle = tint(colors.accent, alpha);
    ctx.fillText(text, x, ty);
  }

  function markerText(trip) {
    switch (trip.phase) {
      case "running":
        return "running";
      case "due":
        return "now";
      case "paused":
        return "paused";
      case "enroute": {
        const end = Date.parse(board?.nextRunAt || "");
        return Number.isFinite(end) ? remain(end - Date.now()) : "";
      }
      default:
        return "";
    }
  }

  /* ---------- pointer ---------- */

  function stationAt(event) {
    const box = canvas.getBoundingClientRect();
    const px = event.clientX - box.left;
    const py = event.clientY - box.top;
    for (const station of stations) {
      const [x, y] = station.xy;
      if (Math.hypot(px - x, py - y) <= 12) return station;
    }
    return null;
  }

  function setHover(station) {
    const next = station ? station.id : null;
    if (next === hover) return;
    hover = next;
    canvas.style.cursor = hover && onToggle ? "pointer" : "default";
    canvas.title = station ? `Turn ${station.name.toLowerCase()} ${target[station.id] === 1 ? "off" : "on"}` : "";
  }

  function onMove(event) {
    setHover(stationAt(event));
  }

  function onClick(event) {
    const station = stationAt(event);
    if (!station || !onToggle) return;
    onToggle(station.id, target[station.id] !== 1);
  }

  return { mount, sync, update, focus, setDraft, whenDue, whenToggle };
})();

export default WireMap;
