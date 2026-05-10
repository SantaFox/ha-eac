"""Tests for the eac_cyprus sensor platform."""
from __future__ import annotations

import pytest
from homeassistant.components.sensor import SensorDeviceClass, SensorStateClass
from homeassistant.const import UnitOfEnergy
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.eac_cyprus.const import CONF_EMAIL, CONF_PASSWORD, DOMAIN


async def _setup_integration(
    hass: HomeAssistant, patch_eac_client
) -> MockConfigEntry:
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="test@example.com",
        data={CONF_EMAIL: "test@example.com", CONF_PASSWORD: "x"},
        title="Test User",
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


async def test_full_setup_creates_sensors(
    hass: HomeAssistant, patch_eac_client
) -> None:
    await _setup_integration(hass, patch_eac_client)

    states = [
        s for s in hass.states.async_all() if s.entity_id.startswith("sensor.")
    ]
    # One sensor per channel that returned data — 2 channels for the active SP.
    assert len(states) == 2

    # Check the cumulative total sensor.
    total = next(s for s in states if "total_24h" in s.entity_id)
    assert total.state == "2974.0"
    assert total.attributes["unit_of_measurement"] == UnitOfEnergy.KILO_WATT_HOUR
    assert total.attributes["device_class"] == SensorDeviceClass.ENERGY
    assert total.attributes["state_class"] == SensorStateClass.TOTAL_INCREASING
    assert total.attributes["channel_type"] == "S-KWH-24H"
    assert "last_cumulative_reading" in total.attributes


async def test_30min_channel_is_power(
    hass: HomeAssistant, patch_eac_client
) -> None:
    """30-min interval channels are re-projected to average power (kW)."""
    from homeassistant.const import UnitOfPower

    await _setup_integration(hass, patch_eac_client)

    state = next(s for s in hass.states.async_all() if "30_min" in s.entity_id)
    assert state.attributes["state_class"] == SensorStateClass.MEASUREMENT
    assert state.attributes["device_class"] == SensorDeviceClass.POWER
    assert state.attributes["unit_of_measurement"] == UnitOfPower.KILO_WATT
    assert state.attributes["interval"] is True
    # 0.315 kWh in 30 min = 0.63 kW average
    assert float(state.state) == pytest.approx(0.63)
    # Interval channels have no cumulative reading attribute
    assert "last_cumulative_reading" not in state.attributes


async def test_unload_marks_entities_unavailable(
    hass: HomeAssistant, patch_eac_client
) -> None:
    """After unload the entry is in NOT_LOADED state and entities go unavailable."""
    from homeassistant.config_entries import ConfigEntryState

    entry = await _setup_integration(hass, patch_eac_client)
    assert entry.state == ConfigEntryState.LOADED

    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()

    assert entry.state == ConfigEntryState.NOT_LOADED
    # Entities still registered but report as unavailable.
    states = [
        s for s in hass.states.async_all() if s.entity_id.startswith("sensor.")
    ]
    assert states, "expected restored entity stubs after unload"
    for s in states:
        assert s.state == "unavailable"
