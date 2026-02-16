"""Config flow for Feelfit integration."""
from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol
from aiohttp import ClientSession

from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers import selector

from .api import FeelfitApi, FeelfitApiError
from .const import CONF_PROFILES_LIST, CONF_SELECTED_PROFILES, DOMAIN, LOGGER

_LOGGER = logging.getLogger(LOGGER)

STEP_USER_DATA_SCHEMA = vol.Schema(
    {
        vol.Required("email"): str,
        vol.Required("password"): str,
    }
)

async def validate_input(
    hass: HomeAssistant, data: dict[str, Any]
) -> dict[str, Any]:
    """Validate the user input allows us to connect."""
    session: ClientSession = async_get_clientsession(hass)
    client = FeelfitApi(hass, session, data["email"].strip())

    login_data = await client.async_login(data["password"])
    return login_data

class FeelfitConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Feelfit."""

    VERSION = 1

    def __init__(self) -> None:
        """Initialize the config flow."""
        self._email: str | None = None
        self._token: str | None = None
        self._token_expires: str | None = None
        self._user_info: dict[str, Any] = {}
        self._all_profiles: list[dict[str, Any]] = []
        self._label_to_user_id: dict[str, str] = {}

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle the initial step which asks for email and password."""
        errors: dict[str, str] = {}

        if user_input is not None:
            try:
                login_data = await validate_input(self.hass, user_input)
            except FeelfitApiError as exc:
                _LOGGER.debug("Login attempt failed: %s", exc)
                errors["base"] = "invalid_auth"
            except Exception:
                _LOGGER.exception("Unexpected exception")
                errors["base"] = "unknown"
            else:

                self._user_info = login_data.get("user_info", {})
                token_info = login_data.get("token_info", {})
                self._token = token_info.get("token")
                remaining_time = token_info.get("remaining_time")
                self._token_expires = str(int(remaining_time)) if remaining_time else None
                self._email = user_input["email"].strip()

                session = async_get_clientsession(self.hass)
                client = FeelfitApi(self.hass, session, self._email)
                client.token = self._token

                try:
                    self._all_profiles = await client.async_list_all_profiles()
                    _LOGGER.debug("Found %d profiles", len(self._all_profiles))
                    for idx, p in enumerate(self._all_profiles):
                        _LOGGER.debug(
                            "Profile %d: user_id=%s, account_name=%s, nickname=%s, email=%s, is_primary=%s",
                            idx + 1,
                            p.get("user_id"),
                            p.get("account_name"),
                            p.get("nickname"),
                            p.get("email"),
                            p.get("is_primary")
                        )
                except Exception as exc:
                    _LOGGER.error("Failed to fetch profiles: %s", exc)
                    self._all_profiles = [self._user_info]

                return await self.async_step_select_profiles()

        return self.async_show_form(
            step_id="user",
            data_schema=STEP_USER_DATA_SCHEMA,
            errors=errors,
        )

    async def async_step_select_profiles(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle profile selection step."""
        if user_input is not None:

            selected = []
            for label, is_selected in user_input.items():
                if is_selected and label != CONF_SELECTED_PROFILES:

                    user_id = self._label_to_user_id.get(label, label)
                    selected.append(user_id)

            if not selected:

                primary = next(
                    (p for p in self._all_profiles if p.get("is_primary", False)),
                    self._all_profiles[0] if self._all_profiles else self._user_info
                )
                selected = [str(primary.get("user_id"))]

            unique_id = self._user_info.get("user_id") or self._email
            await self.async_set_unique_id(str(unique_id))
            self._abort_if_unique_id_configured()

            entry_data = {
                "email": self._email,
                "token": self._token,
                "token_expires": self._token_expires,
                "user_info": self._user_info,
                CONF_SELECTED_PROFILES: selected,
                CONF_PROFILES_LIST: [
                    {
                        "user_id": str(p.get("user_id")),
                        "account_name": p.get("account_name"),
                        "is_primary": p.get("is_primary", False),
                    }
                    for p in self._all_profiles
                ],
            }

            title = self._user_info.get("account_name") or self._email
            if len(selected) > 1:
                title = f"{title} (+{len(selected)-1} profili)"

            return self.async_create_entry(title=title, data=entry_data)

        profiles_schema = {}
        for profile in self._all_profiles:
            user_id = str(profile.get("user_id"))
            account_name = profile.get("account_name", "Profilo sconosciuto")
            is_primary = profile.get("is_primary", False)

            label = f"{account_name}"
            if is_primary:
                label += " (Primario)"

            email = profile.get("email")
            if email:
                label += f" - {email}"

            _LOGGER.debug("Profile in UI: user_id=%s, label=%s", user_id, label)

            profiles_schema[vol.Optional(label, default=is_primary, description={"suggested_value": is_primary})] = selector.BooleanSelector()

        self._label_to_user_id = {}
        for p in self._all_profiles:
            account_name = p.get('account_name', 'Profilo sconosciuto')
            is_primary_label = ' (Primario)' if p.get('is_primary') else ''
            email_part = f" - {p.get('email')}" if p.get('email') else ''
            label_key = f"{account_name}{is_primary_label}{email_part}"
            self._label_to_user_id[label_key] = str(p.get("user_id"))

        return self.async_show_form(
            step_id="select_profiles",
            data_schema=vol.Schema(profiles_schema),
            description_placeholders={
                "num_profiles": str(len(self._all_profiles))
            },
        )

    @staticmethod
    @config_entries.callback
    def async_get_options_flow(
        config_entry: config_entries.ConfigEntry,
    ) -> config_entries.OptionsFlow:
        """Get the options flow for this handler."""
        return FeelfitOptionsFlowHandler(config_entry)

class FeelfitOptionsFlowHandler(config_entries.OptionsFlow):
    """Handle Feelfit options."""

    def __init__(self, config_entry: config_entries.ConfigEntry) -> None:
        """Initialize options flow."""

        self._all_profiles: list[dict[str, Any]] = []
        self._label_to_user_id: dict[str, str] = {}

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Manage the options."""
        return await self.async_step_profiles()

    async def async_step_profiles(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle profile selection in options."""

        email = self.config_entry.data.get("email")
        token = self.config_entry.data.get("token")
        user_info = self.config_entry.data.get("user_info") or {}

        if not email or not token:
            return self.async_abort(reason="missing_credentials")

        try:
            session: ClientSession = async_get_clientsession(self.hass)
            api = FeelfitApi(self.hass, session, email)
            api.token = token
            api.user_info = user_info

            self._all_profiles = await api.async_list_all_profiles()

        except Exception as exc:
            _LOGGER.error("Failed to fetch profiles in options: %s", exc, exc_info=True)
            return self.async_abort(reason="fetch_failed")

        if user_input is not None:

            selected = []
            for label, is_selected in user_input.items():
                if is_selected:
                    user_id = self._label_to_user_id.get(label, label)
                    selected.append(user_id)

            if not selected:

                primary = next(
                    (p for p in self._all_profiles if p.get("is_primary", False)),
                    self._all_profiles[0] if self._all_profiles else None
                )
                if primary:
                    selected = [str(primary.get("user_id"))]

            return self.async_create_entry(
                title="",
                data={CONF_SELECTED_PROFILES: selected}
            )

        profiles_schema = {}

        current_selection = self.config_entry.options.get(
            CONF_SELECTED_PROFILES,
            self.config_entry.data.get(CONF_SELECTED_PROFILES, [])
        )

        for profile in self._all_profiles:
            user_id = str(profile.get("user_id"))
            account_name = profile.get("account_name", "Profilo sconosciuto")
            is_primary = profile.get("is_primary", False)

            label = f"{account_name}"
            if is_primary:
                label += " (Primario)"

            email_addr = profile.get("email")
            if email_addr:
                label += f" - {email_addr}"

            is_selected = user_id in current_selection

            _LOGGER.debug("Options profile: user_id=%s, label=%s, selected=%s", user_id, label, is_selected)

            profiles_schema[vol.Optional(label, default=is_selected)] = selector.BooleanSelector()

        for p in self._all_profiles:
            account_name = p.get('account_name', 'Profilo sconosciuto')
            is_primary_label = ' (Primario)' if p.get('is_primary') else ''
            email_part = f" - {p.get('email')}" if p.get('email') else ''
            label_key = f"{account_name}{is_primary_label}{email_part}"
            self._label_to_user_id[label_key] = str(p.get("user_id"))

        return self.async_show_form(
            step_id="profiles",
            data_schema=vol.Schema(profiles_schema),
            description_placeholders={
                "num_profiles": str(len(self._all_profiles))
            },
        )
