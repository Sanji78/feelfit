"""Sensor platform for Feelfit — coordinator-backed entities (auto-refresh)."""
from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any

from homeassistant.components.sensor import SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import (
    CoordinatorEntity,
    DataUpdateCoordinator,
    UpdateFailed,
)

from .const import CONF_SELECTED_PROFILES, DOMAIN, LOGGER, SCAN_INTERVAL

_LOGGER = logging.getLogger(LOGGER)

try:
    from homeassistant.const import UnitOfMass
    KG_UNIT = UnitOfMass.KILOGRAMS
except ImportError:
    KG_UNIT = "kg"

PERCENT = "%"
KCAL = "kcal"
BPM = "bpm"

def _map_date_format(fmt: str) -> str:
    """Map Feelfit date format to Python strftime format."""
    if not fmt:
        return "%Y-%m-%d"
    mapping = {"dd": "%d", "MM": "%m", "yyyy": "%Y", "yy": "%y"}
    out = fmt
    for k, v in mapping.items():
        out = out.replace(k, v)
    return out

def _format_birthday(raw_birthday: Any, date_format: str | None) -> str | None:
    """Format birthday from various input formats."""
    if not raw_birthday:
        return None

    try:
        if isinstance(raw_birthday, int):
            dt = datetime.fromtimestamp(raw_birthday)
            fmt = _map_date_format(date_format or "")
            return dt.strftime(fmt)
        if isinstance(raw_birthday, str) and raw_birthday.isdigit():
            ts = int(raw_birthday)
            dt = datetime.fromtimestamp(ts)
            fmt = _map_date_format(date_format or "")
            return dt.strftime(fmt)
    except (ValueError, OSError):
        pass

    try:
        dt = datetime.strptime(str(raw_birthday), "%Y-%m-%d")
        fmt = _map_date_format(date_format or "")
        return dt.strftime(fmt)
    except ValueError:
        return str(raw_birthday)

