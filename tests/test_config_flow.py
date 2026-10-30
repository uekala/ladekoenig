"""Tests für Einrichtung und Optionen."""

from __future__ import annotations

from unittest.mock import patch

from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.ladekoenig.const import DOMAIN

LOCATION = {"latitude": 50.1101, "longitude": 8.6801, "radius": 10000}


async def test_user_flow_creates_entry(hass: HomeAssistant) -> None:
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"

    with patch("custom_components.ladekoenig.async_setup_entry", return_value=True):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {
                "name": "Supercharger zuhause",
                "location": LOCATION,
                "min_power": "50",
                "current_type": "dc",
                "connector": "ccs",
                "operators": " Tesla ",
            },
        )
        await hass.async_block_till_done()

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Supercharger zuhause"
    assert result["data"] == {
        "location": LOCATION,
        "min_power": "50",
        "current_type": "dc",
        "connector": "ccs",
        "operators": "Tesla",
    }


async def test_radius_limits(hass: HomeAssistant) -> None:
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            "name": "Zu groß",
            "location": {**LOCATION, "radius": 80000},
            "min_power": "0",
            "current_type": "any",
            "connector": "any",
        },
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"location": "radius_too_large"}


async def test_options_flow_updates_filters(hass: HomeAssistant) -> None:
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Ladekönig",
        data={
            "location": LOCATION,
            "min_power": "0",
            "current_type": "any",
            "connector": "any",
            "operators": "",
        },
    )
    entry.add_to_hass(hass)

    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] is FlowResultType.FORM

    with patch("custom_components.ladekoenig.async_setup_entry", return_value=True):
        result = await hass.config_entries.options.async_configure(
            result["flow_id"],
            {
                "location": {**LOCATION, "radius": 5000},
                "min_power": "150",
                "current_type": "dc",
                "connector": "any",
                "operators": "EnBW",
                "data_url": "https://example.invalid/v1",
            },
        )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert entry.options["min_power"] == "150"
    assert entry.options["location"]["radius"] == 5000
    assert entry.options["data_url"] == "https://example.invalid/v1"


async def test_user_flow_with_location_entity(hass: HomeAssistant) -> None:
    hass.states.async_set("device_tracker.auto", "home", {"latitude": 50.1, "longitude": 8.6})
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    with patch("custom_components.ladekoenig.async_setup_entry", return_value=True):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {
                "name": "Unterwegs",
                "location": LOCATION,
                "min_power": "0",
                "current_type": "dc",
                "connector": "any",
                "operators": "Tesla",
                "location_entity": "device_tracker.auto",
            },
        )
        await hass.async_block_till_done()
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"]["location_entity"] == "device_tracker.auto"
