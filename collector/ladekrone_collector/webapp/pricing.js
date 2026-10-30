// Preislogik der Web-App: Übertragung von custom_components/ladekrone/pricing.py.
// Beide müssen gleich rechnen; tests/collector/test_webapp_pricing.py prüft das.
// Alle Uhrzeiten in den Daten sind Ortszeit (Europe/Berlin).

const TZ = "Europe/Berlin";
const EARTH_RADIUS_KM = 6371.0088;
export const STEP_MS = 15 * 60 * 1000;
export const HORIZON_STEPS = 96; // 24 Stunden in Viertelstunden
export const TOP_OFFERS = 5;

const CONNECTOR_ALIASES = { ccs: ["ccs"], type2: ["type2"], chademo: ["chademo"] };
const WEEKDAYS = { Mon: 0, Tue: 1, Wed: 2, Thu: 3, Fri: 4, Sat: 5, Sun: 6 };
const END_OF_DAY = 1440 - 1e-8; // "24:00" wie time(23, 59, 59, 999999)

const partsFormat = new Intl.DateTimeFormat("en-GB", {
  timeZone: TZ,
  hourCycle: "h23",
  year: "numeric",
  month: "numeric",
  day: "numeric",
  hour: "numeric",
  minute: "numeric",
  second: "numeric",
  weekday: "short",
});

/** Ortszeit-Bestandteile zu einem Zeitpunkt (Millisekunden seit 1970). */
export function localTime(ms) {
  const parts = {};
  for (const { type, value } of partsFormat.formatToParts(new Date(ms))) parts[type] = value;
  const msPart = ((ms % 1000) + 1000) % 1000;
  return {
    t: ms,
    year: +parts.year,
    month: +parts.month,
    day: +parts.day,
    hour: +parts.hour,
    minute: +parts.minute,
    second: +parts.second,
    weekday: WEEKDAYS[parts.weekday],
    tod: +parts.hour * 60 + +parts.minute + +parts.second / 60 + msPart / 60000,
  };
}

function offsetMs(ms) {
  const whole = Math.floor(ms / 1000) * 1000;
  const l = localTime(whole);
  return Date.UTC(l.year, l.month - 1, l.day, l.hour, l.minute, l.second) - whole;
}

/** Ortszeit (Europe/Berlin) ohne Zonenangabe in einen Zeitpunkt umrechnen.
 * Wie zoneinfo mit fold=0: doppelte Stunde -> erste, fehlende Stunde -> alte Abweichung. */
function berlinToMs(y, mo, d, h, mi, s, frac) {
  const naive = Date.UTC(y, mo - 1, d, h, mi, s, frac);
  const before = offsetMs(naive - 6 * 3600000);
  const after = offsetMs(naive + 6 * 3600000);
  const valid = [before, after].map((o) => naive - o).filter((ms) => naive - offsetMs(ms) === ms);
  return valid.length ? Math.min(...valid) : naive - before;
}

const ISO_RE =
  /^(\d{4})-(\d{2})-(\d{2})(?:[T ](\d{2}):(\d{2})(?::(\d{2})(?:[.,](\d+))?)?)?\s*(Z|[+-]\d{2}(?::?\d{2})?)?$/i;

/** Wie _parse_dt: ISO-Zeitpunkt, ohne Zone als Ortszeit gelesen. */
export function parseDt(value) {
  if (typeof value !== "string") return null;
  const m = ISO_RE.exec(value.trim());
  if (!m) return null;
  const [, y, mo, d, h = "0", mi = "0", s = "0", fracRaw = "", zone] = m;
  const frac = fracRaw ? Math.floor(+`0.${fracRaw}` * 1000) : 0;
  if (+mo < 1 || +mo > 12 || +d < 1 || +d > 31 || +h > 23 || +mi > 59 || +s > 59) return null;
  if (!zone) return berlinToMs(+y, +mo, +d, +h, +mi, +s, frac);
  let offset = 0;
  if (zone.toUpperCase() !== "Z") {
    const sign = zone[0] === "-" ? -1 : 1;
    const digits = zone.slice(1).replace(":", "");
    offset = sign * (+digits.slice(0, 2) * 60 + +(digits.slice(2, 4) || 0)) * 60000;
  }
  return Date.UTC(+y, +mo - 1, +d, +h, +mi, +s, frac) - offset;
}

