"""Runtime configuration, all overridable via environment variables."""

import os


def _f(name, default):
    try:
        return float(os.environ[name])
    except (KeyError, ValueError):
        return default


def _i(name, default):
    try:
        return int(os.environ[name])
    except (KeyError, ValueError):
        return default


# --- Location ---------------------------------------------------------------
# Google Geolocation API key for WiFi-based positioning. If unset we fall back
# to IP-based geolocation (no key required, coarser accuracy).
GOOGLE_GEOLOCATION_KEY = os.environ.get("GOOGLE_GEOLOCATION_KEY", "").strip()

# Force a fixed location and skip all geolocation (decimal degrees).
# e.g. export ATC_LAT=51.47 ATC_LON=-0.4543
FIXED_LAT = _f("ATC_LAT", None) if "ATC_LAT" in os.environ else None
FIXED_LON = _f("ATC_LON", None) if "ATC_LON" in os.environ else None

# Wireless interface to scan for WiFi geolocation.
WIFI_IFACE = os.environ.get("ATC_WIFI_IFACE", "wlan0")

# How often to re-resolve location (seconds). Location rarely changes.
LOCATION_TTL = _i("ATC_LOCATION_TTL", 3600)


# --- Traffic ----------------------------------------------------------------
# Radius (km) of the search box around our location.
SEARCH_RADIUS_KM = _f("ATC_RADIUS_KM", 50.0)

# Optional OpenSky credentials raise the anonymous rate limit.
OPENSKY_USER = os.environ.get("OPENSKY_USER", "").strip()
OPENSKY_PASS = os.environ.get("OPENSKY_PASS", "").strip()

# Seconds between traffic polls. OpenSky anonymous limit is ~10s resolution.
POLL_INTERVAL = _i("ATC_POLL_INTERVAL", 30)

# Max aircraft to list on the display.
MAX_AIRCRAFT = _i("ATC_MAX_AIRCRAFT", 8)

HTTP_TIMEOUT = _i("ATC_HTTP_TIMEOUT", 15)


# --- Display ----------------------------------------------------------------
# Currently hardcoded for the Waveshare 1.44" LCD HAT (ST7735S, 128x128).
# Kept for future panel selection / dev-mode tagging.
EPD_PANEL = os.environ.get("ATC_PANEL", "st7735_1in44")

# When the LCD driver / hardware isn't reachable (e.g. dev on a laptop),
# render to a PNG file and print a text summary instead of touching hardware.
# "auto" picks hardware if available, else dev. Force with "hardware"/"dev".
DISPLAY_MODE = os.environ.get("ATC_DISPLAY_MODE", "auto").strip().lower()

# Where the dev renderer writes its PNG.
DEV_IMAGE_PATH = os.environ.get("ATC_DEV_IMAGE", "atc_radar_frame.png")

# Rotate the rendered image (degrees): 0/90/180/270.
DISPLAY_ROTATE = _i("ATC_ROTATE", 0)
