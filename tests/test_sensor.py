"""Tests für Abruf und Sensoren."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from freezegun.api import FrozenDateTimeFactory
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_fire_time_changed
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.ladekrone.const import DOMAIN

from .conftest import DATA_URL

LOCATION = {"latitude": 50.1101, "longitude": 8.6801, "radius": 10000}


def _entry(hass: HomeAssistant, **extra) -> MockConfigEntry:
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Ladekrone",
        data={
            "location": LOCATION,
            "min_power": "0",
            "current_type": "any",
            "connector": "any",
            "operators": "",
            **extra,
        },
        options={"data_url": DATA_URL},
    )
    entry.add_to_hass(hass)
    return entry


def _mock_data(aioclient_mock: AiohttpClientMocker, published_data) -> None:
    meta, tiles = published_data
    aioclient_mock.get(f"{DATA_URL}/meta.json", json=meta)
    for key, tile in tiles.items():
        aioclient_mock.get(f"{DATA_URL}/tiles/{key}.json", json=tile)


async def test_sensors_show_cheapest_offer(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    freezer: FrozenDateTimeFactory,
    published_data,
) -> None:
    freezer.move_to(datetime(2026, 10, 27, 11, 0, tzinfo=UTC))  # 12:00 Ortszeit
    _mock_data(aioclient_mock, published_data)
    entry = _entry(hass)

    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.LOADED

    price = hass.states.get("sensor.ladekrone_cheapest_price")
    assert price is not None
    assert float(price.state) == 0.49
    assert price.attributes["unit_of_measurement"] == "€/kWh"
    assert price.attributes["operator"] == "Stadtwerke Musterstadt"
    assert len(price.attributes["offers"]) == 3
    assert "Tesla Germany GmbH" in price.attributes["attribution"]

    count = hass.states.get("sensor.ladekrone_charging_stations_in_area")
    assert count.state == "4"
    assert count.attributes["with_price"] == 3

    average = hass.states.get("sensor.ladekrone_average_price")
    prices = [offer["price"] for offer in price.attributes["offers"]]
    assert float(average.state) == pytest.approx(sum(prices) / len(prices), abs=1e-3)
    assert average.attributes["stations"] == 3
    assert average.attributes["min_price"] == min(prices)
    assert average.attributes["max_price"] == max(prices)

    stamp = hass.states.get("sensor.ladekrone_data_timestamp")
    assert stamp.state == "2026-10-27T03:00:00+00:00"

    # 23:00 Ortszeit Berlin = 22:00 UTC, Nachttarif bis 07:00 = 06:00 UTC
    night = datetime(2026, 10, 27, 22, 0, tzinfo=UTC)
    morning = datetime(2026, 10, 28, 6, 0, tzinfo=UTC)
    tesla = next(o for o in price.attributes["offers"] if o["operator"] == "Tesla")
    assert dt_util.parse_datetime(tesla["until"]) == night
    assert tesla["next_price"] == 0.39

    ahead = hass.states.get("sensor.ladekrone_cheapest_price_within_24_h")
    assert float(ahead.state) == 0.39
    assert ahead.attributes["operator"] == "Tesla"
    assert dt_util.parse_datetime(ahead.attributes["from"]) == night
    assert dt_util.parse_datetime(ahead.attributes["until"]) == morning
    assert ahead.attributes["now"] is False


async def test_price_follows_time_of_day_without_refetch(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    freezer: FrozenDateTimeFactory,
    published_data,
) -> None:
    freezer.move_to(datetime(2026, 10, 27, 18, 0, tzinfo=UTC))  # 19:00 Ortszeit
    _mock_data(aioclient_mock, published_data)
    entry = _entry(hass, operators="Tesla")
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    assert float(hass.states.get("sensor.ladekrone_cheapest_price").state) == 0.59
    calls = aioclient_mock.call_count

    freezer.move_to(datetime(2026, 10, 27, 22, 10, tzinfo=UTC))  # 23:10 Ortszeit
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(minutes=6))
    await hass.async_block_till_done(wait_background_tasks=True)

    assert float(hass.states.get("sensor.ladekrone_cheapest_price").state) == 0.39
    assert aioclient_mock.call_count == calls  # nur neu bewertet, nicht neu geladen


async def test_unavailable_source_retries_setup(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    aioclient_mock.get(f"{DATA_URL}/meta.json", status=500)
    entry = _entry(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.SETUP_RETRY


async def test_keeps_old_data_when_refetch_fails(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    freezer: FrozenDateTimeFactory,
    published_data,
) -> None:
    freezer.move_to(datetime(2026, 10, 27, 11, 0, tzinfo=UTC))
    _mock_data(aioclient_mock, published_data)
    entry = _entry(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    aioclient_mock.clear_requests()
    aioclient_mock.get(f"{DATA_URL}/meta.json", status=503)
    freezer.tick(timedelta(hours=7))
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(minutes=6))
    await hass.async_block_till_done(wait_background_tasks=True)

    assert aioclient_mock.call_count == 1  # Neuabruf wurde versucht
    state = hass.states.get("sensor.ladekrone_cheapest_price")
    assert state.state != "unavailable"
    assert float(state.state) == 0.49


async def test_unload(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, published_data
) -> None:
    _mock_data(aioclient_mock, published_data)
    entry = _entry(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert await hass.config_entries.async_unload(entry.entry_id)
    assert entry.state is ConfigEntryState.NOT_LOADED


async def test_search_follows_location_entity(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    freezer: FrozenDateTimeFactory,
    published_data,
) -> None:
    freezer.move_to(datetime(2026, 10, 27, 11, 0, tzinfo=UTC))
    _mock_data(aioclient_mock, published_data)
    # Fester Ort weit weg (Berlin), das Auto steht aber in Frankfurt bei den Stationen
    hass.states.async_set(
        "device_tracker.auto", "not_home", {"latitude": 50.1101, "longitude": 8.6801}
    )
    entry = _entry(
        hass,
        location={"latitude": 52.52, "longitude": 13.405, "radius": 10000},
        location_entity="device_tracker.auto",
    )
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    price = hass.states.get("sensor.ladekrone_cheapest_price")
    assert float(price.state) == 0.49
    count = hass.states.get("sensor.ladekrone_charging_stations_in_area")
    assert count.attributes["center_source"] == "device_tracker.auto"

    # Auto fährt nach Berlin: dort gibt es keine Stationen im Beispiel
    hass.states.async_set(
        "device_tracker.auto", "not_home", {"latitude": 52.52, "longitude": 13.405}
    )
    freezer.tick(timedelta(seconds=15))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert hass.states.get("sensor.ladekrone_cheapest_price").state == "unknown"

    # Ohne Position gilt der feste Ort
    hass.states.async_set("device_tracker.auto", "unavailable", {})
    freezer.tick(timedelta(seconds=15))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    count = hass.states.get("sensor.ladekrone_charging_stations_in_area")
    assert count.attributes["center_source"] == "fixed"
