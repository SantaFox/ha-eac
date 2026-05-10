"""DataUpdateCoordinator that polls the EAC DSO portal."""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from eac_dso_portal import (
    ChannelReadings,
    EacApiError,
    EacAuthError,
    EacClient,
    MeasurementChannel,
    MeterConfig,
    Reading,
    ServicePoint,
    UserDetails,
)
from .const import DOMAIN, HISTORY_DAYS, SCAN_INTERVAL

_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class ChannelState:
    """Current state of a single measurement channel on a service point."""
    channel: MeasurementChannel
    latest: Reading | None  # most recent reading we have (cumulative or interval)


@dataclass(frozen=True, slots=True)
class ServicePointState:
    """Per-service-point snapshot built every refresh."""
    service_point: ServicePoint
    active_meter: MeterConfig | None
    channels: dict[str, ChannelState]  # keyed by channel id


@dataclass(frozen=True, slots=True)
class EacData:
    """What the coordinator hands to entities."""
    user: UserDetails
    points: dict[str, ServicePointState]  # keyed by service-point id


class EacCoordinator(DataUpdateCoordinator[EacData]):
    """Fetch service points and per-channel readings on a schedule."""

    def __init__(self, hass: HomeAssistant, client: EacClient, entry: ConfigEntry) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN} ({entry.title})",
            update_interval=SCAN_INTERVAL,
        )
        self._client = client
        self._entry = entry

    async def _async_update_data(self) -> EacData:
        try:
            user = await self._client.get_user_details()
            sps = await self._client.list_service_points()
        except EacAuthError as err:
            raise UpdateFailed(f"Authentication failed: {err}") from err
        except EacApiError as err:
            raise UpdateFailed(f"API error: {err}") from err

        end = datetime.now(timezone.utc)
        start = end - timedelta(days=HISTORY_DAYS)

        points: dict[str, ServicePointState] = {}
        for sp in sps:
            if not sp.active or not sp.device_exists:
                # Inactive points have no fresh data; we still expose the
                # service point itself so the user can see it in HA.
                points[sp.id] = ServicePointState(
                    service_point=sp, active_meter=None, channels={}
                )
                continue

            try:
                configs = await self._client.get_meter_configs(sp.id)
            except EacApiError as err:
                _LOGGER.warning("Failed to fetch meter config for %s: %s", sp.id, err)
                points[sp.id] = ServicePointState(
                    service_point=sp, active_meter=None, channels={}
                )
                continue

            active_meter = next((c for c in configs if c.removal_date is None), None)
            channels: dict[str, ChannelState] = {}

            if active_meter is not None:
                for ch in active_meter.channels:
                    try:
                        per_channel = await self._client.get_readings(
                            sp.id, start, end, mc_id=ch.id
                        )
                    except EacApiError as err:
                        _LOGGER.debug(
                            "No readings for sp=%s mc=%s: %s", sp.id, ch.id, err
                        )
                        continue
                    latest = _latest_reading(per_channel, ch.id)
                    if latest is not None:
                        channels[ch.id] = ChannelState(channel=ch, latest=latest)

            points[sp.id] = ServicePointState(
                service_point=sp, active_meter=active_meter, channels=channels
            )

        return EacData(user=user, points=points)


def _latest_reading(channels: list[ChannelReadings], channel_id: str) -> Reading | None:
    for cr in channels:
        if cr.channel_id != channel_id or not cr.readings:
            continue
        return max(cr.readings, key=lambda r: r.dt)
    return None
