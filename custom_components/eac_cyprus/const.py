"""Constants for the EAC Cyprus integration."""
from __future__ import annotations

from datetime import timedelta

DOMAIN = "eac_cyprus"

CONF_EMAIL = "email"
CONF_PASSWORD = "password"
CONF_BASE_URL = "base_url"

DEFAULT_BASE_URL = "https://meterreading-dso.eac.com.cy"

# Smart-meter daily values arrive with several hours of lag from the DSO.
# Polling more often gains nothing, so we stay polite.
SCAN_INTERVAL = timedelta(hours=6)

# How much history to fetch on every refresh. The cumulative meter reading is
# used as the sensor state, so we only need the latest value, but pulling a
# few days lets us recover gracefully from missed reports.
HISTORY_DAYS = 14

MANUFACTURER = "EAC (Electricity Authority of Cyprus)"
