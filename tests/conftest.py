"""Gemeinsame Test-Fixtures."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ladekrone_collector.build import merge_sources, tile_key
from ladekrone_collector.datex import parse_bytes

pytest_plugins = ["pytest_homeassistant_custom_component"]

FIXTURE = Path(__file__).parent / "fixtures" / "afir_static_sample.json"
DATA_URL = "https://example.invalid/ladekrone/v1"


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    """Benutzerdefinierte Integrationen in allen Tests erlauben."""
    return


@pytest.fixture
def published_data() -> tuple[dict, dict[str, dict]]:
    """Meta- und Kacheldaten so, wie der Sammler sie veröffentlicht."""
    records = parse_bytes(FIXTURE.read_bytes(), "tesla")
    stations, stats = merge_sources([({"key": "tesla"}, records)])
    tiles: dict[str, dict] = {}
    for station in stations:
        key = tile_key(station["la"], station["lo"])
        tiles.setdefault(key, {"v": 1, "tile": key, "stations": []})["stations"].append(station)
    meta = {
        "v": 1,
        "generated": "2026-10-27T03:00:00+00:00",
        "tile_deg": 0.25,
        "tiles": sorted(tiles),
        "stations": len(stations),
        "sources": [
            {
                "key": "tesla",
                "name": "Tesla Supercharger",
                "license": "CC0-1.0",
                "attribution": "Tesla Germany GmbH",
                **stats["tesla"],
            }
        ],
    }
    return json.loads(json.dumps(meta)), tiles
