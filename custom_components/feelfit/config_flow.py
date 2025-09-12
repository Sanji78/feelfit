"""Config flow for Feelfit integration."""
from __future__ import annotations

import logging
from typing import Any, Dict

from aiohttp import ClientSession
import voluptuous as vol

from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import FeelfitApi, FeelfitApiError
from .const import DOMAIN, LOGGER

_LOGGER = logging.getLogger(LOGGER)


class FeelfitConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Feelfit."""

    VERSION = 1
    CONNECTION_CLASS = config_entries.CONN_CLASS_CLOUD_POLL

    def __init__(self) -> None:
        self._email: str | None = None

    async def async_step_user(self, user_input: Dict[str, Any] | None = None):
        """Handle the initial step which asks for email and password."""
        errors = {}
        if user_input is not None:
            email = user_input["email"].strip()
            password = user_input["password"]

            session: ClientSession = async_get_clientsession(self.hass)
            client = FeelfitApi(self.hass, session, email)
            try:
                data = await client.async_login(password)
            except FeelfitApiError as exc:
                _LOGGER.debug("Login attempt failed: %s", exc)
                errors["base"] = "auth"
            else:
                user_info = data.get("user_info", {})
                token = data.get("token_info", {}).get("token")
                remaining_time = data.get("token_info", {}).get("remaining_time")
                unique_id = user_info.get("user_id") or email
                await self.async_set_unique_id(str(unique_id))
                self._abort_if_unique_id_configured()
                # Store login token + user_info in config entry data for now
                entry_data = {
                    "email": email,
                    "token": token,
                    "token_expires": None if not remaining_time else str(int(remaining_time)),
                    "user_info": user_info,
                }
                return self.async_create_entry(title=user_info.get("account_name") or email, data=entry_data)

        data_schema = vol.Schema(
            {
                vol.Required("email"): str,
                vol.Required("password"): str,
            }
        )
        return self.async_show_form(step_id="user", data_schema=data_schema, errors=errors)
