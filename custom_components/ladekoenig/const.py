"""Konstanten für Ladekönig."""

from datetime import timedelta
from typing import Final

DOMAIN: Final = "ladekoenig"

DEFAULT_NAME: Final = "Ladekönig"
DEFAULT_DATA_URL: Final = "https://uekala.github.io/ladekoenig/v1"
DEFAULT_RADIUS_M: Final = 10_000
MAX_RADIUS_M: Final = 50_000

CONF_LOCATION: Final = "location"
CONF_MIN_POWER: Final = "min_power"
CONF_CURRENT: Final = "current_type"
CONF_CONNECTOR: Final = "connector"
CONF_OPERATORS: Final = "operators"
CONF_DATA_URL: Final = "data_url"
CONF_LOCATION_ENTITY: Final = "location_entity"

MIN_POWER_OPTIONS: Final = ["0", "22", "50", "150"]
CURRENT_OPTIONS: Final = ["any", "ac", "dc"]
CONNECTOR_OPTIONS: Final = ["any", "ccs", "type2", "chademo"]

UPDATE_INTERVAL: Final = timedelta(minutes=5)
FETCH_INTERVAL: Final = timedelta(hours=6)
REQUEST_TIMEOUT: Final = 30
TOP_OFFERS: Final = 5
SUPPORTED_FORMAT: Final = 1
MOVE_THRESHOLD_KM: Final = 0.5
MAX_CACHED_TILES: Final = 40
LOCATION_DOMAINS: Final = ["device_tracker", "person", "zone"]
