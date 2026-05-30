"""Main polling loop: locate -> fetch traffic -> render -> repeat.

The joystick lets the user pick one aircraft from the sorted list. The
selected aircraft renders at full brightness on the radar and footer list;
the others dim to 40%. Selection is tracked by ICAO24 so the highlight
stays on the same aircraft across traffic refreshes.
"""

import logging
import signal
import subprocess
import sys
import time

import requests

from . import config
from .display import Display
from .input import Input
from .location import get_location
from .traffic import get_traffic

log = logging.getLogger("atc")

_running = True


def _stop(*_):
    global _running
    _running = False


def _resolve_selection(selected_icao, aircraft, delta, clear):
    """Apply joystick input to the selection. Returns new selected_icao."""
    if not aircraft:
        return None
    if clear:
        return None

    if selected_icao is None:
        # First UP/DOWN press: enter selection at the nearest aircraft.
        if delta != 0:
            return aircraft[0].icao24
        return None

    # Find current selection in the (possibly new) list.
    try:
        idx = next(i for i, a in enumerate(aircraft)
                   if a.icao24 == selected_icao)
    except StopIteration:
        # Previously-selected aircraft is gone from this poll. Reset to top
        # if the user is still interacting; else clear.
        return aircraft[0].icao24 if delta != 0 else None

    new_idx = max(0, min(idx + delta, len(aircraft) - 1))
    return aircraft[new_idx].icao24


def run():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )
    signal.signal(signal.SIGINT, _stop)
    signal.signal(signal.SIGTERM, _stop)

    display = Display()
    inp = Input()
    log.info("Display mode: %s   input: %s",
             display.mode, "on" if inp.ok else "off")

    selected_icao = None
    loc = None
    aircraft = []
    next_poll = 0.0

    try:
        while _running:
            now = time.monotonic()

            # Time to refresh traffic?
            if now >= next_poll:
                try:
                    loc = get_location()
                    aircraft = get_traffic(loc[0], loc[1])
                except requests.RequestException as e:
                    log.warning("Network error this cycle: %s", e)
                except Exception:
                    log.exception("Unexpected error this cycle")
                next_poll = time.monotonic() + config.POLL_INTERVAL

                # Re-resolve selection in case the aircraft list changed.
                selected_icao = _resolve_selection(
                    selected_icao, aircraft, delta=0, clear=False)

            # Shutdown via held KEY3 — handle before anything else.
            if inp.consume_shutdown():
                log.warning("Initiating system shutdown")
                try:
                    display.show_message("shutting down...", color=(255, 80, 80))
                except Exception:
                    pass
                subprocess.Popen(
                    ["sudo", "shutdown", "-h", "now"],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                )
                _stop()
                break

            # Apply any pending input.
            delta = inp.consume_delta()
            clear = inp.consume_clear()
            inp.consume_press()    # reserved for future "details" view
            if delta or clear:
                selected_icao = _resolve_selection(
                    selected_icao, aircraft, delta, clear)

            # Render and wait either for input or for next poll.
            if loc is not None:
                display.show(loc, aircraft, selected_icao=selected_icao)

            sleep_for = max(0.5, next_poll - time.monotonic())
            if not inp.wait(timeout=sleep_for):
                pass   # timeout — fall through to refresh traffic
    finally:
        try:
            inp.cleanup()
        finally:
            display.sleep()
            log.info("Shutting down")


if __name__ == "__main__":
    sys.exit(run())
