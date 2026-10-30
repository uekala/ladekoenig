"""Baut aus den Mobilithek-Angeboten die Kacheldateien für GitHub Pages."""

from __future__ import annotations

import argparse
import html
import json
import logging
import math
import os
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .datex import (
    RateUpdates,
    UnsupportedFormatError,
    diagnose,
    diagnose_status,
    load_document,
    parse_document,
    parse_status,
    rate_ids,
)
from .mobilithek import FetchError, build_ssl_context, fetch_subscription
from .operators import build_operator_names, resolve_operator_ids

TILE_DEG = 0.25
FORMAT_VERSION = 1
OPEN_LICENSES = {"CC0-1.0", "CC-BY-4.0", "DL-DE-ZERO-2.0", "DL-DE-BY-2.0"}
DYNAMIC_ROUNDS = 300

_LOGGER = logging.getLogger("ladekoenig")


def tile_key(lat: float, lon: float) -> str:
    return f"{math.floor(lat / TILE_DEG)}_{math.floor(lon / TILE_DEG)}"


def load_sources(path: Path) -> list[dict]:
    data = json.loads(path.read_text(encoding="utf-8"))
    return [s for s in data["sources"] if s.get("enabled", True)]


def merge_sources(
    parsed: list[tuple[dict, list[tuple[dict, set[str]]]]],
) -> tuple[list[dict], dict[str, dict[str, int]]]:
    """Führt Quellen zusammen. Frühere Quellen haben Vorrang; eine Station wird
    übersprungen, wenn einer ihrer Ladepunkte (EVSE-ID) schon vorkam."""
    seen_evse: set[str] = set()
    seen_ids: set[str] = set()
    stations: list[dict] = []
    stats: dict[str, dict[str, int]] = {}
    for source, records in parsed:
        count = {"stations": 0, "points": 0, "duplicates": 0, "with_price": 0}
        for record, evse_ids in records:
            if record["id"] in seen_ids or (evse_ids and evse_ids & seen_evse):
                count["duplicates"] += 1
                continue
            seen_ids.add(record["id"])
            seen_evse |= evse_ids
            stations.append(record)
            count["stations"] += 1
            count["points"] += sum(g["n"] for g in record["g"])
            if record.get("r"):
                count["with_price"] += 1
        stats[source["key"]] = count
    return stations, stats


