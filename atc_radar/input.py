"""Joystick + button handling for the Waveshare 1.44\" LCD HAT.

Hardware reference: https://www.waveshare.com/wiki/1.44inch_LCD_HAT
All inputs are active-low with internal pull-ups enabled.
"""

import logging
import threading
import time

log = logging.getLogger("atc.input")

# BCM pin numbers (from the Waveshare wiki).
PIN_UP = 6
PIN_DOWN = 19
PIN_LEFT = 5
PIN_RIGHT = 26
PIN_PRESS = 13
PIN_KEY1 = 21
PIN_KEY2 = 20
PIN_KEY3 = 16

_ALL_PINS = (PIN_UP, PIN_DOWN, PIN_LEFT, PIN_RIGHT, PIN_PRESS,
             PIN_KEY1, PIN_KEY2, PIN_KEY3)


class Input:
    """Event-driven joystick input. Coalesces presses between consumer reads.

    Use:
        inp = Input()
        ...
        inp.wait(timeout=30)        # blocks until input or timeout
        delta = inp.consume_delta() # net UP(-)/DOWN(+) since last consume
        if inp.consume_clear():
            ...                     # LEFT pressed -> clear selection
    """

    # Hold KEY3 this long to trigger a shutdown.
    SHUTDOWN_HOLD_SECONDS = 2.0

    def __init__(self):
        self.ok = False
        self.event = threading.Event()
        self._lock = threading.Lock()
        self._delta = 0
        self._clear = False
        self._press = False
        self._shutdown = False
        self._GPIO = None

        try:
            import RPi.GPIO as GPIO
            self._GPIO = GPIO
            GPIO.setmode(GPIO.BCM)
            GPIO.setwarnings(False)
            for pin in _ALL_PINS:
                GPIO.setup(pin, GPIO.IN, pull_up_down=GPIO.PUD_UP)
            # Falling edge = button pressed (active-low).
            GPIO.add_event_detect(PIN_UP,    GPIO.FALLING,
                                  callback=lambda p: self._bump(-1), bouncetime=180)
            GPIO.add_event_detect(PIN_DOWN,  GPIO.FALLING,
                                  callback=lambda p: self._bump(+1), bouncetime=180)
            GPIO.add_event_detect(PIN_LEFT,  GPIO.FALLING,
                                  callback=lambda p: self._mark_clear(), bouncetime=250)
            GPIO.add_event_detect(PIN_KEY1,  GPIO.FALLING,
                                  callback=lambda p: self._mark_clear(), bouncetime=250)
            GPIO.add_event_detect(PIN_PRESS, GPIO.FALLING,
                                  callback=lambda p: self._mark_press(), bouncetime=250)
            # KEY3 (bottom-right) = hold-to-shutdown.
            GPIO.add_event_detect(PIN_KEY3, GPIO.FALLING,
                                  callback=lambda p: self._on_shutdown_press(),
                                  bouncetime=250)
            self.ok = True
            log.info("Joystick input ready")
        except Exception as e:
            log.warning("No joystick input (%s); selection disabled", e)

    # -- callbacks (run on GPIO threads) -------------------------------------
    def _bump(self, n):
        with self._lock:
            self._delta += n
        self.event.set()

    def _mark_clear(self):
        with self._lock:
            self._clear = True
        self.event.set()

    def _mark_press(self):
        with self._lock:
            self._press = True
        self.event.set()

    def _on_shutdown_press(self):
        """KEY3 fell. Watch until it's released or held long enough to act."""
        threading.Thread(target=self._watch_shutdown_hold, daemon=True).start()

    def _watch_shutdown_hold(self):
        time.sleep(self.SHUTDOWN_HOLD_SECONDS)
        # Active-low: pressed == 0. If still down after the hold time, fire.
        try:
            still_down = self._GPIO.input(PIN_KEY3) == 0
        except Exception:
            still_down = False
        if still_down:
            log.warning("KEY3 held %.1fs — shutdown requested",
                        self.SHUTDOWN_HOLD_SECONDS)
            with self._lock:
                self._shutdown = True
            self.event.set()

    # -- consumer API --------------------------------------------------------
    def wait(self, timeout):
        """Block until any input or timeout. Returns True if input arrived."""
        triggered = self.event.wait(timeout=timeout)
        if triggered:
            self.event.clear()
        return triggered

    def consume_delta(self):
        with self._lock:
            d, self._delta = self._delta, 0
        return d

    def consume_clear(self):
        with self._lock:
            c, self._clear = self._clear, False
        return c

    def consume_press(self):
        with self._lock:
            p, self._press = self._press, False
        return p

    def consume_shutdown(self):
        with self._lock:
            s, self._shutdown = self._shutdown, False
        return s

    def cleanup(self):
        if self._GPIO is None:
            return
        try:
            for pin in _ALL_PINS:
                try:
                    self._GPIO.remove_event_detect(pin)
                except Exception:
                    pass
            self._GPIO.cleanup(list(_ALL_PINS))
        except Exception as e:
            log.debug("input cleanup: %s", e)
