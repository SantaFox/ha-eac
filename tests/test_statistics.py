"""Tests for the long-term-statistics backfill module."""
from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import patch

import pytest
from eac_dso_portal import Reading
from homeassistant.const import UnitOfEnergy
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.eac_cyprus.const import CONF_EMAIL, CONF_PASSWORD, DOMAIN
from custom_components.eac_cyprus.statistics import import_cumulative_history


def _entry(hass: HomeAssistant) -> MockConfigEntry:
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="test@example.com",
        data={CONF_EMAIL: "test@example.com", CONF_PASSWORD: "x"},
        title="Test User",
    )
    entry.add_to_hass(hass)
    return entry


def _readings(*pairs: tuple[str, float | None, float]) -> tuple[Reading, ...]:
    """Build Readings from (iso_dt, reading|None, value) triples."""
    return tuple(
        Reading(dt=datetime.fromisoformat(dt), reading=r, value=v)
        for dt, r, v in pairs
    )


async def test_no_op_when_entity_not_registered(hass: HomeAssistant) -> None:
    """First-run safety: skip silently if the sensor is not in the registry yet."""
    rdgs = _readings(("2026-05-01T00:00:00", 100.0, 5.0))
    with patch(
        "custom_components.eac_cyprus.statistics.async_import_statistics"
    ) as mock_import:
        import_cumulative_history(hass, "999", "mc-X", "S-KWH-24H", rdgs)
    mock_import.assert_not_called()


async def test_no_op_when_no_cumulative_readings(hass: HomeAssistant) -> None:
    """Interval channels (reading=None) produce no statistics."""
    registry = er.async_get(hass)
    registry.async_get_or_create("sensor", DOMAIN, "111_mc-30min")
    interval_only = _readings(
        ("2026-05-01T01:00:00", None, 0.5),
        ("2026-05-01T01:30:00", None, 0.6),
    )
    with patch(
        "custom_components.eac_cyprus.statistics.async_import_statistics"
    ) as mock_import:
        import_cumulative_history(hass, "111", "mc-30min", "KWH-30MIN-LP-IMP", interval_only)
    mock_import.assert_not_called()


async def test_imports_cumulative_kwh_with_correct_metadata(
    hass: HomeAssistant,
) -> None:
    registry = er.async_get(hass)
    entry = registry.async_get_or_create("sensor", DOMAIN, "111_mc-total")
    rdgs = _readings(
        ("2026-05-01T00:00:00", 2900.0, 23.0),
        ("2026-05-02T00:00:00", 2924.0, 24.0),
        ("2026-05-03T00:00:00", 2950.0, 26.0),
    )

    with patch(
        "custom_components.eac_cyprus.statistics.async_import_statistics"
    ) as mock_import:
        import_cumulative_history(hass, "111", "mc-total", "S-KWH-24H", rdgs)

    mock_import.assert_called_once()
    _, metadata, stats = mock_import.call_args.args
    assert metadata["statistic_id"] == entry.entity_id
    assert metadata["source"] == "recorder"
    assert metadata["has_sum"] is True
    assert metadata["has_mean"] is False
    assert metadata["unit_of_measurement"] == UnitOfEnergy.KILO_WATT_HOUR
    assert len(stats) == 3
    # Each stat has tz-aware UTC start at the hour boundary, sum and state set.
    for s, expected_reading in zip(stats, [2900.0, 2924.0, 2950.0], strict=True):
        assert s["start"].tzinfo is UTC
        assert s["start"].minute == 0 and s["start"].second == 0
        assert s["sum"] == expected_reading
        assert s["state"] == expected_reading


async def test_coordinator_pushes_stats_for_cumulative_channel_only(
    hass: HomeAssistant, mock_client
) -> None:
    """Integration check: cumulative channel triggers import, 30-min channel does not."""
    from custom_components.eac_cyprus.coordinator import EacCoordinator

    entry = _entry(hass)

    # Pre-register the two sensors so import has somewhere to attach.
    registry = er.async_get(hass)
    registry.async_get_or_create("sensor", DOMAIN, "111111111111_mc-total-24h")
    registry.async_get_or_create("sensor", DOMAIN, "111111111111_mc-30min-imp")

    coord = EacCoordinator(hass, mock_client, entry)
    with patch(
        "custom_components.eac_cyprus.coordinator.import_cumulative_history"
    ) as mock_import:
        await coord._async_update_data()

    # The shared mock_client returns a 30-min reading (no `reading`) and a
    # daily total (with `reading`); only the latter qualifies.
    called_channels = [c.kwargs.get("channel_id") or c.args[2] for c in mock_import.call_args_list]
    assert "mc-total-24h" in called_channels
    assert "mc-30min-imp" not in called_channels


@pytest.mark.usefixtures("hass")
def _unused_marker():
    """Anchor for collecting pytest-homeassistant-custom-component fixtures."""
    pass
