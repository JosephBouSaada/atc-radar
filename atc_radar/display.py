"""Render the radar frame and push it to a Waveshare e-Paper panel.

Falls back to writing a PNG + printing a text summary when the Waveshare
library or hardware isn't present, so the whole pipeline runs on a laptop.
"""

import logging
import math
from datetime import datetime, timezone

from PIL import Image, ImageDraw, ImageFont

from . import config

log = logging.getLogger("atc.display")

# Approx resolutions for dev mode when the real panel can't report its size.
_PANEL_SIZES = {
    "epd2in13": (250, 122), "epd2in13_v2": (250, 122),
    "epd2in13_v3": (250, 122), "epd2in13_v4": (250, 122),
    "epd2in7": (264, 176), "epd2in7_v2": (264, 176),
    "epd2in9": (296, 128), "epd2in9_v2": (296, 128),
    "epd4in2": (400, 300), "epd7in5_v2": (800, 480),
}


def _font(size):
    for path in (
        "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/System/Library/Fonts/Menlo.ttc",
        "/System/Library/Fonts/Supplemental/Arial.ttf",
    ):
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            continue
    return ImageFont.load_default()


class Display:
    def __init__(self):
        self.mode = config.DISPLAY_MODE
        self.epd = None
        self.width, self.height = _PANEL_SIZES.get(config.EPD_PANEL.lower(), (250, 122))

        if self.mode in ("auto", "hardware"):
            try:
                from waveshare_epd import __dict__ as _wsd  # noqa: F401
                mod = __import__(
                    f"waveshare_epd.{config.EPD_PANEL}", fromlist=["EPD"]
                )
                self.epd = mod.EPD()
                self.epd.init()
                self.epd.Clear(0xFF)
                # Panel reports its own native dimensions.
                self.width, self.height = self.epd.width, self.epd.height
                self.mode = "hardware"
                log.info("Waveshare %s ready (%dx%d)",
                         config.EPD_PANEL, self.width, self.height)
            except Exception as e:  # ImportError or RuntimeError from GPIO
                if self.mode == "hardware":
                    raise
                log.warning("No e-Paper hardware (%s); using dev PNG renderer", e)
                self.mode = "dev"

        # e-Paper buffers are landscape; many 2.13" panels are 122x250 native
        # and rendered rotated. We draw in landscape (w >= h).
        if self.height > self.width:
            self.width, self.height = self.height, self.width

    # -- drawing -------------------------------------------------------------
    def _draw_frame(self, loc, aircraft):
        W, H = self.width, self.height
        img = Image.new("1", (W, H), 255)  # 1-bit, white background
        d = ImageDraw.Draw(img)

        f_small = _font(max(9, H // 13))
        f_tiny = _font(max(8, H // 15))

        # Radar occupies a square on the left.
        radar = min(H, W // 2) - 4
        cx, cy = 2 + radar // 2, H // 2
        R = radar // 2 - 1

        # Range rings + crosshair.
        for frac in (0.5, 1.0):
            rr = int(R * frac)
            d.ellipse([cx - rr, cy - rr, cx + rr, cy + rr], outline=0)
        d.line([cx - R, cy, cx + R, cy], fill=0)
        d.line([cx, cy - R, cx, cy + R], fill=0)

        # Blips: map (bearing, distance) -> screen. North is up.
        max_km = config.SEARCH_RADIUS_KM
        for ac in aircraft:
            rr = R * min(ac.distance_km / max_km, 1.0)
            ang = math.radians(ac.bearing_deg)
            px = cx + rr * math.sin(ang)
            py = cy - rr * math.cos(ang)
            d.ellipse([px - 2, py - 2, px + 2, py + 2], fill=0)

        # Header line on the right column.
        rx = 2 * (cx) + 4
        if rx > W - 40:
            rx = W // 2 + 4
        now = datetime.now(timezone.utc).strftime("%H:%MZ")
        d.text((rx, 1), f"ATC {len(aircraft):2d}ac {now}", font=f_tiny, fill=0)
        d.text((rx, 1 + f_tiny.size + 1),
               f"{loc[0]:.2f},{loc[1]:.2f} {loc[2]}", font=f_tiny, fill=0)

        # Aircraft list.
        y = 2 + 2 * (f_tiny.size + 1) + 1
        line_h = f_small.size + 2
        for ac in aircraft[: config.MAX_AIRCRAFT]:
            if y + line_h > H:
                break
            alt = ac.altitude_ft
            alt_s = f"{int(alt/100):03d}" if alt is not None else "---"
            txt = f"{ac.label:<7.7} {ac.distance_km:3.0f}k FL{alt_s}"
            d.text((rx, y), txt, font=f_small, fill=0)
            y += line_h

        if not aircraft:
            d.text((rx, y), "no traffic", font=f_small, fill=0)

        if config.DISPLAY_ROTATE:
            img = img.rotate(config.DISPLAY_ROTATE, expand=True)
        return img

    # -- output --------------------------------------------------------------
    def show(self, loc, aircraft):
        img = self._draw_frame(loc, aircraft)
        if self.mode == "hardware":
            # e-Paper buffer expects portrait native orientation for many panels;
            # getbuffer handles the packing. Rotate back if we swapped above.
            buf_img = img
            if self.epd.width < self.epd.height and img.width > img.height:
                buf_img = img.rotate(90, expand=True)
            self.epd.display(self.epd.getbuffer(buf_img))
        else:
            img.save(config.DEV_IMAGE_PATH)
            self._print_summary(loc, aircraft)
            log.info("Dev frame written to %s", config.DEV_IMAGE_PATH)

    def _print_summary(self, loc, aircraft):
        print(f"\n=== ATC RADAR  {loc[0]:.4f},{loc[1]:.4f} ({loc[2]})  "
              f"{len(aircraft)} aircraft ===")
        for ac in aircraft[: config.MAX_AIRCRAFT]:
            alt = ac.altitude_ft
            print(f"  {ac.label:<8} {ac.distance_km:5.1f} km  "
                  f"brg {ac.bearing_deg:3.0f}  "
                  f"{'FL'+str(int(alt/100)) if alt else '  ---'}  "
                  f"{ac.country}")
        if not aircraft:
            print("  (no traffic in range)")

    def sleep(self):
        if self.mode == "hardware" and self.epd is not None:
            try:
                self.epd.sleep()
            except Exception as e:
                log.debug("epd.sleep failed: %s", e)
