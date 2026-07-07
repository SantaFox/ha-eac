"""Tests for the one-time full-history import."""
from __future__ import annotations

from datetime import datetime
from unittest.mock import patch

from homeassistant.core import HomeAssistant


async def test_import_full_history_imports_cumulative_channels(
    hass: HomeAssistant, mock_client
) -> None:
    """Full import writes stats for each cumulative channel of the active meter."""
    from custom_components.eac_cyprus.history import async_import_full_history

    with patch(
        "custom_components.eac_cyprus.history.import_cumulative_history"
    ) as mock_import:
        results = await async_import_full_history(hass, mock_client)

    # One cumulative kWh channel (mc-total-24h); the 30-min interval channel and
    # the inactive service point are skipped.
    assert len(results) == 1
    assert results[0]["channel"] == "S-KWH-24H"
    assert results[0]["meter"] == "NEW123"
    mock_import.assert_called_once()
    _hass, sp_id, ch_id, display_name, _readings = mock_import.call_args.args
    assert sp_id == "111111111111"
    assert ch_id == "mc-total-24h"
    # Named from the meter serial, not the transliterated address.
    assert display_name.startswith("NEW123 ")


async def test_import_full_history_spans_install_window(
    hass: HomeAssistant, mock_client
) -> None:
    """Readings are fetched from the meter's install date, not a rolling window."""
    from custom_components.eac_cyprus.history import async_import_full_history

    with patch("custom_components.eac_cyprus.history.import_cumulative_history"):
        await async_import_full_history(hass, mock_client)

    calls = [
        c
        for c in mock_client.get_readings.call_args_list
        if c.kwargs.get("mc_id") == "mc-total-24h"
    ]
    assert calls
    # fake_active_meter install_date is 2026-02-02; start should be that date.
    start = calls[-1].args[1]
    assert isinstance(start, datetime)
    assert (start.year, start.month, start.day) == (2026, 2, 2)
