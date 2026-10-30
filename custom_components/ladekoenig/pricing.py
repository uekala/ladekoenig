"""Preislogik: Zeitfenster auswerten und günstigste Ladepunkte finden.

Reines Python ohne Home-Assistant-Abhängigkeiten, damit es sich gut testen lässt.
Alle Uhrzeiten aus den Daten sind Ortszeit (Europe/Berlin).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import UTC, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

LOCAL_TZ = ZoneInfo("Europe/Berlin")
EARTH_RADIUS_KM = 6371.0088
STEP = timedelta(minutes=15)
HORIZON = timedelta(hours=24)

CONNECTOR_ALIASES = {
    "ccs": {"ccs"},
    "type2": {"type2"},
    "chademo": {"chademo"},
}


@dataclass(frozen=True)
class Filters:
    """Suchkriterien aus der Konfiguration."""

    min_power_kw: float = 0
    current: str = "any"  # any | ac | dc
    connector: str = "any"  # any | ccs | type2 | chademo
    operators: tuple[str, ...] = ()


@dataclass
class Price:
    """Aktuell gültiger Ad-hoc-Preis eines Tarifs."""

    kwh: float
    minute_fee: float | None = None
    minute_fee_from: int | None = None
    minute_fee_cap: float | None = None
    session_fee: float | None = None
    updated: str | None = None


@dataclass
class Offer:
    """Günstigstes passendes Angebot einer Station."""

    station_id: str
    operator: str | None
    address: str | None
    latitude: float
    longitude: float
    distance_km: float
    price: Price
    power_kw: float | None
    current: str | None
    connectors: list[str] = field(default_factory=list)
    points: int = 0
    until: datetime | None = None
    next_price: float | None = None

    def as_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "operator": self.operator,
            "address": self.address,
            "distance_km": round(self.distance_km, 1),
            "price": round(self.price.kwh, 2),
            "power_kw": self.power_kw,
            "current_type": self.current,
            "connectors": self.connectors,
            "points": self.points,
            "latitude": self.latitude,
            "longitude": self.longitude,
        }
        if self.price.minute_fee is not None:
            data["minute_fee"] = round(self.price.minute_fee, 2)
            data["minute_fee_from"] = self.price.minute_fee_from
        if self.price.session_fee is not None:
            data["session_fee"] = round(self.price.session_fee, 2)
        if self.until is not None:
            data["until"] = self.until
            data["next_price"] = round(self.next_price, 2) if self.next_price is not None else None
        return data


# --------------------------------------------------------------------------
# Geometrie
# --------------------------------------------------------------------------


def distance_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = phi2 - phi1
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    return 2 * EARTH_RADIUS_KM * math.asin(min(1.0, math.sqrt(a)))


def tiles_for_radius(lat: float, lon: float, radius_km: float, tile_deg: float) -> list[str]:
    """Alle Kachelschlüssel, die den Suchkreis berühren."""
    dlat = radius_km / 111.32
    dlon = radius_km / (111.32 * max(0.01, math.cos(math.radians(lat))))
    lat_min, lat_max = math.floor((lat - dlat) / tile_deg), math.floor((lat + dlat) / tile_deg)
    lon_min, lon_max = math.floor((lon - dlon) / tile_deg), math.floor((lon + dlon) / tile_deg)
    return [f"{y}_{x}" for y in range(lat_min, lat_max + 1) for x in range(lon_min, lon_max + 1)]


# --------------------------------------------------------------------------
# Zeitliche Gültigkeit
# --------------------------------------------------------------------------


def _parse_clock(value: str) -> time | None:
    try:
        hour, minute = (int(part) for part in value.split(":")[:2])
    except (ValueError, AttributeError):
        return None
    if hour == 24 and minute == 0:
        return time(23, 59, 59, 999999)
    if not (0 <= hour < 24 and 0 <= minute < 60):
        return None
    return time(hour, minute)


def _in_window(now: time, start: time, end: time) -> bool:
    if start == end:
        return True
    if start < end:
        return start <= now < end
    return now >= start or now < end  # über Mitternacht


def _parse_dt(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=LOCAL_TZ)
    return parsed


def period_matches(period: dict, local: datetime) -> bool:
    """Alle angegebenen Kriterien einer Periode müssen zutreffen."""
    if period.get("sp"):
        # Sondertage (z. B. Feiertage) lassen sich nicht auswerten.
        return False
    absolute = period.get("a")
    if absolute:
        start, end = _parse_dt(absolute[0]), _parse_dt(absolute[1])
        if start and local < start:
            return False
        if end and local >= end:
            return False
    if (days := period.get("d")) and local.weekday() not in days:
        return False
    if (months := period.get("m")) and local.month not in months:
        return False
    windows = period.get("t")
    if windows:
        now = local.time()
        matched = False
        for start_raw, end_raw in windows:
            start, end = _parse_clock(start_raw), _parse_clock(end_raw)
            if start is not None and end is not None and _in_window(now, start, end):
                matched = True
                break
        if not matched:
            return False
    return True


def validity_matches(validity: dict | None, local: datetime) -> bool:
    if not validity:
        return True
    start, end = _parse_dt(validity.get("s")), _parse_dt(validity.get("e"))
    if start and local < start:
        return False
    if end and local > end:
        return False
    valid = validity.get("in")
    if valid and not any(period_matches(p, local) for p in valid):
        return False
    return not any(period_matches(p, local) for p in validity.get("ex") or [])


def _restricted(component: dict) -> bool:
    validity = component.get("w") or {}
    return bool(validity.get("in") or validity.get("ex"))


def current_price(rate: dict, local: datetime) -> Price | None:
    """Ermittelt den zum Zeitpunkt gültigen Preis eines Ad-hoc-Tarifs.

    Mehrere kWh-Preise werden über ihre Zeitfenster aufgelöst. Greifen
    mehrere gleichzeitig, gewinnt der zeitlich eingeschränkte und davon der
    höhere – lieber etwas zu teuer anzeigen als zu günstig.
    """
    if rate.get("cur", "EUR") != "EUR" or not validity_matches(rate.get("w"), local):
        return None
    active = [c for c in rate.get("p", []) if validity_matches(c.get("w"), local)]

    kwh = [c for c in active if c["t"] == "kwh" and not (c.get("f") or 0)]
    if not kwh:
        kwh = [c for c in active if c["t"] == "kwh"]
    free = any(c["t"] == "free" for c in active)
    if kwh:
        restricted = [c for c in kwh if _restricted(c)]
        energy_price = max(c["v"] for c in (restricted or kwh))
    elif free:
        energy_price = 0.0
    else:
        return None

    price = Price(kwh=energy_price, updated=rate.get("u"))
    minute = [c for c in active if c["t"] == "min"]
    if minute:
        fee = min(minute, key=lambda c: c.get("f") or 0)
        price.minute_fee = fee["v"]
        price.minute_fee_from = fee.get("f")
        price.minute_fee_cap = fee.get("cap")
    session = [c["v"] for c in active if c["t"] in ("base", "flat")]
    if session:
        price.session_fee = sum(session)
    return price


# --------------------------------------------------------------------------
# Suche
# --------------------------------------------------------------------------


def _group_matches(group: dict, filters: Filters) -> bool:
    if filters.min_power_kw > 0:
        power = group.get("kw")
        if power is None or power < filters.min_power_kw:
            return False
    if filters.current != "any" and group.get("c") != filters.current:
        return False
    if filters.connector != "any":
        wanted = CONNECTOR_ALIASES.get(filters.connector, {filters.connector})
        if not wanted & set(group.get("k", [])):
            return False
    return True


def _operator_matches(station: dict, operators: tuple[str, ...]) -> bool:
    if not operators:
        return True
    haystack = " ".join(filter(None, (station.get("o"), station.get("b")))).casefold()
    return any(op.casefold() in haystack for op in operators)


def best_offer(
    station: dict, lat: float, lon: float, filters: Filters, local: datetime
) -> Offer | None:
    """Günstigstes passendes Angebot einer Station oder None."""
    if not _operator_matches(station, filters.operators):
        return None
    rates = station.get("r") or []
    best: tuple[Price, dict] | None = None
    for group in station.get("g", []):
        if not _group_matches(group, filters):
            continue
        for index in group.get("r", []):
            if not 0 <= index < len(rates):
                continue
            price = current_price(rates[index], local)
            if price is None:
                continue
            if (
                best is None
                or price.kwh < best[0].kwh
                or (price.kwh == best[0].kwh and (group.get("kw") or 0) > (best[1].get("kw") or 0))
            ):
                best = (price, group)
    if best is None:
        return None
    price, group = best
    return Offer(
        station_id=station["id"],
        operator=station.get("o") or station.get("b"),
        address=station.get("a"),
        latitude=station["la"],
        longitude=station["lo"],
        distance_km=distance_km(lat, lon, station["la"], station["lo"]),
        price=price,
        power_kw=group.get("kw"),
        current=group.get("c"),
        connectors=list(group.get("k", [])),
        points=group.get("n", 0),
    )


def find_offers(
    stations: list[dict],
    lat: float,
    lon: float,
    radius_km: float,
    filters: Filters,
    now: datetime,
) -> list[Offer]:
    """Alle passenden Angebote im Umkreis, sortiert nach Preis und Entfernung."""
    local = now.astimezone(LOCAL_TZ)
    offers: list[Offer] = []
    for station in stations:
        try:
            if distance_km(lat, lon, station["la"], station["lo"]) > radius_km:
                continue
            offer = best_offer(station, lat, lon, filters, local)
        except (KeyError, TypeError, ValueError):
            continue
        if offer is not None:
            offers.append(offer)
    offers.sort(key=lambda o: (round(o.price.kwh, 4), o.distance_km))
    return offers


def count_stations(
    stations: list[dict], lat: float, lon: float, radius_km: float, filters: Filters
) -> int:
    """Passende Stationen im Umkreis, mit oder ohne gemeldeten Preis."""
    total = 0
    for station in stations:
        try:
            if distance_km(lat, lon, station["la"], station["lo"]) > radius_km:
                continue
        except (KeyError, TypeError):
            continue
        if _operator_matches(station, filters.operators) and any(
            _group_matches(g, filters) for g in station.get("g", [])
        ):
            total += 1
    return total


def stations_in_radius(
    stations: list[dict], lat: float, lon: float, radius_km: float
) -> list[dict]:
    """Stationen innerhalb des Suchkreises."""
    nearby: list[dict] = []
    for station in stations:
        try:
            if distance_km(lat, lon, station["la"], station["lo"]) <= radius_km:
                nearby.append(station)
        except (KeyError, TypeError):
            continue
    return nearby


# --------------------------------------------------------------------------
# Vorschau: Preiswechsel in den nächsten 24 Stunden
# --------------------------------------------------------------------------


def _has_time(validity: dict | None) -> bool:
    return bool(
        validity
        and (validity.get("in") or validity.get("ex") or validity.get("s") or validity.get("e"))
    )


def is_time_dependent(station: dict) -> bool:
    """True, wenn sich der Preis einer Station mit der Zeit ändern kann."""
    for rate in station.get("r") or []:
        if _has_time(rate.get("w")):
            return True
        if any(_has_time(c.get("w")) for c in rate.get("p", [])):
            return True
    return False


def future_steps(now: datetime, horizon: timedelta = HORIZON) -> list[datetime]:
    """Viertelstunden-Zeitpunkte ab der nächsten vollen Viertelstunde (UTC).

    Gerechnet wird in UTC, damit die Zeitumstellung keine Schritte verschluckt
    oder doppelt; ausgewertet wird jeweils in Ortszeit.
    """
    base = now.astimezone(UTC).replace(second=0, microsecond=0)
    step_min = int(STEP.total_seconds() // 60)
    first = base.replace(minute=0) + timedelta(minutes=(base.minute // step_min + 1) * step_min)
    return [first + i * STEP for i in range(int(horizon / STEP))]


def _price_at(
    station: dict, lat: float, lon: float, filters: Filters, when: datetime
) -> Offer | None:
    return best_offer(station, lat, lon, filters, when.astimezone(LOCAL_TZ))


def next_change(
    station: dict,
    lat: float,
    lon: float,
    filters: Filters,
    current_kwh: float,
    steps: list[datetime],
) -> tuple[datetime | None, float | None]:
    """Erster Zeitpunkt, an dem sich der Preis ändert, und der neue Preis.

    Ein neuer Preis None heißt: Ab dann gibt es dort keinen Ad-hoc-Preis mehr.
    """
    for when in steps:
        offer = _price_at(station, lat, lon, filters, when)
        price = offer.price.kwh if offer else None
        if price is None or round(price, 4) != round(current_kwh, 4):
            return when, price
    return None, None


def annotate_changes(
    offers: list[Offer],
    stations_by_id: dict[str, dict],
    lat: float,
    lon: float,
    filters: Filters,
    steps: list[datetime],
) -> None:
    """Ergänzt bei zeitabhängigen Angeboten, bis wann der Preis gilt."""
    for offer in offers:
        station = stations_by_id.get(offer.station_id)
        if station is None or not is_time_dependent(station):
            continue
        offer.until, offer.next_price = next_change(
            station, lat, lon, filters, offer.price.kwh, steps
        )


@dataclass
class Outlook:
    """Günstigstes Angebot in den nächsten 24 Stunden."""

    offer: Offer
    start: datetime
    end: datetime | None


def cheapest_ahead(
    nearby: list[dict],
    current: Offer | None,
    lat: float,
    lon: float,
    filters: Filters,
    now: datetime,
    steps: list[datetime] | None = None,
) -> Outlook | None:
    """Sucht das günstigste Angebot bis 24 Stunden im Voraus.

    Stationen mit festem Preis können später nicht günstiger werden als jetzt;
    durchgerechnet werden deshalb nur die zeitabhängigen.
    """
    steps = steps if steps is not None else future_steps(now)
    best, start = current, now
    dependent = [s for s in nearby if is_time_dependent(s)]
    for when in steps:
        for station in dependent:
            try:
                offer = _price_at(station, lat, lon, filters, when)
            except (KeyError, TypeError, ValueError):
                continue
            if offer is not None and (best is None or offer.price.kwh < best.price.kwh - 1e-9):
                best, start = offer, when
    if best is None:
        return None
    station = next((s for s in nearby if s.get("id") == best.station_id), None)
    end = None
    if station is not None and is_time_dependent(station):
        end, _ = next_change(
            station, lat, lon, filters, best.price.kwh, [t for t in steps if t > start]
        )
    return Outlook(best, start, end)
