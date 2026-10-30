"""Einrichtungsdialog für Ladekönig."""

from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.const import CONF_NAME
from homeassistant.core import callback
from homeassistant.helpers import selector

from .const import (
    CONF_CONNECTOR,
    CONF_CURRENT,
    CONF_DATA_URL,
    CONF_LOCATION,
    CONF_LOCATION_ENTITY,
    CONF_MIN_POWER,
    CONF_OPERATORS,
    CONNECTOR_OPTIONS,
    CURRENT_OPTIONS,
    DEFAULT_DATA_URL,
    DEFAULT_NAME,
    DEFAULT_RADIUS_M,
    DOMAIN,
    LOCATION_DOMAINS,
    MAX_RADIUS_M,
    MIN_POWER_OPTIONS,
)


def _select(options: list[str], key: str) -> selector.SelectSelector:
    return selector.SelectSelector(
        selector.SelectSelectorConfig(
            options=options,
            translation_key=key,
            mode=selector.SelectSelectorMode.DROPDOWN,
        )
    )


def _search_schema(defaults: dict[str, Any]) -> dict:
    return {
        vol.Required(CONF_LOCATION, default=defaults[CONF_LOCATION]): selector.LocationSelector(
            selector.LocationSelectorConfig(radius=True)
        ),
        vol.Required(CONF_MIN_POWER, default=defaults.get(CONF_MIN_POWER, "0")): _select(
            MIN_POWER_OPTIONS, CONF_MIN_POWER
        ),
        vol.Required(CONF_CURRENT, default=defaults.get(CONF_CURRENT, "any")): _select(
            CURRENT_OPTIONS, CONF_CURRENT
        ),
        vol.Required(CONF_CONNECTOR, default=defaults.get(CONF_CONNECTOR, "any")): _select(
            CONNECTOR_OPTIONS, CONF_CONNECTOR
        ),
        vol.Optional(
            CONF_OPERATORS,
            description={"suggested_value": defaults.get(CONF_OPERATORS, "")},
        ): selector.TextSelector(),
        vol.Optional(
            CONF_LOCATION_ENTITY,
            description={"suggested_value": defaults.get(CONF_LOCATION_ENTITY)},
        ): selector.EntitySelector(selector.EntitySelectorConfig(domain=LOCATION_DOMAINS)),
    }


def _validate(user_input: dict[str, Any]) -> dict[str, str]:
    errors: dict[str, str] = {}
    radius = float(user_input[CONF_LOCATION].get("radius", DEFAULT_RADIUS_M))
    if radius > MAX_RADIUS_M:
        errors[CONF_LOCATION] = "radius_too_large"
    elif radius < 500:
        errors[CONF_LOCATION] = "radius_too_small"
    return errors


def _clean(user_input: dict[str, Any]) -> dict[str, Any]:
    data = dict(user_input)
    location = dict(data[CONF_LOCATION])
    location.setdefault("radius", DEFAULT_RADIUS_M)
    data[CONF_LOCATION] = location
    data[CONF_OPERATORS] = str(data.get(CONF_OPERATORS) or "").strip()
    if not data.get(CONF_LOCATION_ENTITY):
        data.pop(CONF_LOCATION_ENTITY, None)
    return data


class LadekoenigConfigFlow(ConfigFlow, domain=DOMAIN):
    """Erstellt einen Ladekönig-Eintrag pro Suchgebiet."""

    VERSION = 1

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            errors = _validate(user_input)
            if not errors:
                data = _clean(user_input)
                name = data.pop(CONF_NAME, None) or DEFAULT_NAME
                return self.async_create_entry(title=name, data=data)

        defaults = user_input or {
            CONF_NAME: DEFAULT_NAME,
            CONF_LOCATION: {
                "latitude": self.hass.config.latitude,
                "longitude": self.hass.config.longitude,
                "radius": DEFAULT_RADIUS_M,
            },
        }
        schema = vol.Schema(
            {
                vol.Required(CONF_NAME, default=defaults.get(CONF_NAME, DEFAULT_NAME)): str,
                **_search_schema(defaults),
            }
        )
        return self.async_show_form(step_id="user", data_schema=schema, errors=errors)

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        return LadekoenigOptionsFlow()


class LadekoenigOptionsFlow(OptionsFlow):
    """Suchgebiet und Filter nachträglich ändern."""

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            errors = _validate(user_input)
            if not errors:
                data = _clean(user_input)
                data[CONF_DATA_URL] = str(data.get(CONF_DATA_URL) or DEFAULT_DATA_URL).strip()
                return self.async_create_entry(data=data)

        defaults = user_input or {**self.config_entry.data, **self.config_entry.options}
        schema = vol.Schema(
            {
                **_search_schema(defaults),
                vol.Optional(
                    CONF_DATA_URL, default=defaults.get(CONF_DATA_URL, DEFAULT_DATA_URL)
                ): selector.TextSelector(
                    selector.TextSelectorConfig(type=selector.TextSelectorType.URL)
                ),
            }
        )
        return self.async_show_form(step_id="init", data_schema=schema, errors=errors)