async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up sensors for Feelfit - multi-profile support."""
    data = hass.data[DOMAIN][entry.entry_id]
    api = data["api"]
    selected_profiles = data.get("selected_profiles") or []
    initial_user_info = data.get("user_info") or {}
    user_id = initial_user_info.get("user_id") or entry.unique_id or entry.entry_id

    async def async_update_data() -> dict[str, Any]:
        """Coordinator update method."""
        try:
            payload = await api.async_fetch_all(
                str(user_id),
                selected_profiles=selected_profiles if selected_profiles else None
            )
            _LOGGER.debug("Feelfit coordinator fetched keys: %s", list(payload.keys()))
            return payload
        except Exception as err:
            _LOGGER.debug("Feelfit coordinator update failed: %s", err)
            raise UpdateFailed(f"Feelfit fetch failed: {err}") from err

    coordinator: DataUpdateCoordinator[dict[str, Any]] = DataUpdateCoordinator(
        hass,
        _LOGGER,
        name="feelfit",
        update_method=async_update_data,
        update_interval=SCAN_INTERVAL,
    )

    await coordinator.async_refresh()
    data_fetched = coordinator.data or {}

    profiles = data_fetched.get("profiles") or []
    device_binds_payload = data_fetched.get("device_binds") or {}

    entities: list[SensorEntity] = []

    for profile_data in profiles:
        user_info = profile_data.get("user_info") or {}
        profile_user_id = str(user_info.get("user_id", ""))
        account_name = user_info.get("account_name") or "Unknown"
        is_primary = user_info.get("is_primary", True)

        prefix = "" if is_primary else f"{account_name.lower().replace(' ', '_')}_"
        display_prefix = "" if is_primary else f"{account_name} - "

        _LOGGER.debug(
            "Creating sensors for profile: %s (user_id=%s, is_primary=%s, prefix=%s)",
            account_name, profile_user_id, is_primary, prefix
        )

        if user_info:
            entities.append(
                FeelfitUserSensor(
                    coordinator, entry.entry_id,
                    f"{prefix}account_name",
                    "account_name",
                    f"{display_prefix}Account Name", None, profile_user_id
                )
            )
            if user_info.get("weight") is not None:
                entities.append(
                    FeelfitUserSensor(
                        coordinator, entry.entry_id,
                        f"{prefix}weight",
                        "weight",
                        f"{display_prefix}Weight", KG_UNIT, profile_user_id
                    )
                )
            if user_info.get("height") is not None:
                entities.append(
                    FeelfitUserSensor(
                        coordinator, entry.entry_id,
                        f"{prefix}height",
                        "height",
                        f"{display_prefix}Height", "cm", profile_user_id
                    )
                )
            if "birthday" in user_info:
                entities.append(
                    FeelfitBirthdaySensor(
                        coordinator, entry.entry_id, f"{prefix}birthday",
                        "birthday",
                        f"{display_prefix}Birthday", profile_user_id
                    )
                )
            if user_info.get("email"):
                entities.append(
                    FeelfitUserSensor(
                        coordinator, entry.entry_id,
                        f"{prefix}email",
                        "email",
                        f"{display_prefix}Email", None, profile_user_id
                    )
                )

        goals_payload = profile_data.get("goals") or {}
        goals_list = goals_payload.get("goals") or []
        _LOGGER.debug(
            "Profile %s: Processing %d goals",
            user_info.get("account_name"), len(goals_list)
        )
        for g in goals_list:
            g_type = g.get("goal_type")
            _LOGGER.debug(
                "Profile %s goal: type=%s, value=%s, full_data=%s",
                user_info.get("account_name"), g_type, g.get("goal_value"), g
            )
            if not g_type:
                _LOGGER.warning(
                    "Skipping goal with missing goal_type for profile %s: %s",
                    user_info.get("account_name"), g
                )
                continue
            unique_key = f"{prefix}goal_{g_type}"
            translation_key = f"goal_{g_type}"
            unit: str | None = None
            if g_type == "weight":
                unit = KG_UNIT
            elif g_type == "bodyfat":
                unit = PERCENT
            elif g_type == "water":
                unit = "ml"
            entities.append(
                FeelfitGoalSensor(
                    coordinator, entry.entry_id, unique_key, translation_key, g_type, unit, profile_user_id
                )
            )

        measurements_payload = profile_data.get("measurements") or {}
        last_measurement = measurements_payload.get("last_measurement")

        if last_measurement:
            measurement_keys: list[tuple[str, str, str | None]] = [
                ("weight", "Weight", KG_UNIT),
                ("bodyfat", "Bodyfat", PERCENT),
                ("bmi", "BMI", None),
                ("bmr", "BMR", KCAL),
                ("bodyage", "Metabolic Age", "y"),
                ("fat_free_weight", "Fat Free Weight", KG_UNIT),
                ("muscle", "Muscle (%)", PERCENT),
                ("protein", "Protein (%)", PERCENT),
                ("sinew", "Sinew", PERCENT),
                ("subfat", "Subcutaneous Fat (%)", PERCENT),
                ("visfat", "Visceral Fat", None),
                ("water", "Hydration (%)", PERCENT),
                ("bone", "Bone Mass", KG_UNIT),
                ("heart_rate", "Heart Rate", BPM),
                ("score", "Score", None),
                ("time_stamp", "Measurement Timestamp", None),
                ("body_water_mass", "Body Water Mass", KG_UNIT),
                ("protein_mass", "Protein Mass", KG_UNIT),
                ("body_fat_mass", "Body Fat Mass", KG_UNIT),
            ]

            seen: set[str] = set()
            for key, label, unit in measurement_keys:
                if key in seen:
                    continue
                seen.add(key)
                unique = f"{prefix}measurement_{key}"
                name = f"{display_prefix}{label}"
                entities.append(
                    FeelfitMeasurementSensor(
                        coordinator,
                        entry.entry_id,
                        unique,
                        name,
                        unit,
                        measurement_key=key,
                        profile_user_id=profile_user_id,
                    )
                )

    device_binds = (device_binds_payload or {}).get("device_binds") or []
    for idx, d in enumerate(device_binds):
        scale_name = d.get("scale_name") or d.get("internal_model") or f"device_{idx}"
        unique = f"device_{idx}_{d.get('mac') or idx}"
        label = f"Feelfit {scale_name}"
        entities.append(
            FeelfitDeviceSensor(
                coordinator, entry.entry_id, unique, label, None, device_index=idx
            )
        )

    async_add_entities(entities, True)

class FeelfitUserSensor(CoordinatorEntity[DataUpdateCoordinator[dict[str, Any]]], SensorEntity):
    """Sensor for user info attributes."""

    def __init__(
        self,
        coordinator: DataUpdateCoordinator[dict[str, Any]],
        entry_id: str,
        unique_key: str,
        attr_key: str,
        name: str,
        unit: str | None,
        profile_user_id: str | None = None,
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator)
        self._entry_id = entry_id
        self._attr_key = attr_key
        self._name = name
        self._unit = unit
        self._profile_user_id = profile_user_id

        self._unique_id = f"{entry_id}_{unique_key}_{profile_user_id or 'primary'}"
        self._attr_translation_key = attr_key
        self._attr_has_entity_name = True

    @property
    def unique_id(self) -> str:
        """Return unique ID."""
        return self._unique_id

    @property
    def native_unit_of_measurement(self) -> str | None:
        """Return unit of measurement."""
        return self._unit

    @property
    def native_value(self) -> Any:
        """Return sensor value."""
        profiles = (self.coordinator.data or {}).get("profiles") or []

        user_info = None
        for profile_data in profiles:
            profile_info = profile_data.get("user_info") or {}
            if self._profile_user_id and str(profile_info.get("user_id")) == str(self._profile_user_id):
                user_info = profile_info
                break

        if not user_info and profiles:

            user_info = profiles[0].get("user_info") or {}

        if not user_info:
            return None

        return user_info.get(self._attr_key)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return extra attributes."""
        return {"source": "feelfit", "attribute": self._attr_key}

    @property
    def device_info(self) -> dict[str, Any]:
        """Return device info."""
        profiles = (self.coordinator.data or {}).get("profiles") or []

        user_info = None
        for profile_data in profiles:
            profile_info = profile_data.get("user_info") or {}
            if self._profile_user_id and str(profile_info.get("user_id")) == str(self._profile_user_id):
                user_info = profile_info
                break

        if not user_info and profiles:
            user_info = profiles[0].get("user_info") or {}

        if not user_info:
            user_info = {}

        user_id = user_info.get("user_id") or self._entry_id
        return {
            "identifiers": {(DOMAIN, f"user_{user_id}")},
            "name": user_info.get("account_name") or f"Feelfit User {user_id}",
            "manufacturer": "Feelfit",
            "model": "Feelfit Account",
        }

