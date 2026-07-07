"""Binary sensor platform: per-meter data-staleness diagnostic.

The portal publishes with a 2-3 day lag, so "no fresh reading" is only a real
problem once it exceeds a configurable threshold (``stale_threshold_days``).
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import CONF_STALE_THRESHOLD_DAYS, DEFAULT_STALE_THRESHOLD_DAYS, DOMAIN
from .coordinator import EacCoordinator
from .sensor import _MeterEntity, meter_device_info


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """One staleness sensor per active meter."""
    coordinator: EacCoordinator = hass.data[DOMAIN][entry.entry_id]
    known: set[str] = set()

    @callback
    def _add_new() -> None:
        new: list[EacStaleSensor] = []
        for sp_state in coordinator.data.points.values():
            for ms in sp_state.meters.values():
                if ms.active and ms.key not in known:
                    known.add(ms.key)
                    new.append(
                        EacStaleSensor(
                            coordinator, sp_state.service_point.id, ms.key, entry
                        )
                    )
        if new:
            async_add_entities(new)

    _add_new()
    entry.async_on_unload(coordinator.async_add_listener(_add_new))


class EacStaleSensor(_MeterEntity, BinarySensorEntity):
    """On when the newest reading is older than the configured threshold."""

    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_device_class = BinarySensorDeviceClass.PROBLEM
    _attr_name = "Data stale"

    def __init__(
        self,
        coordinator: EacCoordinator,
        sp_id: str,
        meter_key: str,
        entry: ConfigEntry,
    ) -> None:
        super().__init__(coordinator, sp_id, meter_key)
        self._entry = entry
        self._attr_unique_id = f"{meter_key}_data_stale"
        self._attr_device_info = meter_device_info(self._meter)

    @property
    def is_on(self) -> bool | None:
        ms = self._meter
        if ms is None or ms.measured_at is None:
            return None
        threshold_days = self._entry.options.get(
            CONF_STALE_THRESHOLD_DAYS, DEFAULT_STALE_THRESHOLD_DAYS
        )
        return (datetime.now(UTC) - ms.measured_at) > timedelta(days=threshold_days)
