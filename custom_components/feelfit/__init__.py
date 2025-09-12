from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import DOMAIN
from .api import FeelfitApi, FeelfitApiError

_LOGGER = logging.getLogger("custom_components.feelfit")


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Feelfit from a config entry."""
    hass.data.setdefault(DOMAIN, {})

    email = entry.data.get("email")
    token = entry.data.get("token")
    saved_user_info = entry.data.get("user_info") or {}

    session = async_get_clientsession(hass)
    api = FeelfitApi(hass, session, email)

    # restore token and user_info if present in entry.data (from config flow)
    if token:
        api.token = token
    if saved_user_info:
        api.user_info = saved_user_info

    # store api and initial user_info; will be refreshed by sensor coordinator
    hass.data[DOMAIN][entry.entry_id] = {"api": api, "user_info": api.user_info}

    # try an initial fetch to populate user_settings/goals/device_binds so sensors aren't empty
    try:
        user_id = api.user_info.get("user_id") if api.user_info else entry.unique_id or entry.entry_id
        if api.token:
            payload = await api.async_fetch_all(str(user_id))
            # update stored values
            hass.data[DOMAIN][entry.entry_id]["user_info"] = payload.get("user_info") or api.user_info
            hass.data[DOMAIN][entry.entry_id]["user_settings"] = payload.get("user_settings") or {}
            hass.data[DOMAIN][entry.entry_id]["goals"] = payload.get("goals") or {}
            hass.data[DOMAIN][entry.entry_id]["device_binds"] = payload.get("device_binds") or {}
    except FeelfitApiError as err:
        _LOGGER.debug("Initial fetch failed (will retry via coordinator): %s", err)
    except Exception as err:  # be defensive
        _LOGGER.exception("Unexpected error during initial Feelfit fetch: %s", err)

    # forward platform setup
    await hass.config_entries.async_forward_entry_setups(entry, ["sensor"])

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, ["sensor"])
    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id, None)
    return unload_ok
