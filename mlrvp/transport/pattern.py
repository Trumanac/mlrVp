"""Pattern recorder for mlrVp.

Records grid button presses and plays them back in a loop,
replicating the mlrV2 pattern recorder system.
"""

import logging
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
        self.length_beats = max(1, int(length_beats))

    def toggle_record(self, current_beat: float):
        """Toggle recording on/off."""
        if self._recording:
            self._recording = False
            self._playing = True  # Auto-play after recording
            self._start_beat = current_beat
        else:
            self.events.clear()
            self._recording = True
            self._playing = False
            self._start_beat = current_beat

    def toggle_play(self, current_beat: float):
        """Toggle playback on/off."""
        if self._playing:
            self._playing = False
        else:
            self._playing = True
            self._start_beat = current_beat

    def stop(self):
        """Stop recording and playback."""
        self._recording = False
        self._playing = False

    def clear(self):
        """Clear all recorded events."""
        self.events.clear()
        self._recording = False
        self._playing = False

    def record_event(self, current_beat: float, row: int, column: int,
                     event_type: str = 'trigger'):
        """Record a grid event during recording."""
        if not self._recording:
            return

        # Calculate position within the pattern
        pos_in_pattern = (current_beat - self._start_beat) % self.length_beats

        self.events.append(PatternEvent(
            beat_position=pos_in_pattern,
            event_type=event_type,
            row=row,
            column=column,
        ))

    def update(self, current_beat: float):
        """Called every tick to check if any events should fire during playback."""
        # Auto-stop recording when pattern duration reached
        if self._recording:
            elapsed = current_beat - self._start_beat
            if elapsed >= self.length_beats:
                self._recording = False
                if self.events:
                    self._playing = True
                if self._state_change_callback:
                    state = 'playing' if self._playing else 'idle'
                    try:
                        self._state_change_callback(self.index, state)
                    except Exception as exc:
                        log.warning("Pattern state callback error: %s", exc)

        if not self._playing or not self.events or self._trigger_callback is None:
            return

        # Current position in pattern
        pos = (current_beat - self._start_beat) % self.length_beats

        # Check for events that fall between last_pos and current_pos
        if self._last_playback_pos < 0:
            self._last_playback_pos = pos
            return

        for event in self.events:
            # Check if event falls in the window
            if self._last_playback_pos <= event.beat_position < pos:
                try:
                    self._trigger_callback(event.row, event.column, event.event_type)
                except Exception as exc:
                    log.warning("Pattern trigger callback error: %s", exc)
            elif pos < self._last_playback_pos:
                # Wrapped around
                if event.beat_position >= self._last_playback_pos or event.beat_position < pos:
                    try:
                        self._trigger_callback(event.row, event.column, event.event_type)
                    except Exception as exc:
                        log.warning("Pattern trigger callback error: %s", exc)

        self._last_playback_pos = pos

    @property
    def progress(self) -> float:
        """Current playback position as fraction 0-1 within pattern length."""
        if not self._playing:
            return 0.0
        return self._last_playback_pos / self.length_beats if self.length_beats > 0 else 0.0
