"""Tests for the EAC Cyprus DataUpdateCoordinator."""
from __future__ import annotations

from unittest.mock import AsyncMock

from eac_dso_portal import EacApiError, EacAuthError
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import UpdateFailed
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.eac_cyprus.const import CONF_EMAIL, CONF_PASSWORD, DOMAIN
from custom_components.eac_cyprus.coordinator import EacCoordinator


def _make_entry(hass: HomeAssistant) -> MockConfigEntry:
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="test@example.com",
        data={CONF_EMAIL: "test@example.com", CONF_PASSWORD: "x"},
        title="Test",
    )
    entry.add_to_hass(hass)
    return entry


async def test_coordinator_update_active_and_inactive_points(
    hass: HomeAssistant, mock_client
) -> None:
    entry = _make_entry(hass)
    coord = EacCoordinator(hass, mock_client, entry)
    data = await coord._async_update_data()

    assert data.user.name == "Test User"
    assert set(data.points) == {"111111111111", "222222222222"}

    active = data.points["111111111111"]
    assert active.active_meter is not None
    assert active.active_meter.serial_number == "NEW123"
    # Both channels (total + 30min) should produce a ChannelState
    assert set(active.channels) == {"mc-total-24h", "mc-30min-imp"}

    inactive = data.points["222222222222"]
    assert inactive.active_meter is None
    assert inactive.channels == {}


async def test_coordinator_update_failed_on_auth_error(
    hass: HomeAssistant, mock_client
) -> None:
    mock_client.list_service_points.side_effect = EacAuthError("expired")
    entry = _make_entry(hass)
    coord = EacCoordinator(hass, mock_client, entry)

    try:
        await coord._async_update_data()
    except UpdateFailed:
        pass
    else:
        raise AssertionError("UpdateFailed was not raised")


async def test_coordinator_skips_failing_service_point(
    hass: HomeAssistant, mock_client
) -> None:
    """If get_meter_configs fails for one SP, others still come through."""
    mock_client.get_meter_configs.side_effect = EacApiError(
        500, "boom", "/api/portal/servicePoints/x"
    )
    entry = _make_entry(hass)
    coord = EacCoordinator(hass, mock_client, entry)
    data = await coord._async_update_data()

    # Both SPs still listed, but the active one has no channels because
    # we couldn't fetch its config.
    assert set(data.points) == {"111111111111", "222222222222"}
    assert data.points["111111111111"].channels == {}


async def test_coordinator_drops_channels_without_data(
    hass: HomeAssistant, mock_client, fake_active_meter
) -> None:
    """Channels that the API has nothing for don't become sensors."""
    # Make get_readings raise for the 30-min channel only.
    real_side_effect = mock_client.get_readings.side_effect
    raise_for: set[str] = {"mc-30min-imp"}

    async def selective(sp_id, start, end, mc_id=None):
        if mc_id in raise_for:
            raise EacApiError(404, "no data", "/api/portal/readings/list")
        return real_side_effect(sp_id, start, end, mc_id=mc_id)

    mock_client.get_readings = AsyncMock(side_effect=selective)

    entry = _make_entry(hass)
    coord = EacCoordinator(hass, mock_client, entry)
    data = await coord._async_update_data()

    # Only the channel that returned data is present.
    assert set(data.points["111111111111"].channels) == {"mc-total-24h"}
