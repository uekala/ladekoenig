"""Ortsverzeichnis für die Suche in der Web-App.

Aus den Adressen der Ladestationen ("Straße 1, 12345 Ort") entsteht je
Postleitzahl ein Eintrag mit Ortsname und Mittelpunkt. So kann die Web-App
Orte und Postleitzahlen finden, ohne einen fremden Suchdienst zu fragen.
"""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from typing import Any

GERMANY = (47.2, 55.1, 5.8, 15.1)
PLACE_RE = re.compile(r"(?:^|,\s*)(\d{5})\s+([^,\d][^,]*)$")
SMALL_WORDS = {"am", "an", "auf", "bei", "der", "die", "dem", "den", "im", "in", "ob", "vor", "zu"}


def clean_city(raw: str) -> str | None:
    """Ortsnamen vereinheitlichen; GROSS geschriebene Namen normal schreiben."""
    city = " ".join(raw.split()).strip(" -,;")
    if len(city) < 2 or not any(ch.isalpha() for ch in city):
        return None
    if city.isupper():
        words = []
        for index, word in enumerate(city.split(" ")):
            lower = word.lower()
            if index and lower in SMALL_WORDS:
                words.append(lower)
            else:
                parts = word.split("-")
                words.append("-".join(part[:1].upper() + part[1:].lower() for part in parts))
        city = " ".join(words)
    return city


def build_places(stations: list[dict[str, Any]]) -> list[list[Any]]:
    """[[PLZ, Ort, Breite, Länge, Stationen], …], sortiert nach Postleitzahl."""
    points: dict[str, list[tuple[float, float]]] = defaultdict(list)
    names: dict[str, Counter[str]] = defaultdict(Counter)
    for station in stations:
        address, lat, lon = station.get("a"), station.get("la"), station.get("lo")
        if not isinstance(address, str) or not isinstance(lat, (int, float)):
            continue
        if not isinstance(lon, (int, float)):
            continue
        if not (GERMANY[0] <= lat <= GERMANY[1] and GERMANY[2] <= lon <= GERMANY[3]):
            continue
        match = PLACE_RE.search(address.strip())
        if not match:
            continue
        city = clean_city(match.group(2))
        if city is None:
            continue
        postcode = match.group(1)
        points[postcode].append((lat, lon))
        names[postcode][city] += 1
    places = []
    for postcode in sorted(points):
        coords = points[postcode]
        lat = sum(c[0] for c in coords) / len(coords)
        lon = sum(c[1] for c in coords) / len(coords)
        city = names[postcode].most_common(1)[0][0]
        places.append([postcode, city, round(lat, 3), round(lon, 3), len(coords)])
    return places
