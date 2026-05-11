"""Sensor platform for the EAC Cyprus integration.

Sensors are created dynamically per service point × per measurement channel
that returned data on the latest poll. When the meter starts reporting new
channels (e.g. ``S-KWH-EXP`` after a PV install) those sensors appear on the
next refresh without re-configuring the integration.
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
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, MANUFACTURER
from .coordinator import EacCoordinator, ServicePointState

_LOGGER = logging.getLogger(__name__)


# Human labels for the channel types EAC exposes. The keys mirror mcList[].type
# from /api/portal/servicePoints/{id}.
_CHANNEL_LABELS: dict[str, str] = {
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

# kWh consumed in a fixed-length slot is meaningful as average power: a slot
# of length T hours consuming E kWh means average power E / T kW. For 30-min
# slots that is 2 × the kWh value.
_INTERVAL_TO_POWER_FACTOR: dict[str, float] = {
    "30MIN": 2.0,
}


def _label(ch_type: str) -> str:
    return _CHANNEL_LABELS.get(ch_type, ch_type)


def _description_for(uom: str, ch_type: str, interval: bool) -> SensorEntityDescription:
    """Build the SensorEntityDescription for one channel.

    Cumulative kWh channels become ``total_increasing`` so they feed straight
    into Home Assistant's Energy Dashboard. Interval (30-min) kWh channels are
    re-projected to average power (kW) since HA's sensor contract forbids
    ``device_class=energy`` with ``state_class=measurement``; the conversion
    happens in :meth:`EacChannelSensor.native_value`.
    """
    uom_upper = (uom or "").upper()
    key = f"{ch_type}".lower().replace("-", "_") or "channel"

    if uom_upper == "KWH" and interval:
        return SensorEntityDescription(
            key=key,
            name=_label(ch_type),
            native_unit_of_measurement=UnitOfPower.KILO_WATT,
            device_class=SensorDeviceClass.POWER,
            state_class=SensorStateClass.MEASUREMENT,
            suggested_display_precision=2,
        )
    if uom_upper == "KWH":
        # No state_class on cumulative kWh sensors: the EAC portal publishes
        # data with a 2-3 day lag, so HA's auto-recorder would write hourly
        # statistics off the "latest known value" and clash with our
        # backfilled history. The canonical long-term data for these
        # channels lives in external statistics under
        # eac_cyprus:<sp>_<channel> and is what Energy Dashboard should
        # pull from. The sensor entity here is just a "latest known value"
        # display.
        return SensorEntityDescription(
            key=key,
            translation_key=key,
            name=_label(ch_type),
            native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
            device_class=SensorDeviceClass.ENERGY,
            suggested_display_precision=0,
        )
    if uom_upper == "KVAH":
        return SensorEntityDescription(
            key=key,
            name=_label(ch_type),
            native_unit_of_measurement=UnitOfApparentPower.VOLT_AMPERE,
            entity_registry_enabled_default=False,
        )
    if uom_upper in ("KVARH", "KVRH"):
        return SensorEntityDescription(
            key=key,
            name=_label(ch_type),
            native_unit_of_measurement=UnitOfReactivePower.VOLT_AMPERE_REACTIVE,
            entity_registry_enabled_default=False,
        )
    return SensorEntityDescription(
        key=key,
        name=_label(ch_type),
        native_unit_of_measurement=uom,
        entity_registry_enabled_default=False,
    )


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Create sensors for all currently-known channels and watch for new ones."""
    coordinator: EacCoordinator = hass.data[DOMAIN][entry.entry_id]

    known_channels: set[tuple[str, str]] = set()  # (service_point_id, channel_id)
    known_diagnostic: set[str] = set()  # service_point_id

    @callback
    def _add_new_entities() -> None:
        new_entities: list[CoordinatorEntity[EacCoordinator]] = []
        for sp_state in coordinator.data.points.values():
            for ch_state in sp_state.channels.values():
                key = (sp_state.service_point.id, ch_state.channel.id)
                if key in known_channels:
                    continue
                known_channels.add(key)
                new_entities.append(
                    EacChannelSensor(coordinator, sp_state, ch_state.channel.id)
                )
            # One diagnostic sensor per device (= per active service point that
            # produced a meter config). Inactive points don't get a device, so
            # they don't get one either.
            if (
                sp_state.active_meter is not None
                and sp_state.service_point.id not in known_diagnostic
            ):
                known_diagnostic.add(sp_state.service_point.id)
                new_entities.append(EacLastUpdateSensor(coordinator, sp_state))
        if new_entities:
            async_add_entities(new_entities)

    _add_new_entities()
    entry.async_on_unload(coordinator.async_add_listener(_add_new_entities))


