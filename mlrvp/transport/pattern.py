"""Pattern recorder for mlrVp.

Records grid button presses and plays them back in a loop,
replicating the mlrV2 pattern recorder system.
"""

import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Optional, Callable

log = logging.getLogger(__name__)


@dataclass
class PatternEvent:
    """A single recorded event."""
    beat_position: float     # when in the pattern (in beats)
    event_type: str          # 'trigger' or 'stop'
    row: int
    column: int = 0


class PatternRecorder:
    """Records and plays back grid events in a loop.

    mlrV2 has 4 pattern recorders with configurable loop lengths.
    """

    def __init__(self, index: int, length_beats: int = 4):
        self.index = index
        self.length_beats = length_beats
        self.events: list[PatternEvent] = []
        self._lock = threading.Lock()
        self._recording = False
        self._playing = False
        self._start_beat: float = 0.0
        self._current_beat: float = 0.0
        self._last_playback_pos: float = -1.0
        self._trigger_callback: Optional[Callable] = None
        self._state_change_callback: Optional[Callable] = None

    @property
    def recording(self) -> bool:
        return self._recording

    @property
    def playing(self) -> bool:
        return self._playing

    @property
    def has_events(self) -> bool:
        return len(self.events) > 0

    def set_trigger_callback(self, callback: Callable):
        """Set callback for playback triggers: callback(row, column, event_type)."""
        self._trigger_callback = callback

    def set_state_change_callback(self, callback: Callable):
        """Set callback for auto state changes: callback(index, state_str)."""
        self._state_change_callback = callback

    def set_length(self, length_beats: int):
        """Update the pattern loop length in beats."""
        with self._lock:
            self.length_beats = max(1, int(length_beats))

    def toggle_record(self, current_beat: float):
        """Toggle recording on/off."""
        with self._lock:
            if self._recording:
                self._recording = False
                self._playing = True  # Auto-play after recording
                self._start_beat = current_beat
                self._last_playback_pos = -1.0
            else:
                self.events.clear()
                self._recording = True
                self._playing = False
                self._start_beat = current_beat

    def toggle_play(self, current_beat: float):
        """Toggle playback on/off."""
        with self._lock:
            if self._playing:
                self._playing = False
            else:
                self._playing = True
                self._start_beat = current_beat
                self._last_playback_pos = -1.0

    def stop(self):
        """Stop recording and playback."""
        with self._lock:
            self._recording = False
            self._playing = False

    def clear(self):
        """Clear all recorded events."""
        with self._lock:
            self.events.clear()
            self._recording = False
            self._playing = False
            self._last_playback_pos = -1.0

    def record_event(self, current_beat: float, row: int, column: int,
                     event_type: str = 'trigger'):
        """Record a grid event during recording."""
        with self._lock:
            if not self._recording:
                return

            # Cap events to prevent unbounded memory growth.
            # Drop the oldest 10% in one shot rather than popping a single
            # element each call: list.pop(0) is O(n) and at the cap (n=10k)
            # every subsequent record_event would memmove ~10k pointers,
            # spiking the clock-thread under sustained recording. Trimming
            # in batches amortises that work over ~1000 events.
            if len(self.events) >= 10000:
                del self.events[:1000]

            # Calculate position within the pattern
            length = self.length_beats
            pos_in_pattern = (current_beat - self._start_beat) % length

            self.events.append(PatternEvent(
                beat_position=pos_in_pattern,
                event_type=event_type,
                row=row,
                column=column,
            ))

    def update(self, current_beat: float):
        """Called every beat to check if any events should fire during playback."""
        state_cb = None
        state = None
        events_snapshot = None
        trigger_cb = None

        with self._lock:
            # Auto-stop recording when pattern duration reached
            if self._recording:
                elapsed = current_beat - self._start_beat
                if elapsed >= self.length_beats:
                    self._recording = False
                    if self.events:
                        self._playing = True
                    state_cb = self._state_change_callback
                    state = 'playing' if self._playing else 'idle'

            if self._playing and self.events and self._trigger_callback is not None:
                events_snapshot = list(self.events)
                trigger_cb = self._trigger_callback
                start_beat = self._start_beat
                length_beats = self.length_beats
                last_pos = self._last_playback_pos

        # Fire state callback outside lock
        if state_cb is not None:
            try:
                state_cb(self.index, state)
            except Exception as exc:
                log.warning("Pattern state callback error: %s", exc)

        if events_snapshot is None:
            return

        # Current position in pattern
        pos = (current_beat - start_beat) % length_beats

        # Check for events that fall between last_pos and current_pos
        if last_pos < 0:
            with self._lock:
                self._last_playback_pos = pos
            return

        for event in events_snapshot:
            # Check if event falls in the window
            if last_pos <= event.beat_position < pos:
                try:
                    trigger_cb(event.row, event.column, event.event_type)
                except Exception as exc:
                    log.warning("Pattern trigger callback error: %s", exc)
            elif pos < last_pos:
                # Wrapped around
                if event.beat_position >= last_pos or event.beat_position < pos:
                    try:
                        trigger_cb(event.row, event.column, event.event_type)
                    except Exception as exc:
                        log.warning("Pattern trigger callback error: %s", exc)

        with self._lock:
            self._last_playback_pos = pos

    @property
    def progress(self) -> float:
        """Current playback position as fraction 0-1 within pattern length."""
        with self._lock:
            if not self._playing:
                return 0.0
            pos = self._last_playback_pos
        if pos < 0.0:
            return 0.0
        return pos / self.length_beats if self.length_beats > 0 else 0.0
