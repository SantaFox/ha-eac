"""Tests for the long-term-statistics backfill module."""
from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import patch

from eac_dso_portal import Reading
from homeassistant.const import UnitOfEnergy
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.eac_cyprus.const import CONF_EMAIL, CONF_PASSWORD, DOMAIN
from custom_components.eac_cyprus.statistics import (
    external_statistic_id,
    import_cumulative_history,
)


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
        Reading(dt=datetime.fromisoformat(dt), reading=r, value=v) for dt, r, v in pairs
    )


def test_external_statistic_id_format() -> None:
    """The ID must contain a colon and start with the integration domain.
    HA's recorder rejects external statistic_ids that do not follow that
    pattern."""
    sid = external_statistic_id("863224497404", "129343890428")
    assert sid == "eac_cyprus:863224497404_129343890428"
    assert ":" in sid


async def test_no_op_when_no_cumulative_readings(hass: HomeAssistant) -> None:
    """Interval channels (reading=None) produce no statistics."""
    interval_only = _readings(
        ("2026-05-01T01:00:00", None, 0.5),
        ("2026-05-01T01:30:00", None, 0.6),
    )
    with patch(
        "custom_components.eac_cyprus.statistics.async_add_external_statistics"
    ) as mock_add:
        import_cumulative_history(
            hass, "111", "mc-30min", "KWH-30MIN-LP-IMP", "1 Test St", interval_only
        )
    mock_add.assert_not_called()


async def test_imports_cumulative_kwh_with_correct_metadata(
    hass: HomeAssistant,
) -> None:
    """Verify the metadata and StatisticData rows we hand to the recorder."""
    rdgs = _readings(
        ("2026-05-01T00:00:00", 2900.0, 23.0),
        ("2026-05-02T00:00:00", 2924.0, 24.0),
        ("2026-05-03T00:00:00", 2950.0, 26.0),
    )

    with patch(
        "custom_components.eac_cyprus.statistics.async_add_external_statistics"
    ) as mock_add:
        import_cumulative_history(
            hass, "111", "mc-total", "S-KWH-24H", "1 Test St", rdgs
        )

    mock_add.assert_called_once()
    _, metadata, stats = mock_add.call_args.args
    assert metadata["statistic_id"] == "eac_cyprus:111_mc-total"
    assert metadata["source"] == DOMAIN
    assert metadata["has_sum"] is True
    assert metadata["has_mean"] is False
    assert metadata["unit_of_measurement"] == UnitOfEnergy.KILO_WATT_HOUR
    assert "EAC" in metadata["name"]
    assert "1 Test St" in metadata["name"]
    assert "S-KWH-24H" in metadata["name"]

    assert len(stats) == 3
    for s, expected_reading in zip(stats, [2900.0, 2924.0, 2950.0], strict=True):
        assert s["start"].tzinfo is UTC
        assert s["start"].minute == 0 and s["start"].second == 0
        assert s["sum"] == expected_reading
        # External statistics do not carry `state`; we only push `sum`.
        assert "state" not in s


async def test_imports_work_without_known_address(hass: HomeAssistant) -> None:
    """When the service point has no address yet, the name still makes sense."""
    rdgs = _readings(("2026-05-01T00:00:00", 100.0, 5.0))
    with patch(
        "custom_components.eac_cyprus.statistics.async_add_external_statistics"
    ) as mock_add:
        import_cumulative_history(hass, "111", "mc-x", "S-KWH-24H", "", rdgs)
    metadata = mock_add.call_args.args[1]
    assert metadata["name"] == "EAC S-KWH-24H"


async def test_coordinator_pushes_stats_for_cumulative_channel_only(
    hass: HomeAssistant, mock_client
) -> None:
    """Integration check: cumulative channel triggers import, 30-min channel does not."""
    from custom_components.eac_cyprus.coordinator import EacCoordinator

    entry = _entry(hass)
    coord = EacCoordinator(hass, mock_client, entry)
    with patch(
        "custom_components.eac_cyprus.coordinator.import_cumulative_history"
    ) as mock_import:
        await coord._async_update_data()

    called_channels = [
        c.kwargs.get("channel_id") or c.args[2] for c in mock_import.call_args_list
    ]
    assert "mc-total-24h" in called_channels
    assert "mc-30min-imp" not in called_channels
    # The address is passed through so the metadata name is meaningful.
    for c in mock_import.call_args_list:
        # signature: (hass, sp_id, channel_id, channel_name, address, readings)
        assert c.args[4] == "1 Test St, City"
