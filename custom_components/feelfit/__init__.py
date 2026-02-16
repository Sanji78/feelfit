"""The Feelfit integration."""
from __future__ import annotations

import logging
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import FeelfitApi, FeelfitApiError
from .const import CONF_SELECTED_PROFILES, DOMAIN, PLATFORMS

_LOGGER = logging.getLogger("custom_components.feelfit")

async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Feelfit from a config entry."""
    hass.data.setdefault(DOMAIN, {})

    email: str | None = entry.data.get("email")
    token: str | None = entry.data.get("token")
    saved_user_info: dict[str, Any] = entry.data.get("user_info") or {}

    selected_profiles: list[str] = (
        entry.options.get(CONF_SELECTED_PROFILES) or
        entry.data.get(CONF_SELECTED_PROFILES) or
        []
    )

    if not email:
        _LOGGER.error("No email found in config entry")
        return False

    session = async_get_clientsession(hass)
    api = FeelfitApi(hass, session, email)

    if token:
        api.token = token
    if saved_user_info:
        api.user_info = saved_user_info

    hass.data[DOMAIN][entry.entry_id] = {
        "api": api,
        "user_info": api.user_info,
        "selected_profiles": selected_profiles,
    }

    try:
        user_id = (
            api.user_info.get("user_id")
            if api.user_info
            else entry.unique_id or entry.entry_id
        )
        if api.token and user_id:
            payload = await api.async_fetch_all(
                str(user_id),
                selected_profiles=selected_profiles if selected_profiles else None
            )
            hass.data[DOMAIN][entry.entry_id].update({
                "profiles": payload.get("profiles") or [],
                "device_binds": payload.get("device_binds") or {},
            })
    except FeelfitApiError as err:
        _LOGGER.debug("Initial fetch failed (will retry via coordinator): %s", err)
    except Exception as err:
        _LOGGER.exception("Unexpected error during initial Feelfit fetch: %s", err)

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    entry.async_on_unload(entry.add_update_listener(async_update_options))

    return True

async def async_update_options(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Handle options update - rimuove entità e device dei profili disattivati."""
    from homeassistant.helpers import entity_registry as er, device_registry as dr

    new_selected = entry.options.get(CONF_SELECTED_PROFILES, [])
    old_selected = entry.data.get(CONF_SELECTED_PROFILES, [])

    removed_profiles = set(old_selected) - set(new_selected)

    if removed_profiles:
        _LOGGER.debug("Removing entities for deselected profiles: %s", removed_profiles)

        entity_registry = er.async_get(hass)
        device_registry = dr.async_get(hass)

        devices_to_remove = []
        for device_entry in device_registry.devices.values():

            for identifier_tuple in device_entry.identifiers:
                if identifier_tuple[0] == DOMAIN:
                    device_id_str = identifier_tuple[1]

                    if device_id_str.startswith("user_"):
                        user_id = device_id_str.replace("user_", "")
                        if user_id in removed_profiles:
                            devices_to_remove.append(device_entry.id)
                            _LOGGER.debug("Found device to remove: %s (user_id: %s)",
                                        device_entry.name, user_id)
                            break

        for device_id in devices_to_remove:
            device_registry.async_remove_device(device_id)
            _LOGGER.info("Removed device: %s", device_id)

        if devices_to_remove:
            _LOGGER.info("Removed %d devices for deselected profiles", len(devices_to_remove))
        else:

            entries_to_remove = []
            for entity_entry in entity_registry.entities.values():
                if entity_entry.config_entry_id == entry.entry_id:

                    for removed_user_id in removed_profiles:
                        if str(removed_user_id) in entity_entry.unique_id:
                            entries_to_remove.append(entity_entry.entity_id)
                            _LOGGER.debug("Removing entity: %s", entity_entry.entity_id)
                            break

            for entity_id in entries_to_remove:
                entity_registry.async_remove(entity_id)

            if entries_to_remove:
                _LOGGER.info("Removed %d entities for deselected profiles", len(entries_to_remove))

    await hass.config_entries.async_reload(entry.entry_id)

async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id, None)
    return unload_ok