def _device_info_for(sp_state: ServicePointState) -> DeviceInfo:
    meter = sp_state.active_meter
    return DeviceInfo(
        identifiers={(DOMAIN, sp_state.service_point.id)},
        name=sp_state.service_point.address or f"EAC {sp_state.service_point.id}",
        manufacturer=meter.manufacturer if meter else MANUFACTURER,
        model=meter.model if meter else None,
        serial_number=meter.serial_number if meter else sp_state.service_point.serial_number,
        configuration_url=(
            f"https://meterreading-dso.eac.com.cy/sp/{sp_state.service_point.id}"
        ),
    )


class EacChannelSensor(CoordinatorEntity[EacCoordinator], SensorEntity):
    """One reading channel of one service point."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: EacCoordinator,
        sp_state: ServicePointState,
        channel_id: str,
    ) -> None:
        super().__init__(coordinator)
        self._sp_id = sp_state.service_point.id
        self._channel_id = channel_id
        ch_state = sp_state.channels[channel_id]
        ch = ch_state.channel
        self.entity_description = _description_for(ch.uom, ch.type, ch.interval)
        self._attr_unique_id = f"{self._sp_id}_{self._channel_id}"

        self._attr_device_info = _device_info_for(sp_state)

    @property
    def _ch_state(self):
        sp_state = self.coordinator.data.points.get(self._sp_id)
        if sp_state is None:
            return None
        return sp_state.channels.get(self._channel_id)

    @property
    def available(self) -> bool:
        return super().available and self._ch_state is not None and self._ch_state.latest is not None

    @property
    def native_value(self) -> float | None:
        ch_state = self._ch_state
        if ch_state is None or ch_state.latest is None:
            return None
        latest = ch_state.latest
        # Cumulative channels report their running meter total directly.
        if latest.reading is not None:
            return latest.reading
        # Interval channels (e.g. 30-min load profile) get re-projected to
        # average power: kWh consumed in T hours = kW average for that slot.
        ch = ch_state.channel
        factor = _INTERVAL_TO_POWER_FACTOR.get(ch.tou)
        if factor is not None:
            return latest.value * factor
        return latest.value

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        ch_state = self._ch_state
        if ch_state is None or ch_state.latest is None:
            return {}
        latest = ch_state.latest
        attrs: dict[str, Any] = {
            "channel_id": self._channel_id,
            "channel_type": ch_state.channel.type,
            "tou": ch_state.channel.tou,
            "interval": ch_state.channel.interval,
            "last_reading_at": _iso(latest.dt),
            "last_slot_value": latest.value,
        }
        if latest.reading is not None:
            attrs["last_cumulative_reading"] = latest.reading
        return attrs


def _iso(dt: datetime | None) -> str | None:
    return dt.isoformat() if dt is not None else None


class EacLastUpdateSensor(CoordinatorEntity[EacCoordinator], SensorEntity):
    """Diagnostic timestamp showing when the coordinator last refreshed.

    Lets users distinguish "polling is alive but the DSO has no fresh data"
    from "polling is broken" without digging into logs. Reports the same
    value across every device the integration owns — there is one polling
    cycle for the whole config entry.
    """

    _attr_has_entity_name = True
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _attr_name = "Last successful update"

    def __init__(
        self, coordinator: EacCoordinator, sp_state: ServicePointState
    ) -> None:
        super().__init__(coordinator)
        self._sp_id = sp_state.service_point.id
        self._attr_unique_id = f"{self._sp_id}_last_update"
        self._attr_device_info = _device_info_for(sp_state)

    @property
    def native_value(self) -> datetime | None:
        if self.coordinator.data is None:
            return None
        return self.coordinator.data.last_success_time
