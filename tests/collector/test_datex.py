"""Tests für den DATEX-II-Parser und den Kachelbau des Sammlers."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ladekrone_collector.build import main, merge_sources, tile_key
from ladekrone_collector.datex import UnsupportedFormatError, parse_bytes

FIXTURE = Path(__file__).parent.parent / "fixtures" / "afir_static_sample.json"
SOURCES = Path(__file__).parents[2] / "collector" / "sources.json"


@pytest.fixture
def stations() -> dict[str, dict]:
    records = parse_bytes(FIXTURE.read_bytes(), "test")
    return {record["id"]: record for record, _ in records}


def test_parses_all_stations_with_coordinates(stations: dict[str, dict]) -> None:
    assert set(stations) == {
        "test:ST-SC-1",
        "test:ST-HPC-1",
        "test:ST-AC-1",
        "test:ST-NP-1",
        "test:ST-FAR-1",
    }


def test_time_windows_are_kept_as_local_clock(stations: dict[str, dict]) -> None:
    station = stations["test:ST-SC-1"]
    assert station["o"] == "Tesla"
    assert station["b"] == "Supercharger"
    assert station["a"] == "Ladeweg 1, 60311 Musterstadt"
    assert station["g"] == [{"n": 2, "c": "dc", "kw": 250.0, "k": ["ccs"], "r": [0]}]
    windows = [c["w"]["in"][0]["t"][0] for c in station["r"][0]["p"]]
    assert windows == [["07:00", "23:00"], ["23:00", "07:00"]]


def test_net_prices_become_gross_and_contracts_are_ignored(stations: dict[str, dict]) -> None:
    station = stations["test:ST-HPC-1"]
    assert len(station["r"]) == 1
    kwh, minute = station["r"][0]["p"]
    assert kwh == {"t": "kwh", "v": 0.79}
    assert minute["t"] == "min"
    assert minute["f"] == 30
    assert minute["v"] == pytest.approx(0.24, abs=0.001)


def test_rate_references_resolve_and_points_are_grouped(stations: dict[str, dict]) -> None:
    station = stations["test:ST-AC-1"]
    assert station["g"] == [{"n": 2, "c": "ac", "kw": 22.0, "k": ["type2"], "r": [0]}]
    assert station["r"][0]["p"] == [{"t": "kwh", "v": 0.49}]


def test_station_without_price_is_kept(stations: dict[str, dict]) -> None:
    station = stations["test:ST-NP-1"]
    assert "r" not in station
    assert station["g"][0] == {"n": 1, "c": "ac", "kw": 11.0, "k": ["type2"]}


def test_xml_is_rejected_clearly() -> None:
    with pytest.raises(UnsupportedFormatError):
        parse_bytes(b"<?xml version='1.0'?><d2:payload/>", "test")


def test_duplicates_across_sources_are_dropped() -> None:
    first = parse_bytes(FIXTURE.read_bytes(), "own")
    second = parse_bytes(FIXTURE.read_bytes(), "aggregator")
    stations, stats = merge_sources([({"key": "own"}, first), ({"key": "aggregator"}, second)])
    assert stats["own"]["stations"] == 5
    # Stationen mit EVSE-IDs werden erkannt, die ohne IDs bleiben (andere Quelle, andere ID)
    assert stats["aggregator"]["duplicates"] == 2
    assert len(stations) == 8


def test_build_writes_tiles_and_meta(tmp_path: Path) -> None:
    exit_code = main(
        [
            "--sources",
            str(SOURCES),
            "--out",
            str(tmp_path),
            "--input",
            f"tesla={FIXTURE}",
            "--generated",
            "2026-10-27T03:00:00+00:00",
        ]
    )
    assert exit_code == 0
    meta = json.loads((tmp_path / "v1" / "meta.json").read_text())
    assert meta["stations"] == 5
    assert meta["sources"][0]["key"] == "tesla"
    assert meta["sources"][0]["with_price"] == 4
    key = tile_key(50.1101, 8.6801)
    assert key in meta["tiles"]
    tile = json.loads((tmp_path / "v1" / "tiles" / f"{key}.json").read_text())
    assert {s["id"] for s in tile["stations"]} >= {"tesla:ST-SC-1", "tesla:ST-AC-1"}
    assert (tmp_path / "index.html").exists()


def test_build_fails_without_any_source(tmp_path: Path) -> None:
    assert main(["--sources", str(SOURCES), "--out", str(tmp_path)]) == 1


def test_diagnose_counts_rate_policies() -> None:
    from ladekrone_collector.datex import diagnose, load_document

    result = diagnose(load_document(FIXTURE.read_bytes()))
    assert result["rates"] > 0
    assert result["rate_policies"].get("adHoc", 0) > 0
    assert result["price_types"]
    assert isinstance(result["rate_sample"], dict)
    assert result["points"] > 0


def test_legal_name_wins_over_operator_id() -> None:
    from ladekrone_collector.datex import _organisation_name

    org = {
        "afacAnOrganisation": {
            "name": {"values": [{"lang": "de", "value": "DE*EWE"}]},
            "legalName": {"values": [{"lang": "de", "value": "EWE Go GmbH"}]},
        }
    }
    assert _organisation_name(org) == "EWE Go GmbH"
    org["afacAnOrganisation"]["name"]["values"][0]["value"] = "EWE Go"
    assert _organisation_name(org) == "EWE Go"


def _status_doc(point_id: str, rate_ref: str, price: float) -> dict:
    return {
        "messageContainer": {
            "payload": [
                {
                    "aegiEnergyInfrastructureStatusPublication": {
                        "energyInfrastructureSiteStatus": [
                            {
                                "energyInfrastructureStationStatus": [
                                    {
                                        "refillPointStatus": [
                                            {
                                                "aegiElectricChargingPointStatus": {
                                                    "reference": {"idG": point_id},
                                                    "energyRateUpdate": [
                                                        {
                                                            "lastUpdated": "2026-10-30T10:00:00Z",
                                                            "energyRateReference": {
                                                                "targetClass": "EnergyRate",
                                                                "idG": rate_ref,
                                                            },
                                                            "energyPrice": [
                                                                {
                                                                    "priceType": {
                                                                        "value": "pricePerKWh"
                                                                    },
                                                                    "value": price,
                                                                    "taxIncluded": True,
                                                                }
                                                            ],
                                                        }
                                                    ],
                                                }
                                            }
                                        ]
                                    }
                                ]
                            }
                        ]
                    }
                }
            ]
        }
    }


def _first_point_id(document: dict) -> str:
    from ladekrone_collector.datex import _iter_dicts

    for item in _iter_dicts(document):
        point = item.get("aegiElectricChargingPoint")
        if isinstance(point, dict) and point.get("idG"):
            return point["idG"]
    raise AssertionError("kein Ladepunkt mit idG im Beispiel")


def test_dynamic_update_without_static_rate_becomes_adhoc() -> None:
    import copy

    from ladekrone_collector.datex import _iter_dicts, load_document, parse_document, parse_status

    document = load_document(FIXTURE.read_bytes())
    stripped = copy.deepcopy(document)
    for item in _iter_dicts(stripped):
        item.pop("electricEnergy", None)
    point_id = _first_point_id(stripped)
    assert all(not rec.get("r") for rec, _ in parse_document(stripped, "x"))

    updates = parse_status(_status_doc(point_id, "unbekannt", 0.55))
    records = [rec for rec, _ in parse_document(stripped, "x", updates)]
    priced = [rec for rec in records if rec.get("r")]
    assert len(priced) == 1
    assert priced[0]["r"][0]["p"][0]["v"] == 0.55


def test_dynamic_update_replaces_known_rate() -> None:
    from ladekrone_collector.datex import _iter_dicts, load_document, parse_document, parse_status

    document = load_document(FIXTURE.read_bytes())
    rate = next(
        item
        for item in _iter_dicts(document)
        if "ratePolicy" in item and isinstance(item.get("idG"), str)
    )
    updates = parse_status(_status_doc("egal", rate["idG"], 0.11))
    records = [rec for rec, _ in parse_document(document, "x", updates)]
    values = {c["v"] for rec in records for r in rec.get("r", []) for c in r["p"]}
    assert 0.11 in values
