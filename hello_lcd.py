"""Minimal sanity check for the Waveshare 1.44\" LCD HAT (ST7735S).

Run with the venv that has luma.lcd installed:
    .venv/bin/python hello_lcd.py

What you should see, in order:
    1. Backlight turns on.
    2. Red, green, blue full-screen flashes (proves pixel writes work).
    3. White screen with "HELLO" text and a diagonal line.
    4. Stays on for 5 seconds, then backlight off, exit.

Any traceback means a specific thing is wrong; paste it back.
"""

import sys
import time

from PIL import Image, ImageDraw, ImageFont

from luma.core.interface.serial import spi
from luma.lcd.device import st7735


def main():
    print("opening SPI...")
    serial = spi(port=0, device=0, gpio_DC=25, gpio_RST=27)

    print("initialising ST7735S...")
    device = st7735(
        serial,
        width=128, height=128,
        h_offset=2, v_offset=1,
        bgr=True,
        gpio_LIGHT=24, active_low=False,
    )
    device.backlight(True)
    print(f"device ready: {device.width}x{device.height}")

    for name, color in (("RED", (255, 0, 0)),
                        ("GREEN", (0, 255, 0)),
                        ("BLUE", (0, 0, 255))):
        print(f"flash {name}")
        img = Image.new("RGB", (device.width, device.height), color)
        device.display(img)
        time.sleep(0.7)

    print("draw HELLO")
    img = Image.new("RGB", (device.width, device.height), (255, 255, 255))
    d = ImageDraw.Draw(img)
    try:
        font = ImageFont.truetype(
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 20)
    except OSError:
        font = ImageFont.load_default()
    d.line([(0, 0), (device.width, device.height)], fill=(255, 0, 0), width=2)
    d.line([(device.width, 0), (0, device.height)], fill=(0, 0, 255), width=2)
    d.rectangle([2, 2, device.width - 3, device.height - 3],
                outline=(0, 0, 0), width=2)
    d.text((20, 50), "HELLO", fill=(0, 0, 0), font=font)
    device.display(img)

    print("holding for 5s...")
    time.sleep(5)
    device.backlight(False)
    print("done — if you saw colors + HELLO, the screen is fully working.")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"FAILED: {type(e).__name__}: {e}", file=sys.stderr)
        raise
