"""Tests für die Preislogik (Zeitfenster, Filter, Sortierung)."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from custom_components.ladekoenig.pricing import (
    LOCAL_TZ,
    Filters,
    current_price,
    find_offers,
    period_matches,
    tiles_for_radius,
    validity_matches,
)
from ladekoenig_collector.build import tile_key
from ladekoenig_collector.datex import parse_bytes

FIXTURE = Path(__file__).parent / "fixtures" / "afir_static_sample.json"
HOME = (50.1101, 8.6801)


@pytest.fixture(scope="module")
def stations() -> list[dict]:
    return [record for record, _ in parse_bytes(FIXTURE.read_bytes(), "test")]


def _local(hour: int, minute: int = 0, day: int = 27, month: int = 10) -> datetime:
    return datetime(2026, month, day, hour, minute, tzinfo=LOCAL_TZ)


def _station(stations: list[dict], suffix: str) -> dict:
    return next(s for s in stations if s["id"].endswith(suffix))


@pytest.mark.parametrize(
    ("hour", "expected"),
    [(12, 0.59), (6, 0.39), (7, 0.59), (22, 0.59), (23, 0.39), (2, 0.39)],
)
def test_tesla_time_windows(stations: list[dict], hour: int, expected: float) -> None:
    rate = _station(stations, "ST-SC-1")["r"][0]
    assert current_price(rate, _local(hour)).kwh == expected


def test_windows_use_local_time_not_utc(stations: list[dict]) -> None:
    rate = _station(stations, "ST-SC-1")["r"][0]
    # 22:30 UTC im Oktober (Winterzeit ab 25.10.) = 23:30 Ortszeit -> Nachtpreis
    utc = datetime(2026, 10, 27, 22, 30, tzinfo=UTC)
    assert current_price(rate, utc.astimezone(LOCAL_TZ)).kwh == 0.39
    # Im Sommer (MESZ) ist 21:30 UTC ebenfalls 23:30 Ortszeit
    summer = datetime(2026, 7, 1, 21, 30, tzinfo=UTC)
    assert current_price(rate, summer.astimezone(LOCAL_TZ)).kwh == 0.39


def test_minute_fee_is_reported(stations: list[dict]) -> None:
    price = current_price(_station(stations, "ST-HPC-1")["r"][0], _local(12))
    assert price.kwh == 0.79
    assert price.minute_fee == pytest.approx(0.24, abs=0.001)
    assert price.minute_fee_from == 30


def test_exception_periods_and_days() -> None:
    weekend_only = {"in": [{"d": [5, 6]}]}
    assert validity_matches(weekend_only, _local(12, day=31))  # Samstag
    assert not validity_matches(weekend_only, _local(12, day=27))  # Dienstag
    not_at_night = {"ex": [{"t": [["20:00", "08:00"]]}]}
    assert validity_matches(not_at_night, _local(12))
    assert not validity_matches(not_at_night, _local(21))
    assert not period_matches({"sp": True}, _local(12))
    expired = {"s": "2025-01-01T00:00:00+01:00", "e": "2025-12-31T23:59:59+01:00"}
    assert not validity_matches(expired, _local(12))


def test_foreign_currency_is_skipped() -> None:
    rate = {"cur": "DKK", "p": [{"t": "kwh", "v": 3.5}]}
    assert current_price(rate, _local(12)) is None


def test_free_charging_counts_as_zero() -> None:
    rate = {"p": [{"t": "free", "v": 0.0}]}
    assert current_price(rate, _local(12)).kwh == 0.0


def test_offers_sorted_by_price_within_radius(stations: list[dict]) -> None:
    now = _local(12).astimezone(UTC)
    offers = find_offers(stations, *HOME, 10, Filters(), now)
    assert [o.operator for o in offers] == ["Stadtwerke Musterstadt", "Tesla", "EnBW mobility+"]
    assert offers[0].price.kwh == 0.49
    assert offers[0].distance_km == pytest.approx(0.65, abs=0.05)


def test_night_changes_the_winner(stations: list[dict]) -> None:
    now = _local(2).astimezone(UTC)
    offers = find_offers(stations, *HOME, 10, Filters(), now)
    assert offers[0].operator == "Tesla"
    assert offers[0].price.kwh == 0.39


@pytest.mark.parametrize(
    ("filters", "operators"),
    [
        (Filters(min_power_kw=50), ["Tesla", "EnBW mobility+"]),
        (Filters(current="ac"), ["Stadtwerke Musterstadt"]),
        (Filters(connector="type2"), ["Stadtwerke Musterstadt"]),
        (Filters(operators=("enbw",)), ["EnBW mobility+"]),
        (Filters(operators=("supercharger",)), ["Tesla"]),
    ],
)
def test_filters(stations: list[dict], filters: Filters, operators: list[str]) -> None:
    now = _local(12).astimezone(UTC)
    assert [o.operator for o in find_offers(stations, *HOME, 10, filters, now)] == operators


def test_large_radius_includes_far_station(stations: list[dict]) -> None:
    now = _local(12).astimezone(UTC)
    offers = find_offers(stations, *HOME, 150, Filters(), now)
    assert offers[0].operator == "Weit Weg AG"


def test_tiles_for_radius_cover_station_tiles() -> None:
    keys = tiles_for_radius(*HOME, 10, 0.25)
    for lat, lon in [(50.1101, 8.6801), (50.1301, 8.7001), (50.19, 8.80), (50.03, 8.56)]:
        assert tile_key(lat, lon) in keys
