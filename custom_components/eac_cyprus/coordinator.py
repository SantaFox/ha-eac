"""DataUpdateCoordinator that polls the EAC DSO portal."""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from eac_dso_portal import (
    EacApiError,
    EacAuthError,
    EacClient,
    MeasurementChannel,
    MeterConfig,
    Reading,
    ServicePoint,
    UserDetails,
)
from .const import (
    CONF_HISTORY_DAYS,
    CONF_SCAN_INTERVAL_MINUTES,
    DEFAULT_HISTORY_DAYS,
    DEFAULT_SCAN_INTERVAL_MINUTES,
    DOMAIN,
)
from .statistics import import_cumulative_history

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
    last_success_time: datetime  # tz-aware UTC; surfaced via a diagnostic sensor


class EacCoordinator(DataUpdateCoordinator[EacData]):
    """Fetch service points and per-channel readings on a schedule."""

    def __init__(self, hass: HomeAssistant, client: EacClient, entry: ConfigEntry) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN} ({entry.title})",
            update_interval=_interval_from_options(entry),
        )
        self._client = client
        self._entry = entry

    def apply_options(self) -> None:
        """Re-read the config entry's options and adjust polling cadence.

        Called by the options-flow update listener. ``history_days`` is read
        on every refresh, so only the poll interval needs to be propagated
        here. The new value takes effect on the next scheduled tick.
        """
        self.update_interval = _interval_from_options(self._entry)

    async def _async_update_data(self) -> EacData:
        try:
            user = await self._client.get_user_details()
            sps = await self._client.list_service_points()
        except EacAuthError as err:
            raise UpdateFailed(f"Authentication failed: {err}") from err
        except EacApiError as err:
            raise UpdateFailed(f"API error: {err}") from err

        end = datetime.now(UTC)
        history_days = self._entry.options.get(CONF_HISTORY_DAYS, DEFAULT_HISTORY_DAYS)
        start = end - timedelta(days=history_days)

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
                    matching = next(
                        (cr for cr in per_channel if cr.channel_id == ch.id), None
                    )
                    if matching is None or not matching.readings:
                        continue
                    latest = max(matching.readings, key=lambda r: r.dt)
                    channels[ch.id] = ChannelState(channel=ch, latest=latest)
                    # Backfill long-term statistics for cumulative kWh channels
                    # so the Energy Dashboard graph stays continuous between
                    # polls. Idempotent — safe to call on every refresh.
                    if ch.uom.upper() == "KWH" and not ch.interval:
                        import_cumulative_history(
                            self.hass,
                            sp.id,
                            ch.id,
                            ch.type,
                            sp.address,
                            matching.readings,
                        )

            points[sp.id] = ServicePointState(
                service_point=sp, active_meter=active_meter, channels=channels
            )

        return EacData(user=user, points=points, last_success_time=datetime.now(UTC))


def _interval_from_options(entry: ConfigEntry) -> timedelta:
    minutes = entry.options.get(
        CONF_SCAN_INTERVAL_MINUTES, DEFAULT_SCAN_INTERVAL_MINUTES
    )
    return timedelta(minutes=int(minutes))