class FeelfitBirthdaySensor(CoordinatorEntity[DataUpdateCoordinator[dict[str, Any]]], SensorEntity):
    """Sensor for birthday with date formatting."""

    def __init__(
        self,
        coordinator: DataUpdateCoordinator[dict[str, Any]],
        entry_id: str,
        unique_key: str,
        attr_key: str,
        name: str,
        profile_user_id: str | None = None,
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator)
        self._entry_id = entry_id
        self._attr_key = attr_key
        self._name = name
        self._profile_user_id = profile_user_id

        self._unique_id = f"{entry_id}_{unique_key}_{profile_user_id or 'primary'}"
        self._attr_translation_key = attr_key
        self._attr_has_entity_name = True

    @property
    def unique_id(self) -> str:
        """Return unique ID."""
        return self._unique_id

    @property
    def native_value(self) -> str | None:
        """Return formatted birthday."""
        profiles = (self.coordinator.data or {}).get("profiles") or []

        user_info = None
        for profile_data in profiles:
            profile_info = profile_data.get("user_info") or {}
            if self._profile_user_id and str(profile_info.get("user_id")) == str(self._profile_user_id):
                user_info = profile_info
                user_settings = profile_data.get("user_settings") or {}
                break

        if not user_info and profiles:
            user_info = profiles[0].get("user_info") or {}
            user_settings = profiles[0].get("user_settings") or {}
        elif not user_info:
            return None

        raw = user_info.get("birthday")
        fmt = user_settings.get("date_format")
        return _format_birthday(raw, fmt)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return extra attributes."""
        profiles = (self.coordinator.data or {}).get("profiles") or []

        user_settings = {}
        for profile_data in profiles:
            profile_info = profile_data.get("user_info") or {}
            if self._profile_user_id and str(profile_info.get("user_id")) == str(self._profile_user_id):
                user_settings = profile_data.get("user_settings") or {}
                break

        return {
            "source": "feelfit",
            "attribute": self._attr_key,
            "date_format": user_settings.get("date_format"),
        }

    @property
    def device_info(self) -> dict[str, Any]:
        """Return device info."""
        profiles = (self.coordinator.data or {}).get("profiles") or []

        user_info = None
        for profile_data in profiles:
            profile_info = profile_data.get("user_info") or {}
            if self._profile_user_id and str(profile_info.get("user_id")) == str(self._profile_user_id):
                user_info = profile_info
                break

        if not user_info and profiles:
            user_info = profiles[0].get("user_info") or {}

        if not user_info:
            user_info = {}
        user_id = user_info.get("user_id") or self._entry_id
        return {
            "identifiers": {(DOMAIN, f"user_{user_id}")},
            "name": user_info.get("account_name") or f"Feelfit User {user_id}",
            "manufacturer": "Feelfit",
            "model": "Feelfit Account",
        }

class FeelfitGoalSensor(CoordinatorEntity[DataUpdateCoordinator[dict[str, Any]]], SensorEntity):
    """Sensor for goal values."""

    def __init__(
        self,
        coordinator: DataUpdateCoordinator[dict[str, Any]],
        entry_id: str,
        unique_key: str,
        translation_key: str,
        goal_type: str,
        unit: str | None,
        profile_user_id: str | None = None,
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator)
        self._entry_id = entry_id

        self._unique_id = f"{entry_id}_{unique_key}_{profile_user_id or 'primary'}"
        self._unit = unit
        self._goal_type = goal_type
        self._profile_user_id = profile_user_id
        self._attr_translation_key = translation_key
        self._attr_has_entity_name = True

    @property
    def unique_id(self) -> str:
        """Return unique ID."""
        return self._unique_id

    @property
    def native_unit_of_measurement(self) -> str | None:
        """Return unit of measurement."""
        return self._unit

    @property
    def native_value(self) -> Any:
        """Return goal value."""
        profiles = (self.coordinator.data or {}).get("profiles") or []

        goals_list = []
        for profile_data in profiles:
            profile_info = profile_data.get("user_info") or {}
            if self._profile_user_id and str(profile_info.get("user_id")) == str(self._profile_user_id):
                goals_payload = profile_data.get("goals") or {}
                goals_list = goals_payload.get("goals") or []
                break

        if not goals_list and profiles:
            goals_payload = profiles[0].get("goals") or {}
            goals_list = goals_payload.get("goals") or []

        for g in goals_list:
            if g.get("goal_type") == self._goal_type:
                return g.get("goal_value")
        return None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return extra attributes."""
        return {"source": "feelfit", "goal_type": self._goal_type}

    @property
    def device_info(self) -> dict[str, Any]:
        """Return device info."""
        profiles = (self.coordinator.data or {}).get("profiles") or []

        user_info = None
        for profile_data in profiles:
            profile_info = profile_data.get("user_info") or {}
            if self._profile_user_id and str(profile_info.get("user_id")) == str(self._profile_user_id):
                user_info = profile_info
                break

        if not user_info and profiles:
            user_info = profiles[0].get("user_info") or {}

        if not user_info:
            user_info = {}

        user_id = user_info.get("user_id") or self._entry_id
        return {
            "identifiers": {(DOMAIN, f"user_{user_id}")},
            "name": user_info.get("account_name") or f"Feelfit User {user_id}",
            "manufacturer": "Feelfit",
            "model": "Feelfit Account",
        }

