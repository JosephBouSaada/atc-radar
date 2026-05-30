"""Precipitation radar tiles from the free RainViewer API.

API ref:   https://www.rainviewer.com/api.html
The API publishes a JSON index of recent radar frames; each frame is served
as 256x256 web-mercator slippy-map PNG tiles. We fetch a 3x3 grid centered
on our position so the crop window is always covered regardless of where
our lat/lon falls within its tile, then crop a 128x128 view.
"""

import io
import logging
import math
import time

import requests
from PIL import Image

from . import config

log = logging.getLogger("atc.weather")

_API_URL = "https://api.rainviewer.com/public/weather-maps.json"
_INDEX_TTL = 300         # 5 min — index is small and rarely changes
_IMAGE_TTL = 300         # 5 min — radar data refreshes every ~10 min

_idx_cache = {"ts": 0.0, "data": None}
_img_cache = {"ts": 0.0, "img": None, "frame_ts": 0, "key": None}


# -- tile math --------------------------------------------------------------
def _latlon_to_tile_xy(lat, lon, z):
    n = 2 ** z
    x = (lon + 180.0) / 360.0 * n
    lat_r = math.radians(lat)
    y = (1.0 - math.log(math.tan(lat_r) + 1 / math.cos(lat_r)) / math.pi) / 2.0 * n
    return x, y


def _km_per_tile(lat, z):
    """Approximate east-west km covered by one tile at this latitude."""
    return 40075.0 * math.cos(math.radians(lat)) / (2 ** z)


def _pick_zoom(lat, radius_km):
    """Pick zoom such that the 128x128 viewport shows ~2*radius_km wide.

    The viewport (128 px) should equal 2*radius_km. So 1 tile (256 px) covers
    4*radius_km. Find the largest zoom whose tile is still that wide.
    """
    target_km = 4.0 * radius_km
    for z in range(14, 2, -1):
        if _km_per_tile(lat, z) >= target_km:
            return z
    return 3


# -- API -------------------------------------------------------------------
def _get_index():
    now = time.monotonic()
    if _idx_cache["data"] and now - _idx_cache["ts"] < _INDEX_TTL:
        return _idx_cache["data"]
    r = requests.get(_API_URL, timeout=config.HTTP_TIMEOUT)
    r.raise_for_status()
    _idx_cache.update(ts=now, data=r.json())
    return _idx_cache["data"]


def get_radar_image(lat, lon, size_px=128, radius_km=None):
    """Return (PIL RGB image of size_px x size_px, frame_unix_ts).

    Returns (None, 0) if the API has no recent frame or all tile fetches fail.
    """
    radius_km = radius_km if radius_km is not None else config.SEARCH_RADIUS_KM

    idx = _get_index()
    host = idx.get("host")
    past = (idx.get("radar") or {}).get("past") or []
    if not host or not past:
        log.warning("RainViewer returned no recent radar frames")
        return None, 0

    latest = past[-1]
    frame_ts = int(latest["time"])
    frame_path = latest["path"]

    z = _pick_zoom(lat, radius_km)
    tx_f, ty_f = _latlon_to_tile_xy(lat, lon, z)
    tx, ty = int(tx_f), int(ty_f)

    cache_key = (z, frame_ts, tx, ty,
                 round(tx_f - tx, 2), round(ty_f - ty, 2),
                 size_px)
    now = time.monotonic()
    if (_img_cache["img"] is not None
            and _img_cache["key"] == cache_key
            and now - _img_cache["ts"] < _IMAGE_TTL):
        return _img_cache["img"], _img_cache["frame_ts"]

    # color scheme 2 = "Universal Blue", smooth=1, snow=1 = "1_1.png"
    composite = Image.new("RGBA", (256 * 3, 256 * 3), (0, 0, 0, 0))
    fetched = 0
    for dx in (-1, 0, 1):
        for dy in (-1, 0, 1):
            url = f"{host}{frame_path}/256/{z}/{tx + dx}/{ty + dy}/2/1_1.png"
            try:
                r = requests.get(url, timeout=config.HTTP_TIMEOUT)
                r.raise_for_status()
                tile = Image.open(io.BytesIO(r.content)).convert("RGBA")
                composite.paste(tile, ((dx + 1) * 256, (dy + 1) * 256))
                fetched += 1
            except requests.RequestException as e:
                log.debug("tile %d/%d/%d failed: %s", z, tx + dx, ty + dy, e)

    if fetched == 0:
        log.warning("All RainViewer tile fetches failed")
        return None, 0

    # Center of the 3x3 composite is at the middle tile + our fractional
    # position within it.
    cx = (tx_f - tx) * 256 + 256
    cy = (ty_f - ty) * 256 + 256
    left = int(round(cx - size_px / 2))
    top = int(round(cy - size_px / 2))
    crop = composite.crop((left, top, left + size_px, top + size_px))

    # Flatten over black so we can hand it to the display as plain RGB.
    out = Image.new("RGB", (size_px, size_px), (0, 0, 0))
    out.paste(crop, (0, 0), crop)

    _img_cache.update(ts=now, img=out, frame_ts=frame_ts, key=cache_key)
    log.info("Weather radar refreshed (z=%d, frame=%d, %d/9 tiles)",
             z, frame_ts, fetched)
    return out, frame_ts
