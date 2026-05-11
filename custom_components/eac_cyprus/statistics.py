"""Long-term-statistics backfill for cumulative kWh sensors.

The sensor platform's ``state_class=total_increasing`` already produces hourly
statistics from the live state on every recorder tick — but the state only
reflects the *latest* reading the integration has fetched. Between polls (6
hours by default) the recorder has nothing fresh to record, so the Energy
Dashboard graph develops gaps right where the EAC API actually has data.

This module closes those gaps. On every poll the coordinator hands us the full
window of readings the API returned (typically the last 14 days), and we push
each one as a long-term-statistics record attached to the existing sensor's
``entity_id``. HA's ``async_import_statistics`` is idempotent on
``(statistic_id, start_hour)``: re-pushing a row for an hour we already have
*replaces* it, so the per-poll overlap is harmless. We never need to track a
"last imported timestamp" ourselves.

v1 scope: cumulative kWh channels only (those that carry a running ``reading``
in addition to the per-slot ``value``). Interval channels like the 30-min load
profile have no cumulative counter on the API side — backfilling them needs to
synthesise a running sum, which is left for a follow-up.
"""
from __future__ import annotations

import logging
from datetime import UTC

from homeassistant.components.recorder.models.statistics import (
    StatisticData,
    StatisticMetaData,
)
from homeassistant.components.recorder.statistics import async_import_statistics
from homeassistant.const import UnitOfEnergy
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er

from eac_dso_portal import Reading

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)


def _resolve_entity_id(
    hass: HomeAssistant, sp_id: str, channel_id: str
) -> str | None:
    """Map our (service-point, channel) unique_id to the sensor's entity_id."""
    registry = er.async_get(hass)
    return registry.async_get_entity_id("sensor", DOMAIN, f"{sp_id}_{channel_id}")


def import_cumulative_history(
    hass: HomeAssistant,
    sp_id: str,
    channel_id: str,
    channel_name: str,
    readings: tuple[Reading, ...],
) -> None:
    """Push readings for one cumulative kWh channel as hourly statistics.

    No-op when the sensor entity does not yet exist in the registry — that
    happens on the very first poll for a brand-new install; HA will call us
    again on the next refresh once the platform has registered the entity.
    """
    entity_id = _resolve_entity_id(hass, sp_id, channel_id)
    if entity_id is None:
        return

    points = [r for r in readings if r.reading is not None]
    if not points:
        return

    metadata = StatisticMetaData(
        has_mean=False,
        has_sum=True,
        name=channel_name,
        source="recorder",
        statistic_id=entity_id,
        unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
    )

    stats: list[StatisticData] = []
    for r in points:
        # The portal's `dt` is naive ISO; daily readings sit at 00:00 Cyprus
        # local time. Treat as UTC for statistics: a few hours of TZ drift on
        # a *daily* lifetime counter is invisible in Energy Dashboard, and
        # converting properly would require pulling the user's TZ config.
        start = r.dt.replace(minute=0, second=0, microsecond=0)
        if start.tzinfo is None:
            start = start.replace(tzinfo=UTC)
        stats.append(StatisticData(start=start, sum=r.reading, state=r.reading))

    _LOGGER.debug(
        "Importing %d statistics rows for %s (sp=%s mc=%s)",
        len(stats),
        entity_id,
        sp_id,
        channel_id,
    )
    async_import_statistics(hass, metadata, stats)