// --------------------------------------------------------------------------
// Geometrie
// --------------------------------------------------------------------------

const rad = (deg) => (deg * Math.PI) / 180;

export function distanceKm(lat1, lon1, lat2, lon2) {
  const p1 = rad(lat1);
  const p2 = rad(lat2);
  const dp = p2 - p1;
  const dl = rad(lon2 - lon1);
  const a = Math.sin(dp / 2) ** 2 + Math.cos(p1) * Math.cos(p2) * Math.sin(dl / 2) ** 2;
  return 2 * EARTH_RADIUS_KM * Math.asin(Math.min(1, Math.sqrt(a)));
}

export function tilesForRadius(lat, lon, radiusKm, tileDeg) {
  const dlat = radiusKm / 111.32;
  const dlon = radiusKm / (111.32 * Math.max(0.01, Math.cos(rad(lat))));
  const keys = [];
  for (let y = Math.floor((lat - dlat) / tileDeg); y <= Math.floor((lat + dlat) / tileDeg); y++) {
    for (let x = Math.floor((lon - dlon) / tileDeg); x <= Math.floor((lon + dlon) / tileDeg); x++) {
      keys.push(`${y}_${x}`);
    }
  }
  return keys;
}

// --------------------------------------------------------------------------
// Zeitliche Gültigkeit
// --------------------------------------------------------------------------

function parseClock(value) {
  if (typeof value !== "string") return null;
  const [h, m] = value.split(":");
  if (!/^\s*[+-]?\d+\s*$/.test(h ?? "") || !/^\s*[+-]?\d+\s*$/.test(m ?? "")) return null;
  const hour = parseInt(h, 10);
  const minute = parseInt(m, 10);
  if (hour === 24 && minute === 0) return END_OF_DAY;
  if (!(hour >= 0 && hour < 24 && minute >= 0 && minute < 60)) return null;
  return hour * 60 + minute;
}

function inWindow(now, start, end) {
  if (start === end) return true;
  if (start < end) return start <= now && now < end;
  return now >= start || now < end; // über Mitternacht
}

const filled = (list) => Array.isArray(list) && list.length > 0;

export function periodMatches(period, local) {
  if (period.sp) return false; // Sondertage (z. B. Feiertage) lassen sich nicht auswerten
  const absolute = period.a;
  if (filled(absolute)) {
    const start = parseDt(absolute[0]);
    const end = parseDt(absolute[1]);
    if (start !== null && local.t < start) return false;
    if (end !== null && local.t >= end) return false;
  }
  if (filled(period.d) && !period.d.includes(local.weekday)) return false;
  if (filled(period.m) && !period.m.includes(local.month)) return false;
  if (filled(period.t)) {
    const matched = period.t.some(([startRaw, endRaw]) => {
      const start = parseClock(startRaw);
      const end = parseClock(endRaw);
      return start !== null && end !== null && inWindow(local.tod, start, end);
    });
    if (!matched) return false;
  }
  return true;
}

export function validityMatches(validity, local) {
  if (!validity || Object.keys(validity).length === 0) return true;
  const start = parseDt(validity.s);
  const end = parseDt(validity.e);
  if (start !== null && local.t < start) return false;
  if (end !== null && local.t > end) return false;
  if (filled(validity.in) && !validity.in.some((p) => periodMatches(p, local))) return false;
  return !(validity.ex || []).some((p) => periodMatches(p, local));
}

const restricted = (c) => {
  const w = c.w || {};
  return filled(w.in) || filled(w.ex);
};

