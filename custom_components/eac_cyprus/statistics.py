"""Long-term-statistics backfill for cumulative kWh channels.

The EAC portal publishes meter readings with a lag of 2-3 days. Home
Assistant's `state_class=total_increasing` contract assumes the live state
moves more or less in real time, and the recorder writes its own auto-stats
based on that assumption. Pushing backfilled hourly rows through
`async_import_statistics` against the same `entity_id` collides with that
auto-record (see issue #1) and produces a `sum` cliff at the boundary
between the API's data window and the HA-restart point.

Solution: use `async_add_external_statistics` with our own
`eac_cyprus:<sp>_<channel>` statistic IDs. External statistics are an
independent source as far as the recorder is concerned, with no auto-record
behind them, so the historical view stays consistent regardless of when
HA was started.

Energy Dashboard then sees `EAC ... <channel>` as separate entries in the
'Add consumption' dropdown. The sensor entities themselves keep being
'latest known value' displays and are not the right thing to put into
Energy Dashboard for a delayed-data provider.

v1 scope (this module): cumulative kWh channels only. Interval channels
(30-min load profile) have no cumulative reading on the API side, so the
running sum has to be synthesised. Tracked separately.
"""
from __future__ import annotations

import logging
from datetime import UTC

from homeassistant.components.recorder.models.statistics import (
    StatisticData,
    StatisticMetaData,
)
from homeassistant.components.recorder.statistics import (
    async_add_external_statistics,
)
from homeassistant.const import UnitOfEnergy
from homeassistant.core import HomeAssistant

from eac_dso_portal import Reading

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)


def external_statistic_id(sp_id: str, channel_id: str) -> str:
    """Stable identifier for the channel's external statistic.

    Stays the same across upgrades and restarts so the recorder keeps
    appending to the same series instead of orphaning it on every release.
    """
    return f"{DOMAIN}:{sp_id}_{channel_id}"


def import_cumulative_history(
    hass: HomeAssistant,
    sp_id: str,
    channel_id: str,
    display_name: str,
    readings: tuple[Reading, ...],
) -> None:
    """Push readings for one cumulative kWh channel as hourly external stats.

    ``display_name`` is what the Energy Dashboard shows; we name it from the
    meter serial + channel label so it reads e.g. "1281890 Energy total (24h)"
    instead of a transliterated address. The ``statistic_id`` stays derived
    from (service point, channel) so it is stable across renames and restarts.
    """
    points = [r for r in readings if r.reading is not None]
    if not points:
        _LOGGER.info(
            "Skipping stats import for sp=%s mc=%s: %d readings, none cumulative",
            sp_id,
            channel_id,
            len(readings),
        )
        return

    statistic_id = external_statistic_id(sp_id, channel_id)

    metadata: StatisticMetaData = {
        "has_mean": False,
        "has_sum": True,
        "name": display_name,
        "source": DOMAIN,
        "statistic_id": statistic_id,
        "unit_of_measurement": UnitOfEnergy.KILO_WATT_HOUR,
    }

    stats: list[StatisticData] = []
    for r in points:
        # The portal's `dt` is naive ISO; daily readings sit at 00:00 Cyprus
        # local time. Treat as UTC for now: a few hours of drift on a daily
        # lifetime counter is invisible in Energy Dashboard. Proper TZ
        # handling is a follow-up when we start importing 30-minute data.
        start = r.dt.replace(minute=0, second=0, microsecond=0)
        if start.tzinfo is None:
            start = start.replace(tzinfo=UTC)
        # `sum` is the running lifetime total at the end of the hour, which
        # is exactly what the meter's cumulative reading already is.
        stats.append({"start": start, "sum": r.reading})

    try:
        async_add_external_statistics(hass, metadata, stats)
    except Exception:  # noqa: BLE001 - recorder exceptions are interesting
        _LOGGER.exception(
            "async_add_external_statistics raised for %s (rows=%d, first=%s, last=%s)",
            statistic_id,
            len(stats),
            stats[0]["start"].isoformat(),
            stats[-1]["start"].isoformat(),
        )
    else:
        _LOGGER.info(
            "Imported %d statistics rows for %s (range %s to %s)",
            len(stats),
            statistic_id,
            stats[0]["start"].isoformat(),
            stats[-1]["start"].isoformat(),
        )
