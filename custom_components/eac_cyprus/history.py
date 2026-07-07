"""One-time deep backfill of the full available meter history.

The regular coordinator only imports a rolling window (``history_days``). This
pulls the *entire* available series for every cumulative kWh channel of every
meter -- active and retired -- from the portal and writes it as external
statistics, so the Energy Dashboard graph reaches back to each meter's install
date instead of only a couple of weeks. Retired meters (already swapped out)
get their full history too, so their device is not an empty stub.

Exposed as the ``eac_cyprus.import_full_history`` service and offered once via
a persistent notification on first setup. Idempotent -- safe to re-run.
"""
from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from homeassistant.core import HomeAssistant

from eac_dso_portal import EacApiError, EacClient

from .const import channel_label
from .statistics import import_cumulative_history

_LOGGER = logging.getLogger(__name__)

# Lower bound if a meter somehow reports no install date.
_MAX_LOOKBACK = timedelta(days=3650)


def _as_utc(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=UTC)


def _meter_serial(cfg) -> str:
    return cfg.serial_number or (
        cfg.install_date.date().isoformat() if cfg.install_date else "unknown"
    )


async def async_import_full_history(
    hass: HomeAssistant, client: EacClient
) -> list[dict[str, Any]]:
    """Backfill full history for all meters. Returns a per-channel summary."""
    now = datetime.now(UTC)
    results: list[dict[str, Any]] = []

    for sp in await client.list_service_points():
        if not (sp.active and sp.device_exists):
            continue
        try:
            configs = await client.get_meter_configs(sp.id)
        except EacApiError as err:
            _LOGGER.warning("Full import: meter config failed for %s: %s", sp.id, err)
            continue

        for cfg in configs:
            serial = _meter_serial(cfg)
            start = _as_utc(cfg.install_date) or (now - _MAX_LOOKBACK)
            end = _as_utc(cfg.removal_date) or now
            for ch in cfg.channels:
                if ch.uom.upper() != "KWH" or ch.interval:
                    continue
                try:
                    crs = await client.get_readings(sp.id, start, end, mc_id=ch.id)
                except EacApiError as err:
                    _LOGGER.debug(
                        "Full import: no data sp=%s mc=%s: %s", sp.id, ch.id, err
                    )
                    continue
                matching = next(
                    (cr for cr in crs if cr.channel_id == ch.id), None
                )
                if matching is None or not matching.readings:
                    continue
                import_cumulative_history(
                    hass,
                    sp.id,
                    ch.id,
                    f"{serial} {channel_label(ch.type)}",
                    matching.readings,
                )
                results.append(
                    {
                        "meter": serial,
                        "channel": ch.type,
                        "readings": len(matching.readings),
                        "from": start.date().isoformat(),
                        "to": end.date().isoformat(),
                    }
                )
                _LOGGER.info(
                    "Full import: meter %s channel %s (%d readings, %s..%s)",
                    serial,
                    ch.type,
                    len(matching.readings),
                    start.date(),
                    end.date(),
                )

    _LOGGER.info("Full history import complete: %d channel(s)", len(results))
    return results
