"""Diagnosedaten für Ladekrone (Standort wird ausgeblendet)."""

from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.core import HomeAssistant

from . import LadekroneConfigEntry
from .const import CONF_LOCATION

TO_REDACT = {CONF_LOCATION, "latitude", "longitude", "address"}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: LadekroneConfigEntry
) -> dict[str, Any]:
    coordinator = entry.runtime_data
    data = coordinator.data
    settings = coordinator.settings
    return {
        "config": async_redact_data({**entry.data, **entry.options}, TO_REDACT),
        "radius_km": settings.radius_km,
        "filters": {
            "min_power_kw": settings.filters.min_power_kw,
            "current": settings.filters.current,
            "connector": settings.filters.connector,
            "operators": list(settings.filters.operators),
        },
        "data_url": settings.data_url,
        "generated": data.generated.isoformat() if data.generated else None,
        "station_count": data.station_count,
        "offers": len(data.offers),
        "top_offers": async_redact_data([offer.as_dict() for offer in data.offers[:3]], TO_REDACT),
        "sources": data.sources,
    }