def write_output(
    out_dir: Path,
    stations: list[dict],
    sources: list[dict],
    stats: dict[str, dict[str, Any]],
    generated: str,
    diagnostics: dict[str, dict[str, Any]] | None = None,
    extra_diagnostics: dict[str, Any] | None = None,
) -> dict:
    tiles: dict[str, list[dict]] = {}
    for record in stations:
        tiles.setdefault(tile_key(record["la"], record["lo"]), []).append(record)

    base = out_dir / f"v{FORMAT_VERSION}"
    tile_dir = base / "tiles"
    tile_dir.mkdir(parents=True, exist_ok=True)
    for key, records in tiles.items():
        payload = {"v": FORMAT_VERSION, "tile": key, "generated": generated, "stations": records}
        (tile_dir / f"{key}.json").write_text(
            json.dumps(payload, ensure_ascii=False, separators=(",", ":")), encoding="utf-8"
        )

    meta = {
        "v": FORMAT_VERSION,
        "generated": generated,
        "tile_deg": TILE_DEG,
        "tiles": sorted(tiles),
        "stations": len(stations),
        "points": sum(g["n"] for s in stations for g in s["g"]),
        "sources": [
            {
                "key": s["key"],
                "name": s["name"],
                "license": s["license"],
                "attribution": s.get("attribution", s["name"]),
                "offer": s.get("offer"),
                **stats.get(s["key"], {}),
            }
            for s in sources
            if s["key"] in stats
        ],
    }
    (base / "meta.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    diagnostics = diagnostics or {}
    (base / "diag.json").write_text(
        json.dumps(
            {"generated": generated, "sources": diagnostics, **(extra_diagnostics or {})},
            ensure_ascii=False,
            indent=1,
        ),
        encoding="utf-8",
    )
    (out_dir / ".nojekyll").write_text("")
    (out_dir / "index.html").write_text(_index_html(meta, diagnostics), encoding="utf-8")
    return meta


def _index_html(meta: dict, diagnostics: dict[str, dict[str, Any]] | None = None) -> str:
    missing = [
        f"<li>{html.escape(d.get('name', key))}: {html.escape(d['error'])}</li>"
        for key, d in (diagnostics or {}).items()
        if d.get("error")
    ]
    missing_html = (
        "<h2>Abonniert, aber ohne Daten</h2><ul>" + "".join(missing) + "</ul>" if missing else ""
    )
    rows = "\n".join(
        f"<tr><td>{s['attribution']}</td><td>{s['license']}</td>"
        f"<td>{s.get('stations', 0)}</td><td>{s.get('with_price', 0)}</td></tr>"
        for s in meta["sources"]
    )
    return f"""<!doctype html>
<html lang="de"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Ladekönig – Daten</title>
<style>
body{{font-family:system-ui,sans-serif;max-width:52rem;margin:2rem auto;
padding:0 1rem;line-height:1.5}}
table{{border-collapse:collapse;width:100%}}
td,th{{border-bottom:1px solid #ccc;padding:.3rem;text-align:left}}
</style>
</head><body>
<h1>Ladekönig – Ad-hoc-Ladepreise</h1>
<p>Datenbasis für die Home-Assistant-Integration
<a href="https://github.com/uekala/ladekoenig">Ladekönig</a>.
Quelle sind die AFIR-Datenangebote der Betreiber im nationalen Zugangspunkt
<a href="https://mobilithek.info/">Mobilithek</a>. Die Daten wurden normalisiert
(Nettopreise in Bruttopreise umgerechnet, Ladepunkte gruppiert, nach Kacheln aufgeteilt).
Angaben ohne Gewähr.</p>
<p>Stand: {meta["generated"]} · {meta["stations"]} Stationen · {meta["points"]} Ladepunkte</p>
<table><thead><tr><th>Quelle</th><th>Lizenz</th><th>Stationen</th><th>mit Preis</th></tr></thead>
<tbody>
{rows}
</tbody></table>
{missing_html}
<p><a href="v1/diag.json">Diagnose je Quelle</a> (Tarifarten, Preistypen, Beispieldaten)</p>
</body></html>
"""


def subscription_id(
    source: dict, subscriptions: dict[str, str], dynamic: bool = False
) -> str | None:
    """Abo-ID aus dem Secret (Vorrang) oder aus sources.json.

    Abo-IDs funktionieren nur mit dem Zertifikat der Organisation und dürfen
    daher öffentlich in sources.json stehen; Forks überschreiben sie per Secret.
    """
    key = f"{source['key']}_dyn" if dynamic else source["key"]
    if subscriptions.get(key):
        return str(subscriptions[key])
    holder = source.get("dynamic") if dynamic else source
    value = holder.get("subscription") if isinstance(holder, dict) else None
    return str(value) if value else None


def _fetch_updates(
    source: dict,
    subscriptions: dict[str, str],
    context: Any,
    cache: Path,
    local_inputs: dict[str, str],
) -> tuple[RateUpdates | None, Any]:
    """Holt die dynamischen Preise eines Betreibers, falls abonniert."""
    key = f"{source['key']}_dyn"
    if key in local_inputs:
        raw = Path(local_inputs[key]).read_bytes()
    else:
        sub = subscription_id(source, subscriptions, dynamic=True)
        if not sub or context is None:
            return None, None
        raw = fetch_subscription(key, sub, context, cache, max_rounds=DYNAMIC_ROUNDS).body
    document = load_document(raw)
    return parse_status(document), document


def run(args: argparse.Namespace) -> int:
    sources = load_sources(Path(args.sources))
    subscriptions: dict[str, str] = {}
    if args.subscriptions_env and os.environ.get(args.subscriptions_env):
        subscriptions = json.loads(os.environ[args.subscriptions_env])
    local_inputs = dict(item.split("=", 1) for item in args.input or [])

    context = None
    if args.cert and args.key:
        context = build_ssl_context(args.cert, args.key)

    parsed: list[tuple[dict, list[tuple[dict, set[str]]]]] = []
    used_sources: list[dict] = []
    diagnostics: dict[str, dict[str, Any]] = {}
    failures = 0
    for source in sources:
        key = source["key"]
        if source.get("license") not in OPEN_LICENSES:
            _LOGGER.warning("%s: Lizenz %s nicht offen, übersprungen", key, source.get("license"))
            continue
        sub = subscription_id(source, subscriptions)
        try:
            if key in local_inputs:
                raw = Path(local_inputs[key]).read_bytes()
            elif sub and context is not None:
                raw = fetch_subscription(key, sub, context, Path(args.cache)).body
            else:
                continue
            document = load_document(raw)
        except (FetchError, UnsupportedFormatError, OSError) as err:
            failures += 1
            diagnostics[key] = {"name": source["name"], "error": str(err)}
            _LOGGER.error("%s: %s", key, err)
            continue

        dynamic: dict[str, Any] | None = None
        updates: RateUpdates | None = None
        try:
            updates, status_doc = _fetch_updates(
                source, subscriptions, context, Path(args.cache), local_inputs
            )
            if updates is not None:
                dynamic = diagnose_status(status_doc, updates, rate_ids(document))
        except (FetchError, UnsupportedFormatError, OSError) as err:
            dynamic = {"error": str(err)}
            _LOGGER.warning("%s: dynamische Preise nicht verfügbar: %s", key, err)

        records = parse_document(document, key, updates)
        diagnostics[key] = {"name": source["name"], "stations": len(records), **diagnose(document)}
        if dynamic is not None:
            diagnostics[key]["dynamic"] = dynamic
        _LOGGER.info("%s: %d Stationen gelesen", key, len(records))
        parsed.append((source, records))
        used_sources.append(source)

    if not parsed:
        _LOGGER.error("Keine Quelle lieferte Daten – Ausgabe wird nicht geschrieben")
        return 1

    all_records = [item for _source, records in parsed for item in records]
    operator_ids = resolve_operator_ids(all_records, build_operator_names(all_records))
    stations, stats = merge_sources(parsed)
    generated = args.generated or datetime.now(UTC).replace(microsecond=0).isoformat()
    meta = write_output(
        Path(args.out),
        stations,
        used_sources,
        stats,
        generated,
        diagnostics,
        {"operator_ids": operator_ids},
    )
    _LOGGER.info(
        "Fertig: %d Stationen, %d Ladepunkte, %d Kacheln, %d Quellen mit Fehler",
        meta["stations"],
        meta["points"],
        len(meta["tiles"]),
        failures,
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Ladekönig-Datensammler")
    parser.add_argument("--sources", required=True, help="Pfad zu sources.json")
    parser.add_argument("--out", required=True, help="Ausgabeverzeichnis (GitHub Pages)")
    parser.add_argument("--cache", default=".cache/mobilithek", help="Cache für Rohdaten")
    parser.add_argument("--cert", help="Client-Zertifikat (PEM)")
    parser.add_argument("--key", help="Privater Schlüssel (PEM)")
    parser.add_argument(
        "--subscriptions-env",
        default="MOBILITHEK_SUBSCRIPTIONS",
        help="Umgebungsvariable mit JSON {quelle: abo-id}",
    )
    parser.add_argument(
        "--input", action="append", help="Lokale Datei statt Abruf: quelle=pfad (mehrfach)"
    )
    parser.add_argument("--generated", help="Zeitstempel überschreiben (Tests)")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    return run(args)


if __name__ == "__main__":
    sys.exit(main())
