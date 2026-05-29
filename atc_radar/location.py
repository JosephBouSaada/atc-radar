"""Resolve our latitude/longitude.

Order of preference:
  1. Fixed location from config (ATC_LAT / ATC_LON).
  2. WiFi scan -> Google Geolocation API (needs GOOGLE_GEOLOCATION_KEY).
  3. IP-based geolocation (no key, coarse).

The result is cached for config.LOCATION_TTL seconds.
"""

import logging
import re
import subprocess
import time

import requests

from . import config

log = logging.getLogger("atc.location")

_cache = {"ts": 0.0, "loc": None}


def _scan_wifi():
    """Return a list of {"macAddress", "signalStrength"} from a WiFi scan.

    Uses `iw` (preferred on modern Raspberry Pi OS) and falls back to `iwlist`.
    Returns [] if scanning isn't possible (e.g. not on Linux / no permission).
    """
    iface = config.WIFI_IFACE

    # Try `iw dev <iface> scan` first.
    try:
        out = subprocess.run(
            ["iw", "dev", iface, "scan"],
            capture_output=True, text=True, timeout=20,
        ).stdout
        aps = []
        bssid, signal = None, None
        for line in out.splitlines():
            line = line.strip()
            m = re.match(r"BSS ([0-9a-fA-F:]{17})", line)
            if m:
                if bssid:
                    aps.append({"macAddress": bssid, "signalStrength": signal or -75})
                bssid, signal = m.group(1), None
            elif line.startswith("signal:"):
                sm = re.search(r"(-?\d+(?:\.\d+)?)", line)
                if sm:
                    signal = int(float(sm.group(1)))
        if bssid:
            aps.append({"macAddress": bssid, "signalStrength": signal or -75})
        if aps:
            return aps
    except (OSError, subprocess.SubprocessError) as e:
        log.debug("iw scan failed: %s", e)

    # Fall back to `iwlist <iface> scan`.
    try:
        out = subprocess.run(
            ["iwlist", iface, "scan"],
            capture_output=True, text=True, timeout=20,
        ).stdout
        aps = []
        bssid, signal = None, None
        for line in out.splitlines():
            line = line.strip()
            m = re.search(r"Address: ([0-9a-fA-F:]{17})", line)
            if m:
                if bssid:
                    aps.append({"macAddress": bssid, "signalStrength": signal or -75})
                bssid, signal = m.group(1), None
            else:
                sm = re.search(r"Signal level=(-?\d+)", line)
                if sm:
                    signal = int(sm.group(1))
        if bssid:
            aps.append({"macAddress": bssid, "signalStrength": signal or -75})
        return aps
    except (OSError, subprocess.SubprocessError) as e:
        log.debug("iwlist scan failed: %s", e)
        return []


def _geolocate_wifi():
    aps = _scan_wifi()
    if len(aps) < 2 or not config.GOOGLE_GEOLOCATION_KEY:
        return None
    try:
        r = requests.post(
            "https://www.googleapis.com/geolocation/v1/geolocate",
            params={"key": config.GOOGLE_GEOLOCATION_KEY},
            json={"considerIp": False, "wifiAccessPoints": aps},
            timeout=config.HTTP_TIMEOUT,
        )
        r.raise_for_status()
        loc = r.json()["location"]
        return (float(loc["lat"]), float(loc["lng"]), "wifi")
    except (requests.RequestException, KeyError, ValueError) as e:
        log.warning("WiFi geolocation failed: %s", e)
        return None


def _geolocate_ip():
    # ip-api.com: free, no key, JSON, generous for low-volume use.
    for url, lat_k, lon_k in (
        ("http://ip-api.com/json/", "lat", "lon"),
        ("https://ipapi.co/json/", "latitude", "longitude"),
    ):
        try:
            r = requests.get(url, timeout=config.HTTP_TIMEOUT)
            r.raise_for_status()
            d = r.json()
            return (float(d[lat_k]), float(d[lon_k]), "ip")
        except (requests.RequestException, KeyError, ValueError, TypeError) as e:
            log.debug("IP geolocation via %s failed: %s", url, e)
    return None


def get_location(force=False):
    """Return (lat, lon, source). Cached for LOCATION_TTL seconds."""
    now = time.monotonic()
    if not force and _cache["loc"] and now - _cache["ts"] < config.LOCATION_TTL:
        return _cache["loc"]

    loc = None
    if config.FIXED_LAT is not None and config.FIXED_LON is not None:
        loc = (config.FIXED_LAT, config.FIXED_LON, "fixed")
    if loc is None:
        loc = _geolocate_wifi()
    if loc is None:
        loc = _geolocate_ip()

    if loc is None:
        if _cache["loc"]:
            log.warning("Geolocation failed; reusing cached location")
            return _cache["loc"]
        raise RuntimeError("Could not determine location by any method")

    _cache.update(ts=now, loc=loc)
    log.info("Location: %.4f, %.4f (%s)", loc[0], loc[1], loc[2])
    return loc
