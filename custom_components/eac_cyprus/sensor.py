"""Sensor platform for the EAC Cyprus integration.

Devices are meters: one HA device per physical meter on a service point, the
service point itself acting as the parent device. Each meter exposes:

- a "last value" glance per measurement channel (what HA knows right now), and
- per-meter diagnostics: when the newest reading was measured / received, and
  the resulting data lag.

The canonical history lives in external statistics (see statistics.py); the
entities here are the "what does HA know now" half of the picture.
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    UnitOfApparentPower,
    UnitOfEnergy,
    UnitOfPower,
    UnitOfReactivePower,
    UnitOfTime,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, MANUFACTURER, channel_label
from .coordinator import EacCoordinator, MeterState, ServicePoint

_LOGGER = logging.getLogger(__name__)


_INTERVAL_TO_POWER_FACTOR: dict[str, float] = {"30MIN": 2.0}


def _value_description(uom: str, ch_type: str, interval: bool) -> SensorEntityDescription:
    """Describe the 'last value' glance sensor for one channel.

    Cumulative kWh channels are a plain latest-value display with NO state_class
    (the recorder must not auto-compile lag-shifted statistics off them; the
    real history is imported as external statistics). Interval (30-min) kWh
    channels are re-projected to average power.
    """
    uom_upper = (uom or "").upper()
    key = f"{ch_type}".lower().replace("-", "_") or "channel"

    if uom_upper == "KWH" and interval:
        return SensorEntityDescription(
            key=key,
            name=channel_label(ch_type),
            native_unit_of_measurement=UnitOfPower.KILO_WATT,
            device_class=SensorDeviceClass.POWER,
            state_class=SensorStateClass.MEASUREMENT,
            suggested_display_precision=2,
        )
    if uom_upper == "KWH":
        return SensorEntityDescription(
            key=key,
            name=channel_label(ch_type),
            native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
            device_class=SensorDeviceClass.ENERGY,
            suggested_display_precision=0,
        )
    if uom_upper == "KVAH":
        return SensorEntityDescription(
            key=key,
            name=channel_label(ch_type),
            native_unit_of_measurement=UnitOfApparentPower.VOLT_AMPERE,
            entity_registry_enabled_default=False,
        )
    if uom_upper in ("KVARH", "KVRH"):
        return SensorEntityDescription(
            key=key,
            name=channel_label(ch_type),
            native_unit_of_measurement=UnitOfReactivePower.VOLT_AMPERE_REACTIVE,
            entity_registry_enabled_default=False,
        )
    return SensorEntityDescription(
        key=key,
        name=_label(ch_type),
        native_unit_of_measurement=uom,
        entity_registry_enabled_default=False,
    )


def sp_device_info(sp: ServicePoint) -> DeviceInfo:
    """Parent device: the permanent service point (supply contract at an address)."""
    return DeviceInfo(
        identifiers={(DOMAIN, sp.id)},
        name=sp.address or f"EAC service point {sp.id}",
        manufacturer=MANUFACTURER,
        model="Service point",
        entry_type=DeviceEntryType.SERVICE,
        configuration_url=f"https://meterreading-dso.eac.com.cy/sp/{sp.id}",
    )


def meter_device_info(ms: MeterState) -> DeviceInfo:
    """Child device: one physical meter, named by its serial, under the SP."""
    m = ms.meter
    name = m.serial_number or ms.key
    if m.manufacturer:
        name = f"{m.manufacturer} {name}"
    return DeviceInfo(
        identifiers={(DOMAIN, ms.key)},
        name=name,
        manufacturer=m.manufacturer or MANUFACTURER,
        model=m.model,
        serial_number=m.serial_number,
        via_device=(DOMAIN, ms.service_point.id),
    )


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Create meter devices and their sensors; watch for new meters/channels."""
    coordinator: EacCoordinator = hass.data[DOMAIN][entry.entry_id]
    device_reg = dr.async_get(hass)

    known_channels: set[tuple[str, str]] = set()  # (meter_key, channel_id)
    known_meters: set[str] = set()  # meter_key (diagnostics created once)
    known_sps: set[str] = set()

    @callback
    def _add_new_entities() -> None:
        new: list[CoordinatorEntity[EacCoordinator]] = []
        for sp_state in coordinator.data.points.values():
            sp = sp_state.service_point
            if sp_state.meters and sp.id not in known_sps:
                known_sps.add(sp.id)
                device_reg.async_get_or_create(
                    config_entry_id=entry.entry_id, **sp_device_info(sp)
                )
            for ms in sp_state.meters.values():
                for ch_state in ms.channels.values():
                    key = (ms.key, ch_state.channel.id)
                    if key in known_channels:
                        continue
                    known_channels.add(key)
                    new.append(EacChannelSensor(coordinator, sp.id, ms.key, ch_state.channel.id))
                # Per-meter diagnostics, only for the active meter.
                if ms.active and ms.key not in known_meters:
                    known_meters.add(ms.key)
                    new.extend(
                        cls(coordinator, sp.id, ms.key)
                        for cls in (EacMeasuredAtSensor, EacReceivedAtSensor, EacLagSensor)
                    )
        if new:
            async_add_entities(new)

    _add_new_entities()
    entry.async_on_unload(coordinator.async_add_listener(_add_new_entities))


