"""Constants for the EAC Cyprus integration."""
from __future__ import annotations

DOMAIN = "eac_cyprus"

CONF_EMAIL = "email"
CONF_PASSWORD = "password"
CONF_BASE_URL = "base_url"

# Runtime-tunable options (config entry .options dict).
CONF_SCAN_INTERVAL_MINUTES = "scan_interval_minutes"
CONF_HISTORY_DAYS = "history_days"
CONF_STALE_THRESHOLD_DAYS = "stale_threshold_days"

DEFAULT_BASE_URL = "https://meterreading-dso.eac.com.cy"

# 6h is plenty: the EAC portal updates daily and 30-min profile data lags by
# hours, so polling more often gains nothing in practice.
DEFAULT_SCAN_INTERVAL_MINUTES = 360

# Pulling a few days of history on every refresh lets us recover gracefully
# from missed reports (e.g. the integration was down or network blipped).
DEFAULT_HISTORY_DAYS = 14

# The portal publishes with a 2-3 day lag, so "no fresh data" only becomes
# suspicious a bit beyond that. Marks binary_sensor.*_data_stale on.
DEFAULT_STALE_THRESHOLD_DAYS = 4

# Values offered in the options-flow dropdown (minutes).
SCAN_INTERVAL_CHOICES: tuple[int, ...] = (30, 60, 180, 360, 720, 1440)

MANUFACTURER = "EAC (Electricity Authority of Cyprus)"

# Friendly labels for the channel types EAC exposes (mcList[].type). Shared by
# the sensor names and the external-statistic display names.
CHANNEL_LABELS: dict[str, str] = {
    "S-KWH-24H": "Energy total (24h)",
    "S-KWH-NORMAL": "Energy peak tariff",
    "S-KWH-OFFPEAK": "Energy off-peak tariff",
    "S-KWH-EXP": "Energy exported",
    "KWH-30MIN-LP-IMP": "Power import (30-min average)",
    "KWH-30MIN-LP-EXP": "Power export (30-min average)",
    "S-KVAH-24H": "Apparent energy",
    "S-KVAH-24H-EXP": "Apparent energy exported",
    "S-KVRH-24H": "Reactive energy",
    "S-KVRH-24H-EXP": "Reactive energy exported",
    "S-KWH": "Energy total",
}


def channel_label(ch_type: str) -> str:
    return CHANNEL_LABELS.get(ch_type, ch_type)
