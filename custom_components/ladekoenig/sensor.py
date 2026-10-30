"""Sensoren für Ladekönig."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import dt as dt_util

from . import LadekoenigConfigEntry
from .const import DOMAIN, TOP_OFFERS
from .coordinator import LadekoenigCoordinator, LadekoenigData
from .pricing import Offer

PRICE_UNIT = "€/kWh"


@dataclass(frozen=True, kw_only=True)
class LadekoenigSensorDescription(SensorEntityDescription):
    """Beschreibt einen Ladekönig-Sensor."""

    value_fn: Callable[[LadekoenigData], float | int | datetime | None]
    attrs_fn: Callable[[LadekoenigData], dict[str, Any]] | None = None


def _cheapest_value(data: LadekoenigData) -> float | None:
    return round(data.offers[0].price.kwh, 4) if data.offers else None


def _offer_dict(offer: Offer) -> dict[str, Any]:
    """Angebot als Attribut; Zeitpunkte in der Zeitzone von Home Assistant."""
    data = offer.as_dict()
    if isinstance(data.get("until"), datetime):
        data["until"] = dt_util.as_local(data["until"]).isoformat()
    return data


def _cheapest_attrs(data: LadekoenigData) -> dict[str, Any]:
    if not data.offers:
        return {"offers": []}
    best = data.offers[0]
    attrs = _offer_dict(best)
    attrs.pop("price", None)
    attrs["price_updated"] = best.price.updated
    attrs["offers"] = [_offer_dict(offer) for offer in data.offers[:TOP_OFFERS]]
    return attrs


def _ahead_value(data: LadekoenigData) -> float | None:
    return round(data.outlook.offer.price.kwh, 4) if data.outlook else None


def _ahead_attrs(data: LadekoenigData) -> dict[str, Any]:
    outlook = data.outlook
    if outlook is None:
        return {}
    attrs = outlook.offer.as_dict()
    attrs.pop("price", None)
    attrs.pop("until", None)
    attrs.pop("next_price", None)
    attrs["from"] = dt_util.as_local(outlook.start).isoformat()
    attrs["until"] = dt_util.as_local(outlook.end).isoformat() if outlook.end else None
    attrs["now"] = outlook.start <= dt_util.utcnow()
    return attrs


SENSORS: tuple[LadekoenigSensorDescription, ...] = (
    LadekoenigSensorDescription(
        key="cheapest_price",
        translation_key="cheapest_price",
        native_unit_of_measurement=PRICE_UNIT,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=2,
        value_fn=_cheapest_value,
        attrs_fn=_cheapest_attrs,
    ),
    LadekoenigSensorDescription(
        key="cheapest_ahead",
        translation_key="cheapest_ahead",
        native_unit_of_measurement=PRICE_UNIT,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=2,
        value_fn=_ahead_value,
        attrs_fn=_ahead_attrs,
    ),
    LadekoenigSensorDescription(
        key="station_count",
        translation_key="station_count",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda data: data.station_count,
        attrs_fn=lambda data: {
            "with_price": len(data.offers),
            "center_source": data.center_source,
            "center_latitude": data.center[0] if data.center else None,
            "center_longitude": data.center[1] if data.center else None,
        },
    ),
    LadekoenigSensorDescription(
        key="data_updated",
        translation_key="data_updated",
        device_class=SensorDeviceClass.TIMESTAMP,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda data: data.generated,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: LadekoenigConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    coordinator = entry.runtime_data
    async_add_entities(LadekoenigSensor(coordinator, description) for description in SENSORS)


class LadekoenigSensor(CoordinatorEntity[LadekoenigCoordinator], SensorEntity):
    """Ein Ladekönig-Sensor."""

    _attr_has_entity_name = True
    entity_description: LadekoenigSensorDescription

    def __init__(
        self, coordinator: LadekoenigCoordinator, description: LadekoenigSensorDescription
    ) -> None:
        super().__init__(coordinator)
        self.entity_description = description
        entry = coordinator.config_entry
        self._attr_unique_id = f"{entry.entry_id}_{description.key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name=entry.title,
            manufacturer="Ladekönig",
            model="AFIR-Ad-hoc-Preise (Mobilithek)",
            entry_type=DeviceEntryType.SERVICE,
            configuration_url="https://github.com/uekala/ladekoenig",
        )

    @property
    def native_value(self) -> float | int | datetime | None:
        return self.entity_description.value_fn(self.coordinator.data)

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        if self.entity_description.attrs_fn is None:
            return None
        return self.entity_description.attrs_fn(self.coordinator.data)

    @property
    def attribution(self) -> str | None:
        return self.coordinator.data.attribution