class _MeterEntity(CoordinatorEntity[EacCoordinator]):
    """Base: resolves the MeterState for an entity from coordinator data."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: EacCoordinator, sp_id: str, meter_key: str) -> None:
        super().__init__(coordinator)
        self._sp_id = sp_id
        self._meter_key = meter_key

    @property
    def _meter(self) -> MeterState | None:
        sp_state = self.coordinator.data.points.get(self._sp_id)
        if sp_state is None:
            return None
        return sp_state.meters.get(self._meter_key)


class EacChannelSensor(_MeterEntity, SensorEntity):
    """Latest known value of one channel on one meter."""

    def __init__(
        self, coordinator: EacCoordinator, sp_id: str, meter_key: str, channel_id: str
    ) -> None:
        super().__init__(coordinator, sp_id, meter_key)
        self._channel_id = channel_id
        ms = self._meter
        ch = ms.channels[channel_id].channel
        self.entity_description = _value_description(ch.uom, ch.type, ch.interval)
        self._attr_unique_id = f"{meter_key}_{channel_id}"
        self._attr_device_info = meter_device_info(ms)

    @property
    def _ch(self):
        ms = self._meter
        return ms.channels.get(self._channel_id) if ms else None

    @property
    def available(self) -> bool:
        ch = self._ch
        return super().available and ch is not None and ch.latest is not None

    @property
    def native_value(self) -> float | None:
        ch = self._ch
        if ch is None or ch.latest is None:
            return None
        latest = ch.latest
        if latest.reading is not None:
            return latest.reading
        factor = _INTERVAL_TO_POWER_FACTOR.get(ch.channel.tou)
        return latest.value * factor if factor is not None else latest.value

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        ch = self._ch
        if ch is None or ch.latest is None:
            return {}
        attrs: dict[str, Any] = {
            "channel_id": self._channel_id,
            "channel_type": ch.channel.type,
            "tou": ch.channel.tou,
            "interval": ch.channel.interval,
            "measured_at": _iso(ch.latest.dt),
        }
        if ch.latest.reading is not None:
            attrs["cumulative_reading"] = ch.latest.reading
        return attrs


class EacMeasuredAtSensor(_MeterEntity, SensorEntity):
    """When the newest reading was measured (provider timestamp)."""

    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _attr_name = "Data measured at"

    def __init__(self, coordinator: EacCoordinator, sp_id: str, meter_key: str) -> None:
        super().__init__(coordinator, sp_id, meter_key)
        self._attr_unique_id = f"{meter_key}_measured_at"
        self._attr_device_info = meter_device_info(self._meter)

    @property
    def native_value(self) -> datetime | None:
        ms = self._meter
        return ms.measured_at if ms else None


class EacReceivedAtSensor(_MeterEntity, SensorEntity):
    """When HA last fetched fresh data for this meter."""

    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _attr_name = "Data received at"

    def __init__(self, coordinator: EacCoordinator, sp_id: str, meter_key: str) -> None:
        super().__init__(coordinator, sp_id, meter_key)
        self._attr_unique_id = f"{meter_key}_received_at"
        self._attr_device_info = meter_device_info(self._meter)

    @property
    def native_value(self) -> datetime | None:
        ms = self._meter
        return ms.received_at if ms else None


class EacLagSensor(_MeterEntity, SensorEntity):
    """Age of the newest reading = received_at - measured_at, in hours."""

    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_device_class = SensorDeviceClass.DURATION
    _attr_native_unit_of_measurement = UnitOfTime.HOURS
    _attr_suggested_display_precision = 1
    _attr_name = "Data lag"

    def __init__(self, coordinator: EacCoordinator, sp_id: str, meter_key: str) -> None:
        super().__init__(coordinator, sp_id, meter_key)
        self._attr_unique_id = f"{meter_key}_data_lag"
        self._attr_device_info = meter_device_info(self._meter)

    @property
    def native_value(self) -> float | None:
        ms = self._meter
        if ms is None or ms.measured_at is None or ms.received_at is None:
            return None
        return (ms.received_at - ms.measured_at).total_seconds() / 3600.0


def _iso(dt: datetime | None) -> str | None:
    return dt.isoformat() if dt is not None else None
