"""Constants for the Feelfit integration (centralized)."""

from typing import Dict

DOMAIN = "feelfit"
PLATFORMS = ["sensor"]
LOGGER = "custom_components.feelfit"

# Base API
API_BASE = "https://feelfit.qnclouds.com/api/v4"

# Default query params used by all endpoints (centralized)
DEFAULT_QUERY_PARAMS: Dict[str, str] = {
    "app_revision": "4.16.0",
    "html_version": "14.16.0",
    "cellphone_type": "samsung SM-T510",
    "system_type": "11_30",
    "zone": "Europe/Rome",
    "area_code": "IT",
    "locale": "it",
    "app_id": "Feelfit",
    "platform": "android",
}

# Endpoint paths (only paths, not full URLs)
PATH_LOGIN = "/users/sign_in"
PATH_USER_SETTINGS = "/user_settings/show_common_setting"
PATH_GOALS = "/goals/list_goal"
PATH_DEVICE_BINDS = "/device_binds/list_device_bind"
PATH_MEASUREMENTS = "/measurements/list_measurement"
PATH_GET_PRIMARY_USER = "/users/get_primary_user"  

# Public key (for password encryption)
PUBLIC_KEY = """-----BEGIN PUBLIC KEY-----
MIGfMA0GCSqGSIb3DQEBAQUAA4GNADCBiQKBgQC+25I2upukpfQ7rIaaTZtVE744
u2zV+HaagrUhDOTq8fMVf9yFQvEZh2/HKxFudUxP0dXUa8F6X4XmWumHdQnum3zm
Jr04fz2b2WCcN0ta/rbF2nYAnMVAk2OJVZAMudOiMWhcxV1nNJiKgTNNr13de0EQ
IiOL2CUBzu+HmIfUbQIDAQAB
-----END PUBLIC KEY-----"""

# Common headers (Authorization merged per request as needed)
COMMON_HEADERS = {
    "Accept-Encoding": "gzip",
    "Connection": "Keep-Alive",
    "Host": "feelfit.qnclouds.com",
    "User-Agent": "okhttp/4.9.1",
}

# Login-specific headers (content-type + placeholder Authorization as original)
LOGIN_HEADERS = {
    **COMMON_HEADERS,
    "Authorization": "Bearer",
    "Content-Type": "application/json;charset=UTF-8",
}
