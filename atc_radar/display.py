"""Render the radar frame and push it to a Waveshare 1.44" LCD HAT (ST7735S).

Hardware reference: https://www.waveshare.com/wiki/1.44inch_LCD_HAT
Pinout used by this board:
    LCD_CS  -> CE0  (handled by SPI device select)
    LCD_DC  -> GPIO 25
    LCD_RST -> GPIO 27
    LCD_BL  -> GPIO 24  (active-high backlight enable)
    Joystick + 3 buttons on GPIO 5/6/13/19/26/16/20/21 (not used here).

Falls back to writing a PNG when luma.lcd or the hardware isn't reachable,
so the same pipeline runs on a laptop.
"""

import logging
import math
from datetime import datetime, timezone

from PIL import Image, ImageDraw, ImageFont

from . import config

log = logging.getLogger("atc.display")

# 128x128 panel with a small offset because the ST7735S framebuffer is 132x132.
_LCD_W = 128
_LCD_H = 128
_LCD_HOFFSET = 2
_LCD_VOFFSET = 1
_LCD_RST_GPIO = 27
_LCD_DC_GPIO = 25
_LCD_BL_GPIO = 24


def _font(size):
    for path in (
        "/usr/share/fonts/truetype/dejavu/DejaVuSansMono-Bold.ttf",
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
        self.device = None
        self.width, self.height = _LCD_W, _LCD_H

        if self.mode in ("auto", "hardware"):
            try:
                from luma.core.interface.serial import spi
                from luma.lcd.device import st7735
                serial = spi(
                    port=0, device=0,
                    gpio_DC=_LCD_DC_GPIO, gpio_RST=_LCD_RST_GPIO,
                )
                self.device = st7735(
                    serial,
                    width=_LCD_W, height=_LCD_H,
                    h_offset=_LCD_HOFFSET, v_offset=_LCD_VOFFSET,
                    bgr=True,
                    rotate=config.DISPLAY_ROTATE // 90,
                    gpio_LIGHT=_LCD_BL_GPIO,
                    active_low=False,
                )
                self.device.backlight(True)
                self.width, self.height = self.device.width, self.device.height
                self.mode = "hardware"
                log.info("ST7735S LCD ready (%dx%d)", self.width, self.height)
            except Exception as e:
                if self.mode == "hardware":
                    raise
                log.warning("No LCD hardware (%s); using dev PNG renderer", e)
                self.mode = "dev"

    # -- drawing -------------------------------------------------------------
    def _draw_frame(self, loc, aircraft):
        W, H = self.width, self.height
        img = Image.new("RGB", (W, H), (0, 0, 0))
        d = ImageDraw.Draw(img)

        f_hdr = _font(10)
        f_body = _font(9)

        # Header: count + UTC time.
        now = datetime.now(timezone.utc).strftime("%H:%MZ")
        d.text((1, 0), f"ATC {len(aircraft):2d}ac", font=f_hdr, fill=(0, 255, 255))
        d.text((W - 38, 0), now, font=f_hdr, fill=(0, 255, 255))

        # Radar area — square, fills middle of screen.
        radar_top = 12
        radar_bot = H - 36   # leave 36px at bottom for the list
        R = (radar_bot - radar_top) // 2
        cx, cy = W // 2, radar_top + R

        # Range rings (50% and 100%) + crosshair, dim green.
        ring = (0, 100, 0)
        d.ellipse([cx - R, cy - R, cx + R, cy + R], outline=ring)
        d.ellipse([cx - R // 2, cy - R // 2, cx + R // 2, cy + R // 2], outline=ring)
        d.line([cx - R, cy, cx + R, cy], fill=ring)
        d.line([cx, cy - R, cx, cy + R], fill=ring)
        d.text((cx + 2, cy - R - 1), "N", font=f_body, fill=(0, 180, 0))

        # Blips: north-up, bearing/distance -> (x,y).
        max_km = config.SEARCH_RADIUS_KM
        for ac in aircraft:
            rr = R * min(ac.distance_km / max_km, 1.0)
            ang = math.radians(ac.bearing_deg)
            px = cx + rr * math.sin(ang)
            py = cy - rr * math.cos(ang)
            # Color by altitude band.
            alt = ac.altitude_ft or 0
            if alt > 25000:
                color = (255, 80, 80)     # high
            elif alt > 10000:
                color = (255, 255, 0)     # mid
            else:
                color = (0, 255, 0)       # low / ground
            d.ellipse([px - 2, py - 2, px + 2, py + 2], fill=color)
            # Heading tick (3px in track direction).
            if ac.track is not None:
                tr = math.radians(ac.track)
                tx = px + 4 * math.sin(tr)
                ty = py - 4 * math.cos(tr)
                d.line([px, py, tx, ty], fill=color)

        # Footer list: top 3 nearest, very tight.
        y = radar_bot + 1
        line_h = 10
        for ac in aircraft[:3]:
            if y + line_h > H:
                break
            alt = ac.altitude_ft
            alt_s = f"{int(alt/100):03d}" if alt is not None else "---"
            txt = f"{ac.label:<7.7} {ac.distance_km:3.0f}km FL{alt_s}"
            d.text((1, y), txt, font=f_body, fill=(255, 255, 255))
            y += line_h

        if not aircraft:
            d.text((1, radar_bot + 1), "no traffic in range",
                   font=f_body, fill=(180, 180, 180))

        return img

    # -- output --------------------------------------------------------------
    def show(self, loc, aircraft):
        img = self._draw_frame(loc, aircraft)
        if self.mode == "hardware":
            try:
                self.device.backlight(True)
            except Exception as e:
                log.debug("backlight on failed: %s", e)
            self.device.display(img)
            log.info("pushed frame: %d aircraft, %dx%d", len(aircraft), img.width, img.height)
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
        if self.mode == "hardware" and self.device is not None:
            try:
                self.device.backlight(False)
                self.device.cleanup()
            except Exception as e:
                log.debug("device cleanup failed: %s", e)
