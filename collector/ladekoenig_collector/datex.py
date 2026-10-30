"""Parser für statische AFIR-Publikationen (DATEX II v3, JSON) aus der Mobilithek.

Liest eine ``EnergyInfrastructureTablePublication`` und erzeugt daraus
kompakte Stationsdatensätze mit den Ad-hoc-Tarifen. Zeitfenster werden nicht
hier ausgewertet, sondern unverändert (normalisiert) weitergegeben, damit die
Integration den Preis zum jeweiligen Zeitpunkt bestimmen kann.

Hinweis zu Zeitangaben: Laut AFIR-Profil (Rev2) tragen reine Uhrzeiten aus
JSON-Gründen ein ``Z``, das NICHT als UTC zu lesen ist, sondern als Ortszeit.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Iterator
from typing import Any

PUBLICATION_KEY = "aegiEnergyInfrastructureTablePublication"

DAYS = {
    "monday": 0,
    "tuesday": 1,
    "wednesday": 2,
    "thursday": 3,
    "friday": 4,
    "saturday": 5,
    "sunday": 6,
}
MONTHS = {
    "january": 1,
    "february": 2,
    "march": 3,
    "april": 4,
    "may": 5,
    "june": 6,
    "july": 7,
    "august": 8,
    "september": 9,
    "october": 10,
    "november": 11,
    "december": 12,
}
PRICE_TYPES = {
    "pricePerKWh": "kwh",
    "pricePerMinute": "min",
    "basePrice": "base",
    "flatRate": "flat",
    "free": "free",
}
CONNECTORS = {
    "iec62196T2COMBO": "ccs",
    "iec62196T2": "type2",
    "chademo": "chademo",
    "iec62196T1COMBO": "ccs1",
    "iec62196T1": "type1",
    "mcs": "mcs",
    "domesticF": "schuko",
}
DEFAULT_VAT = 19.0


class UnsupportedFormatError(ValueError):
    """Die Nutzdaten liegen nicht im unterstützten JSON-Format vor."""


# --------------------------------------------------------------------------
# Hilfsfunktionen für die DATEX-II-JSON-Konventionen
# --------------------------------------------------------------------------


def _as_list(value: Any) -> list:
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


def _enum(value: Any) -> str | None:
    """Liest einen DATEX-Aufzählungswert (``{"value": "adHoc"}`` oder ``"adHoc"``)."""
    if isinstance(value, dict):
        raw = value.get("value")
        if raw == "extendedG":
            return value.get("extendedValueG") or raw
        return raw if isinstance(raw, str) else None
    return value if isinstance(value, str) else None


def _text(value: Any) -> str | None:
    """Liest einen mehrsprachigen Text, bevorzugt Deutsch."""
    if value is None:
        return None
    if isinstance(value, str):
        return value.strip() or None
    if isinstance(value, list):
        for item in value:
            text = _text(item)
            if text:
                return text
        return None
    if not isinstance(value, dict):
        return None
    values = value.get("values")
    if not isinstance(values, list):
        raw = value.get("value")
        return raw.strip() if isinstance(raw, str) and raw.strip() else None
    for lang in ("de", "en"):
        for item in values:
            if isinstance(item, dict) and item.get("lang") == lang:
                raw = item.get("value")
                if isinstance(raw, str) and raw.strip():
                    return raw.strip()
    for item in values:
        if isinstance(item, dict):
            raw = item.get("value")
            if isinstance(raw, str) and raw.strip():
                return raw.strip()
    return None


def _num(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value.replace(",", "."))
        except ValueError:
            return None
    if isinstance(value, dict):
        return _num(value.get("value"))
    return None


def _find_first(obj: Any, key: str) -> Any:
    """Sucht rekursiv den ersten Wert zu ``key``."""
    if isinstance(obj, dict):
        if key in obj:
            return obj[key]
        for value in obj.values():
            found = _find_first(value, key)
            if found is not None:
                return found
    elif isinstance(obj, list):
        for item in obj:
            found = _find_first(item, key)
            if found is not None:
                return found
    return None


def _iter_dicts(obj: Any) -> Iterator[dict]:
    if isinstance(obj, dict):
        yield obj
        for value in obj.values():
            yield from _iter_dicts(value)
    elif isinstance(obj, list):
        for item in obj:
            yield from _iter_dicts(item)


def _organisation_name(org: Any) -> str | None:
    """Name aus einer OrganisationG (``afacAnOrganisation`` u. ä.)."""
    if not isinstance(org, dict):
        return None
    for value in org.values():
        if isinstance(value, dict):
            name = _text(value.get("name")) or _text(value.get("legalName"))
            if name:
                return name
    return _text(org.get("name"))


def _external_id(obj: dict, wanted: str) -> str | None:
    for ext in _as_list(obj.get("externalIdentifier")):
        if not isinstance(ext, dict):
            continue
        kind = _enum(ext.get("typeOfIdentifier"))
        if kind == wanted and ext.get("identifier"):
            return str(ext["identifier"]).strip()
    return None


# --------------------------------------------------------------------------
# Ort und Adresse
# --------------------------------------------------------------------------


def _coordinates(location: Any) -> tuple[float, float] | None:
    for key in ("coordinatesForDisplay", "pointCoordinates"):
        coords = _find_first(location, key)
        if isinstance(coords, dict):
            lat, lon = _num(coords.get("latitude")), _num(coords.get("longitude"))
            if lat is not None and lon is not None and (lat or lon):
                return lat, lon
    return None


def _address(location: Any) -> str | None:
    address = _find_first(location, "address")
    if not isinstance(address, dict):
        return None
    lines = sorted(
        (line for line in _as_list(address.get("addressLine")) if isinstance(line, dict)),
        key=lambda line: line.get("order", 0),
    )
    street = " ".join(filter(None, (_text(line.get("text")) for line in lines)))
    city = _text(address.get("city"))
    postcode = address.get("postcode")
    place = " ".join(filter(None, (str(postcode).strip() if postcode else None, city)))
    return ", ".join(filter(None, (street or None, place or None))) or None


# --------------------------------------------------------------------------
# Gültigkeiten und Preise
# --------------------------------------------------------------------------

_TIME_RE = re.compile(r"^(\d{1,2}):(\d{2})")


def _clock(value: Any) -> str | None:
    """'21:00:00Z' -> '21:00' (Ortszeit, das Z wird laut Profil ignoriert)."""
    if not isinstance(value, str):
        return None
    match = _TIME_RE.match(value.strip())
    if not match:
        return None
    hour, minute = int(match.group(1)), int(match.group(2))
    if hour == 24 and minute == 0:
        return "24:00"
    if hour > 23 or minute > 59:
        return None
    return f"{hour:02d}:{minute:02d}"


def _period(period: Any) -> dict | None:
    if not isinstance(period, dict):
        return None
    result: dict[str, Any] = {}
    times = []
    for tod in _as_list(period.get("recurringTimePeriodOfDay")):
        if isinstance(tod, dict):
            start, end = _clock(tod.get("startTimeOfPeriod")), _clock(tod.get("endTimeOfPeriod"))
            if start and end:
                times.append([start, end])
    if times:
        result["t"] = times
    days: set[int] = set()
    months: set[int] = set()
    for dwm in _as_list(period.get("recurringDayWeekMonthPeriod")):
        if not isinstance(dwm, dict):
            continue
        inner = dwm.get("comDayWeekMonth", dwm)
        for day in _as_list(inner.get("applicableDay")):
            name = _enum(day)
            if name in DAYS:
                days.add(DAYS[name])
        for month in _as_list(inner.get("applicableMonth")):
            name = _enum(month)
            if name in MONTHS:
                months.add(MONTHS[name])
    if days:
        result["d"] = sorted(days)
    if months:
        result["m"] = sorted(months)
    start, end = period.get("startOfPeriod"), period.get("endOfPeriod")
    if isinstance(start, str) or isinstance(end, str):
        result["a"] = [
            start if isinstance(start, str) else None,
            end if isinstance(end, str) else None,
        ]
    if period.get("recurringSpecialDay"):
        # Feiertage lassen sich ohne Kalender nicht auswerten; Kennzeichen setzen,
        # damit die Integration solche Perioden nicht fälschlich als "immer" liest.
        result["sp"] = True
    return result


def _validity(overall: Any) -> dict | None:
    if not isinstance(overall, dict):
        return None
    result: dict[str, Any] = {}
    if isinstance(overall.get("overallStartTime"), str):
        result["s"] = overall["overallStartTime"]
    if isinstance(overall.get("overallEndTime"), str):
        result["e"] = overall["overallEndTime"]
    valid = [p for p in map(_period, _as_list(overall.get("validPeriod"))) if p]
    exceptions = [p for p in map(_period, _as_list(overall.get("exceptionPeriod"))) if p]
    if valid:
        result["in"] = valid
    if exceptions:
        result["ex"] = exceptions
    return result or None


def _gross(value: float, price: dict) -> float:
    if price.get("taxIncluded") is False:
        rate = _num(price.get("taxRate"))
        value *= 1 + (rate if rate is not None else DEFAULT_VAT) / 100
    return round(value, 4)


def _component(price: Any) -> dict | None:
    if not isinstance(price, dict):
        return None
    kind = PRICE_TYPES.get(_enum(price.get("priceType")) or "", "other")
    value = _num(price.get("value"))
    if value is None:
        if kind != "free":
            return None
        value = 0.0
    comp: dict[str, Any] = {"t": kind, "v": _gross(value, price)}
    cap = _num(price.get("priceCap"))
    if cap is not None:
        comp["cap"] = _gross(cap, price)
    applicability = price.get("timeBasedApplicability")
    if isinstance(applicability, dict):
        if (frm := _num(applicability.get("fromMinute"))) is not None:
            comp["f"] = int(frm)
        if (to := _num(applicability.get("toMinute"))) is not None:
            comp["to"] = int(to)
    energy = price.get("energyBasedApplicability")
    if isinstance(energy, dict):
        # Staffelpreise nach Energiemenge: Grenzwerte mitgeben, Integration nutzt
        # bei Vergleichen den Einstiegspreis.
        bounds = {k: _num(v) for k, v in energy.items() if _num(v) is not None}
        if bounds:
            comp["e"] = bounds
    validity = _validity(price.get("overallPeriod"))
    if validity:
        comp["w"] = validity
    return comp


def _rate(rate: dict) -> dict | None:
    components = [c for c in map(_component, _as_list(rate.get("energyPrice"))) if c]
    if not components:
        return None
    result: dict[str, Any] = {"p": components}
    currencies = [c for c in _as_list(rate.get("applicableCurrency")) if isinstance(c, str)]
    if currencies and currencies[0] != "EUR":
        result["cur"] = currencies[0]
    validity = _validity(rate.get("overallPeriod"))
    if validity:
        result["w"] = validity
    if isinstance(rate.get("lastUpdated"), str):
        result["u"] = rate["lastUpdated"]
    return result


def _is_adhoc(rate: Any) -> bool:
    return isinstance(rate, dict) and _enum(rate.get("ratePolicy")) == "adHoc"


def _adhoc_rates(holder: dict, rate_index: dict[str, dict]) -> list[dict]:
    """Alle Ad-hoc-Tarife eines Elements (Ladepunkt oder Station)."""
    found: list[dict] = []
    for energy in _as_list(holder.get("electricEnergy")):
        if not isinstance(energy, dict):
            continue
        for rate in _as_list(energy.get("energyRate")):
            if _is_adhoc(rate):
                found.append(rate)
        for ref in _as_list(energy.get("energyRateByReference")):
            ref_id = ref.get("idG") if isinstance(ref, dict) else None
            rate = rate_index.get(ref_id) if ref_id else None
            if _is_adhoc(rate):
                found.append(rate)
    return found


# --------------------------------------------------------------------------
# Ladepunkte und Stationen
# --------------------------------------------------------------------------


def _point_power_kw(point: dict) -> float | None:
    watts = [
        _num(conn.get("maxPowerAtSocket"))
        for conn in _as_list(point.get("connector"))
        if isinstance(conn, dict)
    ]
    watts += [_num(p) for p in _as_list(point.get("availableChargingPower"))]
    watts = [w for w in watts if w]
    if not watts:
        return None
    return round(max(watts) / 1000, 1)


def _point_connectors(point: dict) -> list[str]:
    result: set[str] = set()
    for conn in _as_list(point.get("connector")):
        if not isinstance(conn, dict):
            continue
        raw = _enum(conn.get("connectorType"))
        if raw:
            result.add(CONNECTORS.get(raw, "schuko" if raw.startswith("domestic") else raw))
    return sorted(result)


def _rate_key(rate: dict) -> str:
    return hashlib.sha1(json.dumps(rate, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[
        :12
    ]


def _station(
    station: dict,
    site: dict,
    source: str,
    rate_index: dict[str, dict],
) -> tuple[dict, set[str]] | None:
    coords = _coordinates(station.get("locationReference")) or _coordinates(
        site.get("locationReference")
    )
    if coords is None:
        return None
    lat, lon = coords
    operator = (
        _organisation_name(station.get("operator"))
        or _organisation_name(site.get("operator"))
        or _text(site.get("brand"))
    )
    brand = _text(site.get("brand"))
    address = _address(station.get("locationReference")) or _address(site.get("locationReference"))

    station_rates = [r for r in map(_rate, _adhoc_rates(station, rate_index)) if r]
    rates: list[dict] = []
    rate_pos: dict[str, int] = {}

    def register(rate: dict) -> int:
        key = _rate_key(rate)
        if key not in rate_pos:
            rate_pos[key] = len(rates)
            rates.append(rate)
        return rate_pos[key]

    groups: dict[tuple, dict] = {}
    evse_ids: set[str] = set()
    for refill in _as_list(station.get("refillPoint")):
        if not isinstance(refill, dict):
            continue
        point = refill.get("aegiElectricChargingPoint", refill)
        if not isinstance(point, dict):
            continue
        evse = _external_id(point, "evseId")
        if evse:
            evse_ids.add(evse.replace("*", "").replace(" ", "").upper())
        point_rates = [r for r in map(_rate, _adhoc_rates(point, rate_index)) if r]
        chosen = point_rates or station_rates
        rate_refs = tuple(sorted(register(r) for r in chosen))
        current = _enum(point.get("currentType"))
        power = _point_power_kw(point)
        connectors = tuple(_point_connectors(point))
        key = (current, power, connectors, rate_refs)
        group = groups.get(key)
        if group is None:
            group = {"n": 0}
            if current in ("ac", "dc"):
                group["c"] = current
            if power is not None:
                group["kw"] = power
            if connectors:
                group["k"] = list(connectors)
            if rate_refs:
                group["r"] = list(rate_refs)
            groups[key] = group
        group["n"] += 1

    if not groups:
        return None

    station_id = station.get("idG") or _external_id(station, "stationIdBNetzA")
    if not station_id:
        station_id = f"{lat:.5f},{lon:.5f}"
    record: dict[str, Any] = {
        "id": f"{source}:{station_id}",
        "s": source,
        "la": round(lat, 5),
        "lo": round(lon, 5),
        "g": list(groups.values()),
    }
    if operator:
        record["o"] = operator
    if brand and brand != operator:
        record["b"] = brand
    if address:
        record["a"] = address
    if rates:
        record["r"] = rates
    if isinstance(station.get("lastUpdated"), str):
        record["u"] = station["lastUpdated"]
    return record, evse_ids


def iter_publications(document: Any) -> Iterator[dict]:
    """Findet alle Table-Publikationen, auch in Containern und Listen."""
    for item in _iter_dicts(document):
        publication = item.get(PUBLICATION_KEY)
        if isinstance(publication, dict):
            yield publication


def parse_document(document: Any, source: str) -> list[tuple[dict, set[str]]]:
    """Wandelt ein geparstes JSON-Dokument in Stationsdatensätze um."""
    publications = list(iter_publications(document))
    rate_index = {
        item["idG"]: item
        for publication in publications
        for item in _iter_dicts(publication)
        if "ratePolicy" in item and isinstance(item.get("idG"), str)
    }
    stations: list[tuple[dict, set[str]]] = []
    for publication in publications:
        for table in _as_list(publication.get("energyInfrastructureTable")):
            if not isinstance(table, dict):
                continue
            for site in _as_list(table.get("energyInfrastructureSite")):
                if not isinstance(site, dict):
                    continue
                for station in _as_list(site.get("energyInfrastructureStation")):
                    if isinstance(station, dict):
                        parsed = _station(station, site, source, rate_index)
                        if parsed:
                            stations.append(parsed)
    return stations


def load_document(raw: bytes) -> Any:
    """Liest rohe Nutzdaten als JSON. Unterstützt derzeit DATEX II v3 als JSON."""
    text = raw.decode("utf-8-sig", errors="replace").lstrip()
    if text.startswith("<"):
        raise UnsupportedFormatError(
            "XML-Nutzdaten werden noch nicht unterstützt (nur DATEX II v3 JSON)"
        )
    try:
        return json.loads(text)
    except json.JSONDecodeError as err:
        raise UnsupportedFormatError(f"Kein gültiges JSON: {err}") from err


def parse_bytes(raw: bytes, source: str) -> list[tuple[dict, set[str]]]:
    """Parst rohe Nutzdaten. Unterstützt derzeit DATEX II v3 als JSON."""
    return parse_document(load_document(raw), source)


# --------------------------------------------------------------------------
# Diagnose: wie legt eine Quelle ihre Tarife ab?
# --------------------------------------------------------------------------

SAMPLE_LIMIT = 4000


def _sample(value: Any) -> Any:
    """Kürzt ein Beispielobjekt auf eine lesbare Größe (Listen auf zwei Einträge)."""
    if isinstance(value, dict):
        return {k: _sample(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_sample(v) for v in value[:2]] + (["…"] if len(value) > 2 else [])
    return value


def _compact(value: Any) -> Any:
    text = json.dumps(_sample(value), ensure_ascii=False)
    if len(text) <= SAMPLE_LIMIT:
        return json.loads(text)
    return text[:SAMPLE_LIMIT] + "…"


def diagnose(document: Any) -> dict[str, Any]:
    """Zählt Tarifarten und Preistypen und liefert je ein gekürztes Beispiel.

    Dient dazu, Quellen zu verstehen, bei denen keine Ad-hoc-Preise erkannt
    werden. Enthält nur Strukturangaben und offen lizenzierte Beispieldaten.
    """
    policies: dict[str, int] = {}
    price_types: dict[str, int] = {}
    rate_sample: dict | None = None
    energy_sample: Any = None
    site_sample: Any = None
    rates = points = 0
    for item in _iter_dicts(document):
        if "ratePolicy" in item or "energyPrice" in item:
            rates += 1
            policy = _enum(item.get("ratePolicy")) or "–"
            policies[policy] = policies.get(policy, 0) + 1
            for price in _as_list(item.get("energyPrice")):
                if isinstance(price, dict):
                    kind = _enum(price.get("priceType")) or "–"
                    price_types[kind] = price_types.get(kind, 0) + 1
            if rate_sample is None:
                rate_sample = item
        if site_sample is None and "energyInfrastructureStation" in item:
            site_sample = {k: v for k, v in item.items() if k != "energyInfrastructureStation"}
            stations = _as_list(item.get("energyInfrastructureStation"))
            if stations and isinstance(stations[0], dict):
                site_sample["station"] = {
                    k: v for k, v in stations[0].items() if k != "refillPoint"
                }
        if "aegiElectricChargingPoint" in item:
            points += 1
            if energy_sample is None:
                point = item["aegiElectricChargingPoint"]
                if isinstance(point, dict):
                    skip = ("connector", "externalIdentifier")
                    energy_sample = {k: v for k, v in point.items() if k not in skip}
    return {
        "rates": rates,
        "points": points,
        "rate_policies": policies,
        "price_types": price_types,
        "rate_sample": _compact(rate_sample) if rate_sample is not None else None,
        "point_sample": _compact(energy_sample) if energy_sample is not None else None,
        "site_sample": _compact(site_sample) if site_sample is not None else None,
    }
