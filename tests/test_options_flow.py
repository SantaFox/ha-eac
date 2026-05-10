"""Tests for the EAC Cyprus options flow."""
from __future__ import annotations

from datetime import timedelta

from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.eac_cyprus.const import (
    CONF_EMAIL,
    CONF_HISTORY_DAYS,
    CONF_PASSWORD,
    CONF_SCAN_INTERVAL_MINUTES,
    DEFAULT_HISTORY_DAYS,
    DEFAULT_SCAN_INTERVAL_MINUTES,
    DOMAIN,
)


def _entry(hass: HomeAssistant, options: dict | None = None) -> MockConfigEntry:
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="test@example.com",
        data={CONF_EMAIL: "test@example.com", CONF_PASSWORD: "x"},
        options=options or {},
        title="Test User",
    )
    entry.add_to_hass(hass)
    return entry


async def test_options_flow_defaults_on_first_open(
    hass: HomeAssistant, patch_eac_client
) -> None:
    entry = _entry(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "init"


async def test_options_flow_saves_and_applies(
    hass: HomeAssistant, patch_eac_client
) -> None:
    """Submitting the form updates entry.options *and* the coordinator's tick."""
    entry = _entry(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    coordinator = hass.data[DOMAIN][entry.entry_id]
    assert coordinator.update_interval == timedelta(
        minutes=DEFAULT_SCAN_INTERVAL_MINUTES
    )

    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            CONF_SCAN_INTERVAL_MINUTES: "60",  # select selector returns strings
            CONF_HISTORY_DAYS: 7,
        },
    )
    await hass.async_block_till_done()

    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert entry.options[CONF_SCAN_INTERVAL_MINUTES] == 60  # coerced to int
    assert entry.options[CONF_HISTORY_DAYS] == 7
    # The update listener picked up the new interval without reloading.
    assert coordinator.update_interval == timedelta(minutes=60)


async def test_options_flow_form_prefills_current_values(
    hass: HomeAssistant, patch_eac_client
) -> None:
    """The form should show the currently-saved options as defaults, not factory."""
    entry = _entry(
        hass,
        options={CONF_SCAN_INTERVAL_MINUTES: 180, CONF_HISTORY_DAYS: 30},
    )
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    result = await hass.config_entries.options.async_init(entry.entry_id)
    # voluptuous Schema's defaults are exposed via the result's data_schema
    schema = result["data_schema"]
    defaults = {key.schema: key.default() for key in schema.schema}
    assert defaults[CONF_SCAN_INTERVAL_MINUTES] == "180"
    assert defaults[CONF_HISTORY_DAYS] == 30


async def test_coordinator_reads_history_days_from_options(
    hass: HomeAssistant, mock_client
) -> None:
    """history_days is read fresh on every refresh — no reload needed."""
    from custom_components.eac_cyprus.coordinator import EacCoordinator

    entry = _entry(hass, options={CONF_HISTORY_DAYS: 3})
    coord = EacCoordinator(hass, mock_client, entry)
    await coord._async_update_data()

    # get_readings is called once per channel; first call has start = end - 3 days.
    args = mock_client.get_readings.await_args_list
    assert args, "expected at least one get_readings call"
    sp_id, start, end, *_ = args[0].args + tuple(args[0].kwargs.values())[:0]
    assert (end - start) == timedelta(days=3)


async def test_coordinator_falls_back_to_defaults_without_options(
    hass: HomeAssistant, mock_client
) -> None:
    from custom_components.eac_cyprus.coordinator import EacCoordinator

    entry = _entry(hass)
    coord = EacCoordinator(hass, mock_client, entry)
    assert coord.update_interval == timedelta(minutes=DEFAULT_SCAN_INTERVAL_MINUTES)
    await coord._async_update_data()
    args = mock_client.get_readings.await_args_list[0]
    sp_id, start, end = args.args[:3]
    assert (end - start) == timedelta(days=DEFAULT_HISTORY_DAYS)
