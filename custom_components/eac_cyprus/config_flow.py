"""Config flow for the EAC Cyprus integration."""
from __future__ import annotations

import logging
from typing import Any

import aiohttp
import voluptuous as vol

from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from eac_dso_portal import EacApiError, EacAuthError, EacClient
from .const import CONF_BASE_URL, CONF_EMAIL, CONF_PASSWORD, DEFAULT_BASE_URL, DOMAIN

_LOGGER = logging.getLogger(__name__)

STEP_USER_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_EMAIL): str,
        vol.Required(CONF_PASSWORD): str,
        vol.Optional(CONF_BASE_URL, default=DEFAULT_BASE_URL): str,
    }
)


class EacConfigFlow(ConfigFlow, domain=DOMAIN):
    """Single-step user-initiated flow.

    The portal account is per-email and unique; we use the email as the unique
    id of the config entry, preventing duplicate entries for the same account.
    """

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}

        if user_input is not None:
            email = user_input[CONF_EMAIL].strip().lower()
            await self.async_set_unique_id(email)
            self._abort_if_unique_id_configured()

            session = async_get_clientsession(self.hass)
            client = EacClient(
                email,
                user_input[CONF_PASSWORD],
                session=session,
                base_url=user_input.get(CONF_BASE_URL, DEFAULT_BASE_URL),
            )
            try:
                await client.login()
                user = await client.get_user_details()
            except EacAuthError:
                errors["base"] = "invalid_auth"
            except (EacApiError, aiohttp.ClientError, TimeoutError):
                errors["base"] = "cannot_connect"
            except Exception:  # noqa: BLE001 - last-resort safety net
                _LOGGER.exception("Unexpected error during EAC login")
                errors["base"] = "unknown"
            else:
                title = user.name or email
                return self.async_create_entry(
                    title=title,
                    data={
                        CONF_EMAIL: email,
                        CONF_PASSWORD: user_input[CONF_PASSWORD],
                        CONF_BASE_URL: user_input.get(CONF_BASE_URL, DEFAULT_BASE_URL),
                    },
                )

        return self.async_show_form(
            step_id="user", data_schema=STEP_USER_SCHEMA, errors=errors
        )
