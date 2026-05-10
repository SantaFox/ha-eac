"""Constants for the EAC Cyprus integration."""
from __future__ import annotations

DOMAIN = "eac_cyprus"

CONF_EMAIL = "email"
CONF_PASSWORD = "password"
CONF_BASE_URL = "base_url"

# Runtime-tunable options (config entry .options dict).
CONF_SCAN_INTERVAL_MINUTES = "scan_interval_minutes"
CONF_HISTORY_DAYS = "history_days"

DEFAULT_BASE_URL = "https://meterreading-dso.eac.com.cy"

# 6h is plenty: the EAC portal updates daily and 30-min profile data lags by
# hours, so polling more often gains nothing in practice.
DEFAULT_SCAN_INTERVAL_MINUTES = 360

# Pulling a few days of history on every refresh lets us recover gracefully
# from missed reports (e.g. the integration was down or network blipped).
DEFAULT_HISTORY_DAYS = 14

# Values offered in the options-flow dropdown (minutes).
SCAN_INTERVAL_CHOICES: tuple[int, ...] = (30, 60, 180, 360, 720, 1440)

MANUFACTURER = "EAC (Electricity Authority of Cyprus)"
