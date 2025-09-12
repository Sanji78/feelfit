from __future__ import annotations

import base64
import copy
import logging
import time
import urllib.parse
from typing import Any, Dict, Optional

from aiohttp import ClientSession, ClientTimeout
from Crypto.Cipher import PKCS1_v1_5
from Crypto.PublicKey import RSA
import asyncio

from .const import (
    API_BASE,
    DEFAULT_QUERY_PARAMS,
    PATH_LOGIN,
    PATH_USER_SETTINGS,
    PATH_GOALS,
    PATH_DEVICE_BINDS,
    PATH_MEASUREMENTS,
    PATH_GET_PRIMARY_USER,
    LOGIN_HEADERS,
    COMMON_HEADERS,
    PUBLIC_KEY,
)

_LOGGER = logging.getLogger("custom_components.feelfit.api")


class FeelfitApiError(Exception):
    pass


class FeelfitApi:
    """API client for Feelfit with incremental measurement fetching."""

    def __init__(self, hass, session: ClientSession, email: str) -> None:
        self.hass = hass
        self._session = session
        self.email = email
        self.token: Optional[str] = None
        self.token_expires: Optional[float] = None
        # cached last-known user_info (populated at login or initial fetch)
        self.user_info: Dict[str, Any] = {}

        # measurements meta stored for incremental fetches during runtime:
        # {"last_updated_at": int, "last_measurement_id": str}
        self._last_measurements_meta: Dict[str, Any] = {}

    def _build_url(self, path: str, extra_params: Optional[Dict[str, str]] = None) -> str:
        params = copy.deepcopy(DEFAULT_QUERY_PARAMS)
        if extra_params:
            params.update({k: str(v) for k, v in extra_params.items()})
        query = urllib.parse.urlencode(params, safe="/")
        return f"{API_BASE}{path}?{query}"

    async def async_login(self, password: str) -> Dict[str, Any]:
        encrypted_pw = await self.hass.async_add_executor_job(self._encrypt_password, password)
        payload = {"email": self.email, "password": encrypted_pw}

        url = self._build_url(PATH_LOGIN)
        timeout = ClientTimeout(total=15)

        try:
            async with self._session.post(url, headers=LOGIN_HEADERS, json=payload, timeout=timeout) as resp:
                text = await resp.text()
                if resp.status != 200:
                    _LOGGER.error("Login HTTP error %s: %s", resp.status, text)
                    raise FeelfitApiError(f"HTTP {resp.status}: {text}")
                result = await resp.json(content_type=None)
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
        rsa_key = RSA.import_key(PUBLIC_KEY)
        cipher = PKCS1_v1_5.new(rsa_key)
        encrypted_bytes = cipher.encrypt(password.encode("utf-8"))
        return base64.b64encode(encrypted_bytes).decode("utf-8")

    def auth_header(self) -> Dict[str, str]:
        if not self.token:
            return {}
        return {"Authorization": f"Bearer {self.token}"}

    async def _get(self, path: str, extra_params: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
        url = self._build_url(path, extra_params)
        headers = {**COMMON_HEADERS, **self.auth_header()}
        timeout = ClientTimeout(total=15)
        try:
            async with self._session.get(url, headers=headers, timeout=timeout) as resp:
                text = await resp.text()
                if resp.status != 200:
                    _LOGGER.error("GET %s returned %s: %s", url, resp.status, text)
                    raise FeelfitApiError(f"HTTP {resp.status}: {text}")
                result = await resp.json(content_type=None)
        except Exception as exc:
            _LOGGER.exception("Error while GET %s", url)
            raise FeelfitApiError(str(exc)) from exc

        if isinstance(result, dict) and "data" in result:
            return result.get("data") or {}
        return result

    async def async_get_primary_user(self) -> Dict[str, Any]:
        """Get primary user profile (contains time_stamp used by the app)."""
        if not self.token:
            raise FeelfitApiError("Not authenticated")
        return await self._get(PATH_GET_PRIMARY_USER)

    async def async_get_user_settings(self) -> Dict[str, Any]:
        if not self.token:
            raise FeelfitApiError("Not authenticated")
        return await self._get(PATH_USER_SETTINGS)

    async def async_list_goals(self, user_id: str) -> Dict[str, Any]:
        if not self.token:
            raise FeelfitApiError("Not authenticated")
        return await self._get(PATH_GOALS, {"user_id": user_id})

    async def async_list_device_binds(self) -> Dict[str, Any]:
        if not self.token:
            raise FeelfitApiError("Not authenticated")
        return await self._get(PATH_DEVICE_BINDS)

    async def async_get_last_measurements(self, user_id: str, last_updated_at: int = 0, last_measurement_id: int = 0) -> Dict[str, Any]:
        """Call measurements endpoint."""
        if not self.token:
            raise FeelfitApiError("Not authenticated")
        extra = {"user_id": user_id, "last_updated_at": str(last_updated_at), "last_measurement_id": str(last_measurement_id)}
        return await self._get(PATH_MEASUREMENTS, extra)

    async def async_fetch_all(self, user_id: str) -> Dict[str, Any]:
        """Fetch aggregated data and perform robust incremental measurements fetch."""
        if not self.token:
            raise FeelfitApiError("Not authenticated")

        # Step A: get primary user first (to know latest user time_stamp used by the app)
        primary_data: Dict[str, Any] = {}
        try:
            primary_data = await self.async_get_primary_user()
            if isinstance(primary_data, dict) and "user_info" in primary_data:
                # update cached user_info
                self.user_info = primary_data.get("user_info") or self.user_info
        except Exception as exc:
            # log but continue — other endpoints may still work
            _LOGGER.debug("Could not fetch primary user: %s", exc)

        # compute last_updated_at to use for measurements fetch
        last_known_meta = self._last_measurements_meta or {}
        last_known_updated_at = int(last_known_meta.get("last_updated_at") or 0)
        primary_ts = 0
        try:
            pu = primary_data.get("user_info") if isinstance(primary_data, dict) else None
            if pu and pu.get("time_stamp"):
                primary_ts = int(pu.get("time_stamp"))
            elif self.user_info and self.user_info.get("time_stamp"):
                primary_ts = int(self.user_info.get("time_stamp"))
        except Exception:
            primary_ts = 0

        # Decide base_last_updated_at for the request:
        # - if we already have a measurements last_updated_at, use it (incremental)
        # - otherwise, fall back to primary_ts if present, else 0
        if last_known_updated_at > 0:
            request_last_updated_at = last_known_updated_at
        elif primary_ts > 0:
            # if we don't have any previous measurements meta, use primary_ts as baseline
            request_last_updated_at = primary_ts
        else:
            request_last_updated_at = 0

        # For measurement id we will use stored last_measurement_id (if any)
        last_measurement_id = last_known_meta.get("last_measurement_id", 0) or 0

        _LOGGER.debug(
            "Measurements fetch decision: last_known_updated_at=%s primary_ts=%s requesting last_updated_at=%s last_measurement_id=%s",
            last_known_updated_at,
            primary_ts,
            request_last_updated_at,
            last_measurement_id,
        )

        # Now trigger other endpoints concurrently including measurements
        # Use the chosen request_last_updated_at for measurements call
        tasks = [
            self.async_get_user_settings(),
            self.async_list_goals(user_id),
            self.async_list_device_binds(),
            self.async_get_last_measurements(user_id, last_updated_at=request_last_updated_at, last_measurement_id=last_measurement_id),
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        user_settings = {}
        goals = {}
        device_binds_data = {}
        measurements_data = {}

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

        # If measurements returned empty but primary_ts increased compared to last_known_updated_at,
        # try a safe fallback: request a full fetch (last_updated_at=0) to avoid missing a measurement.
        measurements_list = measurements_data.get("measurements") or []
        if not measurements_list:
            # If primary_ts suggests there might be newer data and we didn't have previous metadata, try fallback
            if primary_ts and primary_ts != last_known_updated_at:
                _LOGGER.debug("Measurements empty. primary_ts=%s != last_known_updated_at=%s -> retrying measurements with last_updated_at=0 as fallback", primary_ts, last_known_updated_at)
                try:
                    fallback = await self.async_get_last_measurements(user_id, last_updated_at=0, last_measurement_id=0)
                    measurements_data = fallback or {}
                    measurements_list = measurements_data.get("measurements") or []
                except Exception as exc:
                    _LOGGER.debug("Fallback measurements fetch failed: %s", exc)

        # After potential fallback, update stored measurements meta
        try:
            returned_last_updated_at = int(measurements_data.get("last_updated_at") or 0)
            returned_last_measurement_id = measurements_data.get("last_measurement_id") or 0
            if returned_last_updated_at:
                self._last_measurements_meta["last_updated_at"] = returned_last_updated_at
            if returned_last_measurement_id:
                self._last_measurements_meta["last_measurement_id"] = returned_last_measurement_id
            _LOGGER.debug("Updated internal measurements meta: %s", self._last_measurements_meta)
        except Exception:
            # be defensive: don't break on unexpected types
            _LOGGER.debug("Could not update last measurements meta from response: %s", measurements_data)

        # Enrich device_binds with device_models
        device_binds = device_binds_data.get("device_binds") or []
        device_models = device_binds_data.get("device_models") or []

        model_by_scale_and_internal = {}
        model_by_scale = {}
        for m in device_models:
            key = (m.get("scale_name"), m.get("internal_model"))
            if key not in model_by_scale_and_internal:
                model_by_scale_and_internal[key] = m
            scale = m.get("scale_name")
            if scale and scale not in model_by_scale:
                model_by_scale[scale] = m

        enriched_devices = []
        for d in device_binds:
            match = model_by_scale_and_internal.get((d.get("scale_name"), d.get("internal_model")))
            if not match:
                match = model_by_scale.get(d.get("scale_name"))
            if match:
                merged = dict(d)
                merged["model_info"] = match
                brand = match.get("brand_info") or {}
                if brand.get("brand_name"):
                    merged["brand_name"] = brand.get("brand_name")
            else:
                merged = dict(d)
            enriched_devices.append(merged)

        # choose last_measurement if any
        last_measurement = None
        if measurements_list:
            last_measurement = measurements_list[0]

        payload = {
            "user_info": self.user_info,
            "user_settings": user_settings,
            "goals": goals,
            "device_binds": {"device_binds": enriched_devices, "device_models": device_models},
            "measurements": {"last_measurement": last_measurement, "measurements": measurements_list, "last_updated_at": measurements_data.get("last_updated_at")},
            "primary_user": primary_data or {},
        }
        return payload