class FeelfitDeviceSensor(CoordinatorEntity[DataUpdateCoordinator[dict[str, Any]]], SensorEntity):
    """Sensor for bound device info."""

    def __init__(
        self,
        coordinator: DataUpdateCoordinator[dict[str, Any]],
        entry_id: str,
        unique_key: str,
        name: str,
        unit: str | None,
        device_index: int = 0,
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator)
        self._entry_id = entry_id
        self._device_index = device_index
        self._unique_id = f"{entry_id}_device_{unique_key}"
        self._name = name
        self._unit = unit

    @property
    def unique_id(self) -> str:
        """Return unique ID."""
        return self._unique_id

    @property
    def name(self) -> str:
        """Return sensor name."""
        return self._name

    @property
    def native_value(self) -> str | None:
        """Return device name."""
        device_binds = (
            (self.coordinator.data or {}).get("device_binds", {}).get("device_binds")
            or []
        )
        if len(device_binds) > self._device_index:
            d = device_binds[self._device_index]
            return d.get("scale_name") or d.get("internal_model") or d.get("mac")
        return None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return extra attributes."""
        device_binds = (
            (self.coordinator.data or {}).get("device_binds", {}).get("device_binds")
            or []
        )
        attrs: dict[str, Any] = {}
        if len(device_binds) > self._device_index:
            d = device_binds[self._device_index]
            for key in (
                "user_id",
                "mac",
                "scale_name",
                "internal_model",
                "created_at",
                "wifi_name",
                "functure_type",
                "device_name",
                "switch_states",
                "blood_standard",
                "light_strip_status",
                "sn",
                "scale_setting",
            ):
                if key in d:
                    attrs[key] = d.get(key)

            model_info = d.get("model_info")
            if isinstance(model_info, dict):
                for mk, mv in model_info.items():
                    if mk == "brand_info" and isinstance(mv, dict):
                        for bk, bv in mv.items():
                            attrs[f"model_brand_{bk}"] = bv
                        brand_name = mv.get("brand_name")
                        if brand_name:
                            attrs["brand_name"] = brand_name
                    else:
                        attrs[f"model_{mk}"] = mv
            if d.get("model_info") is None and d.get("internal_model"):
                attrs["model_internal_model"] = d.get("internal_model")
        return attrs

    @property
    def device_info(self) -> dict[str, Any]:
        """Return device info."""
        device_binds = (
            (self.coordinator.data or {}).get("device_binds", {}).get("device_binds")
            or []
        )
        user_info = (self.coordinator.data or {}).get("user_info") or {}

        if len(device_binds) > self._device_index:
            d = device_binds[self._device_index]
            scale_name = (
                d.get("scale_name") or d.get("internal_model") or f"Device {self._device_index}"
            )
            model_info = d.get("model_info") or {}
            brand_info = model_info.get("brand_info") or {}
            brand = d.get("brand_name") or brand_info.get("brand_name")
            friendly_name = f"Feelfit {scale_name}"
            if brand:
                friendly_name = f"{friendly_name} ({brand})"
            identifier = d.get("mac") or f"{user_info.get('user_id')}_device_{self._device_index}"
            return {
                "identifiers": {(DOMAIN, identifier)},
                "name": friendly_name,
                "manufacturer": brand or "Feelfit",
                "model": model_info.get("model") or d.get("internal_model") or "Feelfit Device",
            }

        user_id = user_info.get("user_id")
        return {
            "identifiers": {(DOMAIN, f"{user_id}_device_{self._device_index}")},
            "name": f"{user_info.get('account_name', 'Feelfit User')} device {self._device_index}",
            "manufacturer": "Feelfit",
            "model": "Feelfit Device",
        }

class FeelfitMeasurementSensor(CoordinatorEntity[DataUpdateCoordinator[dict[str, Any]]], SensorEntity):
    """Sensor for measurement values."""

    def __init__(
        self,
        coordinator: DataUpdateCoordinator[dict[str, Any]],
        entry_id: str,
        unique_key: str,
        name: str,
        unit: str | None,
        measurement_key: str,
        profile_user_id: str | None = None,
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator)
        self._entry_id = entry_id

        self._unique_id = f"{entry_id}_{unique_key}_{profile_user_id or 'primary'}"
        self._name = name
        self._unit = unit
        self._measurement_key = measurement_key
        self._profile_user_id = profile_user_id
        self._attr_translation_key = f"measurement_{measurement_key}"
        self._attr_has_entity_name = True

    @property
    def unique_id(self) -> str:
        """Return unique ID."""
        return self._unique_id

    @property
    def native_unit_of_measurement(self) -> str | None:
        """Return unit of measurement."""
        return self._unit

    @property
    def native_value(self) -> Any:
        """Return measurement value."""
        profiles = (self.coordinator.data or {}).get("profiles") or []

        measurement = None
        for profile_data in profiles:
            profile_info = profile_data.get("user_info") or {}
            if self._profile_user_id and str(profile_info.get("user_id")) == str(self._profile_user_id):
                measurements_payload = profile_data.get("measurements") or {}
                measurement = measurements_payload.get("last_measurement")
                break

        if not measurement and profiles:
            measurements_payload = profiles[0].get("measurements") or {}
            measurement = measurements_payload.get("last_measurement")

        if not measurement:
            _LOGGER.debug(
                "FeelfitMeasurementSensor: no measurement for key %s",
                self._measurement_key,
            )
            return None

        raw_val = measurement.get(self._measurement_key)

        if self._measurement_key == "time_stamp" and raw_val:
            try:
                ts = int(raw_val)
                dt = datetime.fromtimestamp(ts)
                return dt.isoformat()
            except (ValueError, OSError):
                return str(raw_val)

        if isinstance(raw_val, (int, float)):
            if self._measurement_key in ("bodyage", "measurement_id", "user_id"):
                try:
                    return int(raw_val)
                except (ValueError, TypeError):
                    return raw_val
            try:
                fval = float(raw_val)
                if fval.is_integer():
                    return int(fval)
                return round(fval, 2)
            except (ValueError, TypeError):
                return raw_val

        if isinstance(raw_val, str):
            cleaned = raw_val.replace(".", "", 1)
            if cleaned.isdigit() or (cleaned.startswith("-") and cleaned[1:].isdigit()):
                try:
                    if "." in raw_val:
                        fval = float(raw_val)
                        if fval.is_integer():
                            return int(fval)
                        return round(fval, 2)
                    return int(raw_val)
                except (ValueError, TypeError):
                    pass
            return raw_val

        return raw_val

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return extra attributes."""
        profiles = (self.coordinator.data or {}).get("profiles") or []

        measurement = None
        for profile_data in profiles:
            profile_info = profile_data.get("user_info") or {}
            if self._profile_user_id and str(profile_info.get("user_id")) == str(self._profile_user_id):
                measurements_payload = profile_data.get("measurements") or {}
                measurement = measurements_payload.get("last_measurement")
                break

        if not measurement and profiles:
            measurements_payload = profiles[0].get("measurements") or {}
            measurement = measurements_payload.get("last_measurement")

        attrs: dict[str, Any] = {}
        if measurement:
            for k in (
                "measurement_id",
                "user_id",
                "scale_name",
                "internal_model",
                "mac",
                "parameter",
                "accuracy_flag",
                "measure_mode_flags",
            ):
                if k in measurement:
                    attrs[k] = measurement.get(k)
        return attrs

    @property
    def device_info(self) -> dict[str, Any]:
        """Return device info."""
        profiles = (self.coordinator.data or {}).get("profiles") or []

        user_info = None
        for profile_data in profiles:
            profile_info = profile_data.get("user_info") or {}
            if self._profile_user_id and str(profile_info.get("user_id")) == str(self._profile_user_id):
                user_info = profile_info
                break

        if not user_info and profiles:
            user_info = profiles[0].get("user_info") or {}

        if not user_info:
            user_info = {}

        user_id = user_info.get("user_id") or self._entry_id
        return {
            "identifiers": {(DOMAIN, f"user_{user_id}")},
            "name": user_info.get("account_name") or f"Feelfit User {user_id}",
            "manufacturer": "Feelfit",
            "model": "Feelfit Account",
        }
