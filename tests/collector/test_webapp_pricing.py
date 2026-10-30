"""Die Web-App muss genauso rechnen wie die Home-Assistant-Integration."""

from __future__ import annotations

import json
import shutil
import subprocess
from datetime import UTC, datetime
from pathlib import Path

import pytest

from custom_components.ladekrone.pricing import (
    Filters,
    annotate_changes,
    cheapest_ahead,
    count_stations,
    find_offers,
    future_steps,
    stations_in_radius,
)
from ladekrone_collector.datex import parse_bytes

ROOT = Path(__file__).parents[2]
PRICING_JS = ROOT / "collector" / "ladekrone_collector" / "webapp" / "pricing.js"
RUNNER = Path(__file__).parent / "webapp_runner.mjs"
FIXTURE = ROOT / "tests" / "fixtures" / "afir_static_sample.json"
HOME = (50.1101, 8.6801)
TOP = 5

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="Node.js fehlt")


def _station(sid: str, offset: float, groups: list[dict], rates: list[dict], **extra) -> dict:
    return {
        "id": sid,
        "la": HOME[0] + offset,
        "lo": HOME[1] + offset,
        "o": extra.pop("o", f"Netz {sid}"),
        "a": f"Teststraße {sid}, 60311 Frankfurt am Main",
        "g": groups,
        "r": rates,
        **extra,
    }


FULL_DAY_AUTUMN = {"t": [["00:00", "24:00"]], "m": [10, 11]}
FRIDAY_NIGHT = {"d": [4], "t": [["22:00", "02:00"]]}
DC = {"kw": 150, "c": "dc", "k": ["ccs"], "n": 2, "r": [0]}
AC = {"kw": 22, "c": "ac", "k": ["type2"], "n": 4, "r": [0]}


def _synthetic() -> list[dict]:
    return [
        _station(
            "night",
            0.010,
            [DC],
            [
                {
                    "p": [
                        {"t": "kwh", "v": 0.59},
                        {"t": "kwh", "v": 0.38, "w": {"in": [{"t": [["23:00", "07:00"]]}]}},
                    ]
                }
            ],
            o="Tesla",
        ),
        _station(
            "weekend",
            0.012,
            [{**DC, "r": [0, 1]}],
            [
                {"w": {"in": [{"d": [5, 6]}]}, "p": [{"t": "kwh", "v": 0.45}]},
                {"p": [{"t": "kwh", "v": 0.69}]},
            ],
        ),
        _station(
            "except",
            -0.008,
            [AC],
            [
                {
                    "p": [
                        {"t": "kwh", "v": 0.30, "w": {"ex": [{"t": [["20:00", "08:00"]]}]}},
                        {"t": "kwh", "v": 0.55},
                    ]
                }
            ],
        ),
        _station(
            "absolute",
            0.020,
            [DC],
            [
                {
                    "p": [
                        {
                            "t": "kwh",
                            "v": 0.25,
                            "w": {"s": "2026-10-27T18:00:00", "e": "2026-10-28T06:00:00+01:00"},
                        },
                        {"t": "kwh", "v": 0.60},
                    ]
                }
            ],
        ),
        _station(
            "from-date",
            -0.015,
            [DC, {**AC, "r": [1]}],
            [
                {
                    "w": {"in": [{"a": ["2026-10-28T00:00:00", None]}]},
                    "p": [{"t": "kwh", "v": 0.33}],
                },
                {"p": [{"t": "kwh", "v": 0.50}]},
            ],
        ),
        _station(
            "fullday",
            0.030,
            [DC],
            [{"p": [{"t": "kwh", "v": 0.41, "w": {"in": [FULL_DAY_AUTUMN]}}]}],
        ),
        _station("dkk", 0.005, [DC], [{"cur": "DKK", "p": [{"t": "kwh", "v": 0.1}]}]),
        _station("free", 0.040, [AC], [{"p": [{"t": "free", "v": 0}]}]),
        _station(
            "fees",
            -0.020,
            [DC],
            [
                {
                    "u": "2026-10-20T08:00:00Z",
                    "p": [
                        {"t": "kwh", "v": 0.79},
                        {"t": "min", "v": 0.24, "f": 30},
                        {"t": "min", "v": 0.10, "f": 45},
                        {"t": "base", "v": 0.5},
                        {"t": "flat", "v": 0.25},
                    ],
                }
            ],
        ),
        _station(
            "holiday", 0.002, [DC], [{"w": {"in": [{"sp": True}]}, "p": [{"t": "kwh", "v": 0.2}]}]
        ),
        _station(
            "friday-night",
            -0.030,
            [{**DC, "kw": 300}],
            [
                {
                    "p": [
                        {"t": "kwh", "v": 0.65},
                        {"t": "kwh", "v": 0.35, "w": {"in": [FRIDAY_NIGHT]}},
                    ]
                }
            ],
            o="Tesla",
        ),
        _station("broken", 0.001, [{**DC, "r": [5]}], [{"p": [{"t": "kwh", "v": 0.1}]}]),
    ]


