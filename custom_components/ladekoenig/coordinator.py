"""Datenabruf und Auswertung für Ladekönig."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import aiohttp
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .const import (
    CONF_CONNECTOR,
    CONF_CURRENT,
    CONF_DATA_URL,
    CONF_LOCATION,
    CONF_MIN_POWER,
    CONF_OPERATORS,
    DEFAULT_DATA_URL,
    DEFAULT_RADIUS_M,
    DOMAIN,
    FETCH_INTERVAL,
    REQUEST_TIMEOUT,
    SUPPORTED_FORMAT,
    TOP_OFFERS,
    UPDATE_INTERVAL,
)
from .pricing import (
    Filters,
    Offer,
    Outlook,
    annotate_changes,
    cheapest_ahead,
    count_stations,
    find_offers,
    future_steps,
    stations_in_radius,
    tiles_for_radius,
)

_LOGGER = logging.getLogger(__name__)


@dataclass
class SearchSettings:
    """Aufbereitete Einstellungen eines Eintrags."""

    latitude: float
    longitude: float
    radius_km: float
    filters: Filters
    data_url: str

    @classmethod
    def from_entry(cls, entry: ConfigEntry) -> SearchSettings:
        config: dict[str, Any] = {**entry.data, **entry.options}
        location = config.get(CONF_LOCATION) or {}
        operators = tuple(
            part.strip()
            for part in str(config.get(CONF_OPERATORS) or "").split(",")
            if part.strip()
        )
        return cls(
            latitude=float(location["latitude"]),
            longitude=float(location["longitude"]),
            radius_km=float(location.get("radius", DEFAULT_RADIUS_M)) / 1000,
            filters=Filters(
                min_power_kw=float(config.get(CONF_MIN_POWER) or 0),
                current=config.get(CONF_CURRENT) or "any",
                connector=config.get(CONF_CONNECTOR) or "any",
                operators=operators,
            ),
            data_url=str(config.get(CONF_DATA_URL) or DEFAULT_DATA_URL).rstrip("/"),
        )


@dataclass
class LadekoenigData:
    """Ergebnis einer Auswertung."""

    offers: list[Offer]
    station_count: int
    generated: datetime | None
    attribution: str
    sources: list[dict[str, Any]] = field(default_factory=list)
    outlook: Outlook | None = None


def evaluate(
    stations: list[dict], settings: SearchSettings, now: datetime, with_outlook: bool = True
) -> tuple[list[Offer], int, Outlook | None]:
    """Preise jetzt und (optional) für die nächsten 24 Stunden (läuft im Executor)."""
    lat, lon, filters = settings.latitude, settings.longitude, settings.filters
    nearby = stations_in_radius(stations, lat, lon, settings.radius_km)
    offers = find_offers(nearby, lat, lon, settings.radius_km, filters, now)
    steps = future_steps(now)
    by_id = {station["id"]: station for station in nearby if "id" in station}
    annotate_changes(offers[:TOP_OFFERS], by_id, lat, lon, filters, steps)
    outlook = None
    if with_outlook:
        current = offers[0] if offers else None
        outlook = cheapest_ahead(nearby, current, lat, lon, filters, now, steps)
    count = count_stations(nearby, lat, lon, settings.radius_km, filters)
    return offers, count, outlook


class LadekoenigCoordinator(DataUpdateCoordinator[LadekoenigData]):
    """Lädt die Kacheln selten neu und wertet die Preise alle paar Minuten aus,
    damit tageszeitabhängige Preise (z. B. Tesla-Nachttarif) rechtzeitig wechseln."""

    config_entry: ConfigEntry

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=DOMAIN,
            update_interval=UPDATE_INTERVAL,
        )
        self.settings = SearchSettings.from_entry(entry)
        self._session = async_get_clientsession(hass)
        self._meta: dict[str, Any] | None = None
        self._tiles: dict[str, list[dict]] = {}
        self._etags: dict[str, str] = {}
        self._last_fetch: datetime | None = None
        self._outlook_key: tuple | None = None
        self._outlook: Outlook | None = None

    async def _async_update_data(self) -> LadekoenigData:
        now = dt_util.utcnow()
        if self._last_fetch is None or now - self._last_fetch >= FETCH_INTERVAL:
            try:
                await self._async_fetch()
            except (aiohttp.ClientError, TimeoutError, ValueError) as err:
                if self._meta is None:
                    raise UpdateFailed(f"Ladepreise nicht abrufbar: {err}") from err
                _LOGGER.warning("Abruf fehlgeschlagen, nutze vorhandene Daten: %s", err)
            else:
                self._last_fetch = now

        stations = [station for tile in self._tiles.values() for station in tile]
        # Die 24-Stunden-Vorschau ändert sich nur zur nächsten Viertelstunde oder
        # mit neuen Daten; dazwischen wird sie nicht neu berechnet.
        outlook_key = (future_steps(now)[0], self._last_fetch, self.settings)
        fresh = outlook_key != self._outlook_key
        offers, count, outlook = await self.hass.async_add_executor_job(
            evaluate, stations, self.settings, now, fresh
        )
        if fresh:
            self._outlook_key, self._outlook = outlook_key, outlook
        outlook = self._outlook
        meta = self._meta or {}
        return LadekoenigData(
            offers=offers,
            station_count=count,
            generated=dt_util.parse_datetime(meta.get("generated") or ""),
            attribution=_attribution(meta),
            sources=list(meta.get("sources") or []),
            outlook=outlook,
        )

    async def _async_fetch(self) -> None:
        settings = self.settings
        meta = await self._async_get_json(f"{settings.data_url}/meta.json", "meta")
        if meta is None:
            if self._meta is None:
                raise ValueError("meta.json nicht verfügbar")
            meta = self._meta
        if int(meta.get("v", 0)) != SUPPORTED_FORMAT:
            raise ValueError(f"Datenformat v{meta.get('v')} wird nicht unterstützt")

        available = set(meta.get("tiles") or [])
        wanted = [
            key
            for key in tiles_for_radius(
                settings.latitude,
                settings.longitude,
                settings.radius_km,
                float(meta.get("tile_deg", 0.25)),
            )
            if key in available
        ]
        results = await asyncio.gather(
            *(self._async_get_json(f"{settings.data_url}/tiles/{key}.json", key) for key in wanted)
        )
        tiles: dict[str, list[dict]] = {}
        for key, payload in zip(wanted, results, strict=True):
            if payload is None:
                tiles[key] = self._tiles.get(key, [])
            else:
                tiles[key] = list(payload.get("stations") or [])
        self._meta = meta
        self._tiles = tiles

    async def _async_get_json(self, url: str, cache_key: str) -> dict[str, Any] | None:
        """Lädt JSON; None bedeutet 'unverändert' (304) bzw. Kachel fehlt (404)."""
        headers = {}
        if etag := self._etags.get(cache_key):
            headers["If-None-Match"] = etag
        async with asyncio.timeout(REQUEST_TIMEOUT):
            async with self._session.get(url, headers=headers) as response:
                if response.status == 304:
                    return None
                if response.status == 404 and cache_key != "meta":
                    self._etags.pop(cache_key, None)
                    return {"stations": []}
                response.raise_for_status()
                payload = await response.json(content_type=None)
                if etag := response.headers.get("ETag"):
                    self._etags[cache_key] = etag
        if not isinstance(payload, dict):
            raise ValueError(f"Unerwartete Antwort von {url}")
        return payload


def _attribution(meta: dict[str, Any]) -> str:
    parts = [
        f"{source.get('attribution') or source.get('name')} ({source.get('license')})"
        for source in meta.get("sources") or []
        if source.get("stations")
    ]
    text = "Daten: Mobilithek / AFIR-Angebote der Betreiber"
    return f"{text}: {', '.join(parts)}" if parts else text
