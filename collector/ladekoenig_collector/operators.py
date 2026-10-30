"""Betreibernamen statt Betreiber-IDs.

Manche Quellen tragen als Betreiber nur die EVSE-Operator-ID ein (z. B.
``DE*ISE``). Die gleiche ID steht am Anfang jeder EVSE-ID der Ladepunkte
(``DE*ISE*E123…``). Andere Quellen nennen zu denselben IDs meist den
Klarnamen. Daraus wird eine Zuordnung ID → Name gebildet und angewendet.
"""

from __future__ import annotations

import re
from collections import Counter

OPERATOR_ID_RE = re.compile(r"^\s*([A-Z]{2})\s*\*?\s*([A-Z0-9]{3})\s*$")
MIN_VOTES = 3
MIN_SHARE = 0.6


def operator_id(name: str | None) -> str | None:
    """'DE*ISE' oder 'DEISE' -> 'DEISE'; sonst None."""
    if not name:
        return None
    match = OPERATOR_ID_RE.match(name.upper())
    return match.group(1) + match.group(2) if match else None


def _prefix(evse_ids: set[str]) -> str | None:
    """Häufigstes Betreiberkürzel der EVSE-IDs einer Station (Land + 3 Zeichen)."""
    prefixes = Counter(e[:5] for e in evse_ids if len(e) >= 5 and e[:2].isalpha())
    return prefixes.most_common(1)[0][0] if prefixes else None


def build_operator_names(
    records: list[tuple[dict, set[str]]],
) -> dict[str, str]:
    """Ermittelt je Betreiber-ID den häufigsten Klarnamen aus allen Quellen."""
    votes: dict[str, Counter[str]] = {}
    for record, evse_ids in records:
        name = record.get("o")
        if not name or operator_id(name):
            continue
        prefix = _prefix(evse_ids)
        if prefix:
            votes.setdefault(prefix, Counter())[name] += 1
    names: dict[str, str] = {}
    for prefix, counter in votes.items():
        name, count = counter.most_common(1)[0]
        if count >= MIN_VOTES and count / sum(counter.values()) >= MIN_SHARE:
            names[prefix] = name
    return names


def resolve_operator_ids(
    records: list[tuple[dict, set[str]]],
    names: dict[str, str],
) -> dict[str, dict[str, int | str]]:
    """Ersetzt Betreiber-IDs durch Klarnamen. Gibt eine Übersicht zurück."""
    summary: dict[str, dict[str, int | str]] = {}
    for record, _evse in records:
        oid = operator_id(record.get("o"))
        if not oid:
            continue
        entry = summary.setdefault(oid, {"stations": 0})
        entry["stations"] = int(entry["stations"]) + 1
        name = names.get(oid)
        if name:
            record["o"] = name
            entry["name"] = name
    return summary
