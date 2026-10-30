import { evaluate, localTime, tilesForRadius, distanceKm } from "./pricing.js";

const DATA = "../v1/";
const SHOWN = 30; // so viele Stationen höchstens in der Liste
const $ = (id) => document.getElementById(id);

const euro = new Intl.NumberFormat("de-DE", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
const one = new Intl.NumberFormat("de-DE", { minimumFractionDigits: 1, maximumFractionDigits: 1 });
const whole = new Intl.NumberFormat("de-DE");
const clock = new Intl.DateTimeFormat("de-DE", { timeZone: "Europe/Berlin", hour: "2-digit", minute: "2-digit" });
const dateLong = new Intl.DateTimeFormat("de-DE", {
  timeZone: "Europe/Berlin", day: "numeric", month: "long", hour: "2-digit", minute: "2-digit",
});
const CONNECTORS = { ccs: "CCS", type2: "Typ 2", chademo: "CHAdeMO" };

// ---------------------------------------------------------------------------
// Zustand und Einstellungen (nur auf diesem Gerät)
// ---------------------------------------------------------------------------

const store = {
  get(key, fallback) {
    try {
      const raw = localStorage.getItem(`ladekrone.${key}`);
      return raw === null ? fallback : JSON.parse(raw);
    } catch {
      return fallback;
    }
  },
  set(key, value) {
    try {
      localStorage.setItem(`ladekrone.${key}`, JSON.stringify(value));
    } catch {
      /* privater Modus: dann eben ohne Merken */
    }
  },
};

const state = {
  meta: null,
  tiles: new Map(),
  place: null, // {lat, lon, label, gps}
  filters: { power: 0, radius: 10, operator: "", connector: "any", ...store.get("filters", {}) },
  quarter: null,
  offline: false,
};

// ---------------------------------------------------------------------------
// Hilfen
// ---------------------------------------------------------------------------

const esc = (value) =>
  String(value ?? "").replace(/[&<>"']/g, (ch) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[ch]);

function splitAddress(address) {
  if (!address) return { street: "", city: "" };
  const parts = address.split(", ");
  const last = parts.length > 1 ? parts.pop() : "";
  return { street: parts.join(", "), city: last.replace(/^\d{5}\s+/, "") };
}

function when(ms) {
  const now = localTime(Date.now());
  const then = localTime(ms);
  const sameDay = now.year === then.year && now.month === then.month && now.day === then.day;
  const tomorrow = localTime(ms - 86400000);
  const isTomorrow = tomorrow.year === now.year && tomorrow.month === now.month && tomorrow.day === now.day;
  const prefix = sameDay ? "" : isTomorrow ? "morgen " : "übermorgen ";
  return `${prefix}${clock.format(ms)} Uhr`;
}

function routeUrl(lat, lon) {
  const apple = /iPad|iPhone|iPod/.test(navigator.userAgent) || (navigator.platform === "MacIntel" && navigator.maxTouchPoints > 1);
  return apple
    ? `https://maps.apple.com/?daddr=${lat},${lon}`
    : `https://www.google.com/maps/dir/?api=1&destination=${lat},${lon}`;
}

function facts(offer) {
  const items = [`${one.format(offer.distanceKm)} km entfernt`];
  if (offer.powerKw) items.push(`${whole.format(Math.round(offer.powerKw))} kW`);
  const plugs = offer.connectors.map((k) => CONNECTORS[k] || k).join(", ");
  const kind = [offer.current ? offer.current.toUpperCase() : "", plugs].filter(Boolean).join(" ");
  if (kind) items.push(kind);
  if (offer.points > 1) items.push(`${offer.points} Ladepunkte`);
  return items;
}

function feeText(price) {
  const parts = [];
  if (price.sessionFee) parts.push(`Startgebühr ${euro.format(price.sessionFee)} €`);
  if (price.minuteFee) {
    parts.push(
      price.minuteFeeFrom
        ? `Blockiergebühr ${euro.format(price.minuteFee)} € pro Minute ab Minute ${price.minuteFeeFrom}`
        : `Zeitgebühr ${euro.format(price.minuteFee)} € pro Minute`,
    );
  }
  return parts.join(". ");
}

function untilText(offer) {
  if (!offer.until) return "";
  if (offer.nextPrice === null) return `Gilt bis ${when(offer.until)}, danach ohne Ad-hoc-Preis`;
  return `Gilt bis ${when(offer.until)}, danach ${euro.format(offer.nextPrice)} €`;
}

function note(text, kind = "") {
  const el = $("note");
  el.textContent = text;
  el.className = `note ${kind}`;
  el.hidden = !text;
}

// ---------------------------------------------------------------------------
// Daten laden
// ---------------------------------------------------------------------------

async function getJson(path) {
  const response = await fetch(DATA + path);
  if (!response.ok) throw new Error(`${path}: ${response.status}`);
  if (response.headers.get("x-ladekrone-cache") === "1") state.offline = true;
  return response.json();
}

async function loadMeta() {
  if (state.meta) return state.meta;
  const meta = await getJson("meta.json");
  meta.tileSet = new Set(meta.tiles);
  state.meta = meta;
  $("stand").textContent = `Stand der Daten: ${dateLong.format(new Date(meta.generated))} Uhr.`;
  return meta;
}

async function loadTiles(lat, lon, radius) {
  const meta = await loadMeta();
  const keys = tilesForRadius(lat, lon, radius, meta.tile_deg).filter((k) => meta.tileSet.has(k));
  const missing = keys.filter((k) => !state.tiles.has(k));
  const results = await Promise.allSettled(missing.map((k) => getJson(`tiles/${k}.json`)));
  let failed = 0;
  results.forEach((result, i) => {
    if (result.status === "fulfilled") state.tiles.set(missing[i], result.value.stations || []);
    else failed += 1;
  });
  return { stations: keys.flatMap((k) => state.tiles.get(k) || []), failed };
}

// Ortsverzeichnis für Suche und "In der Nähe von …"
let placesPromise = null;
function loadPlaces() {
  placesPromise ??= getJson("places.json").then(({ places }) => buildPlaceIndex(places)).catch(() => {
    placesPromise = null;
    return null;
  });
  return placesPromise;
}

const fold = (s) => s.toLowerCase().normalize("NFD").replace(/[̀-ͯ]/g, "").replace(/ß/g, "ss");
const foldUmlaut = (s) =>
  s.toLowerCase().replace(/ä/g, "ae").replace(/ö/g, "oe").replace(/ü/g, "ue").replace(/ß/g, "ss")
    .normalize("NFD").replace(/[̀-ͯ]/g, "");

function buildPlaceIndex(rows) {
  const postcodes = rows.map(([plz, city, lat, lon, n]) => ({ plz, city, lat, lon, n }));
  // Gleichnamige Orte zusammenfassen, solange sie nah beieinander liegen (Berlin hat viele PLZ).
  const byName = new Map();
  for (const p of postcodes) {
    const key = fold(p.city);
    const clusters = byName.get(key) || [];
    let cluster = clusters.find((c) => distanceKm(c.lat, c.lon, p.lat, p.lon) < 25);
    if (!cluster) {
      cluster = { city: p.city, lat: 0, lon: 0, n: 0, plz: p.plz, top: 0 };
      clusters.push(cluster);
    }
    cluster.lat = (cluster.lat * cluster.n + p.lat * p.n) / (cluster.n + p.n);
    cluster.lon = (cluster.lon * cluster.n + p.lon * p.n) / (cluster.n + p.n);
    cluster.n += p.n;
    if (p.n > cluster.top) {
      cluster.top = p.n;
      cluster.plz = p.plz;
    }
    byName.set(key, clusters);
  }
  const cities = [];
  for (const clusters of byName.values()) {
    for (const c of clusters) {
      const hint = clusters.length > 1 ? `PLZ ${c.plz.slice(0, 2)}…` : "";
      const words = [c.city, ...c.city.split(/[\s-]+/).slice(1)];
      cities.push({ ...c, hint, keys: words.flatMap((w) => [fold(w), foldUmlaut(w)]) });
    }
  }
  cities.sort((a, b) => b.n - a.n);
  return { postcodes, cities };
}

function searchPlaces(index, query) {
  const q = query.trim();
  if (!q) return [];
  if (/^\d+$/.test(q)) {
    return index.postcodes
      .filter((p) => p.plz.startsWith(q))
      .slice(0, 8)
      .map((p) => ({ label: `${p.plz} ${p.city}`, hint: "", lat: p.lat, lon: p.lon }));
  }
  const a = fold(q);
  const b = foldUmlaut(q);
  return index.cities
    .filter((c) => c.keys.some((k) => k.startsWith(a) || k.startsWith(b)))
    .slice(0, 8)
    .map((c) => ({ label: c.city, hint: c.hint, lat: c.lat, lon: c.lon }));
}

function nearestCity(index, lat, lon) {
  let best = null;
  for (const p of index.postcodes) {
    const d = distanceKm(lat, lon, p.lat, p.lon);
    if (d < 15 && (!best || d < best.d)) best = { d, city: p.city };
  }
  return best?.city || null;
}

// ---------------------------------------------------------------------------
// Anzeige
// ---------------------------------------------------------------------------

function renderWinner(offer) {
  const { street, city } = splitAddress(offer.address);
  const fees = feeText(offer.price);
  return `<article class="winner" aria-labelledby="winner-price">
    <p class="label"><img src="icons/icon-192.png" alt="">Gerade am günstigsten</p>
    <p class="price" id="winner-price"><span class="num">${euro.format(offer.price.kwh)}</span><span class="unit">€/kWh</span></p>
    <p class="who">${esc(offer.operator || "Unbekanntes Ladenetz")}</p>
    <p class="addr">${esc([street, city].filter(Boolean).join(", "))}</p>
    <ul class="facts">${facts(offer).map((f) => `<li>${esc(f)}</li>`).join("")}</ul>
    ${offer.until ? `<p class="until">${esc(untilText(offer))}</p>` : ""}
    ${fees ? `<p class="fees">${esc(fees)}</p>` : ""}
    <div class="actions"><a class="btn primary" href="${routeUrl(offer.latitude, offer.longitude)}" target="_blank" rel="noopener">Route starten</a></div>
  </article>`;
}

const CLOCK_ICON =
  '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 2a10 10 0 1 1 0 20 10 10 0 0 1 0-20Zm0 2a8 8 0 1 0 0 16 8 8 0 0 0 0-16Zm0 2.2a1 1 0 0 1 1 1v4.4l3 1.8a1 1 0 1 1-1 1.7l-3.5-2.1a1 1 0 0 1-.5-.9V7.2a1 1 0 0 1 1-1Z"/></svg>';

function renderLater(outlook, current) {
  if (!outlook || !current || outlook.start === null || outlook.offer.price.kwh >= current.price.kwh - 1e-9) return "";
  const o = outlook.offer;
  const { city } = splitAddress(o.address);
  const span = outlook.end ? ` bis ${when(outlook.end)}` : "";
  return `<aside class="later" aria-label="Später günstiger">${CLOCK_ICON}
    <strong>Später günstiger: ${euro.format(o.price.kwh)} €/kWh</strong>
    <p>ab ${esc(when(outlook.start))}${esc(span)} bei ${esc(o.operator || "Unbekannt")}${city ? `, ${esc(city)}` : ""}, ${one.format(o.distanceKm)} km</p>
  </aside>`;
}

function renderList(offers) {
  if (!offers.length) return "";
  const rows = offers.map((o) => {
    const { street, city } = splitAddress(o.address);
    const fees = feeText(o.price);
    const head = [city || street, `${one.format(o.distanceKm)} km`, o.powerKw ? `${whole.format(Math.round(o.powerKw))} kW` : ""]
      .filter(Boolean)
      .join(", ");
    return `<li><details>
      <summary>
        <span class="op">${esc(o.operator || "Unbekanntes Ladenetz")}</span>
        <span class="p">${euro.format(o.price.kwh)}<small>€/kWh</small></span>
        <span class="sub">${esc(head)}</span>
        ${o.until ? `<span class="change">${esc(untilText(o))}</span>` : ""}
      </summary>
      <div class="detail">
        <p>${esc([street, city].filter(Boolean).join(", "))}</p>
        <p>${esc(facts(o).slice(1).join(", "))}</p>
        ${fees ? `<p>${esc(fees)}</p>` : ""}
        <a class="btn" href="${routeUrl(o.latitude, o.longitude)}" target="_blank" rel="noopener">Route starten</a>
      </div>
    </details></li>`;
  });
  return `<section class="more" aria-labelledby="more-title"><h3 id="more-title">Weitere Stationen</h3>
    <ul class="list">${rows.join("")}</ul></section>`;
}

function renderEmpty(count) {
  const radius = state.filters.radius;
  const text = count
    ? `Im Umkreis von ${radius} km gibt es ${whole.format(count)} passende Stationen, aber keine meldet gerade einen Preis.`
    : `Im Umkreis von ${radius} km gibt es keine passende Station.`;
  const bigger = [5, 10, 25, 50].find((r) => r > radius);
  const f = state.filters;
  const filtered = f.power > 0 || f.operator || f.connector !== "any";
  return `<div class="empty"><p>${esc(text)}</p><div class="actions">${
    bigger ? `<button class="btn" type="button" data-radius="${bigger}">Umkreis auf ${bigger} km vergrößern</button>` : ""
  }${filtered ? '<button class="btn ghost" type="button" data-reset>Filter zurücksetzen</button>' : ""}</div></div>`;
}

function updateOperatorOptions(stations) {
  const select = $("f-operator");
  const counts = new Map();
  for (const s of stations) {
    const name = s.o || s.b;
    if (name && (s.r || []).length) counts.set(name, (counts.get(name) || 0) + 1);
  }
  const names = [...counts.entries()].sort((a, b) => b[1] - a[1]).slice(0, 40).map(([n]) => n);
  const chosen = state.filters.operator;
  if (chosen && !names.includes(chosen)) names.unshift(chosen);
  names.sort((a, b) => a.localeCompare(b, "de"));
  select.innerHTML =
    '<option value="">Alle Ladenetze</option>' + names.map((n) => `<option value="${esc(n)}">${esc(n)}</option>`).join("");
  select.value = chosen;
}

function syncFilterControls() {
  const f = state.filters;
  $("f-power").value = String(f.power);
  $("f-radius").value = String(f.radius);
  $("f-connector").value = f.connector;
  $("f-power").classList.toggle("set", f.power > 0);
  $("f-operator").classList.toggle("set", Boolean(f.operator));
  $("f-connector").classList.toggle("set", f.connector !== "any");
}

let renderToken = 0;
async function render() {
  const place = state.place;
  if (!place) return;
  const token = ++renderToken;
  state.offline = !navigator.onLine;
  document.body.classList.add("has-place");
  $("results").hidden = false;
  $("place-title").textContent = place.label;
  syncFilterControls();
  const f = state.filters;
  let loaded;
  try {
    loaded = await loadTiles(place.lat, place.lon, f.radius);
  } catch {
    if (token !== renderToken) return;
    $("winner").innerHTML = "";
    $("later").innerHTML = "";
    $("more").innerHTML = "";
    $("summary").textContent = "";
    note("Die Preise konnten nicht geladen werden. Prüf deine Verbindung und versuch es noch einmal.", "error");
    return;
  }
  if (token !== renderToken) return;
  const now = Date.now();
  state.quarter = Math.floor(now / 900000);
  const filters = {
    minPowerKw: f.power,
    connector: f.connector,
    operators: f.operator ? [f.operator] : [],
  };
  const { offers, outlook, count } = evaluate(loaded.stations, place.lat, place.lon, f.radius, filters, now, SHOWN + 1);
  updateOperatorOptions(loaded.stations);
  if (loaded.failed) note("Ein Teil der Umgebung konnte nicht geladen werden. Die Liste ist vielleicht unvollständig.", "error");
  else if (state.offline) note("Du bist offline. Angezeigt werden die zuletzt geladenen Preise.");
  else note("");
  if (!offers.length) {
    $("winner").innerHTML = renderEmpty(count);
    $("later").innerHTML = "";
    $("more").innerHTML = "";
    $("summary").textContent = "";
    return;
  }
  $("winner").innerHTML = renderWinner(offers[0]);
  $("later").innerHTML = renderLater(outlook, offers[0]);
  $("more").innerHTML = renderList(offers.slice(1, SHOWN + 1));
  $("summary").textContent =
    `${whole.format(offers.length)} von ${whole.format(count)} passenden Stationen im Umkreis von ${f.radius} km melden gerade einen Preis.`;
}

// ---------------------------------------------------------------------------
// Ort wählen
// ---------------------------------------------------------------------------

function setPlace(place, remember) {
  state.place = place;
  if (remember) store.set("place", place);
  render();
}

function locate() {
  if (!("geolocation" in navigator)) {
    note("Dein Gerät gibt keinen Standort heraus. Gib einen Ort oder eine PLZ ein.", "error");
    $("q").focus();
    return;
  }
  note("Standort wird ermittelt …");
  navigator.geolocation.getCurrentPosition(
    async ({ coords }) => {
      note("");
      const place = { lat: coords.latitude, lon: coords.longitude, label: "In deiner Nähe", gps: true };
      setPlace(place, false);
      store.set("place", null);
      const index = await loadPlaces();
      const city = index && nearestCity(index, place.lat, place.lon);
      if (city && state.place === place) {
        place.label = `In der Nähe von ${city}`;
        $("place-title").textContent = place.label;
      }
    },
    (error) => {
      const denied = error.code === error.PERMISSION_DENIED;
      note(
        denied
          ? "Der Standort ist für diese Seite blockiert. Gib einen Ort oder eine PLZ ein, oder erlaube den Standort in den Einstellungen deines Browsers."
          : "Der Standort ließ sich gerade nicht bestimmen. Gib einen Ort oder eine PLZ ein.",
        "error",
      );
      $("q").focus();
    },
    { enableHighAccuracy: false, timeout: 15000, maximumAge: 120000 },
  );
}

function setupSearch() {
  const input = $("q");
  const list = $("suggestions");
  let items = [];
  let active = -1;

  const close = () => {
    list.hidden = true;
    input.setAttribute("aria-expanded", "false");
    input.removeAttribute("aria-activedescendant");
    active = -1;
  };
  const choose = (item) => {
    input.value = "";
    close();
    input.blur();
    setPlace({ lat: item.lat, lon: item.lon, label: item.label, gps: false }, true);
  };
  const highlight = (index) => {
    active = index;
    [...list.children].forEach((li, i) => li.setAttribute("aria-selected", String(i === index)));
    if (index >= 0) input.setAttribute("aria-activedescendant", `s-${index}`);
  };

  input.addEventListener("focus", () => loadPlaces());
  input.addEventListener("input", async () => {
    const query = input.value;
    const index = await loadPlaces();
    if (query !== input.value) return;
    if (!index) {
      note("Die Ortssuche ist gerade nicht erreichbar.", "error");
      return;
    }
    items = searchPlaces(index, query);
    if (!items.length) {
      if (query.trim().length >= 3) {
        list.innerHTML = '<li role="option" aria-disabled="true">Kein Ort gefunden</li>';
        list.hidden = false;
        input.setAttribute("aria-expanded", "true");
      } else close();
      return;
    }
    list.innerHTML = items
      .map((item, i) => `<li role="option" id="s-${i}" aria-selected="false">${esc(item.label)}${item.hint ? `<small>${esc(item.hint)}</small>` : ""}</li>`)
      .join("");
    list.hidden = false;
    input.setAttribute("aria-expanded", "true");
    active = -1;
  });
  input.addEventListener("keydown", (event) => {
    if (list.hidden || !items.length) return;
    if (event.key === "ArrowDown") {
      event.preventDefault();
      highlight((active + 1) % items.length);
    } else if (event.key === "ArrowUp") {
      event.preventDefault();
      highlight((active - 1 + items.length) % items.length);
    } else if (event.key === "Enter") {
      event.preventDefault();
      choose(items[Math.max(active, 0)]);
    } else if (event.key === "Escape") {
      close();
    }
  });
  list.addEventListener("pointerdown", (event) => {
    const li = event.target.closest("li[id]");
    if (!li) return;
    event.preventDefault();
    choose(items[Number(li.id.slice(2))]);
  });
  input.addEventListener("blur", () => setTimeout(close, 150));
}

function setupFilters() {
  const bind = (id, key, parse) =>
    $(id).addEventListener("change", (event) => {
      state.filters[key] = parse(event.target.value);
      store.set("filters", state.filters);
      render();
    });
  bind("f-power", "power", Number);
  bind("f-radius", "radius", Number);
  bind("f-operator", "operator", String);
  bind("f-connector", "connector", String);
  $("winner").addEventListener("click", (event) => {
    if (event.target.closest("[data-reset]")) {
      Object.assign(state.filters, { power: 0, operator: "", connector: "any" });
    } else {
      const button = event.target.closest("[data-radius]");
      if (!button) return;
      state.filters.radius = Number(button.dataset.radius);
    }
    store.set("filters", state.filters);
    render();
  });
}

// ---------------------------------------------------------------------------
// Installieren, offline, Start
// ---------------------------------------------------------------------------

function setupInstall() {
  let deferred = null;
  window.addEventListener("beforeinstallprompt", (event) => {
    event.preventDefault();
    deferred = event;
    $("install").hidden = false;
  });
  $("install").addEventListener("click", async () => {
    if (!deferred) return;
    deferred.prompt();
    await deferred.userChoice;
    deferred = null;
    $("install").hidden = true;
  });
  const standalone = window.matchMedia("(display-mode: standalone)").matches || navigator.standalone;
  const ios = /iPad|iPhone|iPod/.test(navigator.userAgent);
  if (ios && !standalone) {
    $("install-hint").textContent = "Auf dem iPhone: Teilen antippen und dann „Zum Home-Bildschirm“ wählen.";
    $("install-hint").hidden = false;
  }
  if ("serviceWorker" in navigator) navigator.serviceWorker.register("sw.js").catch(() => {});
}

async function start() {
  setupSearch();
  setupFilters();
  setupInstall();
  $("locate").addEventListener("click", locate);
  loadMeta().catch(() => note("Die Preise konnten nicht geladen werden. Prüf deine Verbindung.", "error"));

  const saved = store.get("place", null);
  let granted = false;
  try {
    granted = (await navigator.permissions?.query({ name: "geolocation" }))?.state === "granted";
  } catch {
    granted = false;
  }
  if (granted) locate();
  else if (saved && typeof saved.lat === "number") setPlace(saved, false);

  // Preise wechseln zur Viertelstunde (z. B. Nachttarif): dann neu rechnen.
  setInterval(() => {
    if (state.place && Math.floor(Date.now() / 900000) !== state.quarter) render();
  }, 30000);
  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState === "visible" && state.place) render();
  });
}

start();
