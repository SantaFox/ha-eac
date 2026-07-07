"""Config flow for the EAC Cyprus integration."""
from __future__ import annotations

import logging
from typing import Any

import aiohttp
import voluptuous as vol

from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.core import callback
from homeassistant.helpers import selector
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from eac_dso_portal import EacApiError, EacAuthError, EacClient
from .const import (
    CONF_BASE_URL,
    CONF_EMAIL,
    CONF_HISTORY_DAYS,
    CONF_PASSWORD,
    CONF_SCAN_INTERVAL_MINUTES,
    CONF_STALE_THRESHOLD_DAYS,
    DEFAULT_BASE_URL,
    DEFAULT_HISTORY_DAYS,
    DEFAULT_SCAN_INTERVAL_MINUTES,
    DEFAULT_STALE_THRESHOLD_DAYS,
    DOMAIN,
    SCAN_INTERVAL_CHOICES,
)

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
    Editable runtime settings (poll interval, history window) live in the
    options flow below.
    """

    VERSION = 1

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        return EacOptionsFlow()

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


# Human labels for the polling-interval dropdown.
_INTERVAL_LABELS: dict[int, str] = {
    30: "30 minutes",
    60: "1 hour",
    180: "3 hours",
    360: "6 hours (recommended)",
    720: "12 hours",
    1440: "24 hours",
}


class EacOptionsFlow(OptionsFlow):
    """Editable runtime settings: polling cadence and history window."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if user_input is not None:
            # The select selector hands back strings; coerce to int once.
            user_input[CONF_SCAN_INTERVAL_MINUTES] = int(
                user_input[CONF_SCAN_INTERVAL_MINUTES]
            )
            return self.async_create_entry(title="", data=user_input)

        current = self.config_entry.options
        current_interval = current.get(
            CONF_SCAN_INTERVAL_MINUTES, DEFAULT_SCAN_INTERVAL_MINUTES
        )
        current_history = current.get(CONF_HISTORY_DAYS, DEFAULT_HISTORY_DAYS)
        current_stale = current.get(
            CONF_STALE_THRESHOLD_DAYS, DEFAULT_STALE_THRESHOLD_DAYS
        )

        schema = vol.Schema(
            {
                vol.Required(
                    CONF_SCAN_INTERVAL_MINUTES, default=str(current_interval)
                ): selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=[
                            selector.SelectOptionDict(
                                value=str(m), label=_INTERVAL_LABELS[m]
                            )
                            for m in SCAN_INTERVAL_CHOICES
                        ],
                        mode=selector.SelectSelectorMode.DROPDOWN,
                    ),
                ),
                vol.Required(
                    CONF_HISTORY_DAYS, default=current_history
                ): selector.NumberSelector(
                    selector.NumberSelectorConfig(
                        min=1,
                        max=60,
                        step=1,
                        mode=selector.NumberSelectorMode.BOX,
                        unit_of_measurement="days",
                    ),
                ),
                vol.Required(
                    CONF_STALE_THRESHOLD_DAYS, default=current_stale
                ): selector.NumberSelector(
                    selector.NumberSelectorConfig(
                        min=1,
                        max=30,
                        step=1,
                        mode=selector.NumberSelectorMode.BOX,
                        unit_of_measurement="days",
                    ),
                ),
            }
        )
        return self.async_show_form(step_id="init", data_schema=schema)
