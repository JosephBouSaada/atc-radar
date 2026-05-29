"""Fetch nearby aircraft from the OpenSky Network REST API."""

import logging
import math
from dataclasses import dataclass

import requests

from . import config

log = logging.getLogger("atc.traffic")

_OPENSKY_URL = "https://opensky-network.org/api/states/all"
_EARTH_KM = 6371.0


@dataclass
class Aircraft:
    icao24: str
    callsign: str
    country: str
    lon: float
    lat: float
    baro_alt: float | None      # metres
    on_ground: bool
    velocity: float | None      # m/s
    track: float | None         # degrees (true)
    vert_rate: float | None     # m/s
    geo_alt: float | None       # metres
    distance_km: float = 0.0
    bearing_deg: float = 0.0

    @property
    def altitude_ft(self):
        a = self.geo_alt if self.geo_alt is not None else self.baro_alt
        return None if a is None else a * 3.28084

    @property
    def speed_kt(self):
        return None if self.velocity is None else self.velocity * 1.94384

    @property
    def label(self):
        return self.callsign.strip() or self.icao24


def _haversine(lat1, lon1, lat2, lon2):
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlmb = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlmb / 2) ** 2
    return 2 * _EARTH_KM * math.asin(math.sqrt(a))


def _bearing(lat1, lon1, lat2, lon2):
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dl = math.radians(lon2 - lon1)
    y = math.sin(dl) * math.cos(p2)
    x = math.cos(p1) * math.sin(p2) - math.sin(p1) * math.cos(p2) * math.cos(dl)
    return (math.degrees(math.atan2(y, x)) + 360) % 360


def _bbox(lat, lon, radius_km):
    dlat = radius_km / 111.0
    dlon = radius_km / (111.0 * max(math.cos(math.radians(lat)), 0.01))
    return (lat - dlat, lon - dlon, lat + dlat, lon + dlon)


def get_traffic(lat, lon):
    """Return aircraft within config.SEARCH_RADIUS_KM, nearest first."""
    lamin, lomin, lamax, lomax = _bbox(lat, lon, config.SEARCH_RADIUS_KM)
    params = {"lamin": lamin, "lomin": lomin, "lamax": lamax, "lomax": lomax}
    auth = None
    if config.OPENSKY_USER and config.OPENSKY_PASS:
        auth = (config.OPENSKY_USER, config.OPENSKY_PASS)

    r = requests.get(_OPENSKY_URL, params=params, auth=auth, timeout=config.HTTP_TIMEOUT)
    r.raise_for_status()
    states = r.json().get("states") or []

    out = []
    for s in states:
        # OpenSky state vector indices, see API docs.
        s_lon, s_lat = s[5], s[6]
        if s_lon is None or s_lat is None:
            continue
        ac = Aircraft(
            icao24=s[0] or "",
            callsign=(s[1] or ""),
            country=s[2] or "",
            lon=s_lon,
            lat=s_lat,
            baro_alt=s[7],
            on_ground=bool(s[8]),
            velocity=s[9],
            track=s[10],
            vert_rate=s[11],
            geo_alt=s[13],
        )
        ac.distance_km = _haversine(lat, lon, s_lat, s_lon)
        ac.bearing_deg = _bearing(lat, lon, s_lat, s_lon)
        if ac.distance_km <= config.SEARCH_RADIUS_KM:
            out.append(ac)

    out.sort(key=lambda a: a.distance_km)
    log.info("Found %d aircraft within %.0f km", len(out), config.SEARCH_RADIUS_KM)
    return out
