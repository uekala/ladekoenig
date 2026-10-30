"""Tests für den Build-Schritt des Datensammlers."""

from __future__ import annotations

import json
from argparse import Namespace
from pathlib import Path

from ladekrone_collector.build import run

FIXTURE = Path(__file__).parent.parent / "fixtures" / "afir_static_sample.json"


def _sources(tmp_path: Path) -> Path:
    path = tmp_path / "sources.json"
    path.write_text(
        json.dumps(
            {
                "sources": [
                    {"key": "tesla", "name": "Tesla", "license": "CC0-1.0", "offer": "1"},
                    {"key": "broken", "name": "Kaputt", "license": "CC0-1.0", "offer": "2"},
                ]
            }
        ),
        encoding="utf-8",
    )
    return path


def test_run_writes_diagnostics(tmp_path: Path) -> None:
    broken = tmp_path / "broken.xml"
    broken.write_text("<?xml version='1.0'?><x/>", encoding="utf-8")
    out = tmp_path / "public"
    args = Namespace(
        sources=str(_sources(tmp_path)),
        out=str(out),
        cache=str(tmp_path / "cache"),
        cert=None,
        key=None,
        subscriptions_env=None,
        input=[f"tesla={FIXTURE}", f"broken={broken}"],
        generated="2026-10-30T00:00:00+00:00",
    )
    assert run(args) == 0
    diag = json.loads((out / "v1" / "diag.json").read_text(encoding="utf-8"))
    assert diag["sources"]["tesla"]["stations"] > 0
    assert "XML" in diag["sources"]["broken"]["error"]
    page = (out / "index.html").read_text(encoding="utf-8")
    assert "Noch ohne Preise" in page
    assert "Kaputt" in page
    assert '<svg class="map"' in page
    assert (out / "assets" / "fonts" / "atkinson-400.woff2").exists()
    assert (out / "assets" / "icon.svg").exists()
    assert "app/" in page
    app = out / "app"
    for name in ("index.html", "app.js", "pricing.js", "manifest.webmanifest", "datenschutz.html"):
        assert (app / name).exists(), name
    worker = (app / "sw.js").read_text(encoding="utf-8")
    assert "__VERSION__" not in worker
    manifest = json.loads((app / "manifest.webmanifest").read_text(encoding="utf-8"))
    for icon in manifest["icons"]:
        assert (app / icon["src"]).exists()
    places = json.loads((out / "v1" / "places.json").read_text(encoding="utf-8"))["places"]
    assert places and all(len(row) == 5 and len(row[0]) == 5 for row in places)


def test_dot_map_ignores_tiles_outside_germany() -> None:
    from ladekrone_collector.site import dot_map

    inside = dot_map({"205_26": (10, 5)}, 0.25)
    with_outlier = dot_map({"205_26": (10, 5), "0_0": (1, 0)}, 0.25)
    assert inside == with_outlier
    assert inside.count("<circle") == 1


def test_places_from_addresses() -> None:
    from ladekrone_collector.places import build_places, clean_city

    stations = [
        {"a": "Hauptstr. 1, 47441 Moers", "la": 51.45, "lo": 6.62},
        {"a": "Bahnhofstr. 2, 47441 MOERS", "la": 51.46, "lo": 6.64},
        {"a": "Kirchweg 3, 47441 Moers", "la": 51.44, "lo": 6.63},
        {"a": "Am Markt 1, 60311 FRANKFURT AM MAIN", "la": 50.11, "lo": 8.68},
        {"a": "Irgendwo 5, 12345 Fern", "la": 0.0, "lo": 0.0},  # falsche Koordinaten
        {"a": "Ohne Postleitzahl", "la": 50.0, "lo": 8.0},
        {"la": 50.0, "lo": 8.0},
    ]
    places = build_places(stations)
    assert places == [
        ["47441", "Moers", 51.45, 6.63, 3],
        ["60311", "Frankfurt am Main", 50.11, 8.68, 1],
    ]
    assert clean_city("BAD HOMBURG VOR DER HÖHE") == "Bad Homburg vor der Höhe"
    assert clean_city("Castrop-Rauxel") == "Castrop-Rauxel"
    assert clean_city("  ") is None
