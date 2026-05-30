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
    @staticmethod
    def _alt_color(alt_ft):
        a = alt_ft or 0
        if a > 25000:
            return (255, 80, 80)
        if a > 10000:
            return (255, 255, 0)
        return (0, 255, 0)

    @staticmethod
    def _dim(rgb, factor=0.4):
        return tuple(int(c * factor) for c in rgb)

    def _draw_frame(self, loc, aircraft, selected_icao=None):
        W, H = self.width, self.height
        img = Image.new("RGB", (W, H), (0, 0, 0))
        d = ImageDraw.Draw(img)

        f_hdr = _font(10)
        f_body = _font(9)
        has_sel = selected_icao is not None and any(
            a.icao24 == selected_icao for a in aircraft)

        # Header: count + UTC time (cyan, dimmed if a selection is active).
        now = datetime.now(timezone.utc).strftime("%H:%MZ")
        hdr_col = (0, 255, 255) if not has_sel else self._dim((0, 255, 255))
        d.text((1, 0), f"ATC {len(aircraft):2d}ac", font=f_hdr, fill=hdr_col)
        d.text((W - 38, 0), now, font=f_hdr, fill=hdr_col)

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
        d.text((cx + 2, cy - R - 1), "N", font=f_body,
               fill=(0, 180, 0) if not has_sel else self._dim((0, 180, 0)))

        # Blips: north-up, bearing/distance -> (x,y). Draw non-selected first
        # so the selected one ends up on top.
        max_km = config.SEARCH_RADIUS_KM
        sel_ac = None
        sel_pos = None
        for ac in aircraft:
            rr = R * min(ac.distance_km / max_km, 1.0)
            ang = math.radians(ac.bearing_deg)
            px = cx + rr * math.sin(ang)
            py = cy - rr * math.cos(ang)
            base = self._alt_color(ac.altitude_ft)
            selected = has_sel and ac.icao24 == selected_icao
            if selected:
                sel_ac, sel_pos = ac, (px, py, base)
                continue
            color = self._dim(base) if has_sel else base
            d.ellipse([px - 2, py - 2, px + 2, py + 2], fill=color)
            if ac.track is not None:
                tr = math.radians(ac.track)
                d.line([px, py,
                        px + 4 * math.sin(tr), py - 4 * math.cos(tr)],
                       fill=color)

        if sel_pos is not None:
            px, py, base = sel_pos
            # Halo + larger filled blip at full brightness.
            d.ellipse([px - 4, py - 4, px + 4, py + 4], outline=(255, 255, 255))
            d.ellipse([px - 2, py - 2, px + 2, py + 2], fill=base)
            if sel_ac.track is not None:
                tr = math.radians(sel_ac.track)
                d.line([px, py,
                        px + 6 * math.sin(tr), py - 6 * math.cos(tr)],
                       fill=base, width=2)

        # Footer list: 3 rows, scrolled to keep the selected aircraft visible.
        rows = 3
        if has_sel:
            sel_idx = next(i for i, a in enumerate(aircraft)
                           if a.icao24 == selected_icao)
            start = max(0, min(sel_idx - 1, len(aircraft) - rows))
        else:
            start = 0
        visible = aircraft[start:start + rows]

        y = radar_bot + 1
        line_h = 10
        for ac in visible:
            if y + line_h > H:
                break
            alt = ac.altitude_ft
            alt_s = f"{int(alt/100):03d}" if alt is not None else "---"
            selected = has_sel and ac.icao24 == selected_icao
            marker = ">" if selected else " "
            txt = f"{marker}{ac.label:<6.6} {ac.distance_km:3.0f}km FL{alt_s}"
            if has_sel and not selected:
                fg = (102, 102, 102)            # 40% white
            else:
                fg = (255, 255, 255)
            d.text((1, y), txt, font=f_body, fill=fg)
            y += line_h

        if not aircraft:
            d.text((1, radar_bot + 1), "no traffic in range",
                   font=f_body, fill=(180, 180, 180))

        return img

    # -- output --------------------------------------------------------------
    def show(self, loc, aircraft, selected_icao=None):
        img = self._draw_frame(loc, aircraft, selected_icao=selected_icao)
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

    def show_weather(self, loc, wx_image, frame_ts):
        """Render a precipitation radar frame with rings, crosshair, header."""
        W, H = self.width, self.height
        img = Image.new("RGB", (W, H), (0, 0, 0))

        if wx_image is not None:
            # The wx image is already sized to (W, H). Paste straight on.
            img.paste(wx_image, (0, 0))

        d = ImageDraw.Draw(img)
        f_hdr = _font(10)
        f_body = _font(9)

        # Range rings + crosshair overlay, dim so precipitation stays readable.
        radar_top = 12
        radar_bot = H - 14
        R = (radar_bot - radar_top) // 2
        cx, cy = W // 2, radar_top + R
        ring = (160, 160, 160)
        d.ellipse([cx - R, cy - R, cx + R, cy + R], outline=ring)
        d.ellipse([cx - R // 2, cy - R // 2, cx + R // 2, cy + R // 2],
                  outline=ring)
        d.line([cx - R, cy, cx + R, cy], fill=ring)
        d.line([cx, cy - R, cx, cy + R], fill=ring)
        # Center marker (our location).
        d.ellipse([cx - 1, cy - 1, cx + 1, cy + 1], fill=(255, 255, 255))

        # Header.
        d.text((1, 0), "WX RADAR", font=f_hdr, fill=(0, 255, 255))
        if frame_ts:
            ts = datetime.fromtimestamp(frame_ts, tz=timezone.utc)
            d.text((W - 38, 0), ts.strftime("%H:%MZ"),
                   font=f_hdr, fill=(0, 255, 255))
        else:
            d.text((W - 38, 0), "--:--", font=f_hdr, fill=(120, 120, 120))

        # Footer: radius label.
        d.text((1, H - 12), f"{int(config.SEARCH_RADIUS_KM)}km radius",
               font=f_body, fill=(180, 180, 180))

        if self.mode == "hardware":
            try:
                self.device.backlight(True)
            except Exception:
                pass
            self.device.display(img)
            log.info("pushed wx frame: %dx%d", img.width, img.height)
        else:
            img.save(config.DEV_IMAGE_PATH)
            log.info("Dev wx frame written to %s", config.DEV_IMAGE_PATH)

    def show_message(self, text, color=(255, 255, 255)):
        """Render a centered single-line message (used for shutdown notice)."""
        W, H = self.width, self.height
        img = Image.new("RGB", (W, H), (0, 0, 0))
        d = ImageDraw.Draw(img)
        f = _font(12)
        try:
            l, t, r, b = d.textbbox((0, 0), text, font=f)
            tw, th = r - l, b - t
        except AttributeError:
            tw, th = d.textsize(text, font=f)
        d.text(((W - tw) // 2, (H - th) // 2), text, font=f, fill=color)
        if self.mode == "hardware":
            try:
                self.device.backlight(True)
            except Exception:
                pass
            self.device.display(img)
        else:
            img.save(config.DEV_IMAGE_PATH)

    def sleep(self):
        if self.mode == "hardware" and self.device is not None:
            try:
                self.device.backlight(False)
                self.device.cleanup()
            except Exception as e:
                log.debug("device cleanup failed: %s", e)
