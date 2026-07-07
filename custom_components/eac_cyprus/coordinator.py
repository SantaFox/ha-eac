"""DataUpdateCoordinator that polls the EAC DSO portal.

Data model is meter-centric: a service point can carry several meters over its
lifetime (the DSO swaps hardware), and we surface each as its own HA device so
a retired meter keeps its history and final reading instead of vanishing. Every
refresh fetches fresh readings for the *active* meter; retired meters never
change, so their final snapshot is fetched once and cached.
"""
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
    channel_label,
)
from .statistics import import_cumulative_history

_LOGGER = logging.getLogger(__name__)


def _as_utc(dt: datetime | None) -> datetime | None:
    """Normalise a (possibly naive) portal datetime to tz-aware UTC."""
    if dt is None:
        return None
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=UTC)


def meter_key(sp_id: str, meter: MeterConfig) -> str:
    """Stable per-meter identifier: service point + serial (or install date)."""
    serial = meter.serial_number or (
        meter.install_date.date().isoformat() if meter.install_date else "unknown"
    )
    return f"{sp_id}_{serial}"


@dataclass(frozen=True, slots=True)
class ChannelState:
    """Current state of a single measurement channel."""
    channel: MeasurementChannel
    latest: Reading | None  # most recent reading we have (cumulative or interval)


@dataclass(frozen=True, slots=True)
class MeterState:
    """One meter on a service point, active or retired, with its channels."""
    service_point: ServicePoint
    meter: MeterConfig
    active: bool  # removal_date is None
    channels: dict[str, ChannelState]  # keyed by channel id
    measured_at: datetime | None  # provider dt of the newest reading (tz-aware UTC)
    received_at: datetime | None  # when this snapshot was fetched (tz-aware UTC)

    @property
    def key(self) -> str:
        return meter_key(self.service_point.id, self.meter)

    @property
    def serial(self) -> str:
        return self.meter.serial_number or self.key.rsplit("_", 1)[-1]


@dataclass(frozen=True, slots=True)
class ServicePointState:
    """Per-service-point snapshot built every refresh."""
    service_point: ServicePoint
    meters: dict[str, MeterState]  # keyed by meter_key()


@dataclass(frozen=True, slots=True)
class EacData:
    """What the coordinator hands to entities."""
    user: UserDetails
    points: dict[str, ServicePointState]  # keyed by service-point id
    last_success_time: datetime  # tz-aware UTC; surfaced via a diagnostic sensor


class EacCoordinator(DataUpdateCoordinator[EacData]):
    """Fetch service points, meters, and per-channel readings on a schedule."""

    def __init__(self, hass: HomeAssistant, client: EacClient, entry: ConfigEntry) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN} ({entry.title})",
            update_interval=_interval_from_options(entry),
        )
        self._client = client
        self._entry = entry
        # Retired meters never change; cache their final snapshot by meter_key.
        self._retired_cache: dict[str, MeterState] = {}

    @property
    def client(self) -> EacClient:
        """The portal client, for one-off operations like full-history import."""
        return self._client

    def apply_options(self) -> None:
        """Re-read options after the options flow. Only cadence needs pushing."""
        self.update_interval = _interval_from_options(self._entry)

    async def _async_update_data(self) -> EacData:
        try:
            user = await self._client.get_user_details()
            sps = await self._client.list_service_points()
        except EacAuthError as err:
            raise UpdateFailed(f"Authentication failed: {err}") from err
        except EacApiError as err:
            raise UpdateFailed(f"API error: {err}") from err

        received_at = datetime.now(UTC)
        history_days = self._entry.options.get(CONF_HISTORY_DAYS, DEFAULT_HISTORY_DAYS)

        points: dict[str, ServicePointState] = {}
        for sp in sps:
            meters: dict[str, MeterState] = {}
            if sp.active and sp.device_exists:
                try:
                    configs = await self._client.get_meter_configs(sp.id)
                except EacApiError as err:
                    _LOGGER.warning("Meter config fetch failed for %s: %s", sp.id, err)
                    configs = []

                for cfg in configs:
                    if cfg.removal_date is None:
                        end = received_at
                        start = end - timedelta(days=history_days)
                        ms = await self._fetch_meter(sp, cfg, start, end, received_at)
                    else:
                        ms = await self._retired_meter(sp, cfg, received_at)
                    meters[ms.key] = ms

            points[sp.id] = ServicePointState(service_point=sp, meters=meters)

        return EacData(
            user=user, points=points, last_success_time=datetime.now(UTC)
        )

    async def _retired_meter(
        self, sp: ServicePoint, cfg: MeterConfig, received_at: datetime
    ) -> MeterState:
        """Final snapshot of a retired meter, fetched once and cached."""
        key = meter_key(sp.id, cfg)
        if key not in self._retired_cache:
            end = _as_utc(cfg.removal_date) or received_at
            start = end - timedelta(days=14)
            self._retired_cache[key] = await self._fetch_meter(
                sp, cfg, start, end, received_at
            )
        return self._retired_cache[key]

    async def _fetch_meter(
        self,
        sp: ServicePoint,
        cfg: MeterConfig,
        start: datetime,
        end: datetime,
        received_at: datetime,
    ) -> MeterState:
        """Fetch every channel's latest reading for one meter."""
        channels: dict[str, ChannelState] = {}
        measured_at: datetime | None = None
        for ch in cfg.channels:
            try:
                per_channel = await self._client.get_readings(
                    sp.id, start, end, mc_id=ch.id
                )
            except EacApiError as err:
                _LOGGER.debug("No readings for sp=%s mc=%s: %s", sp.id, ch.id, err)
                continue
            matching = next(
                (cr for cr in per_channel if cr.channel_id == ch.id), None
            )
            if matching is None or not matching.readings:
                continue
            latest = max(matching.readings, key=lambda r: r.dt)
            channels[ch.id] = ChannelState(channel=ch, latest=latest)
            dt = _as_utc(latest.dt)
            if dt is not None and (measured_at is None or dt > measured_at):
                measured_at = dt

            # Backfill external statistics (the "what actually happened" half)
            # for cumulative kWh channels, named from the meter serial.
            if ch.uom.upper() == "KWH" and not ch.interval:
                serial = cfg.serial_number or (
                    cfg.install_date.date().isoformat()
                    if cfg.install_date
                    else "unknown"
                )
                import_cumulative_history(
                    self.hass,
                    sp.id,
                    ch.id,
                    f"{serial} {channel_label(ch.type)}",
                    matching.readings,
                )

        return MeterState(
            service_point=sp,
            meter=cfg,
            active=cfg.removal_date is None,
            channels=channels,
            measured_at=measured_at,
            received_at=received_at,
        )


def _interval_from_options(entry: ConfigEntry) -> timedelta:
    minutes = entry.options.get(
        CONF_SCAN_INTERVAL_MINUTES, DEFAULT_SCAN_INTERVAL_MINUTES
    )
    return timedelta(minutes=int(minutes))