/** Zum Zeitpunkt gültiger Ad-hoc-Preis eines Tarifs (wie current_price). */
export function currentPrice(rate, local) {
  const currency = "cur" in rate ? rate.cur : "EUR";
  if (currency !== "EUR" || !validityMatches(rate.w, local)) return null;
  const active = (rate.p || []).filter((c) => validityMatches(c.w, local));
  let kwh = active.filter((c) => c.t === "kwh" && !(c.f || 0));
  if (!kwh.length) kwh = active.filter((c) => c.t === "kwh");
  const free = active.some((c) => c.t === "free");
  let energy;
  if (kwh.length) {
    const limited = kwh.filter(restricted);
    energy = Math.max(...(limited.length ? limited : kwh).map((c) => c.v));
  } else if (free) {
    energy = 0;
  } else {
    return null;
  }
  const price = { kwh: energy, updated: rate.u ?? null, minuteFee: null, minuteFeeFrom: null, sessionFee: null };
  const minute = active.filter((c) => c.t === "min");
  if (minute.length) {
    const fee = minute.reduce((a, b) => ((b.f || 0) < (a.f || 0) ? b : a));
    price.minuteFee = fee.v;
    price.minuteFeeFrom = fee.f ?? null;
  }
  const session = active.filter((c) => c.t === "base" || c.t === "flat").map((c) => c.v);
  if (session.length) price.sessionFee = session.reduce((a, b) => a + b, 0);
  return price;
}

// --------------------------------------------------------------------------
// Suche
// --------------------------------------------------------------------------

function groupMatches(group, filters) {
  if (filters.minPowerKw > 0) {
    if (group.kw == null || group.kw < filters.minPowerKw) return false;
  }
  if (filters.current !== "any" && group.c !== filters.current) return false;
  if (filters.connector !== "any") {
    const wanted = CONNECTOR_ALIASES[filters.connector] || [filters.connector];
    if (!wanted.some((k) => (group.k || []).includes(k))) return false;
  }
  return true;
}

function operatorMatches(station, operators) {
  if (!operators.length) return true;
  const haystack = [station.o, station.b].filter(Boolean).join(" ").toLowerCase();
  return operators.some((op) => haystack.includes(op.toLowerCase()));
}

export function normalizeFilters(filters = {}) {
  return {
    minPowerKw: filters.minPowerKw || 0,
    current: filters.current || "any",
    connector: filters.connector || "any",
    operators: filters.operators || [],
  };
}

/** Günstigstes passendes Angebot einer Station oder null. */
export function bestOffer(station, lat, lon, filters, local) {
  if (!operatorMatches(station, filters.operators)) return null;
  const rates = station.r || [];
  let best = null;
  for (const group of station.g || []) {
    if (!groupMatches(group, filters)) continue;
    for (const index of group.r || []) {
      if (!(index >= 0 && index < rates.length)) continue;
      const price = currentPrice(rates[index], local);
      if (price === null) continue;
      if (
        best === null ||
        price.kwh < best.price.kwh ||
        (price.kwh === best.price.kwh && (group.kw || 0) > (best.group.kw || 0))
      ) {
        best = { price, group };
      }
    }
  }
  if (best === null) return null;
  const { price, group } = best;
  return {
    stationId: station.id,
    operator: station.o || station.b || null,
    address: station.a || null,
    latitude: station.la,
    longitude: station.lo,
    distanceKm: distanceKm(lat, lon, station.la, station.lo),
    price,
    powerKw: group.kw ?? null,
    current: group.c ?? null,
    connectors: [...(group.k || [])],
    points: group.n || 0,
    until: null,
    nextPrice: null,
  };
}

const round4 = (x) => Math.round(x * 1e4) / 1e4;

function safe(fn) {
  try {
    return fn();
  } catch {
    return undefined;
  }
}

export function stationsInRadius(stations, lat, lon, radiusKm) {
  return stations.filter((s) => {
    const d = safe(() => distanceKm(lat, lon, s.la, s.lo));
    return d !== undefined && d <= radiusKm;
  });
}

