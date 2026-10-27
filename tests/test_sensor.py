"""Tests für Abruf und Sensoren."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from freezegun.api import FrozenDateTimeFactory
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_fire_time_changed
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.ladekoenig.const import DOMAIN

from .conftest import DATA_URL

LOCATION = {"latitude": 50.1101, "longitude": 8.6801, "radius": 10000}


def _entry(hass: HomeAssistant, **extra) -> MockConfigEntry:
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Ladekönig",
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

    price = hass.states.get("sensor.ladekonig_cheapest_price")
    assert price is not None
    assert float(price.state) == 0.49
    assert price.attributes["unit_of_measurement"] == "€/kWh"
    assert price.attributes["operator"] == "Stadtwerke Musterstadt"
    assert len(price.attributes["offers"]) == 3
    assert "Tesla Germany GmbH" in price.attributes["attribution"]

    count = hass.states.get("sensor.ladekonig_charging_stations_in_area")
    assert count.state == "4"
    assert count.attributes["with_price"] == 3

    stamp = hass.states.get("sensor.ladekonig_data_timestamp")
    assert stamp.state == "2026-10-27T03:00:00+00:00"


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

    assert float(hass.states.get("sensor.ladekonig_cheapest_price").state) == 0.59
    calls = aioclient_mock.call_count

    freezer.move_to(datetime(2026, 10, 27, 22, 10, tzinfo=UTC))  # 23:10 Ortszeit
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(minutes=6))
    await hass.async_block_till_done(wait_background_tasks=True)

    assert float(hass.states.get("sensor.ladekonig_cheapest_price").state) == 0.39
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
    state = hass.states.get("sensor.ladekonig_cheapest_price")
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
