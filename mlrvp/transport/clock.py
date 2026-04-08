"""Master clock / tempo engine for mlrVp.

Provides a stable tempo clock with beat/bar tracking and quantized triggering.
"""

import time
import logging
import threading
from typing import Callable, Optional

from mlrvp.config import QuantizeMode

log = logging.getLogger("mlrvp")


class MasterClock:
    """Master tempo clock with beat subdivision tracking.

    Provides:
    - Stable BPM-based timing
    - Beat/bar position tracking
    - Quantized trigger scheduling
    - Callbacks on beat boundaries
    """

    def __init__(self, tempo: float = 120.0, quantize: QuantizeMode = QuantizeMode.SIXTEENTH):
        self.tempo = tempo
        self.quantize = quantize
        self._running = False
        self._thread: Optional[threading.Thread] = None

        # Position tracking
        self._beat_position: float = 0.0  # in beats (0-based)
        self._bar_position: int = 0
        self._last_time: float = 0.0
        self._start_time: float = 0.0

        # Callbacks
        self._beat_callbacks: list[Callable] = []
        self._bar_callbacks: list[Callable] = []
        self._tick_callbacks: list[Callable] = []  # called every tick (~1ms)
        self._quantize_callbacks: list[Callable] = []

        # Pending tempo change (for quantized tempo changes)
        self._next_tempo: Optional[float] = None

    @property
    def beat_position(self) -> float:
        return self._beat_position

    @property
    def bar_position(self) -> int:
        return self._bar_position

    @property
    def beat_in_bar(self) -> float:
        return self._beat_position % 4.0

    def on_beat(self, callback: Callable):
        self._beat_callbacks.append(callback)

    def on_bar(self, callback: Callable):
        self._bar_callbacks.append(callback)

    def on_tick(self, callback: Callable):
        self._tick_callbacks.append(callback)

    def on_quantize(self, callback: Callable):
        self._quantize_callbacks.append(callback)

    def start(self):
        if self._running:
            return
        self._running = True
        self._start_time = time.perf_counter()
        self._last_time = self._start_time
        self._beat_position = 0.0
        self._bar_position = 0
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self):
        self._running = False
        if self._thread is not None:
            self._thread.join(timeout=1.0)
            self._thread = None

    def set_tempo(self, tempo: float, immediate: bool = False):
        """Set tempo. If not immediate, change happens on next beat."""
        tempo = max(20.0, min(300.0, tempo))
        if immediate:
            self.tempo = tempo
        else:
            self._next_tempo = tempo

    def get_quantize_delay(self) -> float:
        """Get delay in seconds until next quantize boundary."""
        if self.quantize == QuantizeMode.NONE:
            return 0.0

        beats_per_quant = {
            QuantizeMode.QUARTER: 1.0,
            QuantizeMode.EIGHTH: 0.5,
            QuantizeMode.SIXTEENTH: 0.25,
            QuantizeMode.BAR: 4.0,
        }.get(self.quantize, 0.25)

        current_in_quant = self._beat_position % beats_per_quant
        if current_in_quant < 0.001:
            return 0.0
        remaining_beats = beats_per_quant - current_in_quant
        return remaining_beats * (60.0 / self.tempo)

    def beats_to_seconds(self, beats: float) -> float:
        return beats * (60.0 / self.tempo)

    def seconds_to_beats(self, seconds: float) -> float:
        return seconds * (self.tempo / 60.0)

    def _run(self):
        """Clock thread main loop."""
        prev_beat_int = -1
        prev_bar = -1

        # Quantize tracking
        quant_divisions = {
            QuantizeMode.NONE: 0,
            QuantizeMode.QUARTER: 1.0,
            QuantizeMode.EIGHTH: 0.5,
            QuantizeMode.SIXTEENTH: 0.25,
            QuantizeMode.BAR: 4.0,
        }
        prev_quant_pos = -1.0

        while self._running:
            now = time.perf_counter()
            dt = now - self._last_time
            self._last_time = now

            # Advance beat position
            beats_elapsed = dt * (self.tempo / 60.0)
            self._beat_position += beats_elapsed

            # Check beat boundary
            current_beat_int = int(self._beat_position)
            if current_beat_int > prev_beat_int:
                prev_beat_int = current_beat_int
                for cb in self._beat_callbacks:
                    try:
                        cb(current_beat_int, self._beat_position)
                    except Exception as exc:
                        log.warning("Beat callback error: %s", exc)

                # Apply pending tempo change on beat
                if self._next_tempo is not None:
                    self.tempo = self._next_tempo
                    self._next_tempo = None

            # Check bar boundary
            current_bar = int(self._beat_position / 4)
            if current_bar > prev_bar:
                prev_bar = current_bar
                self._bar_position = current_bar
                for cb in self._bar_callbacks:
                    try:
                        cb(current_bar)
                    except Exception as exc:
                        log.warning("Bar callback error: %s", exc)

            # Check quantize boundary
            quant_div = quant_divisions.get(self.quantize, 0.25)
            if quant_div > 0:
                quant_pos = self._beat_position // quant_div
                if quant_pos > prev_quant_pos:
                    prev_quant_pos = quant_pos
                    for cb in self._quantize_callbacks:
                        try:
                            cb(self._beat_position)
                        except Exception as exc:
                            log.warning("Quantize callback error: %s", exc)

            # Tick callbacks
            for cb in self._tick_callbacks:
                try:
                    cb(self._beat_position, self.tempo)
                except Exception as exc:
                    log.warning("Tick callback error: %s", exc)

            # Sleep ~1ms for resolution
            time.sleep(0.001)
