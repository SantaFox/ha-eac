"""Shared HA test fixtures.

Tests use ``pytest-homeassistant-custom-component`` which simulates the
parts of Home Assistant needed for integration testing — no real HA
process is started.
"""
from __future__ import annotations

from collections.abc import Generator
from datetime import datetime
from unittest.mock import AsyncMock, patch

import pytest
from eac_dso_portal import (
    ChannelReadings,
    MeasurementChannel,
    MeterConfig,
    Reading,
    ServicePoint,
    UserDetails,
)

# Required to register HA test fixtures (`hass`, `enable_custom_integrations`,
# `MockConfigEntry`, etc.) from pytest-homeassistant-custom-component.
pytest_plugins = "pytest_homeassistant_custom_component"


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(recorder_mock, enable_custom_integrations):
    """Make custom_components/eac_cyprus loadable + spin up an in-memory recorder.

    The order matters: ``recorder_mock`` must be requested *before* the ``hass``
    fixture is set up (``enable_custom_integrations`` triggers hass), otherwise
    PHACC asserts. The recorder is needed because the integration now declares
    a hard dependency on it for long-term-statistics imports.
    """
    yield


# ---------------- synthetic library objects ----------------


@pytest.fixture
def fake_user() -> UserDetails:
    return UserDetails(name="Test User", admin=False, raw={})


@pytest.fixture
def fake_active_sp() -> ServicePoint:
    return ServicePoint(
        id="111111111111",
        address="1 Test St, City",
        active=True,
        device_exists=True,
        serial_number="S111",
        phases="3",
        load_entitlement="30",
        load_entitlement_uom="AMP",
        raw={},
    )


@pytest.fixture
def fake_inactive_sp() -> ServicePoint:
    return ServicePoint(
        id="222222222222",
        address="2 Old Rd, City",
        active=False,
        device_exists=True,
        serial_number="S222",
        phases=None,
        load_entitlement=None,
        load_entitlement_uom=None,
        raw={},
    )


@pytest.fixture
def fake_meter_total_channel() -> MeasurementChannel:
    return MeasurementChannel(
        id="mc-total-24h",
        type="S-KWH-24H",
        uom="KWH",
        tou="24H",
        interval=False,
        digits_left=8,
        digits_right=0,
        raw={},
    )


@pytest.fixture
def fake_meter_30min_channel() -> MeasurementChannel:
    return MeasurementChannel(
        id="mc-30min-imp",
        type="KWH-30MIN-LP-IMP",
        uom="KWH",
        tou="30MIN",
        interval=True,
        digits_left=4,
        digits_right=3,
        raw={},
    )


@pytest.fixture
def fake_active_meter(fake_meter_total_channel, fake_meter_30min_channel) -> MeterConfig:
    return MeterConfig(
        serial_number="NEW123",
        manufacturer="LANDIS & GYR",
        model="E450",
        install_date=datetime(2026, 2, 2),
        removal_date=None,
        channels=(fake_meter_total_channel, fake_meter_30min_channel),
        raw={},
    )


# ---------------- mock client ----------------


@pytest.fixture
def mock_client(
    fake_user,
    fake_active_sp,
    fake_inactive_sp,
    fake_active_meter,
):
    """Replace EacClient with an AsyncMock that returns synthetic data."""
    client = AsyncMock()
    client.login.return_value = None
    client.get_user_details.return_value = fake_user
    client.list_service_points.return_value = [fake_active_sp, fake_inactive_sp]
    client.get_meter_configs.return_value = [fake_active_meter]

    # readings/list returns one ChannelReadings per call (per mc_id)
    def get_readings_side_effect(sp_id, start, end, mc_id=None):
        if mc_id == "mc-total-24h":
            return [
                ChannelReadings(
                    channel_id="mc-total-24h",
                    readings=(
                        Reading(
                            dt=datetime(2026, 5, 8), value=24.0, reading=2974.0
                        ),
                    ),
                )
            ]
        if mc_id == "mc-30min-imp":
            return [
                ChannelReadings(
                    channel_id="mc-30min-imp",
                    readings=(
                        Reading(
                            dt=datetime(2026, 5, 8, 14, 0), value=0.315, reading=None
                        ),
                    ),
                )
            ]
        return [ChannelReadings(channel_id=mc_id or "default", readings=())]

    client.get_readings.side_effect = get_readings_side_effect
    return client


@pytest.fixture
def patch_eac_client(mock_client) -> Generator[AsyncMock, None, None]:
    """Patch EacClient everywhere the integration's modules reference it.

    Each of the three integration modules (`__init__`, `config_flow`,
    `coordinator`) does ``from eac_dso_portal import EacClient`` at the
    top, so each holds its own bound name pointing at the class object.
    Patching only the source library would not update those bound names.
    Importing the modules first guarantees the targets exist.

    We also stub out ``async_get_clientsession`` so HA does not spin up
    a real aiohttp session (which leaks a daemon thread that PHACC's
    teardown check refuses to tolerate). Our mocked client never uses
    the session anyway.
    """
    import custom_components.eac_cyprus  # noqa: F401
    import custom_components.eac_cyprus.config_flow  # noqa: F401
    import custom_components.eac_cyprus.coordinator  # noqa: F401

    fake_session = AsyncMock()
    targets = [
        ("custom_components.eac_cyprus.EacClient", mock_client),
        ("custom_components.eac_cyprus.config_flow.EacClient", mock_client),
        ("custom_components.eac_cyprus.coordinator.EacClient", mock_client),
        ("custom_components.eac_cyprus.async_get_clientsession", fake_session),
        ("custom_components.eac_cyprus.config_flow.async_get_clientsession", fake_session),
    ]
    patches = [patch(t, return_value=v) for t, v in targets]
    for p in patches:
        p.start()
    try:
        yield mock_client
    finally:
        for p in patches:
            p.stop()