def _python(stations: list[dict], case: dict) -> dict:
    lat, lon, radius = case["lat"], case["lon"], case["radius"]
    raw = case["filters"]
    filters = Filters(
        min_power_kw=raw.get("minPowerKw", 0),
        current=raw.get("current", "any"),
        connector=raw.get("connector", "any"),
        operators=tuple(raw.get("operators", ())),
    )
    now = datetime.fromtimestamp(case["now"] / 1000, UTC)
    nearby = stations_in_radius(stations, lat, lon, radius)
    offers = find_offers(nearby, lat, lon, radius, filters, now)
    steps = future_steps(now)
    by_id = {s["id"]: s for s in nearby}
    annotate_changes(offers[:TOP], by_id, lat, lon, filters, steps)
    outlook = cheapest_ahead(nearby, offers[0] if offers else None, lat, lon, filters, now, steps)

    def ms(value: datetime | None) -> int | None:
        return None if value is None else round(value.timestamp() * 1000)

    return {
        "offers": [
            [
                o.station_id,
                o.price.kwh,
                o.distance_km,
                ms(o.until),
                o.next_price,
                o.price.minute_fee,
                o.price.session_fee,
            ]
            for o in offers
        ],
        "outlook": outlook
        and [outlook.offer.station_id, outlook.offer.price.kwh, ms(outlook.start), ms(outlook.end)],
        "count": count_stations(nearby, lat, lon, radius, filters),
    }


MOMENTS = [
    datetime(2026, 10, 27, 11, 0, tzinfo=UTC),  # Dienstag, Mittag
    datetime(2026, 10, 27, 21, 50, tzinfo=UTC),  # kurz vor dem Nachttarif
    datetime(2026, 10, 24, 21, 59, tzinfo=UTC),  # Samstagnacht vor der Zeitumstellung
    datetime(2026, 10, 25, 0, 55, tzinfo=UTC),  # in der doppelten Stunde
    datetime(2026, 3, 28, 22, 0, tzinfo=UTC),  # vor der Sommerzeit
    datetime(2026, 3, 29, 0, 40, tzinfo=UTC),  # mitten in der Umstellung
    datetime(2026, 10, 30, 21, 7, 31, tzinfo=UTC),  # Freitagabend
    datetime(2026, 10, 31, 5, 0, tzinfo=UTC),
]
FILTERS = [
    {},
    {"minPowerKw": 50},
    {"current": "ac"},
    {"connector": "type2"},
    {"connector": "ccs", "minPowerKw": 150},
    {"operators": ["tesla"]},
    {"operators": ["enbw", "stadtwerke"]},
]


def test_webapp_matches_integration(tmp_path: Path) -> None:
    stations = [r for r, _ in parse_bytes(FIXTURE.read_bytes(), "test")] + _synthetic()
    cases = [
        {
            "lat": HOME[0],
            "lon": HOME[1],
            "radius": radius,
            "filters": filters,
            "now": round(moment.timestamp() * 1000),
        }
        for moment in MOMENTS
        for filters in FILTERS
        for radius in (3, 25)
    ]
    source = tmp_path / "input.json"
    source.write_text(json.dumps({"stations": stations, "cases": cases}), encoding="utf-8")
    run = subprocess.run(
        ["node", str(RUNNER), str(PRICING_JS), str(source)],
        capture_output=True,
        text=True,
        check=True,
    )
    js_results = json.loads(run.stdout)
    assert len(js_results) == len(cases)
    checked_changes = 0
    for case, js in zip(cases, js_results, strict=True):
        py = _python(stations, case)
        assert js["count"] == py["count"], case
        assert [o[0] for o in js["offers"]] == [o[0] for o in py["offers"]], case
        for js_offer, py_offer in zip(js["offers"], py["offers"], strict=True):
            assert js_offer[1] == pytest.approx(py_offer[1], abs=1e-12), case
            assert js_offer[2] == pytest.approx(py_offer[2], abs=1e-9), case
            assert js_offer[3:] == py_offer[3:], (case, js_offer, py_offer)
            checked_changes += js_offer[3] is not None
        assert js["outlook"] == py["outlook"], case
    assert checked_changes > 20  # die Zeitfenster wurden wirklich geprüft
