"""Main polling loop: locate -> fetch traffic -> render -> repeat."""

import logging
import signal
import sys
import time

import requests

from . import config
from .display import Display
from .location import get_location
from .traffic import get_traffic

log = logging.getLogger("atc")

_running = True


def _stop(*_):
    global _running
    _running = False


def run():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )
    signal.signal(signal.SIGINT, _stop)
    signal.signal(signal.SIGTERM, _stop)

    display = Display()
    log.info("Display mode: %s", display.mode)

    try:
        while _running:
            start = time.monotonic()
            try:
                loc = get_location()
                aircraft = get_traffic(loc[0], loc[1])
                display.show(loc, aircraft)
            except requests.RequestException as e:
                log.warning("Network error this cycle: %s", e)
            except Exception:
                log.exception("Unexpected error this cycle")

            elapsed = time.monotonic() - start
            sleep_for = max(1.0, config.POLL_INTERVAL - elapsed)
            # Wake promptly on signal instead of sleeping the whole interval.
            while _running and sleep_for > 0:
                step = min(1.0, sleep_for)
                time.sleep(step)
                sleep_for -= step
    finally:
        display.sleep()
        log.info("Shutting down")


if __name__ == "__main__":
    sys.exit(run())
