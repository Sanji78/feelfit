"""Sensor platform for Feelfit — coordinator-backed entities (auto-refresh)."""

from __future__ import annotations

import logging
from typing import Any
from datetime import timedelta, datetime

from homeassistant.components.sensor import SensorEntity
from homeassistant.helpers.update_coordinator import (
    DataUpdateCoordinator,
    UpdateFailed,
    CoordinatorEntity,
)

from .const import DOMAIN, LOGGER

_LOGGER = logging.getLogger(LOGGER)

# Units
try:
    from homeassistant.const import UnitOfMass
    KG_UNIT = UnitOfMass.KILOGRAMS
except Exception:
    KG_UNIT = "kg"

PERCENT = "%"
KCAL = "kcal"
BPM = "bpm"


def _map_date_format(fmt: str) -> str:
    if not fmt:
        return "%Y-%m-%d"
    mapping = {"dd": "%d", "MM": "%m", "yyyy": "%Y", "yy": "%y"}
    out = fmt
    for k, v in mapping.items():
        out = out.replace(k, v)
    return out


def _format_birthday(raw_birthday: Any, date_format: str | None) -> str | None:
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
    except Exception:
        pass
    try:
        dt = datetime.strptime(raw_birthday, "%Y-%m-%d")
        fmt = _map_date_format(date_format or "")
        return dt.strftime(fmt)
    except Exception:
        return str(raw_birthday)


