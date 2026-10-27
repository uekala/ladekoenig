"""Ladekönig – günstigste Ad-hoc-Ladepreise in der Umgebung (AFIR/Mobilithek)."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant

from .coordinator import LadekoenigCoordinator

PLATFORMS: list[Platform] = [Platform.SENSOR]

type LadekoenigConfigEntry = ConfigEntry[LadekoenigCoordinator]


async def async_setup_entry(hass: HomeAssistant, entry: LadekoenigConfigEntry) -> bool:
    coordinator = LadekoenigCoordinator(hass, entry)
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))
    return True


async def async_unload_entry(hass: HomeAssistant, entry: LadekoenigConfigEntry) -> bool:
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def _async_update_listener(hass: HomeAssistant, entry: LadekoenigConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)
