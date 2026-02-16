"""API client for Feelfit integration."""
from __future__ import annotations

import asyncio
import base64
import copy
import logging
import time
import urllib.parse
from typing import Any

from aiohttp import ClientSession, ClientTimeout
from Crypto.Cipher import PKCS1_v1_5
from Crypto.PublicKey import RSA

from .const import (
    API_BASE,
    COMMON_HEADERS,
    DEFAULT_QUERY_PARAMS,
    LOGIN_HEADERS,
    PATH_DEVICE_BINDS,
    PATH_GET_PRIMARY_USER,
    PATH_GOALS,
    PATH_LOGIN,
    PATH_MEASUREMENTS,
    PATH_USER_SETTINGS,
    PUBLIC_KEY,
)

_LOGGER = logging.getLogger("custom_components.feelfit.api")

class FeelfitApiError(Exception):
    """Exception for Feelfit API errors."""

class FeelfitApi:
    """API client for Feelfit with incremental measurement fetching."""

    def __init__(self, hass: Any, session: ClientSession, email: str) -> None:
        """Initialize the API client."""
        self.hass = hass
        self._session = session
        self.email = email
        self.token: str | None = None
        self.token_expires: float | None = None
        self.user_info: dict[str, Any] = {}
        self._last_measurements_meta: dict[str, dict[str, Any]] = {}

    def _build_url(self, path: str, extra_params: dict[str, str] | None = None) -> str:
        """Build URL with query parameters."""
        params = copy.deepcopy(DEFAULT_QUERY_PARAMS)
        if extra_params:
            params.update({k: str(v) for k, v in extra_params.items()})
        query = urllib.parse.urlencode(params, safe="/")
        return f"{API_BASE}{path}?{query}"

    async def async_login(self, password: str) -> dict[str, Any]:
        """Authenticate with Feelfit API."""
        encrypted_pw = await self.hass.async_add_executor_job(
            self._encrypt_password, password
        )
        payload = {"email": self.email, "password": encrypted_pw}

        url = self._build_url(PATH_LOGIN)
        timeout = ClientTimeout(total=15)

        try:
            async with self._session.post(
                url, headers=LOGIN_HEADERS, json=payload, timeout=timeout
            ) as resp:
                text = await resp.text()
                if resp.status != 200:
                    _LOGGER.error("Login HTTP error %s: %s", resp.status, text)
                    raise FeelfitApiError(f"HTTP {resp.status}: {text}")
                result = await resp.json(content_type=None)
        except FeelfitApiError:
            raise
        except Exception as exc:
            _LOGGER.exception("Error while calling Feelfit login endpoint")
            raise FeelfitApiError(str(exc)) from exc

        if str(result.get("code")) not in ("200", "0"):
            _LOGGER.error("Login failed: %s", result)
            raise FeelfitApiError(f"Login failed: {result}")

        data = result.get("data") or {}
        token_info = data.get("token_info") or {}
        token = token_info.get("token")
        remaining = token_info.get("remaining_time") or 0

        if token:
            self.token = token
            self.token_expires = time.time() + float(remaining or 0)

        self.user_info = data.get("user_info") or {}
        _LOGGER.debug("Login success user_id=%s", self.user_info.get("user_id"))
        return data

    def _encrypt_password(self, password: str) -> str:
        """Encrypt password with RSA public key."""
        rsa_key = RSA.import_key(PUBLIC_KEY)
        cipher = PKCS1_v1_5.new(rsa_key)
        encrypted_bytes = cipher.encrypt(password.encode("utf-8"))
        return base64.b64encode(encrypted_bytes).decode("utf-8")

    def auth_header(self) -> dict[str, str]:
        """Return authorization header."""
        if not self.token:
            return {}
        return {"Authorization": f"Bearer {self.token}"}

    async def _get(
        self, path: str, extra_params: dict[str, str] | None = None
    ) -> dict[str, Any]:
        """Perform GET request to API."""
        url = self._build_url(path, extra_params)
        headers = {**COMMON_HEADERS, **self.auth_header()}
        timeout = ClientTimeout(total=15)

        try:
            async with self._session.get(
                url, headers=headers, timeout=timeout
            ) as resp:
                text = await resp.text()
                if resp.status != 200:
                    _LOGGER.error("GET %s returned %s: %s", url, resp.status, text)
                    raise FeelfitApiError(f"HTTP {resp.status}: {text}")
                result = await resp.json(content_type=None)
        except FeelfitApiError:
            raise
        except Exception as exc:
            _LOGGER.exception("Error while GET %s", url)
            raise FeelfitApiError(str(exc)) from exc

        if isinstance(result, dict) and "data" in result:
            return result.get("data") or {}
        return result

    async def async_get_primary_user(self) -> dict[str, Any]:
        """Get primary user profile."""
        if not self.token:
            raise FeelfitApiError("Not authenticated")
        return await self._get(PATH_GET_PRIMARY_USER)

    async def async_get_user_settings(self) -> dict[str, Any]:
        """Get user settings."""
        if not self.token:
            raise FeelfitApiError("Not authenticated")
        return await self._get(PATH_USER_SETTINGS)

    async def async_list_goals(self, user_id: str) -> dict[str, Any]:
        """List user goals."""
        if not self.token:
            raise FeelfitApiError("Not authenticated")
        return await self._get(PATH_GOALS, {"user_id": user_id})

    async def async_list_device_binds(self) -> dict[str, Any]:
        """List bound devices."""
        if not self.token:
            raise FeelfitApiError("Not authenticated")
        return await self._get(PATH_DEVICE_BINDS)

    async def async_list_all_profiles(self) -> list[dict[str, Any]]:
        """List all user profiles (primary + sub users)."""
        if not self.token:
            raise FeelfitApiError("Not authenticated")

        profiles: list[dict[str, Any]] = []

        try:
            primary_data = await self._get(PATH_GET_PRIMARY_USER)
            primary_user = primary_data.get("user_info")

            if primary_user:
                primary_user["is_primary"] = True

                if not primary_user.get("account_name"):
                    primary_user["account_name"] = (
                        primary_user.get("nickname") or
                        primary_user.get("name") or
                        primary_user.get("username") or
                        primary_user.get("email", "").split("@")[0] if primary_user.get("email") else None or
                        "Profilo Primario"
                    )
                _LOGGER.debug("Primary profile: user_id=%s, account_name=%s",
                             primary_user.get("user_id"),
                             primary_user.get("account_name"))
                profiles.append(primary_user)
        except Exception as exc:
            _LOGGER.error("Failed to fetch primary user: %s", exc)

        try:

            sub_users_data = await self._get("/sub_users/list_sub_user")

            _LOGGER.debug("Sub users response: %s", sub_users_data)

            sub_users = []
            if isinstance(sub_users_data, dict):
                sub_users = (
                    sub_users_data.get("sub_users") or
                    sub_users_data.get("data") or
                    sub_users_data.get("users") or
                    []
                )
            elif isinstance(sub_users_data, list):
                sub_users = sub_users_data

            _LOGGER.debug("Found %d sub users", len(sub_users))

            for idx, user in enumerate(sub_users):
                user["is_primary"] = False

                if not user.get("account_name"):
                    user["account_name"] = (
                        user.get("nickname") or
                        user.get("name") or
                        user.get("username") or
                        user.get("email", "").split("@")[0] if user.get("email") else None or
                        f"Profilo {idx + 2}"
                    )
                _LOGGER.debug("Sub user %d: user_id=%s, account_name=%s",
                             idx + 1,
                             user.get("user_id"),
                             user.get("account_name"))
                profiles.append(user)

        except Exception as exc:
            _LOGGER.warning("Failed to fetch sub users (might not exist): %s", exc)

        _LOGGER.info("Found %d profiles total: %s",
                     len(profiles),
                     [f"{p.get('account_name')} (id={p.get('user_id')})" for p in profiles])
        return profiles

    async def async_get_last_measurements(
        self, user_id: str, last_updated_at: int = 0, last_measurement_id: int = 0
    ) -> dict[str, Any]:
        """Get measurements from API."""
        if not self.token:
            raise FeelfitApiError("Not authenticated")
        extra = {
            "user_id": user_id,
            "last_updated_at": str(last_updated_at),
            "last_measurement_id": str(last_measurement_id),
        }
        return await self._get(PATH_MEASUREMENTS, extra)

    async def async_fetch_all(
        self, user_id: str, selected_profiles: list[str] | None = None
    ) -> dict[str, Any]:
        """Fetch all data from API for selected profiles.

        Args:
            user_id: Primary user ID (for backward compatibility)
            selected_profiles: List of user_ids to fetch. If None, fetch all profiles.
        """
        if not self.token:
            raise FeelfitApiError("Not authenticated")

        all_profiles: list[dict[str, Any]] = []
        primary_data: dict[str, Any] = {}

        try:
            all_profiles = await self.async_list_all_profiles()
            primary_data = await self.async_get_primary_user()
            if isinstance(primary_data, dict) and "user_info" in primary_data:
                self.user_info = primary_data.get("user_info") or self.user_info
        except Exception as exc:
            _LOGGER.debug("Could not fetch profiles: %s", exc)

        if selected_profiles:
            profiles_to_fetch = [
                p for p in all_profiles
                if str(p.get("user_id")) in selected_profiles
            ]
            _LOGGER.debug(
                "Fetching %d of %d profiles based on selection",
                len(profiles_to_fetch),
                len(all_profiles)
            )
        else:
            profiles_to_fetch = all_profiles
            _LOGGER.debug("Fetching all %d profiles", len(profiles_to_fetch))

        all_profiles_data: list[dict[str, Any]] = []

        for profile in profiles_to_fetch:
            profile_user_id = str(profile.get("user_id"))
            _LOGGER.debug("Fetching data for profile: %s", profile.get("account_name"))

            last_known_meta = self._last_measurements_meta.get(profile_user_id, {})
            last_known_updated_at = int(last_known_meta.get("last_updated_at") or 0)
            primary_ts = 0

            try:
                if profile.get("time_stamp"):
                    primary_ts = int(profile.get("time_stamp"))
            except (ValueError, TypeError):
                primary_ts = 0

            if last_known_updated_at > 0:
                request_last_updated_at = last_known_updated_at
            elif primary_ts > 0:
                request_last_updated_at = primary_ts
            else:
                request_last_updated_at = 0

            last_measurement_id = int(last_known_meta.get("last_measurement_id", 0) or 0)

            _LOGGER.debug(
                "Profile %s measurements fetch: last_known=%s primary_ts=%s request=%s measurement_id=%s",
                profile.get("account_name"),
                last_known_updated_at,
                primary_ts,
                request_last_updated_at,
                last_measurement_id,
            )

            tasks = [
                self.async_get_user_settings(),
                self.async_list_goals(profile_user_id),
                self.async_get_last_measurements(
                    profile_user_id,
                    last_updated_at=request_last_updated_at,
                    last_measurement_id=last_measurement_id,
                ),
            ]
            results = await asyncio.gather(*tasks, return_exceptions=True)

            user_settings: dict[str, Any] = {}
            goals: dict[str, Any] = {}
            measurements_data: dict[str, Any] = {}

            for idx, res in enumerate(results):
                if isinstance(res, Exception):
                    _LOGGER.error("Error fetching index %s for profile %s: %s",
                                idx, profile.get("account_name"), res)
                    continue
                if idx == 0:
                    user_settings = res or {}
                elif idx == 1:
                    goals = res or {}
                elif idx == 2:
                    measurements_data = res or {}

            measurements_list = measurements_data.get("measurements") or []
            if not measurements_list and primary_ts and primary_ts != last_known_updated_at:
                _LOGGER.debug(
                    "Profile %s measurements empty, retrying with last_updated_at=0 as fallback",
                    profile.get("account_name")
                )
                try:
                    fallback = await self.async_get_last_measurements(
                        profile_user_id, last_updated_at=0, last_measurement_id=0
                    )
                    measurements_data = fallback or {}
                    measurements_list = measurements_data.get("measurements") or []
                except Exception as exc:
                    _LOGGER.debug("Fallback measurements fetch failed for profile %s: %s",
                                profile.get("account_name"), exc)

            try:
                returned_last_updated_at = int(measurements_data.get("last_updated_at") or 0)
                returned_last_measurement_id = measurements_data.get("last_measurement_id") or 0

                if profile_user_id not in self._last_measurements_meta:
                    self._last_measurements_meta[profile_user_id] = {}

                if returned_last_updated_at:
                    self._last_measurements_meta[profile_user_id]["last_updated_at"] = returned_last_updated_at
                if returned_last_measurement_id:
                    self._last_measurements_meta[profile_user_id]["last_measurement_id"] = returned_last_measurement_id

                _LOGGER.debug("Updated measurements meta for profile %s: %s",
                            profile.get("account_name"),
                            self._last_measurements_meta[profile_user_id])
            except (ValueError, TypeError):
                _LOGGER.debug(
                    "Could not update measurements meta for profile %s from response: %s",
                    profile.get("account_name"),
                    measurements_data
                )

            last_measurement = measurements_list[0] if measurements_list else None

            profile_data = {
                "user_info": profile,
                "user_settings": user_settings,
                "goals": goals,
                "measurements": {
                    "last_measurement": last_measurement,
                    "measurements": measurements_list,
                    "last_updated_at": measurements_data.get("last_updated_at"),
                },
            }
            all_profiles_data.append(profile_data)

        device_binds_data: dict[str, Any] = {}
        try:
            device_binds_data = await self.async_list_device_binds()
        except Exception as exc:
            _LOGGER.error("Error fetching device binds: %s", exc)

        device_binds = device_binds_data.get("device_binds") or []
        device_models = device_binds_data.get("device_models") or []

        model_by_scale_and_internal: dict[tuple[Any, Any], dict[str, Any]] = {}
        model_by_scale: dict[str, dict[str, Any]] = {}

        for m in device_models:
            key = (m.get("scale_name"), m.get("internal_model"))
            if key not in model_by_scale_and_internal:
                model_by_scale_and_internal[key] = m
            scale = m.get("scale_name")
            if scale and scale not in model_by_scale:
                model_by_scale[scale] = m

        enriched_devices: list[dict[str, Any]] = []
        for d in device_binds:
            match = model_by_scale_and_internal.get(
                (d.get("scale_name"), d.get("internal_model"))
            )
            if not match:
                match = model_by_scale.get(d.get("scale_name", ""))
            merged = dict(d)
            if match:
                merged["model_info"] = match
                brand = match.get("brand_info") or {}
                if brand.get("brand_name"):
                    merged["brand_name"] = brand.get("brand_name")
            enriched_devices.append(merged)

        return {
            "profiles": all_profiles_data,
            "all_profiles": all_profiles,
            "device_binds": {
                "device_binds": enriched_devices,
                "device_models": device_models,
            },
            "primary_user": primary_data or {},
        }
        """Fetch all data from API."""
        if not self.token:
            raise FeelfitApiError("Not authenticated")

        primary_data: dict[str, Any] = {}
        try:
            primary_data = await self.async_get_primary_user()
            if isinstance(primary_data, dict) and "user_info" in primary_data:
                self.user_info = primary_data.get("user_info") or self.user_info
        except Exception as exc:
            _LOGGER.debug("Could not fetch primary user: %s", exc)

        last_known_meta = self._last_measurements_meta or {}
        last_known_updated_at = int(last_known_meta.get("last_updated_at") or 0)
        primary_ts = 0

        try:
            pu = primary_data.get("user_info") if isinstance(primary_data, dict) else None
            if pu and pu.get("time_stamp"):
                primary_ts = int(pu.get("time_stamp"))
            elif self.user_info and self.user_info.get("time_stamp"):
                primary_ts = int(self.user_info.get("time_stamp"))
        except (ValueError, TypeError):
            primary_ts = 0

        if last_known_updated_at > 0:
            request_last_updated_at = last_known_updated_at
        elif primary_ts > 0:
            request_last_updated_at = primary_ts
        else:
            request_last_updated_at = 0

        last_measurement_id = int(last_known_meta.get("last_measurement_id", 0) or 0)

        _LOGGER.debug(
            "Measurements fetch: last_known=%s primary_ts=%s request=%s measurement_id=%s",
            last_known_updated_at,
            primary_ts,
            request_last_updated_at,
            last_measurement_id,
        )

        tasks = [
            self.async_get_user_settings(),
            self.async_list_goals(user_id),
            self.async_list_device_binds(),
            self.async_get_last_measurements(
                user_id,
                last_updated_at=request_last_updated_at,
                last_measurement_id=last_measurement_id,
            ),
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        user_settings: dict[str, Any] = {}
        goals: dict[str, Any] = {}
        device_binds_data: dict[str, Any] = {}
        measurements_data: dict[str, Any] = {}

        for idx, res in enumerate(results):
            if isinstance(res, Exception):
                _LOGGER.error("Error fetching index %s: %s", idx, res)
                continue
            if idx == 0:
                user_settings = res or {}
            elif idx == 1:
                goals = res or {}
            elif idx == 2:
                device_binds_data = res or {}
            elif idx == 3:
                measurements_data = res or {}

        measurements_list = measurements_data.get("measurements") or []
        if not measurements_list and primary_ts and primary_ts != last_known_updated_at:
            _LOGGER.debug(
                "Measurements empty, retrying with last_updated_at=0 as fallback"
            )
            try:
                fallback = await self.async_get_last_measurements(
                    user_id, last_updated_at=0, last_measurement_id=0
                )
                measurements_data = fallback or {}
                measurements_list = measurements_data.get("measurements") or []
            except Exception as exc:
                _LOGGER.debug("Fallback measurements fetch failed: %s", exc)

        try:
            returned_last_updated_at = int(measurements_data.get("last_updated_at") or 0)
            returned_last_measurement_id = measurements_data.get("last_measurement_id") or 0
            if returned_last_updated_at:
                self._last_measurements_meta["last_updated_at"] = returned_last_updated_at
            if returned_last_measurement_id:
                self._last_measurements_meta["last_measurement_id"] = returned_last_measurement_id
            _LOGGER.debug("Updated measurements meta: %s", self._last_measurements_meta)
        except (ValueError, TypeError):
            _LOGGER.debug(
                "Could not update measurements meta from response: %s", measurements_data
            )

        device_binds = device_binds_data.get("device_binds") or []
        device_models = device_binds_data.get("device_models") or []

        model_by_scale_and_internal: dict[tuple[Any, Any], dict[str, Any]] = {}
        model_by_scale: dict[str, dict[str, Any]] = {}

        for m in device_models:
            key = (m.get("scale_name"), m.get("internal_model"))
            if key not in model_by_scale_and_internal:
                model_by_scale_and_internal[key] = m
            scale = m.get("scale_name")
            if scale and scale not in model_by_scale:
                model_by_scale[scale] = m

        enriched_devices: list[dict[str, Any]] = []
        for d in device_binds:
            match = model_by_scale_and_internal.get(
                (d.get("scale_name"), d.get("internal_model"))
            )
            if not match:
                match = model_by_scale.get(d.get("scale_name", ""))
            merged = dict(d)
            if match:
                merged["model_info"] = match
                brand = match.get("brand_info") or {}
                if brand.get("brand_name"):
                    merged["brand_name"] = brand.get("brand_name")
            enriched_devices.append(merged)

        last_measurement = measurements_list[0] if measurements_list else None

        return {
            "user_info": self.user_info,
            "user_settings": user_settings,
            "goals": goals,
            "device_binds": {
                "device_binds": enriched_devices,
                "device_models": device_models,
            },
            "measurements": {
                "last_measurement": last_measurement,
                "measurements": measurements_list,
                "last_updated_at": measurements_data.get("last_updated_at"),
            },
            "primary_user": primary_data or {},
        }
