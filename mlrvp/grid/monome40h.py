"""Direct serial driver for original 40h-protocol monome grids.

Older monome devices (2007–2009 "kit" / "40h" / black-rubber 64) speak a
simple binary protocol over the FTDI USB serial port.  serialosc 1.4.x on
Windows ships only the `series` and `mext` protocol drivers — it does not
recognise 40h hardware, so handshakes time out and the device never appears.

This module bypasses serialosc entirely and talks to the FTDI port directly
with the documented 40h wire protocol (from libmonome src/proto/40h.h):

  PC → monome
    [0x20, (x<<4) | y]      LED off at (x,y)        — 2 bytes
    [0x21, (x<<4) | y]      LED on  at (x,y)        — 2 bytes
    [0x30, intensity]       global brightness 0..15 — 2 bytes
    [0x50, adc_mask]        ADC (tilt) enable        — 2 bytes
    [0x70 | y, byte]        LED row  (8-bit mask)   — 2 bytes
    [0x80 | x, byte]        LED col  (8-bit mask)   — 2 bytes

  monome → PC
    [0x00, (x<<4) | y]      Button release at (x,y) — 2 bytes
    [0x01, (x<<4) | y]      Button press   at (x,y) — 2 bytes
    [0x10–0x13, byte]       AUX_1 tilt-X value      — 2 bytes each
    [0x14–0x17, byte]       AUX_2 tilt-Y value      — 2 bytes each

The 40h protocol has **no LED brightness** — it's binary on/off — and we
use row writes (0x70|y) exclusively for efficiency.  Brightness values from
mlrVp (0/4/8/15) are thresholded: ≥8 = on, <8 = off.  This keeps the
bright moving playback head visible against the dim sub-loop background.

The adapter exposes a `.grid` attribute that mimics `monome.Grid` so the
existing LED-write call sites in `controller.py` need no changes.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from typing import Callable, Optional

log = logging.getLogger("mlrvp")


# ── 40h wire-protocol constants ──────────────────────────────────────────
# Originally taken from libmonome's src/proto/40h.h, BUT older kit firmware
# (pre-2010 black-rubber 64) appears to use SWAPPED LED opcodes — direct
# hardware testing on a M64-0858 confirmed `0x20 [pos]` lights an LED, not
# clears it.  We use the firmware's actual mapping here.
#
#   Input  (device → host) — opcodes still being identified by direct test
#     (see _reader_loop diagnostic logging)
#
#   Output (host → device)
#     0x20 [(x<<4)|y]        LED ON          ← firmware-confirmed
#     0x21 [(x<<4)|y]        LED ON (alias)  ← firmware-confirmed
#     0x30 [(x<<4)|y]        LED OFF         ← firmware-confirmed
#     0xC0                   ADC port 0 (X) activate — 256/m64 protocol (1 byte)
#     0xC1                   ADC port 1 (Y) / tilt activate — 256/m64 protocol (1 byte)
#     0xD0                   ADC port 0 deactivate — 256/m64 protocol (1 byte)
#     0xD1                   ADC port 1 deactivate — 256/m64 protocol (1 byte)
#     0x70|y [byte]          LED row (8-bit mask) — DO NOT USE: a single
#                            dropped wire byte misframes every subsequent
#                            row write and scatters LEDs across rows.
#     0x80|x [byte]          LED column (8-bit mask) — same risk
_CMD_BUTTON_UP    = 0x00  # 256 protocol: button release  [0x00, (x<<4)|y]
_CMD_BUTTON_DOWN  = 0x10  # 256 protocol: button press    [0x10, (x<<4)|y]
_CMD_LED_ON       = 0x20  # 256 protocol: LED ON  (empirically confirmed)
_CMD_LED_ON_ALT   = 0x21  # 256 protocol: LED ON alias
_CMD_LED_OFF      = 0x30  # 256 protocol: LED OFF (empirically confirmed)
# 256/m64 ADC/tilt enable (monomeserial messagePack_256_activatePort / deactivatePort)
# Single-byte commands — no second byte:
_CMD_256_ACTIVATE_PORT   = 0xC0  # + port index  (0=X, 1=Y/tilt)
_CMD_256_DEACTIVATE_PORT = 0xD0  # + port index (TX only)
# 256/m64 ADC data received from device.
# Probe confirmed M64 firmware uses 0xD0+port as the RX tilt data prefix,
# NOT 0xE0 (monomeserial kMessageType_256_auxiliaryInput=14 theory was wrong).
_CMD_256_AUX_INPUT       = 0xD0  # + port index; data byte follows (8-bit ADC val)
_CMD_LED_ROW      = 0x70   # | y — unused (framing-fragile)
_CMD_LED_COL      = 0x80   # | x — unused (framing-fragile)

_BAUD = 9600  # confirmed via diagnose_com7.py — device TX silent at all other rates
_READ_POLL_S = 0.002   # Blocking read timeout — bytes delivered immediately on arrival.

# 40h LEDs are binary on/off, but mlrVp uses 0/4/8/15 brightness levels:
#   LED_OFF=0  LED_DIM=4  LED_MED=8  LED_BRIGHT=15
# Threshold of 1 means any nonzero brightness lights the LED.  We chose
# 1 (not 8) so DIM sub-loop backgrounds and DIM seq-step LEDs are also
# visible on the binary 40h — without them the user reported "no row
# lights" because the bright playback head was missing single-frame
# windows but the dim background was always dark.
_ON_THRESHOLD = 1

# Some 40h firmware variants pack the row mask MSB-first (bit 7 = col 0).
# Set MLRVP_40H_BIT_REVERSE=1 to flip the bit order on every row write.
# NOTE: row writes are no longer used (kit firmware ignores them); this
# env-var is kept as a no-op so existing launch scripts don't error.
_BIT_REVERSE = os.environ.get("MLRVP_40H_BIT_REVERSE", "").strip() in ("1", "true", "yes")  # unused


def _try_import_serial():
    try:
        import serial  # noqa: F401
        return serial
    except ImportError:
        return None


def probe_40h(com_port: str, timeout: float = 0.6) -> bool:
    """Return True if `com_port` looks like a 40h monome.

    We can't actively interrogate (no firmware-version query exists in the
    40h protocol), so we just verify the port can be opened at the right
    baud and DTR can be raised.  A real handshake test would require us to
    light an LED and ask the user to confirm — out of scope here.
    """
    serial = _try_import_serial()
    if serial is None:
        return False
    try:
        sp = serial.Serial(
            port=com_port, baudrate=_BAUD,
            timeout=timeout, write_timeout=timeout,
            dsrdtr=False, rtscts=False,
        )
        # Set DTR HIGH so we don't pulse-reset the AVR on close.
        sp.dtr = True
        sp.rts = True
        time.sleep(0.05)
        sp.close()
        return True
    except (serial.SerialException, OSError):
        return False


class _Monome40hGrid:
    """The actual hardware-facing grid object — mirrors `monome.Grid` API.

    Methods used by `controller.py`:
        led_level_set(x, y, brightness)
        led_level_row(x_offset, y, [8 brightness values])
        tilt_set(sensor, enable)         (no-op for 40h)
        connect(host, port)              (no-op for 40h — already direct)

    Properties read by `controller.py`:
        id        — short identifier ("m64-XXXX")
        width     — 8
        height    — 8
        rotation  — always 0 (40h has no rotation memory)
    """

    def __init__(self, com_port: str, device_id: str = ""):
        self._com_port = com_port
        self.id = device_id or f"40h-{com_port}"
        self.width = 8
        self.height = 8
        self.rotation = 0

        self._sp = None
        self._sp_lock = threading.RLock()
        self._serial_mod = _try_import_serial()
        if self._serial_mod is None:
            raise RuntimeError("pyserial not installed — cannot use 40h direct mode")

        self._open()
        time.sleep(0.050)

        # LED state cache (binary).  GUI thread mutates `_led_on` under
        # `_tx_lock` and marks `_row_dirty[y]` for any row it touched.
        # The writer thread snapshots dirty rows and emits per-LED
        # `0x20 [(x<<4)|y]` / `0x21 [(x<<4)|y]` commands for changed cells.
        # Row writes (0x70|y [mask]) are NOT used — probe confirmed they
        # are silently ignored by this kit firmware.
        # Per-LED command semantics (empirically confirmed on this kit):
        #   0x20 [(x<<4)|y]  = LED ON
        #   0x21 [(x<<4)|y]  = LED ON (alias)
        #   0x30 [(x<<4)|y]  = LED OFF
        self._led_on   = [[False] * self.width for _ in range(self.height)]
        self._led_sent = [[False] * self.width for _ in range(self.height)]
        self._row_dirty = [False] * self.height

        # Clear device to all-off BEFORE starting threads so the initial
        # write goes out cleanly with no writer-thread races.
        self._init_clear_direct()

        # ── Writer thread (per-LED write model) ──────────────────────────
        self._tx_lock = threading.Lock()
        self._tx_event = threading.Event()
        self._tx_stop = threading.Event()
        self._writer_thread = threading.Thread(
            target=self._writer_loop, daemon=True,
            name=f"mlrvp-40h-tx-{com_port}",
        )
        self._writer_thread.start()

        # Reader thread for button events
        self._on_grid_key: Optional[Callable[[int, int, int], None]] = None
        self._on_tilt_cb: Optional[Callable[[int, int, int, int], None]] = None
        # Latest ADC values for each axis (centre = 128), EMA-smoothed (stored as float)
        self._last_tilt_x: float = 128.0
        self._last_tilt_y: float = 128.0
        # EMA low-pass filter state (float accumulators, centre = 128.0)
        # alpha controls how much each new sample moves the filter output.
        # At ~580 Hz, alpha=0.15 ≈ 15 Hz cutoff — removes ADC noise while
        # following actual tilts (0.5-5 Hz) with <15 ms latency.
        self._tilt_ema_x: float = 128.0
        self._tilt_ema_y: float = 128.0
        self._tilt_ema_alpha: float = 0.15
        # Rate limiter: only fire the tilt callback at most 60 Hz.
        # The EMA filter still runs at full sample rate so values are smooth.
        self._tilt_last_emit: float = 0.0
        self._tilt_min_interval: float = 1.0 / 60.0  # 60 Hz → ~17 ms
        self._reader_stop = threading.Event()
        self._reader_thread = threading.Thread(
            target=self._reader_loop, daemon=True,
            name=f"mlrvp-40h-rx-{com_port}",
        )
        self._reader_thread.start()

        # Scrub any firmware-side startup artifacts (ghost LEDs) by queuing a
        # full OFF frame through the writer after all init traffic is sent.
        self._queue_full_off_refresh()

    # ── connection lifecycle ──────────────────────────────────────────────
    def _open(self):
        serial = self._serial_mod
        with self._sp_lock:
            self._sp = serial.Serial(
                port=self._com_port, baudrate=_BAUD,
                timeout=_READ_POLL_S, write_timeout=0.1,
                dsrdtr=False, rtscts=False,
            )
            self._sp.dtr = True
            self._sp.rts = True
            try:
                self._sp.reset_input_buffer()
            except Exception:
                pass
        log.info("40h open: baud=%d dtr=True rts=True", _BAUD)
        # NOTE: We previously tried to lower the FTDI USB latency timer via
        # the D2XX driver (FT_OpenEx + FT_SetLatencyTimer) but that opens a
        # SECOND handle to the same device.  When USB renegotiates, that
        # second handle goes stale and Windows starts returning
        # PermissionError 13 ("Access is denied") on every WriteFile call —
        # killing all LED updates ~20 s into a session.  We rely on the
        # default 16 ms latency timer instead, which is fine for our use.

    def _try_set_ftdi_latency(self, ms: int):
        """Deprecated — see comment in _open(). Kept as no-op for safety."""
        return

    # NOTE: _bootstrap_listen was part of an earlier design and is no longer
    # called.  Removed to prevent AttributeError on self._prefetched_rx which
    # was never initialised in __init__.

    # NOTE: _reopen_for_rx_recovery and _ensure_output_enabled were part of
    # an earlier startup-delay design and are no longer called.  They have
    # been removed to prevent accidental AttributeError crashes (they relied
    # on _last_reopen_ts / _open_profile_idx / _OPEN_PROFILES etc. that were
    # never initialised in __init__).

    def _send_40h_prime(self):
        """Send the 00 01 05 sequence used in the successful diagnostic."""
        try:
            with self._sp_lock:
                if self._sp is None or not self._sp.is_open:
                    return
                self._sp.write(bytes([0x00, 0x01, 0x05]))
                self._sp.flush()
            log.info("40h prime sent: 0x00 0x01 0x05")
        except Exception as e:
            log.warning("40h prime failed: %s", e)

    def _queue_full_off_refresh(self):
        """Mark all cells as off and dirty so the writer re-evaluates state.

        Does NOT touch _led_sent to avoid the race where a cell that should
        be ON gets its _led_sent=True before the writer processes it, causing
        the writer to see no diff and never send the ON command.  If the
        hardware already matches (all off after _init_clear_direct), the
        writer will produce no traffic.  Call force_off_unlit_cells() after
        startup to clear any firmware-side ghost LEDs.
        """
        with self._tx_lock:
            for y in range(self.height):
                for x in range(self.width):
                    self._led_on[y][x] = False
                self._row_dirty[y] = True
        self._tx_event.set()

    def force_off_unlit_cells(self):
        """Force OFF to hardware for every cell that should currently be dark.

        Clears any LEDs the firmware turned on during its boot sequence that
        _init_clear_direct may have missed.  Safe to call from any thread;
        races with _update_control_leds are harmless: a cell that turns ON
        between this call and the writer processing will stay ON on hardware
        (which is correct).
        """
        with self._tx_lock:
            for y in range(self.height):
                row_needs_update = False
                for x in range(self.width):
                    if not self._led_on[y][x]:
                        # Pretend we already sent ON → diff forces OFF
                        self._led_sent[y][x] = True
                        row_needs_update = True
                if row_needs_update:
                    self._row_dirty[y] = True
        self._tx_event.set()

    def _init_clear_direct(self):
        """Reset device LEDs to all-off regardless of prior state.

        On this kit firmware:
            0x20 [(x<<4)|y]  = LED ON
            0x21 [(x<<4)|y]  = LED ON (alias)
            0x30 [(x<<4)|y]  = LED OFF

        Reliable clear strategy:
            64 × LED OFF (0x30 with per-cell coordinate)

        Call only when the writer thread is NOT running (i.e. from __init__
        before threads start, from close() after threads are joined, or
        from within the writer thread itself after a port reopen).
        Updates self._led_sent to all-False to match the new device state.
        """
        try:
            # Keep one command per packet for this firmware's tiny UART buffer.
            # Sending OFF cell-by-cell is deterministic and does not depend on
            # previous LED state.
            with self._sp_lock:
                for y in range(self.height):
                    for x in range(self.width):
                        self._sp.write(bytes([_CMD_LED_OFF,
                                              (x << 4) | (y & 0x0f)]))
                        self._sp.flush()
                        time.sleep(0.010)

            # Reflect known device state: all off
            self._led_sent = [[False] * self.width for _ in range(self.height)]
            log.info("40h LED clear: 64xOFF (0x30) with per-cmd flush (~0.7 s)")
        except Exception as e:
            log.warning("40h LED clear failed: %s", e)

    def close(self):
        # Stop the background threads FIRST so they cannot race with the
        # direct zero-writes below.  Failing to do this causes interleaved
        # bytes on the wire → corrupted LED commands → "random lights" when
        # the app exits.
        log.info("40h close() called for %s — stopping threads + clearing LEDs", self._com_port)
        self._reader_stop.set()
        self._tx_stop.set()
        self._tx_event.set()  # wake writer so it sees the stop flag immediately
        for t in (self._writer_thread, self._reader_thread):
            try:
                if t.is_alive():
                    t.join(timeout=0.5)
            except Exception:
                pass
        # Both threads are stopped — safe to write directly without races.
        # Send explicit OFF to all 64 cells (0x30 with coordinate payload).
        if self._sp is not None and self._sp.is_open:
            self._init_clear_direct()
        try:
            if self._sp is not None and self._sp.is_open:
                self._sp.close()
        except Exception:
            pass
        self._sp = None

    # ── public API expected by controller.py ──────────────────────────────
    async def connect(self, host: str = "", port: int = 0):  # noqa: D401
        """No-op (kept for pymonome API compatibility)."""
        return

    def led_level_set(self, x: int, y: int, brightness: int):
        """Set a single LED.  40h has no levels — threshold = on/off."""
        if not (0 <= x < self.width and 0 <= y < self.height):
            return
        on = brightness >= _ON_THRESHOLD
        with self._tx_lock:
            self._led_on[y][x] = on
            self._row_dirty[y] = True
        self._tx_event.set()

    def led_level_row(self, x_offset: int, y: int, values: list[int]):
        """Set 8 LEDs in row `y`.  `x_offset` ignored (only 8 cols on 64)."""
        if not (0 <= y < self.height):
            return
        with self._tx_lock:
            for x in range(min(8, len(values))):
                self._led_on[y][x] = values[x] >= _ON_THRESHOLD
            self._row_dirty[y] = True
        self._tx_event.set()

    def set_tilt_callback(self, cb: "Callable[[int, int, int, int], None]"):
        """cb(n, x, y, z) — raw 0-255 ADC values per axis."""
        self._on_tilt_cb = cb

    def tilt_set(self, sensor: int, enable: int):
        """Enable/disable ADC tilt streaming.

        M64 firmware uses the 256/m64 serial protocol.  ADC enable is a
        single-byte activatePort command (messagePack_256_activatePort in
        monomeserial source):
            enable:  0xC0 + port  (kMessageType_256_activatePort << 4)
            disable: 0xD0 + port  (kMessageType_256_deactivatePort << 4)

        Both ADC ports are always enabled/disabled together regardless of
        the `sensor` argument, matching monomeserial's tilt-enable behaviour.
        ADC data returns as [0xD0+port, 8bit_val] (auxiliaryInput messages).
        """
        try:
            if enable:
                # Port 0 (X axis): single byte 0xC0
                with self._sp_lock:
                    sp = self._sp
                    if sp is None or not sp.is_open:
                        return
                    sp.write(bytes([_CMD_256_ACTIVATE_PORT + 0]))
                    sp.flush()
                time.sleep(0.020)
                # Port 1 (Y axis / tilt): single byte 0xC1
                with self._sp_lock:
                    sp = self._sp
                    if sp is None or not sp.is_open:
                        return
                    sp.write(bytes([_CMD_256_ACTIVATE_PORT + 1]))
                    sp.flush()
                log.info("40h tilt_set: ADC enabled ([0xC0] + [0xC1])")
            else:
                # Port 0 disable: single byte 0xD0
                with self._sp_lock:
                    sp = self._sp
                    if sp is None or not sp.is_open:
                        return
                    sp.write(bytes([_CMD_256_DEACTIVATE_PORT + 0]))
                    sp.flush()
                time.sleep(0.020)
                # Port 1 disable: single byte 0xD1
                with self._sp_lock:
                    sp = self._sp
                    if sp is None or not sp.is_open:
                        return
                    sp.write(bytes([_CMD_256_DEACTIVATE_PORT + 1]))
                    sp.flush()
                log.info("40h tilt_set: ADC disabled ([0xD0] + [0xD1])")
        except Exception as e:
            log.warning("40h tilt_set failed: %s", e)

    def all(self, state: int):
        """Set every LED on or off."""
        on = bool(state)
        with self._tx_lock:
            for y in range(self.height):
                for x in range(self.width):
                    self._led_on[y][x] = on
                self._row_dirty[y] = True
        self._tx_event.set()

    # ── internals ─────────────────────────────────────────────────────────
    def _writer_loop(self):
        """Flush LED state to the serial port using per-LED writes.

        For each dirty row, compare desired state (`_led_on`) against last
        transmitted state (`_led_sent`) and emit `0x20 [(x<<4)|y]` (ON) or
        `0x30 [(x<<4)|y]` (OFF) only for cells that changed.  This avoids
        re-transmitting unchanged cells and limits the byte rate to the
        number of actual LED changes per frame (~4 bytes for a moving play
        head, 128 bytes worst-case for a full repaint).

        Row writes (0x70|y [mask]) are NOT used — probe confirmed they are
        silently ignored by this kit firmware.  Per-LED writes (0x20/0x30)
        are the only reliable LED mechanism.
        """
        log.info("40h writer thread started (%s)", self._com_port)
        # MAX_CELLS_PER_WRITE: keep packets short for the tiny UART buffer.
        # Cap to 2 cells (4 bytes); dirty rows with remaining
        # cells are re-queued for the next tick.
        MAX_CELLS_PER_WRITE = 2
        write_fail_count = 0
        last_fail_log = 0.0
        last_tx_log = 0.0
        try:
            while not self._tx_stop.is_set():
                try:
                    # Fast-poll (3 ms) when dirty rows exist, idle at 16 ms.
                    with self._tx_lock:
                        has_dirty = any(self._row_dirty)
                    poll_s = 0.003 if has_dirty else 0.016
                    self._tx_event.wait(timeout=poll_s)
                    self._tx_event.clear()

                    # Snapshot desired state for dirty rows under lock
                    pending: list[tuple[int, list[bool]]] = []
                    with self._tx_lock:
                        for y in range(self.height):
                            if not self._row_dirty[y]:
                                continue
                            pending.append((y, list(self._led_on[y])))
                            self._row_dirty[y] = False
                    if not pending:
                        continue
                    sp = self._sp
                    if sp is None or not sp.is_open:
                        # Re-mark dirty so we retry when port comes back
                        with self._tx_lock:
                            for y, _ in pending:
                                self._row_dirty[y] = True
                        continue

                    # Build packet — capped at MAX_CELLS_PER_WRITE.
                    # Collect ALL needed changes first, then truncate.
                    # _led_sent is writer-thread-private — no lock needed.
                    all_changes: list[tuple[int, int, bool]] = []
                    for y, row in pending:
                        for x in range(self.width):
                            on = row[x]
                            if self._led_sent[y][x] != on:
                                all_changes.append((y, x, on))

                    changed_cells = all_changes[:MAX_CELLS_PER_WRITE]
                    overflow     = all_changes[MAX_CELLS_PER_WRITE:]

                    # Re-dirty rows that have unsent cells this tick
                    if overflow:
                        retry_rows = {y for y, x, on in overflow}
                        with self._tx_lock:
                            for ry in retry_rows:
                                self._row_dirty[ry] = True

                    if not changed_cells:
                        continue

                    chunks = bytearray()
                    for y, x, on in changed_cells:
                        chunks.append(_CMD_LED_ON if on else _CMD_LED_OFF)
                        chunks.append((x << 4) | (y & 0x0f))

                    try:
                        # Snapshot _sp under lock, then write/flush WITHOUT holding
                        # the lock so the reader thread is never starved waiting
                        # for write+flush to complete (flush can block up to 16 ms
                        # for FTDI latency timer to fire).
                        with self._sp_lock:
                            sp_out = self._sp
                        if sp_out is None or not sp_out.is_open:
                            raise OSError("serial port not open")
                        sp_out.write(bytes(chunks))
                        sp_out.flush()  # force USB packet now — don't wait for FTDI 16ms timer
                        # Commit sent state only on success
                        for y, x, on in changed_cells:
                            self._led_sent[y][x] = on
                        # Throttled TX diagnostic
                        now_log = time.monotonic()
                        if now_log - last_tx_log >= 1.0:
                            log.debug("40h TX %d cell(s): %s",
                                      len(changed_cells),
                                      " ".join(f"({'ON ' if on else 'off'})"
                                               f"x{x}y{y}"
                                               for y, x, on in changed_cells[:8]))
                            last_tx_log = now_log
                        if write_fail_count > 0:
                            log.info("40h serial write recovered after %d failures",
                                     write_fail_count)
                            write_fail_count = 0
                            last_fail_log = 0.0
                    except Exception as e:
                        write_fail_count += 1
                        now_log = time.monotonic()
                        if now_log - last_fail_log >= 1.0:
                            log.warning("40h serial write failed (#%d): %s",
                                        write_fail_count, e)
                            last_fail_log = now_log
                        # Re-mark dirty so we retry
                        with self._tx_lock:
                            for y, _ in pending:
                                self._row_dirty[y] = True
                        time.sleep(0.05)
                        if write_fail_count >= 10:
                            log.warning("40h: %d consecutive write failures — "
                                        "attempting port reopen", write_fail_count)
                            try:
                                if self._sp is not None:
                                    try:
                                        self._sp.close()
                                    except Exception:
                                        pass
                                self._sp = None
                                time.sleep(0.5)
                                self._open()
                                log.info("40h: port reopen succeeded")
                                write_fail_count = 0
                                last_fail_log = 0.0
                                # Reset device to all-off + sync sent cache
                                self._init_clear_direct()
                                # Force full repaint to restore desired LED state
                                with self._tx_lock:
                                    for y in range(self.height):
                                        self._row_dirty[y] = True
                            except Exception as reopen_err:
                                log.warning("40h: port reopen failed: %s", reopen_err)
                                time.sleep(2.0)
                except Exception as e:
                    log.warning("40h writer iteration error: %s", e, exc_info=True)
                    time.sleep(0.1)
        except BaseException as e:  # noqa: BLE001
            log.error("40h writer thread crashed: %s", e, exc_info=True)
        finally:
            log.info("40h writer thread exiting (%s)", self._com_port)

    def set_button_callback(self, cb: Callable[[int, int, int], None]):
        """cb(x, y, state)  — state 0=release, 1=press."""
        self._on_grid_key = cb

    def _reader_loop(self):
        """Continuously read 2-byte 40h button events from the monome."""
        log.info("40h reader thread started (%s)", self._com_port)
        debug = log.isEnabledFor(logging.DEBUG)
        buf = bytearray()
        # Diagnostic: log every received byte.  Disabled by default to keep
        # the INFO log clean; set MLRVP_40H_RX_DEBUG=1 to enable.
        rx_debug = os.environ.get("MLRVP_40H_RX_DEBUG", "0").strip() not in ("0", "false", "no", "")
        # Heartbeat: every 5 seconds log how many RX bytes we've seen so we
        # can tell whether the reader is alive but starved (zero bytes)
        # vs actually receiving data.
        rx_total = 0
        last_hb = time.monotonic()
        try:
            while not self._reader_stop.is_set():
                try:
                    sp = self._sp
                    if sp is None or not sp.is_open:
                        time.sleep(0.05)
                        continue
                    now_hb = time.monotonic()
                    if now_hb - last_hb >= 5.0:
                        try:
                            with self._sp_lock:
                                cur = self._sp
                                in_w = cur.in_waiting if (cur is not None and cur.is_open) else -1
                        except Exception:
                            in_w = -1
                        log.debug("40h RX heartbeat: total=%d bytes, in_waiting=%d",
                                  rx_total, in_w)
                        last_hb = now_hb
                    # Snapshot port reference WITHOUT holding _sp_lock during
                    # the blocking read — the writer thread must not be starved
                    # while we block waiting for button bytes.
                    with self._sp_lock:
                        cur = self._sp
                    if cur is None or not cur.is_open:
                        time.sleep(0.005)
                        continue
                    try:
                        # Blocking read: returns as soon as ≥1 byte arrives, or
                        # after _READ_POLL_S (2 ms) if nothing comes.  This is
                        # more responsive than polling in_waiting every 5 ms
                        # because the OS wakes us immediately on byte arrival.
                        data = cur.read(1)
                        if data:
                            # Drain any additional bytes that arrived simultaneously
                            avail = cur.in_waiting
                            if avail > 0:
                                data += cur.read(avail)
                    except Exception:
                        time.sleep(0.05)
                        continue
                    if not data:
                        continue
                    rx_total += len(data)
                    if rx_debug:
                        log.info("40h RX (%d bytes): %s", len(data), data.hex())
                    if debug:
                        log.debug("40h rx: %s", data.hex())
                    buf.extend(data)
                    while len(buf) >= 2:
                        cmd = buf[0]

                        # ── Button events ─────────────────────────────────
                        # This hardware sends 0x10 for button press (DOWN) and
                        # 0x00 for release (UP).  0x01 is a fallback for kit
                        # variants that match the libmonome spec.
                        #
                        # NOTE: 0x10 is theoretically also PROTO_40h_AUX_1
                        # channel-0 (tilt X LSB).  However the ADC enable
                        # command does not produce any tilt stream on this
                        # firmware — the RX log shows only [0x10, pos] button
                        # frames, never an accelerometer stream.  Routing 0x10
                        # to the tilt handler (even when the tilt callback is
                        # set) breaks all button presses.  We therefore treat
                        # 0x10 unconditionally as button-down; tilt uses the
                        # remaining sub-opcodes 0x11–0x17 only.
                        if cmd == _CMD_BUTTON_UP or cmd == _CMD_BUTTON_DOWN or cmd == 0x01:
                            pos = buf[1]
                            del buf[:2]
                            state = 0 if cmd == _CMD_BUTTON_UP else 1
                            x = (pos >> 4) & 0x0f
                            y = pos & 0x0f
                            if rx_debug:
                                log.info("40h KEY: cmd=0x%02x x=%d y=%d state=%d",
                                         cmd, x, y, state)
                            cb = self._on_grid_key
                            if cb is not None:
                                try:
                                    cb(x, y, state)
                                except Exception:
                                    log.exception("40h button callback error")
                            continue

                        # ── AUX / tilt ADC data (256/m64 protocol) ───────
                        # M64 firmware uses 0xD0+port as tilt data prefix (empirically
                        # confirmed by probe_tilt.py):
                        #   [0xD0, x_val]  = X axis (port 0), 8-bit value
                        #   [0xD1, y_val]  = Y axis (port 1), 8-bit value
                        # Enabled by activatePort: [0xC0] (X) and [0xC1] (Y).
                        if (cmd & 0xF0) == _CMD_256_AUX_INPUT:
                            adc_val = buf[1]  # 8-bit value (0-255)
                            port = cmd & 0x0F
                            del buf[:2]
                            # ── EMA low-pass filter (runs at full ~580 Hz) ──
                            # Each new ADC sample is blended 15% into the
                            # running average; this cuts noise above ~15 Hz
                            # while keeping real tilt latency under 15 ms.
                            alpha = self._tilt_ema_alpha
                            if port == 0:
                                self._tilt_ema_x = (alpha * adc_val
                                                    + (1.0 - alpha) * self._tilt_ema_x)
                                self._last_tilt_x = self._tilt_ema_x  # float, no rounding
                            elif port == 1:
                                self._tilt_ema_y = (alpha * adc_val
                                                    + (1.0 - alpha) * self._tilt_ema_y)
                                self._last_tilt_y = self._tilt_ema_y  # float, no rounding
                            # ── Rate limiter (60 Hz max) ──
                            # Callback fires once per 33 ms using the latest
                            # smoothed values — prevents flooding Qt's event
                            # queue with 580 signals/sec.
                            cb = self._on_tilt_cb
                            if cb is not None:
                                now = time.monotonic()
                                if now - self._tilt_last_emit >= self._tilt_min_interval:
                                    self._tilt_last_emit = now
                                    try:
                                        cb(0, self._last_tilt_x, self._last_tilt_y, 128)
                                    except Exception:
                                        log.exception("40h tilt callback error")
                            continue

                        # Other unrecognised 2-byte opcodes: discard.
                        if (0x02 <= cmd <= 0x0f) or (0x11 <= cmd <= 0x1f):
                            del buf[:2]
                            continue

                        # Unknown / out of sync — drop one byte and retry
                        if rx_debug:
                            log.info("40h DROP byte 0x%02x (unknown opcode)", cmd)
                        del buf[0]
                except Exception as e:
                    log.warning("40h reader iteration error: %s", e, exc_info=True)
                    time.sleep(0.1)
        except BaseException as e:  # noqa: BLE001
            log.error("40h reader thread crashed: %s", e, exc_info=True)
        finally:
            log.info("40h reader thread exiting (%s)", self._com_port)


class Monome40hApp:
    """Wrapper that mimics `monome.GridApp` for `controller.py`.

    Exposes `.grid` (the `_Monome40hGrid`) and forwards button events to
    overridable hooks (`on_grid_ready`, `on_grid_key`, `on_grid_disconnect`,
    `on_tilt`).  Subclass it and override the hooks the same way you would
    with `monome.GridApp`.
    """

    def __init__(self):
        self.grid: Optional[_Monome40hGrid] = None

    # Hooks — override in subclass (matches pymonome interface)
    def on_grid_ready(self):
        pass

    def on_grid_key(self, x: int, y: int, s: int):
        pass

    def on_grid_disconnect(self, *_):
        pass

    def on_tilt(self, n: int, x: int, y: int, z: int):
        pass

    # ── connection management ────────────────────────────────────────────
    def open_direct(self, com_port: str, device_id: str = ""):
        """Open the 40h device on `com_port` and signal `on_grid_ready`."""
        if self.grid is not None:
            return
        self.grid = _Monome40hGrid(com_port, device_id=device_id)
        self.grid.set_button_callback(self._dispatch_key)
        self.grid.set_tilt_callback(self._dispatch_tilt)
        try:
            self.on_grid_ready()
        except Exception:
            log.exception("on_grid_ready failed")

    def close(self):
        g = self.grid
        if g is None:
            return
        try:
            g.close()
        finally:
            self.grid = None
            try:
                self.on_grid_disconnect()
            except Exception:
                log.exception("on_grid_disconnect failed")

    def _dispatch_key(self, x: int, y: int, s: int):
        try:
            self.on_grid_key(x, y, s)
        except Exception:
            log.exception("on_grid_key failed")

    def _dispatch_tilt(self, n: int, x: int, y: int, z: int):
        try:
            self.on_tilt(n, x, y, z)
        except Exception:
            log.exception("on_tilt failed")
