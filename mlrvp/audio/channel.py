"""Channel / track playback engine for mlrVp.

Each channel represents one row of the grid and handles:
- Sample playback with position tracking
- Speed/pitch control
- Playback modes (normal, reverse, oneshot, retrigger)
- Amplitude envelope (attack/release)
- Group assignment
"""

import numpy as np
from typing import Optional

from mlrvp.config import PlaybackMode, ChannelState, SAMPLE_RATE, BUFFER_SIZE
from mlrvp.audio.sample import SampleBuffer


class Channel:
    """A single playback channel mapped to one grid row."""

    # Crossfade length in samples (~5.8ms at 44100 — eliminates click on retrigger)
    XFADE_LEN = 256

    def __init__(self, index: int, sample_rate: int = SAMPLE_RATE):
        self.index = index
        self.sample_rate = sample_rate
        self.state = ChannelState()
        self.sample: Optional[SampleBuffer] = None

        # Playback state
        self._position: float = 0.0  # in samples, fractional for pitch shift
        self._playing: bool = False
        self._envelope: float = 0.0  # current envelope level (0-1)
        self._releasing: bool = False
        # Crossfade state for glitch-free retriggering / pitch changes
        self._xfade_buf = np.zeros((self.XFADE_LEN, 2), dtype=np.float32)
        self._xfade_active: bool = False
        self._xfade_pos: int = 0
        self._xfade_len: int = 0
        # Equal-power (cosine) crossfade curves — guarantees constant energy
        # for uncorrelated signals (different positions / different pitches)
        _t = np.linspace(0.0, 1.0, self.XFADE_LEN, dtype=np.float32)
        self._xfade_fade = np.cos(_t * (np.pi * 0.5))   # 1→0 fade-out
        self._fade_in_ramp = np.sin(_t * (np.pi * 0.5))  # 0→1 fade-in
        self._fade_in_remaining: int = 0

        # SLICE mode state
        self._slice_btn1: Optional[int] = None
        self._slice_btn2: Optional[int] = None
        self._slice_loop_start: Optional[float] = None
        self._slice_loop_end: Optional[float] = None
        self._slice_reverse: bool = False
        self._slice_latched: bool = False
        self._slice_held: set = set()
        self._slice_col_start: int = -1
        self._slice_col_end: int = -1

        # SEQ mode state
        self._seq_steps: set[int] = set()      # which columns are active
        self._seq_column: int = -1              # current step index (-1 = not started)
        self._seq_columns: int = 8             # total grid columns (set at trigger time)
        self._seq_playing_step: bool = False    # True while the sample is sounding this step

        # Pending sample for live preset transitions
        self._pending_sample_path: Optional[str] = None

        # Pre-allocated output buffer to avoid per-callback allocation
        self._output_buf = np.zeros((BUFFER_SIZE, 2), dtype=np.float32)
        self._buf_size = BUFFER_SIZE

        # Pre-allocated arange buffer for position computation
        self._arange_f64 = np.arange(BUFFER_SIZE, dtype=np.float64)
        self._arange_f32 = np.arange(BUFFER_SIZE, dtype=np.float32)

        # Pitch-shift cache (populated by app.py via librosa in a background thread)
        self._pitch_cache: Optional[np.ndarray] = None
        # Thread-safe pending cache: background thread writes here,
        # audio thread picks it up in process() to avoid cross-thread
        # reads of _position/_envelope in _snapshot_xfade_tail.
        _PITCH_CLEAR = object()  # sentinel meaning "clear the cache"
        self._PITCH_CLEAR = _PITCH_CLEAR
        self._pending_pitch_cache = None  # None=no change, _PITCH_CLEAR=clear, ndarray=new data

    @property
    def playing(self) -> bool:
        return self._playing

    @property
    def position(self) -> float:
        """Current playback position as fraction 0.0 - 1.0."""
        if self.sample is None or self.sample.num_frames == 0:
            return 0.0
        return self._position / self.sample.num_frames

    @property
    def position_samples(self) -> float:
        return self._position

    def load_sample(self, sample: SampleBuffer):
        """Load a sample into this channel."""
        self.sample = sample
        self.state.filename = sample.filename
        self._position = 0.0
        self.state.sel_start = 0.0
        self.state.sel_end = 1.0
        self._pitch_cache = None  # invalidate any cached pitch-shifted audio

    def _get_audio_data(self) -> np.ndarray:
        """Return pitch-shifted audio if cached, otherwise raw sample data."""
        if self._pitch_cache is not None:
            return self._pitch_cache
        return self.sample.data

    def set_pitch_cache(self, data: np.ndarray):
        """Stage new pitch-shifted audio from background thread.

        The actual swap + crossfade happens in process() on the audio
        thread, avoiding unsafe cross-thread reads of playback state.
        """
        self._pending_pitch_cache = data

    def clear_pitch_cache(self):
        """Stage pitch cache removal from any thread.

        The actual clear + crossfade happens in process() on the audio
        thread.
        """
        self._pending_pitch_cache = self._PITCH_CLEAR

    def trigger(self, column: int, total_columns: int):
        """Trigger playback from a grid column position.

        Maps the column to a position within the sample's selected region.
        Uses crossfade when retriggering during playback to avoid clicks.
        """
        if self.sample is None:
            return

        # Calculate position within sample
        sel_start_samp = self.state.sel_start * self.sample.num_frames
        sel_end_samp = self.state.sel_end * self.sample.num_frames
        sel_length = sel_end_samp - sel_start_samp

        if sel_length <= 0:
            return

        # If already playing, snapshot a short fade-out tail from the current
        # position so we can crossfade into the new trigger without a click.
        if self._playing and self.sample is not None and self._envelope > 0.01:
            self._snapshot_xfade_tail()
            self._fade_in_remaining = self.XFADE_LEN
            # Keep envelope at current level — the equal-power crossfade
            # ramp handles the smooth entry.  Resetting to 0 creates a
            # volume fight between attack ramp and crossfade.
        else:
            self._envelope = 0.0  # first trigger: normal attack from zero

        # mlrv step mapping: floor((button / grid_width) * steps) / steps
        # This distributes `state.steps` slices evenly across `total_columns` buttons.
        # Works correctly for downsampling (steps > columns), upsampling (steps < columns),
        # and non-divisible cases (e.g. 14 steps on 8 buttons).
        steps = self.state.steps if self.state.steps > 0 else total_columns
        slice_index = int((column / total_columns) * steps)
        col_frac = slice_index / steps

        # Reverse mode: invert mapping so column 0 starts from end
        if self.state.mode == PlaybackMode.REVERSE:
            self._position = sel_end_samp - col_frac * sel_length
        else:
            self._position = sel_start_samp + col_frac * sel_length

        self._playing = True
        self._releasing = False
        self.state.playing = True

    def _snapshot_xfade_tail(self):
        """Render a short fade-out snippet from the current position (vectorized).

        Now called only from the audio thread (via queued trigger/force_stop),
        so all state reads are safe.
        """
        xlen = self.XFADE_LEN
        buf = self._xfade_buf
        buf[:] = 0
        num_sample_frames = self.sample.num_frames
        if num_sample_frames < 2:
            return
        num_channels = self.sample.num_channels
        audio_data = self._get_audio_data()

        speed = self.state.speed * self.state.pitch
        if self.state.reverse or self.state.mode == PlaybackMode.REVERSE:
            speed = -speed

        positions = self._position + self._arange_f64[:xlen] * speed
        np.clip(positions, 0, num_sample_frames - 1.0, out=positions)
        pos_int = positions.astype(np.intp)
        pos_frac = (positions - pos_int).astype(np.float32)
        next_pos = np.minimum(pos_int + 1, num_sample_frames - 1)

        fade = self._xfade_fade
        env_gain = self._envelope * self.state.gain * self.state.velocity

        if num_channels >= 2:
            for ch in range(2):
                buf[:, ch] = (audio_data[pos_int, ch] * (1.0 - pos_frac) +
                              audio_data[next_pos, ch] * pos_frac) * fade * env_gain
        else:
            mono = (audio_data[pos_int, 0] * (1.0 - pos_frac) +
                    audio_data[next_pos, 0] * pos_frac) * fade * env_gain
            buf[:, 0] = mono
            buf[:, 1] = mono

        self._xfade_pos = 0
        self._xfade_len = xlen
        self._xfade_active = True

    def stop(self):
        """Stop playback (with release)."""
        if self._playing:
            self._releasing = True

    def force_stop(self):
        """Immediately stop playback (with short fade-out to avoid click).

        Called from the audio thread (via queued event).
        """
        if self._playing and self.sample is not None and self._envelope > 0.01:
            self._snapshot_xfade_tail()
        self._playing = False
        self._releasing = False
        self._envelope = 0.0
        self.state.playing = False

    def process(self, num_frames: int) -> np.ndarray:
        """Generate audio output for this channel (vectorized).

        Returns stereo float32 array of shape (num_frames, 2).
        """
        # Use pre-allocated buffer to avoid per-callback allocation
        if num_frames > self._buf_size:
            self._output_buf = np.zeros((num_frames, 2), dtype=np.float32)
            self._buf_size = num_frames
            self._arange_f64 = np.arange(num_frames, dtype=np.float64)
            self._arange_f32 = np.arange(num_frames, dtype=np.float32)
        output = self._output_buf[:num_frames]
        output[:] = 0

        # --- Apply pending pitch cache swap (thread-safe) ---
        # Background thread stages data in _pending_pitch_cache;
        # we consume it here on the audio thread where state reads are safe.
        pending = self._pending_pitch_cache
        if pending is not None:
            self._pending_pitch_cache = None
            if pending is self._PITCH_CLEAR:
                if self._pitch_cache is not None:
                    if self._playing and self.sample is not None and self._envelope > 0.01:
                        self._snapshot_xfade_tail()
                        self._fade_in_remaining = self.XFADE_LEN
                    self._pitch_cache = None
            else:
                if self._playing and self.sample is not None and self._envelope > 0.01:
                    self._snapshot_xfade_tail()
                    self._fade_in_remaining = self.XFADE_LEN
                self._pitch_cache = pending

        if not self._playing or self.sample is None:
            # Mix in crossfade tail even when not playing so
            # force_stop fade-outs are audible
            if self._xfade_active and self._xfade_pos < self._xfade_len:
                remaining = self._xfade_len - self._xfade_pos
                n = min(remaining, num_frames)
                output[:n] += self._xfade_buf[self._xfade_pos:self._xfade_pos + n]
                self._xfade_pos += n
                if self._xfade_pos >= self._xfade_len:
                    self._xfade_active = False
            return output

        sample_data = self._get_audio_data()
        num_sample_frames = self.sample.num_frames
        num_channels = self.sample.num_channels

        sel_start = self.state.sel_start * num_sample_frames
        sel_end = self.state.sel_end * num_sample_frames
        sel_length = sel_end - sel_start

        if sel_length <= 0:
            self._playing = False
            self.state.playing = False
            return output

        # Speed calculation (pitch * speed * direction)
        # Note: transpose is handled via pre-shifted audio in _pitch_cache (see set_pitch_cache)
        speed = self.state.speed * self.state.pitch
        is_reverse = self.state.reverse or self.state.mode == PlaybackMode.REVERSE
        # SLICE mode sub-loop reverse
        if self.state.mode == PlaybackMode.SLICE and getattr(self, '_slice_reverse', False):
            is_reverse = not is_reverse
        if is_reverse:
            speed = -speed

        # Effective loop bounds (SLICE sub-loop overrides)
        loop_start = sel_start
        loop_end = sel_end
        if self.state.mode == PlaybackMode.SLICE:
            slice_ls = getattr(self, '_slice_loop_start', None)
            slice_le = getattr(self, '_slice_loop_end', None)
            if slice_ls is not None and slice_le is not None:
                loop_start = slice_ls
                loop_end = slice_le
        loop_len = loop_end - loop_start

        # Attack/release times in samples
        attack_samples = max(1, int(self.state.attack * self.sample_rate))
        release_samples = max(1, int(self.state.release * self.sample_rate))

        active_frames = num_frames

        # --- Envelope computation (vectorized ramp) ---
        if self._releasing:
            frames_to_zero = max(1, int(self._envelope * release_samples) + 1)
            active_frames = min(active_frames, frames_to_zero)
            env = self._envelope - self._arange_f32[:active_frames] / release_samples
            np.maximum(env, 0.0, out=env)
            self._envelope = float(env[-1]) if active_frames > 0 else 0.0
            if self._envelope <= 0:
                self._envelope = 0.0
                self._playing = False
                self.state.playing = False
        else:
            if self._envelope < 1.0:
                frames_to_full = max(1, int((1.0 - self._envelope) * attack_samples) + 1)
                if frames_to_full >= active_frames:
                    # Still ramping at end of block
                    env = self._envelope + self._arange_f32[:active_frames] / attack_samples
                    np.minimum(env, 1.0, out=env)
                else:
                    # Attack -> sustain transition within block
                    env = np.ones(active_frames, dtype=np.float32)
                    env[:frames_to_full] = self._envelope + self._arange_f32[:frames_to_full] / attack_samples
                    np.minimum(env[:frames_to_full], 1.0, out=env[:frames_to_full])
                self._envelope = float(env[-1])
            else:
                env = None  # Fully open — apply gain only

        if active_frames == 0:
            return output

        # --- Position computation (vectorized) ---
        positions = self._position + self._arange_f64[:active_frames] * speed

        # Handle oneshot boundary (ONESHOT and SEQ both play through once then stop)
        stop_after = False
        if self.state.mode in (PlaybackMode.ONESHOT, PlaybackMode.SEQ):
            if speed > 0:
                out_of_bounds = positions >= loop_end
            elif speed < 0:
                out_of_bounds = positions < loop_start
            else:
                out_of_bounds = np.zeros(active_frames, dtype=bool)

            if out_of_bounds.any():
                stop_idx = int(np.argmax(out_of_bounds))
                if stop_idx == 0:
                    self._playing = False
                    self.state.playing = False
                    return output
                active_frames = stop_idx
                positions = positions[:active_frames]
                if env is not None:
                    env = env[:active_frames]
                stop_after = True
        else:
            # Loop wrapping via modular arithmetic
            if loop_len > 0:
                if speed >= 0:
                    positions = loop_start + np.mod(positions - loop_start, loop_len)
                else:
                    positions = loop_end - np.mod(loop_end - positions, loop_len)

        # Clamp to valid sample range
        np.clip(positions, 0, num_sample_frames - 1.0, out=positions)

        # --- Vectorized linear interpolation ---
        pos_int = positions.astype(np.intp)
        pos_frac = (positions - pos_int).astype(np.float32)
        next_pos = np.minimum(pos_int + 1, num_sample_frames - 1)

        if num_channels >= 2:
            for ch in range(2):
                output[:active_frames, ch] += (
                    sample_data[pos_int, ch] * (1.0 - pos_frac) +
                    sample_data[next_pos, ch] * pos_frac
                )
        else:
            # Mono -> stereo
            mono = (sample_data[pos_int, 0] * (1.0 - pos_frac) +
                    sample_data[next_pos, 0] * pos_frac)
            output[:active_frames, 0] += mono
            output[:active_frames, 1] += mono

        # Apply envelope and gain
        gain = self.state.gain * self.state.velocity
        if env is not None:
            env_gain = env * gain
            output[:active_frames, 0] *= env_gain
            output[:active_frames, 1] *= env_gain
        else:
            output[:active_frames] *= gain

        # Apply fade-in ramp after pitch cache swap / retrigger (new audio
        # 0→1 while crossfade tail handles old audio 1→0)
        if self._fade_in_remaining > 0:
            n = min(self._fade_in_remaining, active_frames)
            offset = self.XFADE_LEN - self._fade_in_remaining
            ramp = self._fade_in_ramp[offset:offset + n]
            output[:n, 0] *= ramp
            output[:n, 1] *= ramp
            self._fade_in_remaining -= n

        # Mix in crossfade tail AFTER all gain/envelope/fade-in processing
        # so the pre-baked tail doesn't get double-gained
        if self._xfade_active and self._xfade_pos < self._xfade_len:
            remaining = self._xfade_len - self._xfade_pos
            n = min(remaining, num_frames)
            output[:n] += self._xfade_buf[self._xfade_pos:self._xfade_pos + n]
            self._xfade_pos += n
            if self._xfade_pos >= self._xfade_len:
                self._xfade_active = False

        # Update position for next block
        if self._playing:
            next_position = self._position + active_frames * speed
            if self.state.mode not in (PlaybackMode.ONESHOT, PlaybackMode.SEQ) and loop_len > 0:
                if speed >= 0:
                    self._position = loop_start + ((next_position - loop_start) % loop_len)
                else:
                    self._position = loop_end - ((loop_end - next_position) % loop_len)
            else:
                self._position = next_position

        if stop_after:
            self._playing = False
            self.state.playing = False

        # Apply mute (zero entire buffer including crossfade tail)
        if self.state.mute:
            output[:num_frames] = 0

        return output

    def get_grid_column(self, total_columns: int) -> int:
        """Get the current playback position as a grid column index."""
        if not self._playing or self.sample is None:
            return -1
        pos = self.position
        sel_start = self.state.sel_start
        sel_end = self.state.sel_end
        sel_len = sel_end - sel_start
        if sel_len <= 0:
            return -1
        normalized = (pos - sel_start) / sel_len
        normalized = max(0.0, min(1.0, normalized))
        return min(int(normalized * total_columns), total_columns - 1)