async def async_setup_entry(hass, entry, async_add_entities):
    """Set up sensors for Feelfit (coordinator, 60s poll)."""
    data = hass.data[DOMAIN][entry.entry_id]
    api = data["api"]
    initial_user_info = data.get("user_info") or {}
    user_id = initial_user_info.get("user_id") or entry.unique_id or entry.entry_id

    async def async_update_data():
        """Coordinator update method — calls api.async_fetch_all which itself obtains primary_user first."""
        try:
            payload = await api.async_fetch_all(str(user_id))
            _LOGGER.debug("Feelfit coordinator fetched payload keys: %s", list(payload.keys()))
            return payload
        except Exception as err:
            _LOGGER.debug("Feelfit coordinator update failed: %s", err)
            raise UpdateFailed(f"Feelfit fetch failed: {err}") from err

    coordinator = DataUpdateCoordinator(
        hass,
        _LOGGER,
        name="feelfit",
        update_method=async_update_data,
        update_interval=timedelta(seconds=30),
    )

    await coordinator.async_refresh()
    data_fetched = coordinator.data or {}
    user_info = data_fetched.get("user_info") or initial_user_info
    user_settings = data_fetched.get("user_settings") or {}
    goals_payload = data_fetched.get("goals") or {}
    device_binds_payload = data_fetched.get("device_binds") or {}
    measurements_payload = data_fetched.get("measurements") or {}

    entities: list[SensorEntity] = []

    # Basic user_info sensors
    if user_info:
        entities.append(FeelfitUserSensor(coordinator, entry.entry_id, "account_name", "Account Name", None))
        if user_info.get("weight") is not None:
            entities.append(FeelfitUserSensor(coordinator, entry.entry_id, "weight", "Weight", KG_UNIT))
        if user_info.get("height") is not None:
            entities.append(FeelfitUserSensor(coordinator, entry.entry_id, "height", "Height", "cm"))
        if "birthday" in user_info:
            entities.append(FeelfitBirthdaySensor(coordinator, entry.entry_id, "birthday", "Birthday"))
        if user_info.get("email"):
            entities.append(FeelfitUserSensor(coordinator, entry.entry_id, "email", "Email", None))

    # goals sensors
    goals_list = goals_payload.get("goals") or []
    for g in goals_list:
        g_type = g.get("goal_type")
        unique = f"goal_{g_type}"
        label = f"Goal {g_type.title()}"
        unit = None
        if g_type == "weight":
            unit = KG_UNIT
        elif g_type == "bodyfat":
            unit = PERCENT
        elif g_type == "water":
            unit = "ml"
        entities.append(FeelfitGoalSensor(coordinator, entry.entry_id, unique, label, unit, g_type))

    # device binds sensors (enriched)
    device_binds = (device_binds_payload or {}).get("device_binds") or []
    for idx, d in enumerate(device_binds):
        scale_name = d.get("scale_name") or d.get("internal_model") or f"device_{idx}"
        unique = f"device_{idx}_{(d.get('mac') or idx)}"
        label = f"Feelfit {scale_name}"
        entities.append(FeelfitDeviceSensor(coordinator, entry.entry_id, unique, label, None, device_index=idx))

    # measurement sensors attached to person
    last_measurement = measurements_payload.get("last_measurement")
    if last_measurement:
        measurement_keys = [
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

        # unique preserve order
        seen = set()
        measurement_keys_unique = []
        for k, l, u in measurement_keys:
            if k not in seen:
                seen.add(k)
                measurement_keys_unique.append((k, l, u))

        for key, label, unit in measurement_keys_unique:
            unique = f"measurement_{key}"
            name = f"Feelfit Last Measurement {label}"
            entities.append(
                FeelfitMeasurementSensor(
                    coordinator,
                    entry.entry_id,
                    unique,
                    name,
                    unit,
                    measurement_key=key,
                )
            )

    async_add_entities(entities, True)


# ----------------------------
# Entities (CoordinatorEntity + SensorEntity)
# ----------------------------

class FeelfitUserSensor(CoordinatorEntity, SensorEntity):
    def __init__(self, coordinator: DataUpdateCoordinator, entry_id: str, attr_key: str, name: str, unit: str | None):
        CoordinatorEntity.__init__(self, coordinator)
        self._entry_id = entry_id
        self._attr_key = attr_key
        self._name = name
        self._unit = unit
        self._unique_id = f"{entry_id}_{attr_key}"
        self._attr_translation_key = attr_key
        self._attr_has_entity_name = True

    @property
    def unique_id(self) -> str:
        return self._unique_id

    # @property
    # def name(self) -> str:
        # return f"Feelfit {self._name}"

    @property
    def native_unit_of_measurement(self):
        return self._unit

    @property
    def native_value(self):
        user_info = (self.coordinator.data or {}).get("user_info") or {}
        return user_info.get(self._attr_key)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {"source": "feelfit", "attribute": self._attr_key}

    @property
    def device_info(self):
        # user_info = (self.coordinator.data or {}).get("user_info") or {}
        # user_id = user_info.get("user_id")
        # name = user_info.get("account_name") or f"Feelfit User {user_id or self._entry_id}"
        # return {
            # "identifiers": {(DOMAIN, user_id or self._entry_id)},
            # "name": name,
            # "manufacturer": "Feelfit",
            # "model": "Feelfit Account",
        # }
        user_info = (self.coordinator.data or {}).get("user_info") or {}
        user_id = user_info.get("user_id") or self._entry_id
        return {
            "identifiers": {(DOMAIN, f"user_{user_id}")},
            "name": user_info.get("account_name") or f"Feelfit User {user_id}",
            "manufacturer": "Feelfit",
            "model": "Feelfit Account",
        }

class FeelfitBirthdaySensor(CoordinatorEntity, SensorEntity):
    def __init__(self, coordinator: DataUpdateCoordinator, entry_id: str, attr_key: str, name: str):
        CoordinatorEntity.__init__(self, coordinator)
        self._entry_id = entry_id
        self._attr_key = attr_key
        self._name = name
        self._unique_id = f"{entry_id}_{attr_key}"
        self._attr_translation_key = attr_key
        self._attr_has_entity_name = True

    @property
    def unique_id(self) -> str:
        return self._unique_id

    # @property
    # def name(self) -> str:
        # return f"Feelfit {self._name}"

    @property
    def native_value(self):
        user_info = (self.coordinator.data or {}).get("user_info") or {}
        raw = user_info.get("birthday")
        user_settings = (self.coordinator.data or {}).get("user_settings") or {}
        fmt = user_settings.get("date_format")
        return _format_birthday(raw, fmt)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {"source": "feelfit", "attribute": self._attr_key, "date_format": (self.coordinator.data or {}).get("user_settings", {}).get("date_format")}

    @property
    def device_info(self):
        # user_info = (self.coordinator.data or {}).get("user_info") or {}
        # user_id = user_info.get("user_id")
        # name = user_info.get("account_name") or f"Feelfit User {user_id or self._entry_id}"
        # return {
            # "identifiers": {(DOMAIN, user_id or self._entry_id)},
            # "name": name,
            # "manufacturer": "Feelfit",
            # "model": "Feelfit Account",
        # }
        user_info = (self.coordinator.data or {}).get("user_info") or {}
        user_id = user_info.get("user_id") or self._entry_id
        return {
            "identifiers": {(DOMAIN, f"user_{user_id}")},
            "name": user_info.get("account_name") or f"Feelfit User {user_id}",
            "manufacturer": "Feelfit",
            "model": "Feelfit Account",
        }

class FeelfitGoalSensor(CoordinatorEntity, SensorEntity):
    def __init__(self, coordinator: DataUpdateCoordinator, entry_id: str, unique_key: str, name: str, unit: str | None, goal_type: str):
        CoordinatorEntity.__init__(self, coordinator)
        self._entry_id = entry_id
        self._unique_id = f"{entry_id}_{unique_key}"
        self._name = name
        self._unit = unit
        self._goal_type = goal_type
        self._attr_translation_key = unique_key
        self._attr_has_entity_name = True

    @property
    def unique_id(self) -> str:
        return self._unique_id

    # @property
    # def name(self) -> str:
        # return f"Feelfit {self._name}"

    @property
    def native_unit_of_measurement(self):
        return self._unit

    @property
    def native_value(self):
        goals_payload = (self.coordinator.data or {}).get("goals") or {}
        goals_list = goals_payload.get("goals") or []
        for g in goals_list:
            if g.get("goal_type") == self._goal_type:
                return g.get("goal_value")
        return None

    @property
    def device_info(self):
        # user_info = (self.coordinator.data or {}).get("user_info") or {}
        # user_id = user_info.get("user_id")
        # name = user_info.get("account_name") or f"Feelfit User {user_id or self._entry_id}"
        # return {"identifiers": {(DOMAIN, user_id or self._entry_id)}, "name": name, "manufacturer": "Feelfit", "model": "Feelfit Goals"}
        user_info = (self.coordinator.data or {}).get("user_info") or {}
        user_id = user_info.get("user_id") or self._entry_id
        return {
            "identifiers": {(DOMAIN, f"user_{user_id}")},
            "name": user_info.get("account_name") or f"Feelfit User {user_id}",
            "manufacturer": "Feelfit",
            "model": "Feelfit Account",
        }

class FeelfitDeviceSensor(CoordinatorEntity, SensorEntity):
    def __init__(self, coordinator: DataUpdateCoordinator, entry_id: str, unique_key: str, name: str, unit: str | None, device_index: int = 0):
        CoordinatorEntity.__init__(self, coordinator)
        self._entry_id = entry_id
        self._device_index = device_index
        self._unique_id = f"{entry_id}_device_{unique_key}"
        self._name = name
        self._unit = unit

    @property
    def unique_id(self) -> str:
        return self._unique_id

    @property
    def name(self) -> str:
        return self._name

    @property
    def native_value(self):
        device_binds = (self.coordinator.data or {}).get("device_binds", {}).get("device_binds") or []
        if len(device_binds) > self._device_index:
            d = device_binds[self._device_index]
            return d.get("scale_name") or d.get("internal_model") or d.get("mac")
        return None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        device_binds = (self.coordinator.data or {}).get("device_binds", {}).get("device_binds") or []
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
    def device_info(self):
        device_binds = (self.coordinator.data or {}).get("device_binds", {}).get("device_binds") or []
        user_info = (self.coordinator.data or {}).get("user_info") or {}
        if len(device_binds) > self._device_index:
            d = device_binds[self._device_index]
            scale_name = d.get("scale_name") or d.get("internal_model") or f"Device {self._device_index}"
            brand = d.get("brand_name") or (d.get("model_info") or {}).get("brand_info", {}).get("brand_name")
            friendly_name = f"Feelfit {scale_name}"
            if brand:
                friendly_name = f"{friendly_name} ({brand})"
            identifier = d.get("mac") or f"{user_info.get('user_id')}_device_{self._device_index}"
            return {
                "identifiers": {(DOMAIN, identifier)},
                "name": friendly_name,
                "manufacturer": brand or "Feelfit",
                "model": (d.get("model_info") or {}).get("model") or d.get("internal_model") or "Feelfit Device",
            }
        user_id = user_info.get("user_id")
        return {
            "identifiers": {(DOMAIN, f"{user_id}_device_{self._device_index}")},
            "name": f"{user_info.get('account_name', 'Feelfit User')} device {self._device_index}",
            "manufacturer": "Feelfit",
            "model": "Feelfit Device",
        }


class FeelfitMeasurementSensor(CoordinatorEntity, SensorEntity):
    """Sensor exposing a single key from the latest measurement, attached to the PERSON."""

    def __init__(self, coordinator: DataUpdateCoordinator, entry_id: str, unique_key: str, name: str, unit: str | None, measurement_key: str):
        CoordinatorEntity.__init__(self, coordinator)
        self._entry_id = entry_id
        self._unique_id = f"{entry_id}_{unique_key}"
        self._name = name
        self._unit = unit
        self._measurement_key = measurement_key
        self._attr_translation_key = "measurement_" + measurement_key
        self._attr_has_entity_name = True
        
    @property
    def unique_id(self) -> str:
        return self._unique_id

    # @property
    # def name(self) -> str:
        # return f"{self._name}"

    @property
    def native_unit_of_measurement(self):
        return self._unit

    @property
    def native_value(self):
        measurement = (self.coordinator.data or {}).get("measurements", {}).get("last_measurement")
        if not measurement:
            _LOGGER.debug("FeelfitMeasurementSensor: no measurement available for key %s", self._measurement_key)
            return None

        _LOGGER.debug("FeelfitMeasurementSensor: last_measurement = %s", measurement)
        raw_val = measurement.get(self._measurement_key)

        if self._measurement_key == "time_stamp" and raw_val:
            try:
                ts = int(raw_val)
                dt = datetime.fromtimestamp(ts)
                return dt.isoformat()
            except Exception:
                return str(raw_val)

        if isinstance(raw_val, (int, float)):
            if self._measurement_key in ("bodyage", "measurement_id", "user_id"):
                try:
                    return int(raw_val)
                except Exception:
                    return raw_val
            try:
                fval = float(raw_val)
                if fval.is_integer():
                    return int(fval)
                return round(fval, 2)
            except Exception:
                return raw_val

        if isinstance(raw_val, str):
            if raw_val.replace(".", "", 1).isdigit():
                try:
                    if "." in raw_val:
                        fval = float(raw_val)
                        if fval.is_integer():
                            return int(fval)
                        return round(fval, 2)
                    else:
                        return int(raw_val)
                except Exception:
                    pass
            return raw_val

        return raw_val

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        measurement = (self.coordinator.data or {}).get("measurements", {}).get("last_measurement")
        attrs: dict[str, Any] = {}
        if measurement:
            for k in ("measurement_id", "user_id", "scale_name", "internal_model", "mac", "parameter", "accuracy_flag", "measure_mode_flags"):
                if k in measurement:
                    attrs[k] = measurement.get(k)
        return attrs

    @property
    def device_info(self):
        # # attach measurement sensors to person device
        # user_info = (self.coordinator.data or {}).get("user_info") or {}
        # user_id = user_info.get("user_id")
        # name = user_info.get("account_name") or f"Feelfit User {user_id or self._entry_id}"
        # return {"identifiers": {(DOMAIN, user_id or self._entry_id)}, "name": name, "manufacturer": "Feelfit", "model": "Feelfit Measurement"}
        user_info = (self.coordinator.data or {}).get("user_info") or {}
        user_id = user_info.get("user_id") or self._entry_id
        return {
            "identifiers": {(DOMAIN, f"user_{user_id}")},
            "name": user_info.get("account_name") or f"Feelfit User {user_id}",
            "manufacturer": "Feelfit",
            "model": "Feelfit Account",
        }