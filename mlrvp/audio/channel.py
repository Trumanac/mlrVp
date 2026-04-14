"""Channel / track playback engine for mlrVp.

Each channel represents one row of the grid and handles:
- Sample playback with position tracking
- Speed/pitch control
- Playback modes (normal, reverse, oneshot, retrigger)
- Amplitude envelope (attack/release)
- Group assignment
"""

import collections

import numpy as np
from typing import Optional

from mlrvp.config import PlaybackMode, ChannelState, SAMPLE_RATE, BUFFER_SIZE, PITCH_SCALE_MAPS
from mlrvp.audio.sample import SampleBuffer

# Import Numba JIT channel kernel — fall back to None if unavailable
try:
    from mlrvp.audio._dsp_kernels import channel_process_kernel as _ch_jit
    from mlrvp.audio._dsp_kernels import xfade_tail_kernel as _xfade_jit
    _HAS_CH_JIT = True
except ImportError:
    _HAS_CH_JIT = False


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

        # STEP mode state (play one slice then stop)
        self._step_mode_start: float = 0.0
        self._step_mode_end: float = 0.0

        # PITCH mode state
        self._pitch_mode_mult: float = 1.0     # rate multiplier from scale degree
        self._pitch_mode_col: int = -1         # last triggered column (for LED feedback)

        # Pending sample for live preset transitions
        self._pending_sample_path: Optional[str] = None

        # Pre-allocated output buffer to avoid per-callback allocation
        _MAX_BUF = max(BUFFER_SIZE, 4096)
        self._output_buf = np.zeros((_MAX_BUF, 2), dtype=np.float32)
        self._buf_size = _MAX_BUF

        # Pre-allocated arange buffer for position computation
        self._arange_f64 = np.arange(_MAX_BUF, dtype=np.float64)
        self._arange_f32 = np.arange(_MAX_BUF, dtype=np.float32)

        # Pre-allocated intermediate buffers — eliminates per-callback
        # temporary array allocations that cause malloc/free churn and
        # reduce headroom for GIL contention with the GUI thread.
        self._pos_buf = np.zeros(_MAX_BUF, dtype=np.float64)
        self._pos_int_buf = np.zeros(_MAX_BUF, dtype=np.intp)
        self._pos_frac_buf = np.zeros(_MAX_BUF, dtype=np.float32)
        self._frac_f64_buf = np.zeros(_MAX_BUF, dtype=np.float64)
        self._next_pos_buf = np.zeros(_MAX_BUF, dtype=np.intp)
        self._inv_frac_buf = np.zeros(_MAX_BUF, dtype=np.float32)
        self._samp_a_buf = np.zeros(_MAX_BUF, dtype=np.float32)
        self._samp_b_buf = np.zeros(_MAX_BUF, dtype=np.float32)
        self._env_buf = np.zeros(_MAX_BUF, dtype=np.float32)
        self._env_gain_buf = np.zeros(_MAX_BUF, dtype=np.float32)
        self._bool_buf = np.zeros(_MAX_BUF, dtype=bool)

        # Pitch-shift cache (populated by app.py via librosa in a background thread)
        self._pitch_cache: Optional[np.ndarray] = None
        # Thread-safe pending cache: background thread writes here,
        # audio thread picks it up in process() to avoid cross-thread
        # reads of _position/_envelope in _snapshot_xfade_tail.
        _PITCH_CLEAR = object()  # sentinel meaning "clear the cache"
        self._PITCH_CLEAR = _PITCH_CLEAR
        self._pending_pitch_cache = None  # None=no change, _PITCH_CLEAR=clear, ndarray=new data

        # Duration-preserving pitch scale cache — keyed by absolute semitone.
        # Each entry is a full-sample librosa.effects.pitch_shift result.
        # Entries accumulate across pitch_root / pitch_scale changes and are
        # only cleared when the source audio changes (sample load or transpose).
        # Capped at _PSC_MAX_ENTRIES to prevent unbounded memory growth.
        self._pitch_scale_cache: dict[int, np.ndarray] = {}
        self._PSC_MAX_ENTRIES = 24  # 2 full octaves — covers any scale
        _PSC_CLEAR = object()  # sentinel for clearing the scale cache
        self._PSC_CLEAR = _PSC_CLEAR
        # Deque of (semitone, ndarray) pairs or _PSC_CLEAR sentinel.
        # Background thread appends; audio thread drains in process().
        self._pending_psc_entries: collections.deque = collections.deque()
        # The shifted audio array for the currently-triggered semitone.
        # Set in trigger() from _pitch_scale_cache; None = use rate-based fallback.
        self._active_pitch_data: Optional[np.ndarray] = None
        self._active_pitch_semitone: int = 0
        # Incremented to cancel in-flight background builds.
        self._pitch_scale_cache_gen: int = 0

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
        self.clear_pitch_scale_cache()  # invalidate duration-preserving scale cache

    def _get_audio_data(self) -> np.ndarray:
        """Return the best available audio source for playback.

        Priority: per-button pitch scale entry > transpose cache > raw sample.
        The scale cache is built *from* the transpose cache (if set), so
        bypassing _pitch_cache when _active_pitch_data is set is correct —
        transpose is already baked in.
        """
        if self._active_pitch_data is not None:
            return self._active_pitch_data
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

    def set_pitch_scale_entry(self, semitone: int, data: np.ndarray):
        """Stage a single pitch-scale cache entry from a background thread.

        GIL-atomic deque append — safe from any thread.
        """
        self._pending_psc_entries.append((semitone, data))

    def clear_pitch_scale_cache(self):
        """Stage pitch-scale cache removal from any thread."""
        self._pending_psc_entries.append(self._PSC_CLEAR)

    def _effective_speed(self) -> float:
        """Combined playback rate including mode-specific pitch.

        PITCH mode uses the per-button scale multiplier instead of
        the generic pitch knob so each column has its own pitch.
        """
        if self.state.mode == PlaybackMode.PITCH:
            # Duration-preserving path: pitch is baked into the cached audio,
            # so play at normal speed.  Falls back to rate-based when the
            # semitone hasn't been pre-computed yet.
            if self._active_pitch_data is not None:
                return self.state.speed
            return self.state.speed * self._pitch_mode_mult
        return self.state.speed * self.state.pitch

    def trigger(self, column: int, total_columns: int):
        """Trigger playback from a grid column position.

        Maps the column to a position within the sample's selected region.
        Uses crossfade when retriggering during playback to avoid clicks.
        """
        if self.sample is None or total_columns <= 0 or column < 0 or column >= total_columns:
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

        # Non-PITCH modes: clear any stale pitch-mode audio selection
        # so _get_audio_data() returns the correct source.
        if self.state.mode != PlaybackMode.PITCH:
            self._active_pitch_data = None
            self._active_pitch_semitone = None

        # PITCH mode: each column = different pitch, always play from start
        if self.state.mode == PlaybackMode.PITCH:
            scale = PITCH_SCALE_MAPS.get(self.state.pitch_scale, PITCH_SCALE_MAPS[0])
            col_idx = min(column, len(scale) - 1)
            semitone = scale[col_idx] + self.state.pitch_root
            # Duration-preserving: use pre-shifted audio if available.
            # Keep rate-based mult as fallback for un-cached semitones.
            self._active_pitch_data = self._pitch_scale_cache.get(semitone)
            self._active_pitch_semitone = semitone
            self._pitch_mode_mult = 2.0 ** (semitone / 12.0)
            self._pitch_mode_col = column
            self._position = sel_start_samp
            self._playing = True
            self._releasing = False
            self.state.playing = True
            return

        # mlrv step mapping: floor((button / grid_width) * steps) / steps
        # This distributes `state.steps` slices evenly across `total_columns` buttons.
        # Works correctly for downsampling (steps > columns), upsampling (steps < columns),
        # and non-divisible cases (e.g. 14 steps on 8 buttons).
        steps = self.state.steps if self.state.steps > 0 else total_columns
        slice_index = int((column / total_columns) * steps)
        col_frac = slice_index / steps

        # STEP mode: play only the slice this button covers, then stop
        if self.state.mode == PlaybackMode.STEP:
            self._step_mode_start = sel_start_samp + col_frac * sel_length
            self._step_mode_end = sel_start_samp + ((slice_index + 1) / steps) * sel_length
            self._position = self._step_mode_start
            self._playing = True
            self._releasing = False
            self.state.playing = True
            return

        # Reverse mode: invert mapping so column 0 starts from end
        if self.state.mode == PlaybackMode.REVERSE or (
            self.state.mode == PlaybackMode.ONESHOT and self.state.reverse
        ):
            self._position = sel_end_samp - col_frac * sel_length
        else:
            self._position = sel_start_samp + col_frac * sel_length

        self._playing = True
        self._releasing = False
        self.state.playing = True

    def _snapshot_xfade_tail(self):
        """Render a short fade-out snippet from the current position.

        Now called only from the audio thread (via queued trigger/force_stop),
        so all state reads are safe.
        """
        xlen = self.XFADE_LEN
        buf = self._xfade_buf
        audio_data = self._get_audio_data()
        num_sample_frames = audio_data.shape[0]
        if num_sample_frames < 2:
            buf[:] = 0
            return
        speed = self._effective_speed()
        if self.state.reverse or self.state.mode == PlaybackMode.REVERSE:
            speed = -speed

        env_gain = self._envelope * self.state.gain * self.state.velocity

        if _HAS_CH_JIT:
            _xfade_jit(
                audio_data, num_sample_frames, self.sample.num_channels,
                float(self._position), float(speed),
                buf, self._xfade_fade, xlen, float(env_gain),
            )
        else:
            # Python fallback
            buf[:] = 0
            num_channels = self.sample.num_channels
            positions = self._pos_buf[:xlen]
            np.multiply(self._arange_f64[:xlen], speed, out=positions)
            np.add(positions, self._position, out=positions)
            np.clip(positions, 0, num_sample_frames - 1.0, out=positions)
            pos_int = self._pos_int_buf[:xlen]
            pos_int[:] = positions
            frac_f64 = self._frac_f64_buf[:xlen]
            np.subtract(positions, pos_int, out=frac_f64)
            pos_frac = self._pos_frac_buf[:xlen]
            pos_frac[:] = frac_f64
            next_pos = self._next_pos_buf[:xlen]
            np.add(pos_int, 1, out=next_pos)
            np.minimum(next_pos, num_sample_frames - 1, out=next_pos)

            fade = self._xfade_fade
            inv_frac = self._inv_frac_buf[:xlen]
            np.subtract(1.0, pos_frac, out=inv_frac)
            samp_a = self._samp_a_buf[:xlen]
            samp_b = self._samp_b_buf[:xlen]

            if num_channels >= 2:
                for ch in range(2):
                    np.take(audio_data[:, ch], pos_int, out=samp_a)
                    np.take(audio_data[:, ch], next_pos, out=samp_b)
                    np.multiply(samp_a, inv_frac, out=samp_a)
                    np.multiply(samp_b, pos_frac, out=samp_b)
                    np.add(samp_a, samp_b, out=samp_b)
                    np.multiply(samp_b, fade, out=samp_b)
                    samp_b *= env_gain
                    buf[:, ch] = samp_b
            else:
                np.take(audio_data[:, 0], pos_int, out=samp_a)
                np.take(audio_data[:, 0], next_pos, out=samp_b)
                np.multiply(samp_a, inv_frac, out=samp_a)
                np.multiply(samp_b, pos_frac, out=samp_b)
                np.add(samp_a, samp_b, out=samp_b)
                np.multiply(samp_b, fade, out=samp_b)
                samp_b *= env_gain
                buf[:, 0] = samp_b
                buf[:, 1] = samp_b

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
            self._pos_buf = np.zeros(num_frames, dtype=np.float64)
            self._pos_int_buf = np.zeros(num_frames, dtype=np.intp)
            self._pos_frac_buf = np.zeros(num_frames, dtype=np.float32)
            self._frac_f64_buf = np.zeros(num_frames, dtype=np.float64)
            self._next_pos_buf = np.zeros(num_frames, dtype=np.intp)
            self._inv_frac_buf = np.zeros(num_frames, dtype=np.float32)
            self._samp_a_buf = np.zeros(num_frames, dtype=np.float32)
            self._samp_b_buf = np.zeros(num_frames, dtype=np.float32)
            self._env_buf = np.zeros(num_frames, dtype=np.float32)
            self._env_gain_buf = np.zeros(num_frames, dtype=np.float32)
            self._bool_buf = np.zeros(num_frames, dtype=bool)
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

        # --- Drain pending pitch-scale cache entries (thread-safe) ---
        # Background builder appends (semitone, ndarray) pairs; we drain
        # here so the audio thread owns _pitch_scale_cache exclusively.
        psc_q = self._pending_psc_entries
        while psc_q:
            entry = psc_q.popleft()
            if entry is self._PSC_CLEAR:
                self._pitch_scale_cache = {}
                self._active_pitch_data = None
                self._active_pitch_semitone = None
            else:
                st, data = entry
                # Evict oldest entry if cache is full
                if st not in self._pitch_scale_cache and len(self._pitch_scale_cache) >= self._PSC_MAX_ENTRIES:
                    oldest = next(iter(self._pitch_scale_cache))
                    del self._pitch_scale_cache[oldest]
                self._pitch_scale_cache[st] = data
                # Auto-update: if this semitone matches the currently-playing
                # button, apply it immediately (mid-playback pickup).
                if st == self._active_pitch_semitone and self._active_pitch_data is None:
                    self._active_pitch_data = data

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
        num_sample_frames = sample_data.shape[0]
        num_channels = self.sample.num_channels

        sel_start = max(0.0, min(1.0, self.state.sel_start)) * num_sample_frames
        sel_end = max(0.0, min(1.0, self.state.sel_end)) * num_sample_frames
        sel_length = sel_end - sel_start

        if sel_length <= 0:
            self._playing = False
            self.state.playing = False
            return output

        # Speed calculation (pitch * speed * direction)
        # Note: transpose is handled via pre-shifted audio in _pitch_cache (see set_pitch_cache)
        # PITCH mode uses per-button multiplier via _effective_speed()
        speed = self._effective_speed()
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
                if slice_ls < slice_le:
                    loop_start = slice_ls
                    loop_end = slice_le
                else:
                    # Inverted or equal bounds — fall back to full selection
                    pass
        if self.state.mode == PlaybackMode.STEP:
            loop_start = self._step_mode_start
            loop_end = self._step_mode_end
        loop_len = loop_end - loop_start

        # Attack/release times in samples
        attack_samples = max(1, int(self.state.attack * self.sample_rate))
        release_samples = max(1, int(self.state.release * self.sample_rate))

        active_frames = num_frames

        # Determine loop mode
        pitch_loops = (self.state.mode == PlaybackMode.PITCH and self.state.pitch_hold)
        do_loop = not (self.state.mode in (PlaybackMode.ONESHOT, PlaybackMode.SEQ, PlaybackMode.PITCH, PlaybackMode.STEP) and not pitch_loops)

        # ── Numba JIT path ──────────────────────────────────────────
        if _HAS_CH_JIT:
            # Gather crossfade / fade-in state (kernel handles both)
            xf_pos = self._xfade_pos if self._xfade_active else 0
            xf_len = self._xfade_len if self._xfade_active else 0
            fi_off = self.XFADE_LEN - self._fade_in_remaining
            fi_rem = self._fade_in_remaining

            new_pos, new_env, active_frames, stopped, new_xf_pos, new_fi_rem = _ch_jit(
                sample_data, num_sample_frames, num_channels,
                output, num_frames,
                float(self._position), float(speed),
                float(loop_start), float(loop_end), do_loop,
                float(self._envelope), bool(self._releasing),
                float(attack_samples), float(release_samples),
                float(self.state.gain * self.state.velocity),
                self._xfade_buf, xf_pos, xf_len,
                self._fade_in_ramp, fi_off, fi_rem,
                bool(self.state.mute),
            )

            self._envelope = new_env
            self._position = new_pos
            self._fade_in_remaining = new_fi_rem
            if xf_len > 0:
                self._xfade_pos = new_xf_pos
                if new_xf_pos >= xf_len:
                    self._xfade_active = False

            if stopped:
                self._playing = False
                self.state.playing = False

            return output

        # ── Python fallback path ────────────────────────────────────

        # --- Envelope computation (vectorized ramp, zero-alloc) ---
        if self._releasing:
            frames_to_zero = max(1, int(self._envelope * release_samples) + 1)
            active_frames = min(active_frames, frames_to_zero)
            env = self._env_buf[:active_frames]
            np.divide(self._arange_f32[:active_frames], release_samples, out=env)
            np.subtract(self._envelope, env, out=env)
            np.maximum(env, 0.0, out=env)
            self._envelope = float(env[-1]) if active_frames > 0 else 0.0
            if self._envelope < 1e-6:
                self._envelope = 0.0
                self._playing = False
                self.state.playing = False
        else:
            if self._envelope < 1.0:
                frames_to_full = max(1, int((1.0 - self._envelope) * attack_samples) + 1)
                if frames_to_full >= active_frames:
                    # Still ramping at end of block
                    env = self._env_buf[:active_frames]
                    np.divide(self._arange_f32[:active_frames], attack_samples, out=env)
                    np.add(env, self._envelope, out=env)
                    np.minimum(env, 1.0, out=env)
                else:
                    # Attack -> sustain transition within block
                    env = self._env_buf[:active_frames]
                    env[:] = 1.0
                    np.divide(self._arange_f32[:frames_to_full], attack_samples, out=env[:frames_to_full])
                    np.add(env[:frames_to_full], self._envelope, out=env[:frames_to_full])
                    np.minimum(env[:frames_to_full], 1.0, out=env[:frames_to_full])
                self._envelope = float(env[-1])
            else:
                env = None  # Fully open — apply gain only

        if active_frames == 0:
            return output

        # --- Position computation (vectorized, zero-alloc) ---
        positions = self._pos_buf[:active_frames]
        np.multiply(self._arange_f64[:active_frames], speed, out=positions)
        np.add(positions, self._position, out=positions)

        # Handle oneshot boundary (ONESHOT and SEQ both play through once then stop)
        # PITCH mode stops at boundary unless pitch_hold is active (loop behaviour)
        stop_after = False
        if not do_loop:
            out_of_bounds = self._bool_buf[:active_frames]
            if speed > 0:
                np.greater_equal(positions, loop_end, out=out_of_bounds)
            elif speed < 0:
                np.less(positions, loop_start, out=out_of_bounds)
            else:
                out_of_bounds[:] = False

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
            # Loop wrapping via modular arithmetic (in-place)
            if loop_len > 0:
                if speed >= 0:
                    np.subtract(positions, loop_start, out=positions)
                    np.mod(positions, loop_len, out=positions)
                    np.add(positions, loop_start, out=positions)
                else:
                    np.subtract(loop_end, positions, out=positions)
                    np.mod(positions, loop_len, out=positions)
                    np.subtract(loop_end, positions, out=positions)

        # Clamp to valid sample range
        np.clip(positions, 0, num_sample_frames - 1.0, out=positions)

        # --- Vectorized linear interpolation (zero-alloc) ---
        pos_int = self._pos_int_buf[:active_frames]
        pos_int[:] = positions  # float64 → intp truncation
        frac_f64 = self._frac_f64_buf[:active_frames]
        np.subtract(positions, pos_int, out=frac_f64)
        pos_frac = self._pos_frac_buf[:active_frames]
        pos_frac[:] = frac_f64  # float64 → float32
        next_pos = self._next_pos_buf[:active_frames]
        np.add(pos_int, 1, out=next_pos)
        np.minimum(next_pos, num_sample_frames - 1, out=next_pos)

        inv_frac = self._inv_frac_buf[:active_frames]
        np.subtract(1.0, pos_frac, out=inv_frac)
        samp_a = self._samp_a_buf[:active_frames]
        samp_b = self._samp_b_buf[:active_frames]

        if num_channels >= 2:
            for ch in range(2):
                np.take(sample_data[:, ch], pos_int, out=samp_a)
                np.take(sample_data[:, ch], next_pos, out=samp_b)
                np.multiply(samp_a, inv_frac, out=samp_a)
                np.multiply(samp_b, pos_frac, out=samp_b)
                np.add(samp_a, samp_b, out=samp_b)
                output[:active_frames, ch] += samp_b
        else:
            # Mono -> stereo
            np.take(sample_data[:, 0], pos_int, out=samp_a)
            np.take(sample_data[:, 0], next_pos, out=samp_b)
            np.multiply(samp_a, inv_frac, out=samp_a)
            np.multiply(samp_b, pos_frac, out=samp_b)
            np.add(samp_a, samp_b, out=samp_b)
            output[:active_frames, 0] += samp_b
            output[:active_frames, 1] += samp_b

        # Apply envelope and gain (zero-alloc)
        gain = self.state.gain * self.state.velocity
        if env is not None:
            env_gain = self._env_gain_buf[:active_frames]
            np.multiply(env, gain, out=env_gain)
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
        # so the pre-baked tail doesn't get double-gained.
        # Use num_frames (not active_frames) — the tail must ring out even when
        # playback stopped mid-buffer, since it's a fade-out of the OLD audio.
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
            if do_loop:
                if loop_len > 0:
                    if speed >= 0:
                        self._position = loop_start + ((next_position - loop_start) % loop_len)
                    else:
                        self._position = loop_end - ((loop_end - next_position) % loop_len)
                else:
                    self._position = next_position
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