export function findOffers(stations, lat, lon, radiusKm, filters, nowMs) {
  const local = localTime(nowMs);
  const offers = [];
  for (const station of stations) {
    if (!(distanceKm(lat, lon, station.la, station.lo) <= radiusKm)) continue;
    const offer = safe(() => bestOffer(station, lat, lon, filters, local));
    if (offer) offers.push(offer);
  }
  offers.sort((a, b) => round4(a.price.kwh) - round4(b.price.kwh) || a.distanceKm - b.distanceKm);
  return offers;
}

export function countStations(stations, lat, lon, radiusKm, filters) {
  let total = 0;
  for (const station of stations) {
    if (!(distanceKm(lat, lon, station.la, station.lo) <= radiusKm)) continue;
    if (operatorMatches(station, filters.operators) && (station.g || []).some((g) => groupMatches(g, filters))) {
      total += 1;
    }
  }
  return total;
}

// --------------------------------------------------------------------------
// Vorschau: Preiswechsel in den nächsten 24 Stunden
// --------------------------------------------------------------------------

const hasTime = (w) => Boolean(w && (filled(w.in) || filled(w.ex) || w.s || w.e));

export function isTimeDependent(station) {
  return (station.r || []).some((rate) => hasTime(rate.w) || (rate.p || []).some((c) => hasTime(c.w)));
}

/** Viertelstunden ab der nächsten vollen Viertelstunde, wie future_steps. */
export function futureSteps(nowMs, count = HORIZON_STEPS) {
  const minute = Math.floor(nowMs / 60000) * 60000;
  const hour = Math.floor(minute / 3600000) * 3600000;
  const minutes = (minute - hour) / 60000;
  const first = hour + (Math.floor(minutes / 15) + 1) * STEP_MS;
  return Array.from({ length: count }, (_, i) => first + i * STEP_MS);
}

function stepLocals(steps) {
  return steps.map((t) => localTime(t));
}

export function nextChange(station, lat, lon, filters, currentKwh, locals) {
  for (const local of locals) {
    const offer = bestOffer(station, lat, lon, filters, local);
    const price = offer ? offer.price.kwh : null;
    if (price === null || round4(price) !== round4(currentKwh)) return [local.t, price];
  }
  return [null, null];
}

export function annotateChanges(offers, byId, lat, lon, filters, locals) {
  for (const offer of offers) {
    const station = byId.get(offer.stationId);
    if (!station || !isTimeDependent(station)) continue;
    [offer.until, offer.nextPrice] = nextChange(station, lat, lon, filters, offer.price.kwh, locals);
  }
}

export function cheapestAhead(nearby, current, lat, lon, filters, nowMs, locals) {
  let best = current;
  let start = nowMs;
  const dependent = nearby.filter(isTimeDependent);
  for (const local of locals) {
    for (const station of dependent) {
      const offer = safe(() => bestOffer(station, lat, lon, filters, local));
      if (offer && (best === null || offer.price.kwh < best.price.kwh - 1e-9)) {
        best = offer;
        start = local.t;
      }
    }
  }
  if (best === null) return null;
  const station = nearby.find((s) => s.id === best.stationId);
  let end = null;
  if (station && isTimeDependent(station)) {
    [end] = nextChange(station, lat, lon, filters, best.price.kwh, locals.filter((l) => l.t > start));
  }
  return { offer: best, start, end };
}

/** Alles für eine Anzeige: Angebote (Top 5 mit Zeitfenster), Vorschau, Anzahl. */
export function evaluate(stations, lat, lon, radiusKm, rawFilters, nowMs, annotate = TOP_OFFERS) {
  const filters = normalizeFilters(rawFilters);
  const nearby = stationsInRadius(stations, lat, lon, radiusKm);
  const offers = findOffers(nearby, lat, lon, radiusKm, filters, nowMs);
  const locals = stepLocals(futureSteps(nowMs));
  const byId = new Map(nearby.filter((s) => "id" in s).map((s) => [s.id, s]));
  annotateChanges(offers.slice(0, annotate), byId, lat, lon, filters, locals);
  const outlook = cheapestAhead(nearby, offers[0] || null, lat, lon, filters, nowMs, locals);
  const count = countStations(nearby, lat, lon, radiusKm, filters);
  return { offers, outlook, count };
}
