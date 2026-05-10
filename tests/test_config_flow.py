"""Tests for the EAC Cyprus config flow."""
from __future__ import annotations

import aiohttp
import pytest
from eac_dso_portal import EacApiError, EacAuthError
from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.eac_cyprus.const import (
    CONF_BASE_URL,
    CONF_EMAIL,
    CONF_PASSWORD,
    DEFAULT_BASE_URL,
    DOMAIN,
)


USER_INPUT = {
    CONF_EMAIL: "Test@Example.COM",
    CONF_PASSWORD: "secret",
}


async def test_config_flow_happy_path(hass: HomeAssistant, patch_eac_client) -> None:
    """A correct login creates a config entry titled with the user's name."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "user"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], USER_INPUT
    )
    await hass.async_block_till_done()
    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert result["title"] == "Test User"
    assert result["data"][CONF_EMAIL] == "test@example.com"  # normalised
    assert result["data"][CONF_BASE_URL] == DEFAULT_BASE_URL


async def test_config_flow_invalid_auth_shown_inline(
    hass: HomeAssistant, patch_eac_client
) -> None:
    patch_eac_client.login.side_effect = EacAuthError("nope")

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], USER_INPUT
    )
    await hass.async_block_till_done()

    assert result["type"] == FlowResultType.FORM
    assert result["errors"] == {"base": "invalid_auth"}


@pytest.mark.parametrize(
    "exc",
    [
        EacApiError(500, "boom", "/api/portal/login"),
        aiohttp.ClientError("network down"),
        TimeoutError(),
    ],
)
async def test_config_flow_connection_errors(
    hass: HomeAssistant, patch_eac_client, exc: BaseException
) -> None:
    patch_eac_client.login.side_effect = exc

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], USER_INPUT
    )
    await hass.async_block_till_done()
    assert result["type"] == FlowResultType.FORM
    assert result["errors"] == {"base": "cannot_connect"}


async def test_config_flow_dedup_by_email(
    hass: HomeAssistant, patch_eac_client
) -> None:
    """Adding the same account twice is rejected."""
    existing = MockConfigEntry(
        domain=DOMAIN,
        unique_id="test@example.com",
        data={CONF_EMAIL: "test@example.com", CONF_PASSWORD: "x"},
    )
    existing.add_to_hass(hass)

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], USER_INPUT
    )
    await hass.async_block_till_done()
    assert result["type"] == FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def test_config_flow_unexpected_exception(
    hass: HomeAssistant, patch_eac_client
) -> None:
    """Anything we don't anticipate becomes 'unknown' rather than crashing."""
    patch_eac_client.login.side_effect = RuntimeError("surprise!")

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], USER_INPUT
    )
    await hass.async_block_till_done()
    assert result["type"] == FlowResultType.FORM
    assert result["errors"] == {"base": "unknown"}
