"""DSP effects for mlrVp: delay, filters, modulation, etc."""

import math
import random
import numpy as np
from scipy.signal import lfilter
from mlrvp.config import (
    SAMPLE_RATE, DelayState, FilterState, FilterBState, TapeDelayState, ModState,
    ReelToReelState, CassetteTapeState, VHSState, VinylState, CompressorState,
    HallReverbState, PlateReverbState, GlueCompressorState, EtherDriftState,
    NoiseGateState,
    TremoloState,
    AutoWahState,
    FETLimiterState,
    RotaryState,
    ExciterState,
    TransientShaperState,
    HarmonizerState,
)

# Import Numba JIT kernels — fall back to None if unavailable
try:
    from mlrvp.audio._dsp_kernels import (
        moog_ladder_kernel as _moog_jit,
        phaser_kernel as _phaser_jit,
        compressor_kernel as _compressor_jit,
        glue_compressor_kernel as _glue_jit,
        biquad_filter_kernel as _biquad_jit,
        gate_envelope_kernel as _gate_env_jit,
        transient_env_kernel as _transient_env_jit,
    )
    _HAS_NUMBA = True
except ImportError:
    _HAS_NUMBA = False


class _FlutterReadBuffers:
    """Pre-allocated scratch buffers for lo-fi pitch-flutter modulated reads.

    Used by ReelToReel/CassetteTape/VHS/Vinyl to avoid per-block allocations
    of rd_floor/rd_frac/rd_i/rd_j and the two fancy-index reads.
    """

    __slots__ = ("rd_floor", "rd_frac", "rd_i", "rd_j", "a_buf", "b_buf",
                 "floor_f64", "size")

    def __init__(self, size: int):
        self.rd_floor = np.empty(size, dtype=np.int64)
        self.rd_frac = np.empty(size, dtype=np.float32)
        self.rd_i = np.empty(size, dtype=np.int64)
        self.rd_j = np.empty(size, dtype=np.int64)
        self.a_buf = np.empty((size, 2), dtype=np.float32)
        self.b_buf = np.empty((size, 2), dtype=np.float32)
        self.floor_f64 = np.empty(size, dtype=np.float64)
        self.size = size

    def grow(self, n: int):
        if n > self.size:
            self.rd_floor = np.empty(n, dtype=np.int64)
            self.rd_frac = np.empty(n, dtype=np.float32)
            self.rd_i = np.empty(n, dtype=np.int64)
            self.rd_j = np.empty(n, dtype=np.int64)
            self.a_buf = np.empty((n, 2), dtype=np.float32)
            self.b_buf = np.empty((n, 2), dtype=np.float32)
            self.floor_f64 = np.empty(n, dtype=np.float64)
            self.size = n


def _flutter_read_into(wet, buf, read_pos, buf_len, fb: _FlutterReadBuffers):
    """In-place modulated-delay read: wet[:] = lerp(buf[i], buf[j], frac).

    Eliminates per-block allocations from
        wet = buf[i] * (1 - frac) + buf[j] * frac
    by reusing pre-allocated scratch buffers in ``fb``.
    """
    n = wet.shape[0]
    fb.grow(n)
    floor_f64 = fb.floor_f64[:n]
    np.floor(read_pos, out=floor_f64)
    rd_floor = fb.rd_floor[:n]
    np.copyto(rd_floor, floor_f64, casting='unsafe')
    np.subtract(read_pos, floor_f64, out=floor_f64)  # frac in floor_f64
    rd_frac = fb.rd_frac[:n]
    np.copyto(rd_frac, floor_f64, casting='unsafe')
    rd_i = fb.rd_i[:n]
    np.mod(rd_floor, buf_len, out=rd_i)
    rd_j = fb.rd_j[:n]
    np.add(rd_floor, 1, out=rd_j)
    np.mod(rd_j, buf_len, out=rd_j)
    a_buf = fb.a_buf[:n]
    b_buf = fb.b_buf[:n]
    np.take(buf, rd_i, axis=0, out=a_buf)
    np.take(buf, rd_j, axis=0, out=b_buf)
    np.subtract(b_buf, a_buf, out=wet)
    wet *= rd_frac[:, np.newaxis]
    wet += a_buf


class BiquadFilter:
    """Simple biquad filter for LP/HP in the delay feedback path."""

    def __init__(self, sample_rate: int = SAMPLE_RATE):
        self.sr = sample_rate
        self.b = np.array([1.0, 0.0, 0.0], dtype=np.float64)
        self.a = np.array([1.0, 0.0, 0.0], dtype=np.float64)
        # Cached as plain Python floats — avoids numpy scalar alloc in process()
        self._b0 = 1.0; self._b1 = 0.0; self._b2 = 0.0
        self._a1 = 0.0; self._a2 = 0.0
        # lfilter state for both channels at once: shape (2, 2) = (order, channels)
        self._zi = np.zeros((2, 2), dtype=np.float64)
        # Pre-allocated conversion buffers to avoid per-call astype allocations
        from mlrvp.config import BUFFER_SIZE
        self._f64_buf = np.zeros((BUFFER_SIZE, 2), dtype=np.float64)
        self._f32_buf = np.zeros((BUFFER_SIZE, 2), dtype=np.float32)

    def _update_coeffs(self):
        """Extract numpy array coefficients as plain Python floats."""
        self._b0 = float(self.b[0])
        self._b1 = float(self.b[1])
        self._b2 = float(self.b[2])
        self._a1 = float(self.a[1])
        self._a2 = float(self.a[2])

    def set_lowpass(self, freq: float, q: float = 0.707):
        freq = max(20.0, min(freq, self.sr * 0.49))
        w0 = 2 * np.pi * freq / self.sr
        alpha = np.sin(w0) / (2 * q)
        cos_w0 = np.cos(w0)
        a0 = 1 + alpha
        self.b = np.array([(1 - cos_w0) / 2, 1 - cos_w0, (1 - cos_w0) / 2], dtype=np.float64) / a0
        self.a = np.array([1.0, (-2 * cos_w0) / a0, (1 - alpha) / a0], dtype=np.float64)
        self._update_coeffs()

    def set_highpass(self, freq: float, q: float = 0.707):
        freq = max(20.0, min(freq, self.sr * 0.49))
        w0 = 2 * np.pi * freq / self.sr
        alpha = np.sin(w0) / (2 * q)
        cos_w0 = np.cos(w0)
        a0 = 1 + alpha
        self.b = np.array([(1 + cos_w0) / 2, -(1 + cos_w0), (1 + cos_w0) / 2], dtype=np.float64) / a0
        self.a = np.array([1.0, (-2 * cos_w0) / a0, (1 - alpha) / a0], dtype=np.float64)
        self._update_coeffs()

    def set_peaking(self, freq: float, gain_db: float, q: float = 1.0):
        """Peaking EQ biquad (Audio EQ Cookbook)."""
        freq = max(20.0, min(freq, self.sr * 0.49))
        q = max(0.1, q)
        A = 10.0 ** (gain_db / 40.0)
        w0 = 2.0 * np.pi * freq / self.sr
        alpha = np.sin(w0) / (2.0 * q)
        cos_w0 = np.cos(w0)
        a0 = 1.0 + alpha / A
        self.b = np.array([1.0 + alpha * A, -2.0 * cos_w0, 1.0 - alpha * A], dtype=np.float64) / a0
        self.a = np.array([1.0, (-2.0 * cos_w0) / a0, (1.0 - alpha / A) / a0], dtype=np.float64)
        self._update_coeffs()

    def set_low_shelf(self, freq: float, gain_db: float):
        """Low-shelving EQ biquad (Audio EQ Cookbook, S=1)."""
        freq = max(20.0, min(freq, self.sr * 0.49))
        A = 10.0 ** (gain_db / 40.0)
        w0 = 2.0 * np.pi * freq / self.sr
        cos_w0 = np.cos(w0)
        alpha = np.sin(w0) / 2.0 * np.sqrt(2.0)   # S=1
        sqA = np.sqrt(A)
        b0 =     A * ((A+1) - (A-1)*cos_w0 + 2*sqA*alpha)
        b1 =   2*A * ((A-1) - (A+1)*cos_w0)
        b2 =     A * ((A+1) - (A-1)*cos_w0 - 2*sqA*alpha)
        a0 =        (A+1) + (A-1)*cos_w0 + 2*sqA*alpha
        a1 =  -2  * ((A-1) + (A+1)*cos_w0)
        a2 =        (A+1) + (A-1)*cos_w0 - 2*sqA*alpha
        self.b = np.array([b0, b1, b2], dtype=np.float64) / a0
        self.a = np.array([1.0, a1 / a0, a2 / a0], dtype=np.float64)
        self._update_coeffs()

    def process(self, data: np.ndarray) -> np.ndarray:
        """Process stereo data — JIT in-place path or scipy fallback."""
        n = len(data)
        if _HAS_NUMBA:
            # In-place biquad via Numba — ZERO allocations
            # We need float32 contiguous copy to write in-place, reuse buf
            if n > self._f32_buf.shape[0]:
                self._f32_buf = np.zeros((n, 2), dtype=np.float32)
            buf32 = self._f32_buf[:n]
            buf32[:] = data
            _biquad_jit(buf32, n,
                        self._b0, self._b1, self._b2,
                        self._a1, self._a2,
                        self._zi)
            return buf32
        # Fallback: scipy lfilter (allocates output array)
        if n > self._f64_buf.shape[0]:
            self._f64_buf = np.zeros((n, 2), dtype=np.float64)
            self._f32_buf = np.zeros((n, 2), dtype=np.float32)
        buf64 = self._f64_buf[:n]
        buf64[:] = data  # float32 → float64 in-place (no temp alloc)
        filtered, self._zi = lfilter(
            self.b, self.a, buf64, axis=0, zi=self._zi)
        # Flush denormals from filter state to prevent idle noise
        self._zi[np.abs(self._zi) < 1e-18] = 0.0
        buf32 = self._f32_buf[:n]
        buf32[:] = filtered  # float64 → float32 in-place (no temp alloc)
        return buf32

    def reset(self):
        self._zi = np.zeros((2, 2), dtype=np.float64)


class StereoDelay:
    """Tempo-syncable stereo delay with filtered feedback.

    Matches the mlrV2 aux delay effect.
    """

    # Beat division table (in beats), aligned with SYNC_DIVISION_NAMES in effects_view.py
    SYNC_DIVISIONS = [
        0.125,      # 1/32
        1.0 / 6,    # 1/16t (triplet)
        0.25,       # 1/16
        1.0 / 3,    # 1/8t (triplet)
        0.5,        # 1/8
        2.0 / 3,    # 1/4t (triplet)
        0.75,       # 3/16
        1.0,        # 1/4
        4.0 / 3,    # 1/3
        1.5,        # 3/8
        2.0,        # 1/2
        3.0,        # 3/4
        4.0,        # 1 bar
    ]

    def __init__(self, sample_rate: int = SAMPLE_RATE, max_delay_sec: float = 4.0):
        self.sr = sample_rate
        self.max_delay_samples = int(max_delay_sec * sample_rate)
        self.buffer = np.zeros((self.max_delay_samples, 2), dtype=np.float32)
        self.write_pos = 0
        self.state = DelayState()

        self.lp_filter = BiquadFilter(sample_rate)
        self.hp_filter = BiquadFilter(sample_rate)
        self._update_filters()

        self.tempo = 120.0
        self._current_fb = 0.0  # envelope-smoothed feedback amount
        # Pre-allocated read buffer for delay line (avoids .copy() / concatenate)
        self._delayed_buf = np.empty((512, 2), dtype=np.float32)
        self._delayed_buf_size = 512

    def _update_filters(self):
        self.lp_filter.set_lowpass(self.state.filter_freq_lp)
        self.hp_filter.set_highpass(self.state.filter_freq_hp)

    def set_tempo(self, tempo: float):
        self.tempo = max(20.0, min(300.0, tempo))

    def _get_delay_samples(self) -> int:
        if self.state.time_mode == 0:
            # Sync mode
            idx = max(0, min(self.state.sync_time, len(self.SYNC_DIVISIONS) - 1))
            beats = self.SYNC_DIVISIONS[idx]
            delay_sec = beats * 60.0 / self.tempo
        else:
            delay_sec = self.state.ms_time / 1000.0
        delay_samples = int(delay_sec * self.sr)
        return max(1, min(delay_samples, self.max_delay_samples - 1))

    def process(self, input_data: np.ndarray) -> np.ndarray:
        """Process stereo input through delay effect (block-vectorised)."""
        num_frames = len(input_data)
        delay_samples = self._get_delay_samples()
        gain = self.state.gain

        # Feedback envelope: smoothly transitions between fb_range_low and
        # fb_range_high based on input signal level (attack/release times).
        fb_high = self.state.fb_range_high / 100.0
        fb_low = self.state.fb_range_low / 100.0
        input_level = float(np.abs(input_data).mean())
        target_fb = fb_high if input_level > 0.01 else fb_low
        atk_ms = max(1.0, self.state.fb_attack)
        rel_ms = max(1.0, self.state.fb_release)
        if target_fb > self._current_fb:
            coeff = math.exp(-1.0 / (atk_ms * 0.001 * self.sr / num_frames))
        else:
            coeff = math.exp(-1.0 / (rel_ms * 0.001 * self.sr / num_frames))
        self._current_fb = coeff * self._current_fb + (1.0 - coeff) * target_fb
        fb_amount = self._current_fb

        # Read delayed block from circular buffer (pre-allocated)
        if num_frames > self._delayed_buf_size:
            self._delayed_buf = np.empty((num_frames, 2), dtype=np.float32)
            self._delayed_buf_size = num_frames
        delayed = self._delayed_buf[:num_frames]
        read_start = (self.write_pos - delay_samples) % self.max_delay_samples
        if read_start + num_frames <= self.max_delay_samples:
            delayed[:] = self.buffer[read_start:read_start + num_frames]
        else:
            # Wrap around
            first = self.max_delay_samples - read_start
            delayed[:first] = self.buffer[read_start:self.max_delay_samples]
            delayed[first:] = self.buffer[:num_frames - first]

        # Output: input + delayed * gain
        output = input_data + delayed * gain

        # Feedback path: filter the delayed signal
        fb_signal = delayed * fb_amount
        if self.state.filter_lp:
            fb_signal = self.lp_filter.process(fb_signal)
        if self.state.filter_hp:
            fb_signal = self.hp_filter.process(fb_signal)

        # Write to buffer: input + filtered feedback (safety-clipped)
        write_data = input_data + fb_signal
        np.clip(write_data, -4.0, 4.0, out=write_data)
        wp = self.write_pos
        if wp + num_frames <= self.max_delay_samples:
            self.buffer[wp:wp + num_frames] = write_data
        else:
            first = self.max_delay_samples - wp
            self.buffer[wp:self.max_delay_samples] = write_data[:first]
            self.buffer[:num_frames - first] = write_data[first:]

        self.write_pos = (self.write_pos + num_frames) % self.max_delay_samples
        return output

    def clear(self):
        """Clear the delay buffer."""
        self.buffer[:] = 0
        self.write_pos = 0
        self._current_fb = 0.0
        self.lp_filter.reset()
        self.hp_filter.reset()

    reset = clear


class ChannelFilter:
    """Simple filter for per-channel use. Sweepable LP/HP."""

    def __init__(self, sample_rate: int = SAMPLE_RATE):
        self.sr = sample_rate
        self.lp = BiquadFilter(sample_rate)
        self.hp = BiquadFilter(sample_rate)
        self._filter_val = 0.0
        self._lp_val: float = 0.0   # from ch_lp knob (0=bypass, 1=closed)
        self._hp_val: float = 0.0   # from ch_hp knob (0=bypass, 1=closed)

    def set_filter(self, value: float):
        """Set filter value. 0 = bypass, negative = LP, positive = HP.
        Range -1 to 1.
        """
        value = max(-1.0, min(1.0, value))
        if abs(value - self._filter_val) < 0.001:
            return  # no meaningful change — skip coefficient rebuild
        self._filter_val = value
        if self._filter_val < 0:
            # Lowpass: map -1..0 to 200Hz..20000Hz
            t = 1.0 + self._filter_val  # 0..1
            freq = 200 * (20000 / 200) ** t
            self.lp.set_lowpass(freq)
        elif self._filter_val > 0:
            # Highpass: map 0..1 to 20Hz..8000Hz
            freq = 20 * (8000 / 20) ** self._filter_val
            self.hp.set_highpass(freq)

    def set_lp(self, val: float):
        """Set LP cutoff from knob: 0=bypass (20 kHz), 1=fully closed (200 Hz)."""
        val = max(0.0, min(1.0, val))
        if abs(val - self._lp_val) < 0.001:
            return
        self._lp_val = val
        freq = 20000.0 * (200.0 / 20000.0) ** val  # 20000 → 200 Hz
        self.lp.set_lowpass(freq)

    def set_hp(self, val: float):
        """Set HP cutoff from knob: 0=bypass (20 Hz), 1=fully closed (8 kHz)."""
        val = max(0.0, min(1.0, val))
        if abs(val - self._hp_val) < 0.001:
            return
        self._hp_val = val
        freq = 20.0 * (8000.0 / 20.0) ** val  # 20 → 8000 Hz
        self.hp.set_highpass(freq)

    def reset(self):
        self.lp.reset()
        self.hp.reset()
        self._filter_val = 0.0
        self._lp_val = 0.0
        self._hp_val = 0.0

    def process(self, data: np.ndarray) -> np.ndarray:
        # New independent LP/HP knob path takes priority
        if self._lp_val > 0.01 or self._hp_val > 0.01:
            out = data
            if self._lp_val > 0.01:
                out = self.lp.process(out)
            if self._hp_val > 0.01:
                out = self.hp.process(out)
            return out
        # Legacy single-sweep path (filter_val)
        if abs(self._filter_val) < 0.01:
            return data
        if self._filter_val < 0:
            return self.lp.process(data)
        else:
            return self.hp.process(data)


class GlobalFilter:
    """Global LP/HP filter + parametric EQ bands for the Band Practice rack module."""

    def __init__(self, sample_rate: int = SAMPLE_RATE):
        self.sr = sample_rate
        self.lp = BiquadFilter(sample_rate)
        self.hp = BiquadFilter(sample_rate)
        self.lf = BiquadFilter(sample_rate)   # LF low shelf
        self.lmf = BiquadFilter(sample_rate)  # LMF peaking
        self.hmf = BiquadFilter(sample_rate)  # HMF peaking
        self.state = FilterState()
        self._update()

    def _update(self):
        self.lp.set_lowpass(self.state.lp_freq, 0.5 + self.state.lp_q * 4.0)
        self.hp.set_highpass(self.state.hp_freq, 0.5 + self.state.hp_q * 4.0)
        # LF low shelf
        lf_db = (self.state.lo_mid_gain - 0.5) * 24.0
        self.lf.set_low_shelf(self.state.lo_mid_freq, lf_db)
        # LMF peaking
        lmf_db = (self.state.mid_gain - 0.5) * 24.0
        lmf_q = 0.2 + self.state.mid_q * 7.8
        self.lmf.set_peaking(self.state.mid_freq, lmf_db, lmf_q)
        # HMF peaking
        hmf_db = (self.state.hi_mid_gain - 0.5) * 24.0
        hmf_q = 0.2 + self.state.hi_mid_q * 7.8
        self.hmf.set_peaking(self.state.hi_mid_freq, hmf_db, hmf_q)

    def update_from_state(self, state: FilterState):
        self.state = state
        self._update()

    def process(self, data: np.ndarray) -> np.ndarray:
        out = data
        if self.state.lp_enabled:
            out = self.lp.process(out)
        if self.state.hp_enabled:
            out = self.hp.process(out)
        # LF low shelf
        if self.state.lf_enabled:
            lf_db = (self.state.lo_mid_gain - 0.5) * 24.0
            if abs(lf_db) > 0.05:
                out = self.lf.process(out)
        # LMF peaking
        lmf_db = (self.state.mid_gain - 0.5) * 24.0
        if abs(lmf_db) > 0.05:
            out = self.lmf.process(out)
        # HMF peaking
        hmf_db = (self.state.hi_mid_gain - 0.5) * 24.0
        if abs(hmf_db) > 0.05:
            out = self.hmf.process(out)
        # Drive (soft saturation, normalised so unity drive = unity gain)
        if self.state.drive > 0.01:
            drive_amt = 1.0 + self.state.drive * 7.0
            np.multiply(out, drive_amt, out=out)
            np.tanh(out, out=out)
            out *= np.float32(1.0 / math.tanh(drive_amt))
        # Output gain (±12 dB)
        out_db = (self.state.output_gain - 0.5) * 24.0
        if abs(out_db) > 0.05:
            out = out * (10.0 ** (out_db / 20.0))
        return out

    def reset(self):
        self.lp.reset()
        self.hp.reset()
        self.lf.reset()
        self.lmf.reset()
        self.hmf.reset()


class MoogLadderFilter:
    """Moog-style 4-pole ladder filter (24dB/oct) with resonance.

    Uses the Huovilainen (2004) per-sample model: tanh saturation at every
    stage and continuous resonance feedback.

    Optimisations vs naïve implementation:
    - math.tanh (pure C libm) instead of np.tanh on scalars — no numpy dispatch
    - tanh output caching: tanh(y_stage) each iteration == tanh(ny_stage) from
      the previous iteration, so 4 of the 9 original calls are eliminated
    - data[:, ch].tolist() for O(1) Python list access inside the inner loop
    - result accumulated as a Python list, converted once at the end
    """

    def __init__(self, sample_rate: int = SAMPLE_RATE):
        self.sr = sample_rate
        self.state = FilterBState()
        # 4 one-pole stage outputs per channel
        self._y = np.zeros((4, 2), dtype=np.float64)
        self._fb = np.zeros(2, dtype=np.float64)
        # Pre-allocated output buffer
        from mlrvp.config import BUFFER_SIZE as _BS
        self._buf_size = _BS
        self._out = np.empty((_BS, 2), dtype=np.float32)
        # Pre-allocated buffers for numba path
        self._x_f64 = np.empty(_BS, dtype=np.float64)
        self._result_f64 = np.empty(_BS, dtype=np.float64)

    def update_from_state(self, state: FilterBState):
        self.state = state

    def process(self, data: np.ndarray) -> np.ndarray:
        n = len(data)
        if n != self._buf_size:
            self._buf_size = n
            self._out = np.empty((n, 2), dtype=np.float32)
            self._x_f64 = np.empty(n, dtype=np.float64)
            self._result_f64 = np.empty(n, dtype=np.float64)
        out = self._out
        cutoff = max(20.0, min(self.state.cutoff, self.sr * 0.49))
        # Scale resonance to [0, 3.8] — keeps headroom below self-oscillation
        resonance = self.state.resonance * 3.8
        drive = 1.0 + self.state.drive * 3.0
        do_drive = drive > 1.01

        # One-pole coefficient (bilinear approximation) — use math.exp/pi
        g = 1.0 - math.exp(-2.0 * math.pi * cutoff / self.sr)

        if _HAS_NUMBA:
            # Numba JIT path — ~20x faster than pure Python
            for ch in range(2):
                x_arr = self._x_f64[:n]
                np.copyto(x_arr, data[:, ch], casting='unsafe')
                result = self._result_f64[:n]
                y0, y1, y2, y3, fb = _moog_jit(
                    x_arr, n, g, resonance, drive, do_drive,
                    float(self._y[0, ch]), float(self._y[1, ch]),
                    float(self._y[2, ch]), float(self._y[3, ch]),
                    float(self._fb[ch]), result,
                )
                self._y[0, ch] = y0
                self._y[1, ch] = y1
                self._y[2, ch] = y2
                self._y[3, ch] = y3
                self._fb[ch] = fb
                out[:, ch] = result
        else:
            _tanh = math.tanh
            for ch in range(2):
                x_list = data[:, ch].tolist()
                y0 = float(self._y[0, ch])
                y1 = float(self._y[1, ch])
                y2 = float(self._y[2, ch])
                y3 = float(self._y[3, ch])
                fb = float(self._fb[ch])
                result = [0.0] * n

                ty0 = _tanh(y0)
                ty1 = _tanh(y1)
                ty2 = _tanh(y2)
                ty3 = _tanh(y3)

                for i in range(n):
                    xn = x_list[i]
                    if do_drive:
                        xn = _tanh(xn * drive)
                    xn -= resonance * fb

                    txn = _tanh(xn)
                    ny0 = y0 + g * (txn - ty0);  ty0 = _tanh(ny0)
                    ny1 = y1 + g * (ty0 - ty1);  ty1 = _tanh(ny1)
                    ny2 = y2 + g * (ty1 - ty2);  ty2 = _tanh(ny2)
                    ny3 = y3 + g * (ty2 - ty3);  ty3 = _tanh(ny3)
                    y0, y1, y2, y3 = ny0, ny1, ny2, ny3
                    fb = ny3
                    result[i] = ny3

                self._y[0, ch] = y0
                self._y[1, ch] = y1
                self._y[2, ch] = y2
                self._y[3, ch] = y3
                self._fb[ch] = fb
                out[:, ch] = result

        # Flush denormals from filter state to avoid CPU spikes after silence
        self._y[np.abs(self._y) < 1e-18] = 0.0
        self._fb[np.abs(self._fb) < 1e-18] = 0.0

        return out

    def reset(self):
        self._y[:] = 0.0
        self._fb[:] = 0.0


class TapeDelay:
    """Reel Space tape echo.

    Inspired by the Roland RE-201 Space Echo: three virtual playback heads
    at 1×/2×/3× the set delay time, seven selectable head combinations,
    wow/flutter tape modulation, soft saturation, tone filter, and a simple
    spring reverb (short allpass chain).  All from a single circular buffer
    so the multi-head read is nearly free.
    """

    SYNC_DIVISIONS = [
        0.125,      # 1/32
        1.0 / 6,    # 1/16t (triplet)
        0.25,       # 1/16
        1.0 / 3,    # 1/8t (triplet)
        0.5,        # 1/8
        2.0 / 3,    # 1/4t (triplet)
        0.75,       # 3/16
        1.0,        # 1/4
        4.0 / 3,    # 1/3
        1.5,        # 3/8
        2.0,        # 1/2
        3.0,        # 3/4
        4.0,        # 1 bar
    ]

    # Playback head delay ratios (relative to base delay time)
    # Head 1 = 1×, Head 2 = 2×, Head 3 = 3× — mirrors RE-201 spacing
    _HEAD_RATIOS = [1.0, 2.0, 3.0]

    # Active heads per mode (indices into _HEAD_RATIOS)
    _HEAD_MODES = [
        (0,),         # 0: Head 1 only
        (1,),         # 1: Head 2 only
        (2,),         # 2: Head 3 only
        (0, 1),       # 3: Head 1 + 2
        (0, 2),       # 4: Head 1 + 3
        (1, 2),       # 5: Head 2 + 3
        (0, 1, 2),    # 6: Head 1 + 2 + 3
    ]

    def __init__(self, sample_rate: int = SAMPLE_RATE, max_delay_sec: float = 4.0,
                 buffer_size: int = 512):
        self.sr = sample_rate
        self.max_delay_samples = int(max_delay_sec * sample_rate)
        self.buffer = np.zeros((self.max_delay_samples, 2), dtype=np.float32)
        self.write_pos = 0
        self.state = TapeDelayState()
        self.tempo = 120.0

        # Tone filter
        self._tone_lp = BiquadFilter(sample_rate)
        self._tone_lp.set_lowpass(8000.0, 0.707)

        # Wow/flutter LFO phases
        self._wow_phase = 0.0
        self._flutter_phase = 0.0

        # Spring reverb — 3-stage allpass chain using prime lengths (~53, 34, 17ms)
        self._spring_aps = [
            _AllpassFilter(max(4, int(0.053 * sample_rate)), 0.55),
            _AllpassFilter(max(4, int(0.034 * sample_rate)), 0.50),
            _AllpassFilter(max(4, int(0.017 * sample_rate)), 0.45),
        ]

        # Pre-allocated index arrays
        self._idx_f64 = np.arange(buffer_size, dtype=np.float64)
        self._idx_intp = np.arange(buffer_size, dtype=np.intp)
        # Pre-allocated delayed sum buffer
        self._delayed_sum = np.zeros((buffer_size, 2), dtype=np.float32)
        # Pre-allocated spring reverb buffer
        self._spring_buf = np.empty((buffer_size, 2), dtype=np.float32)
        # Cache tone to avoid rebuilding filter every block
        self._last_tone: float = -1.0

    def set_tempo(self, tempo: float):
        self.tempo = max(20.0, min(300.0, tempo))

    def _get_delay_samples(self) -> int:
        if self.state.time_mode == 0:
            idx = max(0, min(self.state.sync_time, len(self.SYNC_DIVISIONS) - 1))
            beats = self.SYNC_DIVISIONS[idx]
            delay_sec = beats * 60.0 / self.tempo
        else:
            delay_sec = self.state.ms_time / 1000.0
        return max(1, min(int(delay_sec * self.sr), self.max_delay_samples - 1))

    def process(self, input_data: np.ndarray) -> np.ndarray:
        n = len(input_data)
        base_delay = self._get_delay_samples()
        feedback = self.state.feedback
        gain = self.state.gain
        wow_depth = self.state.wow_flutter

        idx_f64 = self._idx_f64[:n] if n <= len(self._idx_f64) else np.arange(n, dtype=np.float64)
        idx_intp = self._idx_intp[:n] if n <= len(self._idx_intp) else np.arange(n, dtype=np.intp)

        # Tone filter cutoff — only rebuild when changed
        tone_freq = 400.0 + self.state.tone * 16000.0
        if abs(self.state.tone - self._last_tone) > 0.005:
            self._tone_lp.set_lowpass(tone_freq, 0.707)
            self._last_tone = self.state.tone

        # Wow/flutter — same LFO for all heads (unified tape speed variation)
        if wow_depth > 0.01:
            t = idx_f64 / self.sr
            wow = np.sin(2.0 * np.pi * 0.5 * (t + self._wow_phase / (2.0 * np.pi * 0.5)))
            flutter = np.sin(2.0 * np.pi * 6.0 * (t + self._flutter_phase / (2.0 * np.pi * 6.0)))
            mod = (wow * 0.7 + flutter * 0.3) * wow_depth
            delays_mod = (mod * base_delay * 0.05).astype(np.intp)
            base_delays = np.clip(base_delay + delays_mod, 1, self.max_delay_samples // 4)
            self._wow_phase = math.fmod(self._wow_phase + 2.0 * np.pi * 0.5 * n / self.sr, 1000.0)
            self._flutter_phase = math.fmod(self._flutter_phase + 2.0 * np.pi * 6.0 * n / self.sr, 1000.0)
        else:
            base_delays = np.full(n, base_delay, dtype=np.intp)

        write_positions = (self.write_pos + idx_intp) % self.max_delay_samples

        # Read from all active heads and sum
        mode_idx = max(0, min(self.state.head_mode, len(self._HEAD_MODES) - 1))
        active_heads = self._HEAD_MODES[mode_idx]
        delayed_sum = self._delayed_sum[:n]
        delayed_sum[:] = 0

        for head_idx in active_heads:
            ratio = self._HEAD_RATIOS[head_idx]
            head_delays = np.clip((base_delays * ratio).astype(np.intp),
                                  1, self.max_delay_samples - 1)
            read_pos = (write_positions - head_delays) % self.max_delay_samples
            delayed_sum += self.buffer[read_pos]

        if len(active_heads) > 1:
            delayed_sum /= np.float32(len(active_heads))

        # Tone filter on combined echo signal
        delayed_sum = self._tone_lp.process(delayed_sum)

        # Tape saturation in feedback path
        sat = self.state.saturation
        fb_signal = delayed_sum * feedback
        if sat > 0.01:
            drive = 1.0 + sat * 3.0
            np.multiply(fb_signal, drive, out=fb_signal)
            np.tanh(fb_signal, out=fb_signal)
            fb_signal *= np.float32(1.0 / max(math.tanh(drive), 0.01))

        # Write input + feedback to buffer
        write_data = input_data + fb_signal
        np.clip(write_data, -4.0, 4.0, out=write_data)
        self.buffer[write_positions] = write_data

        self.write_pos = (self.write_pos + n) % self.max_delay_samples

        output = input_data + delayed_sum * gain

        # Built-in spring reverb (allpass chain)
        spring_mix = self.state.spring_mix
        if spring_mix > 0.01:
            spring = self._spring_buf[:n] if n <= len(self._spring_buf) else np.empty((n, 2), dtype=np.float32)
            spring[:] = output
            for ap in self._spring_aps:
                spring = ap.process(spring)
            output = output * np.float32(1.0 - spring_mix * 0.5) + spring * np.float32(spring_mix * 0.5)

        return output

    def clear(self):
        self.buffer[:] = 0
        self.write_pos = 0
        self._tone_lp.reset()
        for ap in self._spring_aps:
            ap._buf[:] = 0
            ap._idx = 0

    reset = clear


class Phaser:
    """Phaser using per-sample cascaded first-order allpass filters.

    Why per-sample instead of sub-block lfilter:
    The old implementation injected `fb_sample * feedback` as a CONSTANT
    across each 16-sample sub-block.  When fb_sample changed at every
    sub-block boundary that created a 2756 Hz step-discontinuity in the
    input to the allpass chain, which the high-Q allpass resonated into
    audible clicking/static.  Per-sample processing eliminates that
    staircase artifact entirely.

    Allpass:  y[n] = -c·x[n] + x[n-1] + c·y[n-1]
    """

    def __init__(self, sample_rate: int = SAMPLE_RATE, stages: int = 4):
        self.sr = sample_rate
        self.stages = stages
        self.rate = 0.5
        self.depth = 0.5
        self.feedback = 0.3
        self._phase = 0.0
        # Per-stage, per-channel allpass state as numpy arrays for Numba compat
        self._ap_x = np.zeros((stages, 2), dtype=np.float64)
        self._ap_y = np.zeros((stages, 2), dtype=np.float64)
        # Feedback sample — last allpass output, per channel
        self._fb0 = 0.0
        self._fb1 = 0.0
        # Pre-allocated output buffer
        self._buf_size = 512
        self._out = np.empty((512, 2), dtype=np.float32)

    def process(self, data: np.ndarray) -> np.ndarray:
        n = len(data)
        if n != self._buf_size:
            self._buf_size = n
            self._out = np.empty((n, 2), dtype=np.float32)
        out = self._out

        rps = self.rate / self.sr
        phase = self._phase
        feedback = min(abs(self.feedback), 0.88)
        stages = self.stages

        if _HAS_NUMBA:
            # Numba JIT path
            phase, fb0, fb1 = _phaser_jit(
                data, n, out, rps, phase, feedback, stages,
                self._ap_x, self._ap_y, self._fb0, self._fb1,
            )
            self._phase = phase
            self._fb0 = fb0
            self._fb1 = fb1
        else:
            ap_x = self._ap_x
            ap_y = self._ap_y
            fb0 = self._fb0
            fb1 = self._fb1
            _pi2 = math.pi * 2.0

            for i in range(n):
                lfo = math.sin(_pi2 * phase) * self.depth
                c = 0.5 + lfo * 0.4
                if c < 0.05:
                    c = 0.05
                elif c > 0.95:
                    c = 0.95
                mc = -c
                phase += rps

                x0 = float(data[i, 0]) + fb0 * feedback
                for s in range(stages):
                    xp = ap_x[s, 0];  yp = ap_y[s, 0]
                    y = mc * x0 + xp + c * yp
                    ap_x[s, 0] = x0;  ap_y[s, 0] = y
                    x0 = y
                fb0 = x0

                x1 = float(data[i, 1]) + fb1 * feedback
                for s in range(stages):
                    xp = ap_x[s, 1];  yp = ap_y[s, 1]
                    y = mc * x1 + xp + c * yp
                    ap_x[s, 1] = x1;  ap_y[s, 1] = y
                    x1 = y
                fb1 = x1

                out[i, 0] = float(data[i, 0]) * 0.5 + x0 * 0.5
                out[i, 1] = float(data[i, 1]) * 0.5 + x1 * 0.5

            if phase >= 1.0:
                phase -= int(phase)
            self._phase = phase
            self._fb0 = fb0
            self._fb1 = fb1

        # Flush denormals from allpass state to prevent CPU spikes during decay
        self._ap_x[np.abs(self._ap_x) < 1e-18] = 0.0
        self._ap_y[np.abs(self._ap_y) < 1e-18] = 0.0
        return out

    def reset(self):
        self._ap_x[:] = 0
        self._ap_y[:] = 0
        self._fb0 = 0.0
        self._fb1 = 0.0
        self._phase = 0.0


class Chorus:
    """Simple chorus effect using modulated delay (block-vectorised)."""

    def __init__(self, sample_rate: int = SAMPLE_RATE, buffer_size: int = 512):
        self.sr = sample_rate
        self.rate = 1.0
        self.depth = 0.5
        self.mix = 0.5
        self._phase = 0.0
        max_delay = max(int(0.03 * sample_rate), buffer_size + 64)  # 30ms or block+64
        self._buf = np.zeros((max_delay + 1, 2), dtype=np.float32)
        self._buf_len = max_delay + 1
        self._write_pos = 0
        # Pre-allocated index arrays (reused every callback)
        self._idx_f64 = np.arange(buffer_size, dtype=np.float64)
        self._idx_intp = np.arange(buffer_size, dtype=np.intp)
        # Pre-allocated conversion buffers
        self._d0_buf = np.empty(buffer_size, dtype=np.intp)
        self._frac_buf = np.empty((buffer_size, 1), dtype=np.float32)

    def process(self, data: np.ndarray) -> np.ndarray:
        n = len(data)
        base_delay = int(0.007 * self.sr)
        mod_range = 0.005 * self.sr * self.depth  # keep as float for interpolation

        # Reuse pre-allocated index arrays (resize only if buffer changed)
        idx_f64 = self._idx_f64[:n] if n <= len(self._idx_f64) else np.arange(n, dtype=np.float64)
        idx_intp = self._idx_intp[:n] if n <= len(self._idx_intp) else np.arange(n, dtype=np.intp)

        # Precompute LFO for all samples
        phases = self._phase + idx_f64 * (self.rate / self.sr)
        phases = np.mod(phases, 1.0)
        self._phase = float(phases[-1]) + self.rate / self.sr
        if self._phase >= 1.0:
            self._phase -= 1.0

        lfo = np.sin(2.0 * np.pi * phases)

        # Fractional delay (float) for linear interpolation — avoids quantisation
        # noise (which sounds like bit-crushing) from integer truncation.
        delay_f = base_delay + lfo * mod_range
        d0 = self._d0_buf[:n] if n <= len(self._d0_buf) else np.empty(n, dtype=np.intp)
        np.clip(delay_f, 1, self._buf_len - 2, out=delay_f)
        d0[:] = delay_f.astype(np.intp)
        frac_1d = (delay_f - d0)
        frac = self._frac_buf[:n] if n <= len(self._frac_buf) else np.empty((n, 1), dtype=np.float32)
        frac[:, 0] = frac_1d

        # Write positions
        write_positions = (self._write_pos + idx_intp) % self._buf_len
        # Read two neighbouring positions for linear interpolation
        r0 = (write_positions - d0) % self._buf_len
        r1 = (write_positions - d0 - 1) % self._buf_len

        # WRITE FIRST, then read.
        # With read-before-write, any sample i where i >= d0[i] has its read
        # position fall inside the current block's write range.  The buffer
        # slot hasn't been written this call yet, so we read stale data from
        # ~buf_len (≈30 ms) ago instead of d0 samples ago — producing an
        # audible phantom echo / digital noise artifact.
        # Writing first ensures that for i >= d0[i] we read data[i-d0[i]]
        # (the correct delay), and for i < d0[i] we still read from the
        # previous block's data.
        self._buf[write_positions] = data
        delayed = self._buf[r0] * (1.0 - frac) + self._buf[r1] * frac

        self._write_pos = (self._write_pos + n) % self._buf_len

        # Mix
        return data * (1.0 - self.mix) + delayed * self.mix

    def reset(self):
        self._buf[:] = 0
        self._write_pos = 0
        self._phase = 0.0


class Flanger:
    """Flanger with 1-pole LP-damped feedback path.

    The feedback (REGEN) is routed through a low-pass filter at ~4 kHz
    before being written back to the delay buffer.  This matches the
    behaviour of real bucket-brigade flangers (Boss BF-2 etc.) where the
    audio path has a natural HF roll-off that prevents self-oscillation
    and the harsh digital ringing that occurs when pure feedback approaches 1.
    """

    def __init__(self, sample_rate: int = SAMPLE_RATE, buffer_size: int = 512):
        self.sr = sample_rate
        self.rate = 0.3
        self.depth = 0.5
        self.feedback = 0.3
        self.mix = 0.5
        self._phase = 0.0
        # Buffer must be larger than the maximum audio block size to prevent
        # write_positions from wrapping around within a single block, which
        # would corrupt circular buffer reads/writes.
        max_delay = max(int(0.015 * sample_rate), buffer_size + 64)  # 15ms or block+64
        self._buf = np.zeros((max_delay + 1, 2), dtype=np.float32)
        self._buf_len = max_delay + 1
        self._write_pos = 0
        # Pre-allocated index arrays (reused every callback)
        self._idx_f64 = np.arange(buffer_size, dtype=np.float64)
        self._idx_intp = np.arange(buffer_size, dtype=np.intp)
        # Pre-allocated conversion buffers
        self._d0_buf = np.empty(buffer_size, dtype=np.intp)
        self._frac_buf = np.empty((buffer_size, 1), dtype=np.float32)
        # 1-pole LP in feedback path — fc=4 kHz — damps HF comb resonance
        self._fb_lp_a = np.float32(math.exp(-2.0 * math.pi * 4000.0 / sample_rate))
        # Pre-compute lfilter coefficients for feedback LP (float32 to avoid astype)
        self._fb_lp_b = np.array([1.0 - self._fb_lp_a], dtype=np.float32)
        self._fb_lp_a_coeff = np.array([1.0, -float(self._fb_lp_a)], dtype=np.float32)
        self._fb_lp_zi = np.zeros((1, 2), dtype=np.float32)

    def process(self, data: np.ndarray) -> np.ndarray:
        n = len(data)
        # Keep mod_range as float — required for fractional delay interpolation.
        # Integer mod_range caused 1-sample quantisation jumps at each LFO
        # zero-crossing, producing an audible ~40 Hz buzz at default settings.
        mod_range = 0.003 * self.sr * self.depth
        if mod_range < 0.5:
            return data

        # Reuse pre-allocated index arrays (resize only if buffer changed)
        idx_f64 = self._idx_f64[:n] if n <= len(self._idx_f64) else np.arange(n, dtype=np.float64)
        idx_intp = self._idx_intp[:n] if n <= len(self._idx_intp) else np.arange(n, dtype=np.intp)

        # Precompute LFO for entire block (vectorised)
        phases = self._phase + idx_f64 * (self.rate / self.sr)
        phases = np.mod(phases, 1.0)
        self._phase = float(phases[-1]) + self.rate / self.sr
        if self._phase >= 1.0:
            self._phase -= 1.0

        lfo = np.sin(2.0 * np.pi * phases)

        # Fractional delay → linear interpolation eliminates quantisation clicks
        delay_f = 1.0 + (lfo * 0.5 + 0.5) * mod_range
        d0 = self._d0_buf[:n] if n <= len(self._d0_buf) else np.empty(n, dtype=np.intp)
        np.clip(delay_f, 1, self._buf_len - 2, out=delay_f)
        d0[:] = delay_f.astype(np.intp)
        frac_1d = (delay_f - d0)
        frac = self._frac_buf[:n] if n <= len(self._frac_buf) else np.empty((n, 1), dtype=np.float32)
        frac[:, 0] = frac_1d

        write_positions = (self._write_pos + idx_intp) % self._buf_len

        # Write dry input first, then read (previous-block data for i<d0,
        # just-written sample for i>=d0 — both give the correct delayed value).
        self._buf[write_positions] = data

        # Read with linear interpolation
        r0 = (write_positions - d0)     % self._buf_len
        r1 = (write_positions - d0 - 1) % self._buf_len
        delayed = self._buf[r0] * (1.0 - frac) + self._buf[r1] * frac

        # Cap feedback to prevent self-oscillation at extreme knob values.
        # Real BBD flangers top out around 0.9 due to circuit losses.
        feedback = min(float(self.feedback), 0.9)

        # 1-pole LP on the feedback signal — damps high-frequency comb
        # resonance that sounds like digital ringing.  Both channels at once
        # using axis=0 to avoid per-channel allocation overhead.
        fb_filtered, self._fb_lp_zi = lfilter(
            self._fb_lp_b, self._fb_lp_a_coeff, delayed, axis=0, zi=self._fb_lp_zi)

        # Write input + LP-filtered, capped feedback
        self._buf[write_positions] = np.clip(
            data + fb_filtered * np.float32(feedback), -2.0, 2.0
        )

        self._write_pos = (self._write_pos + n) % self._buf_len

        # Mix dry/wet
        return data * (1.0 - self.mix) + delayed * self.mix

    def reset(self):
        self._buf[:] = 0
        self._write_pos = 0
        self._phase = 0.0
        self._fb_lp_zi[:] = 0


class FXBus:
    """A complete FX processing bus containing filter + modulation effects.

    Groups with fx_route=1 are sent to FX bus 1, fx_route=2 to FX bus 2.
    Each bus processes audio through: Filter -> Phaser -> Chorus -> Flanger
    Only enabled effects in each chain are applied.
    """

    def __init__(self, sample_rate: int = SAMPLE_RATE):
        self.sr = sample_rate
        self.global_filter = GlobalFilter(sample_rate)
        self.phaser = Phaser(sample_rate)
        self.chorus = Chorus(sample_rate)
        self.flanger = Flanger(sample_rate)
        self._filter_enabled = True
        self._phaser_enabled = False
        self._chorus_enabled = False
        self._flanger_enabled = False
        self._mod_active = True  # controlled by per-effect routing
        # In-rack flags — False when module removed from the rack UI
        self.phaser_in_rack = True
        self.chorus_in_rack = True
        self.flanger_in_rack = True

    def update_filter(self, state: FilterState):
        self.global_filter.update_from_state(state)
        self._filter_enabled = state.lp_enabled or state.hp_enabled

    def update_mod(self, state: ModState):
        self._phaser_enabled = state.phaser_enabled
        self.phaser.rate = state.phaser_rate
        self.phaser.depth = state.phaser_depth
        self.phaser.feedback = state.phaser_feedback

        self._chorus_enabled = state.chorus_enabled
        self.chorus.rate = state.chorus_rate
        self.chorus.depth = state.chorus_depth
        self.chorus.mix = state.chorus_mix

        self._flanger_enabled = state.flanger_enabled
        self.flanger.rate = state.flanger_rate
        self.flanger.depth = state.flanger_depth
        self.flanger.feedback = state.flanger_feedback
        self.flanger.mix = state.flanger_mix

    def process(self, data: np.ndarray) -> np.ndarray:
        out = data
        if self._filter_enabled:
            out = self.global_filter.process(out)
        if self._mod_active:
            if self._phaser_enabled:
                out = self.phaser.process(out)
            if self._chorus_enabled:
                out = self.chorus.process(out)
            if self._flanger_enabled:
                out = self.flanger.process(out)
        return out

    def process_mod_only(self, data: np.ndarray) -> np.ndarray:
        """Process only mod effects (phaser, chorus, flanger), skip filter."""
        out = data
        if self._phaser_enabled and self.phaser_in_rack:
            out = self.phaser.process(out)
        if self._chorus_enabled and self.chorus_in_rack:
            out = self.chorus.process(out)
        if self._flanger_enabled and self.flanger_in_rack:
            out = self.flanger.process(out)
        return out

    def reset(self):
        self.global_filter.reset()
        self.phaser.reset()
        self.chorus.reset()
        self.flanger.reset()


# ---------------------------------------------------------------------------
#  Bit Crusher / Sample-Rate Reducer (Byte Me)
# ---------------------------------------------------------------------------

class BitCrusherProcessor:
    """Bit depth and sample-rate reduction.

    Independently controls bit depth (1-32 bits) and sample-rate reduction.
    Both effects are applied in the digital domain: quantisation rounds to
    the nearest step at the target bit depth; sample-rate reduction holds
    each input sample for an integer number of output samples (zero-order
    hold = classic 'downsampled' crunch without anti-aliasing).
    Wet/dry mix blends the crushed signal with the clean input.
    """

    def __init__(self, sample_rate: int = SAMPLE_RATE, buffer_size: int = 512):
        self.sr = sample_rate
        from mlrvp.config import BitCrusherState
        self.state = BitCrusherState()
        self._idx_int = np.arange(buffer_size, dtype=np.intp)
        # Pre-allocated wet buffers — size to max(buffer_size, 4096) so the
        # `else np.empty(...)` fallback in process() is dead in practice
        # (audio engine block size is fixed at buffer_size, but defensive
        # sizing matches the convention used elsewhere and keeps the
        # fast path branch-predictable).
        _bs = max(buffer_size, 4096)
        self._wet_buf = np.empty((_bs, 2), dtype=np.float32)
        self._take_buf = np.empty((_bs, 2), dtype=np.float32)

    def process(self, data: np.ndarray) -> np.ndarray:
        st = self.state
        if not st.enabled:
            return data
        mix = st.mix
        if mix < 0.001:
            return data

        n = len(data)
        wet = self._wet_buf[:n] if n <= len(self._wet_buf) else np.empty((n, 2), dtype=np.float32)
        wet[:] = data

        # --- Sample-rate reduction (zero-order hold) ---
        # reduction=0 → passthrough; reduction=1 → hold every ~32 samples
        hold_len = max(1, int(1.0 + st.sample_rate_reduction * 31))
        if hold_len > 1:
            # Vectorized ZOH: repeat every hold_len-th sample across the block
            # Build a selector index: [0,0,...,hold_len,hold_len,...,2*hold_len,...]
            idx = self._idx_int[:n] if n <= len(self._idx_int) else np.arange(n, dtype=np.intp)
            selector = idx // hold_len * hold_len
            selector = np.clip(selector, 0, n - 1)
            take_buf = self._take_buf[:n] if n <= len(self._take_buf) else np.empty((n, 2), dtype=np.float32)
            np.take(wet, selector, axis=0, out=take_buf)
            wet = take_buf

        # --- Bit depth reduction (in-place to avoid temporaries) ---
        bits = max(1, min(int(st.bit_depth), 32))
        if bits < 32:
            levels = float(2 ** (bits - 1))
            wet *= levels
            np.round(wet, out=wet)
            wet /= levels
            np.clip(wet, -1.0, 1.0, out=wet)

        return data * np.float32(1.0 - mix) + wet * np.float32(mix)

    def reset(self):
        pass


# ---------------------------------------------------------------------------
#  Tube Overdrive (Glow Worm)
# ---------------------------------------------------------------------------

class TubeOverdriveProcessor:
    """Warm asymmetric tube overdrive with bias, drive, tone, and mix.

    Models subtle harmonic enrichment of a 12AX7-style triode stage:
    - Bias offset shifts the operating point for even-harmonic colouring
    - Drive increases pre-gain before the soft-clip stage
    - Soft-clip uses a polynomial approximation (no harsh clipping)
    - Tone is a 1st-order shelving tilt: negative = darker, positive = brighter
    - Wet/dry mix safely blends the overdriven and clean signals
    """

    def __init__(self, sample_rate: int = SAMPLE_RATE):
        self.sr = sample_rate
        from mlrvp.config import TubeOverdriveState
        self.state = TubeOverdriveState()
        self._tone_lp = BiquadFilter(sample_rate)
        self._tone_hp = BiquadFilter(sample_rate)
        self._tone_lp.set_lowpass(3500.0, 0.707)
        self._tone_hp.set_highpass(3500.0, 0.707)
        # Track last tone value to avoid rebuilding filters every block
        self._last_tone: float = -1.0
        # Pre-allocated wet buffer (grow-only, same pattern as all other processors)
        _sz = 4096
        self._wet_buf = np.zeros((_sz, 2), dtype=np.float32)
        self._wet_buf_size = _sz

    def process(self, data: np.ndarray) -> np.ndarray:
        st = self.state
        if not st.enabled:
            return data
        mix = st.mix
        if mix < 0.001:
            return data

        n = len(data)
        n_ch = data.shape[1] if data.ndim == 2 else 1
        if n > self._wet_buf_size or n_ch != (self._wet_buf.shape[1] if self._wet_buf.ndim == 2 else 1):
            self._wet_buf = np.zeros((n, n_ch), dtype=np.float32)
            self._wet_buf_size = n
        wet = self._wet_buf[:n]

        # --- Input drive (in-place) ---
        drive = np.float32(1.0 + st.drive * 8.0)  # 1x to 9x pre-gain
        np.multiply(data, drive, out=wet)

        # --- Bias offset (asymmetric → even harmonics, in-place) ---
        bias_offset = np.float32((st.tube_bias - 0.5) * 0.15)  # ±0.075 offset
        wet += bias_offset

        # --- Polynomial soft clip: x / (1 + |x|), in-place ---
        abs_wet = np.abs(wet)
        abs_wet += np.float32(1.0)
        wet /= abs_wet
        wet -= np.float32(bias_offset * 0.5)   # partial bias compensation to re-center

        # --- Tone control ---
        # tone=0.5 is neutral; <0.5 rolls off highs; >0.5 boosts highs
        tone = st.tone
        if abs(tone - self._last_tone) > 0.005:
            # Only rebuild filter coefficients when tone has actually moved
            if tone < 0.48:
                freq = 400.0 + tone * (3500.0 / 0.48)
                self._tone_lp.set_lowpass(max(400.0, freq), 0.707)
            self._last_tone = tone

        if tone < 0.48:
            wet = self._tone_lp.process(wet)
        elif tone > 0.52:
            # Bright: high-shelf boost via HP component blend
            t = (tone - 0.52) / 0.48  # 0-1 for the bright range
            bright = self._tone_hp.process(wet)
            wet = wet + bright * np.float32(t * 0.6)

        # Safety clip before mix
        np.clip(wet, -2.0, 2.0, out=wet)

        return data * np.float32(1.0 - mix) + wet * np.float32(mix)

    def reset(self):
        self._tone_lp.reset()
        self._tone_hp.reset()


# ---------------------------------------------------------------------------
#  Lo-Fi / media-character DSP processors
# ---------------------------------------------------------------------------

class ReelToReelProcessor:
    """Studio reel-to-reel tape emulation: saturation, flutter, bias, tone.

    Uses soft-clip saturation, LFO-modulated micro-pitch (flutter),
    a bias offset into the saturation curve, and a lowpass tone filter.
    """

    TAPE_SPEEDS = [3.75, 7.5, 15.0, 30.0]  # ips

    def __init__(self, sample_rate: int = SAMPLE_RATE, buffer_size: int = 512):
        self.sr = sample_rate
        self.state = ReelToReelState()
        self._tone_lp = BiquadFilter(sample_rate)
        self._tone_lp.set_lowpass(12000.0, 0.707)
        self._flutter_phase = 0.0
        # Pre-allocated arrays
        self._idx_f64 = np.arange(buffer_size, dtype=np.float64)
        self._idx_int = np.arange(buffer_size, dtype=np.intp)
        # Circular buffer for modulated-delay pitch flutter
        self._delay_buf = np.zeros((4096, 2), dtype=np.float32)
        self._buf_pos = 0
        # Cache last tone value to avoid rebuilding filter every block
        self._last_tone: float = -1.0
        # Pre-allocated wet buffer (oversized so process() fallback is dead)
        self._wet_buf = np.empty((max(buffer_size, 4096), 2), dtype=np.float32)
        # Pre-allocated scratch buffers for pitch-flutter modulated read
        self._flutter_bufs = _FlutterReadBuffers(buffer_size)

    def process(self, data: np.ndarray) -> np.ndarray:
        if not self.state.enabled:
            return data
        n = len(data)
        mix = self.state.mix
        if mix < 0.001:
            return data

        wet = self._wet_buf[:n] if n <= len(self._wet_buf) else np.empty((n, 2), dtype=np.float32)
        wet[:] = data

        # Flutter: modulated-delay pitch wobble (real tape flutter)
        flutter_amt = self.state.flutter
        buf = self._delay_buf
        buf_len = len(buf)
        wp = self._buf_pos
        w_idx = (wp + self._idx_int[:n]) % buf_len
        buf[w_idx] = wet
        if flutter_amt > 0.01:
            speed_idx = max(0, min(self.state.tape_speed, 3))
            speed_factor = 1.0 - (speed_idx / 3.0) * 0.6
            # Mod depth in samples — exponential: subtle pitch at low, warbly at max
            mod_depth = flutter_amt * speed_factor * (8.0 + flutter_amt * 40.0)
            base_delay = mod_depth + 8.0
            idx = self._idx_f64[:n] if n <= len(self._idx_f64) else np.arange(n, dtype=np.float64)
            t = idx / self.sr
            ph = self._flutter_phase
            lfo = np.sin(2.0 * np.pi * 4.5 * (t + ph))
            lfo += 0.4 * np.sin(2.0 * np.pi * 0.7 * (t + ph))
            lfo += 0.2 * np.sin(2.0 * np.pi * 11.3 * (t + ph))
            lfo = lfo / 1.6  # normalize to [-1, 1]
            # Read from circular buffer with pitch-modulated delay
            delay_samps = base_delay + lfo * mod_depth
            read_pos = (wp + idx) - delay_samps
            _flutter_read_into(wet, buf, read_pos, buf_len, self._flutter_bufs)
            self._flutter_phase = math.fmod(self._flutter_phase + n / self.sr, 1000.0)
        self._buf_pos = (wp + n) % buf_len

        # Bias: shifts signal into saturation curve asymmetrically
        bias = self.state.bias
        if bias > 0.01:
            bias_offset = (bias - 0.5) * 0.5
            wet = wet + bias_offset

        # Saturation: heavy tape soft-clip
        sat = self.state.saturation
        if sat > 0.01:
            drive = 1.0 + sat * 8.0
            np.multiply(wet, drive, out=wet)
            np.tanh(wet, out=wet)
            wet *= np.float32(1.0 / math.tanh(drive))

        # Remove bias offset after saturation
        if bias > 0.01:
            wet = wet - (bias - 0.5) * 0.25

        # Tone: lowpass darkens at low values (freq = 1kHz..16kHz)
        tone_freq = 1000.0 + self.state.tone * 15000.0
        if abs(self.state.tone - self._last_tone) > 0.005:
            self._tone_lp.set_lowpass(tone_freq, 0.707)
            self._last_tone = self.state.tone
        wet = self._tone_lp.process(wet)

        return data * (1.0 - mix) + wet * mix

    def reset(self):
        self._tone_lp.reset()
        self._delay_buf[:] = 0
        self._buf_pos = 0
        self._flutter_phase = 0.0
        self._last_tone = -1.0


class CassetteTapeProcessor:
    """Home cassette tape emulation: saturation, hiss, punch, warp, tone.

    Cassette character comes from heavy midrange saturation, tape hiss (noise),
    low-end punch (bass emphasis), and speed wobble (warp).
    """

    def __init__(self, sample_rate: int = SAMPLE_RATE, buffer_size: int = 512):
        self.sr = sample_rate
        self.state = CassetteTapeState()
        self._tone_lp = BiquadFilter(sample_rate)
        self._tone_lp.set_lowpass(10000.0, 0.707)
        self._punch_lp = BiquadFilter(sample_rate)
        self._punch_lp.set_lowpass(200.0, 1.2)
        self._warp_phase = 0.0
        self._idx_f64 = np.arange(buffer_size, dtype=np.float64)
        self._idx_int = np.arange(buffer_size, dtype=np.intp)
        # Circular buffer for modulated-delay pitch warp
        self._delay_buf = np.zeros((4096, 2), dtype=np.float32)
        self._buf_pos = 0
        # Pre-generated noise buffer for tape hiss (avoids per-block RNG + alloc)
        _NL = 8192
        self._noise_buf = np.random.default_rng(42).standard_normal((_NL, 2)).astype(np.float32)
        self._noise_pos = 0
        # Cache last filter params to avoid rebuilding coefficients every block
        self._last_tone: float = -1.0
        self._last_punch: float = -1.0
        # Pre-allocated wet buffer (oversized so process() fallback is dead)
        self._wet_buf = np.empty((max(buffer_size, 4096), 2), dtype=np.float32)
        # Pre-allocated scratch buffers for pitch-warp modulated read
        self._flutter_bufs = _FlutterReadBuffers(buffer_size)

    def process(self, data: np.ndarray) -> np.ndarray:
        if not self.state.enabled:
            return data
        n = len(data)
        mix = self.state.mix
        if mix < 0.001:
            return data

        wet = self._wet_buf[:n] if n <= len(self._wet_buf) else np.empty((n, 2), dtype=np.float32)
        wet[:] = data

        # Warp: modulated-delay pitch wobble (cassette motor inconsistency)
        warp = self.state.warp
        buf = self._delay_buf
        buf_len = len(buf)
        wp = self._buf_pos
        w_idx = (wp + self._idx_int[:n]) % buf_len
        buf[w_idx] = wet
        if warp > 0.01:
            # Mod depth in samples — exponential: natural at low, broken-walkman at max
            mod_depth = warp * (15.0 + warp * 130.0)
            base_delay = mod_depth + 8.0
            idx = self._idx_f64[:n] if n <= len(self._idx_f64) else np.arange(n, dtype=np.float64)
            t = idx / self.sr
            ph = self._warp_phase
            lfo = np.sin(2.0 * np.pi * 1.8 * (t + ph))
            lfo += 0.5 * np.sin(2.0 * np.pi * 0.3 * (t + ph))
            lfo += 0.25 * np.sin(2.0 * np.pi * 5.7 * (t + ph))
            lfo = lfo / 1.75  # normalize to [-1, 1]
            delay_samps = base_delay + lfo * mod_depth
            read_pos = (wp + idx) - delay_samps
            _flutter_read_into(wet, buf, read_pos, buf_len, self._flutter_bufs)
            self._warp_phase = math.fmod(self._warp_phase + n / self.sr, 1000.0)
        self._buf_pos = (wp + n) % buf_len

        # Saturation: cassette tape has more aggressive midrange saturation
        sat = self.state.saturation
        if sat > 0.01:
            drive = 1.0 + sat * 5.0
            np.multiply(wet, drive, out=wet)
            np.tanh(wet, out=wet)
            wet *= np.float32(1.0 / math.tanh(drive))

        # Punch: bass pump with re-saturation for cassette body
        punch = self.state.punch
        if punch > 0.01:
            # Only rebuild filter when punch value changes meaningfully
            if abs(punch - self._last_punch) > 0.005:
                self._punch_lp.set_lowpass(180.0 + punch * 100.0, 0.8 + punch * 0.8)
                self._last_punch = punch
            bass = self._punch_lp.process(wet)
            # Exponential gain: natural boost at low, extreme pump at max
            bass_gain = punch * (1.2 + punch * 2.0)
            wet = wet + bass * bass_gain
            # Re-saturate after bass boost for tape compression feel
            punch_drive = 1.0 + punch * 2.5
            np.multiply(wet, punch_drive, out=wet)
            np.tanh(wet, out=wet)
            wet *= np.float32(1.0 / math.tanh(punch_drive))

        # Hiss: additive tape hiss (band-limited noise) — gated on signal presence
        hiss = self.state.hiss
        has_signal = float(np.abs(data[:n]).max()) > 1e-5
        if hiss > 0.01 and has_signal:
            p = self._noise_pos
            if p + n > len(self._noise_buf):
                p = 0
            noise = self._noise_buf[p:p + n] * np.float32(hiss * 0.015)
            self._noise_pos = p + n
            wet = wet + noise

        # Tone: cassette rolls off highs more than reel-to-reel
        tone_freq = 1500.0 + self.state.tone * 8500.0
        if abs(self.state.tone - self._last_tone) > 0.005:
            self._tone_lp.set_lowpass(tone_freq, 0.707)
            self._last_tone = self.state.tone
        wet = self._tone_lp.process(wet)

        return data * (1.0 - mix) + wet * mix

    def reset(self):
        self._tone_lp.reset()
        self._punch_lp.reset()
        self._delay_buf[:] = 0
        self._buf_pos = 0
        self._warp_phase = 0.0
        self._noise_pos = 0
        self._last_tone = -1.0
        self._last_punch = -1.0


# ---------------------------------------------------------------------------
#  Ether Drift — Bipolar Infinite Sawtooth Phaser (Vaporwave/Slushwave)
# ---------------------------------------------------------------------------

class EtherDriftProcessor:
    """Ether Drift — bipolar infinite sawtooth phaser for vaporwave/slushwave.

    Splits the signal into two phaser chains driven by opposing sawtooth LFOs:
      RISE chain: frequency always sweeps upward   (saw LFO 0→1, wrap)
      FALL chain: frequency always sweeps downward (saw LFO 1→0, wrap)

    Each chain runs N_STAGES second-order allpass biquad sections.  The
    notches sweep continuously in one direction, creating the trademark
    "infinite drift" shimmer associated with slushwave production.

    SPREAD pans RISE toward the left channel and FALL toward the right for
    wide stereo imaging.  SYNC locks both LFOs to the same rate.  ROUTE
    selects BOTH chains, RISE only, or FALL only.
    """

    N_STAGES = 4            # allpass stages per chain — 4 gives rich sweep
    F_MIN    = 150.0        # Hz — sweep floor
    F_MAX    = 4800.0       # Hz — sweep ceiling

    def __init__(self, sample_rate: int = SAMPLE_RATE, buffer_size: int = 512):
        self.sr = sample_rate
        self.state = EtherDriftState()
        # Per-chain per-stage per-channel allpass filter state
        # shape: [2 chains][N_STAGES][2 channels][2 zi values]
        self._zi = np.zeros((2, self.N_STAGES, 2, 2), dtype=np.float64)
        # Feedback delay state (last output sample of each chain, per channel)
        self._fb_state = np.zeros((2, 2), dtype=np.float32)
        # LFO phases: RISE starts at 0 (bottom), FALL at 0.5 (mid, already descending)
        self._phase_rise = 0.0
        self._phase_fall = 0.5
        # Pre-allocated buffers for _run_chain
        self._inp_buf = np.empty((buffer_size, 2), dtype=np.float32)
        self._f64_buf = np.empty((buffer_size, 2), dtype=np.float64)
        # Per-chain output buffers (two chains can run in "BOTH" mode)
        self._out_f32_bufs = [
            np.empty((buffer_size, 2), dtype=np.float32),
            np.empty((buffer_size, 2), dtype=np.float32),
        ]
        # Pre-allocated wet buffer for BOTH-route stereo spread
        self._wet_buf = np.empty((buffer_size, 2), dtype=np.float32)
        self._wet_buf_size = buffer_size
        # Cache log bounds
        self._log_fmin = math.log(self.F_MIN)
        self._log_fmax = math.log(self.F_MAX)
        # Pre-allocated float32 index for decay envelope
        self._idx_f32 = np.arange(buffer_size, dtype=np.float32)

    @staticmethod
    def _ap2_coeffs(freq: float, sr: float) -> tuple:
        """2nd-order allpass biquad coefficients (butterworth Q=0.707)."""
        freq = max(20.0, min(freq, sr * 0.45))
        w0 = 2.0 * math.pi * freq / sr
        alpha = math.sin(w0) / (2.0 * 0.707)
        cos_w0 = math.cos(w0)
        a0 = 1.0 + alpha
        b = np.array([(1.0 - alpha) / a0,
                      (-2.0 * cos_w0) / a0,
                      (1.0 + alpha) / a0], dtype=np.float64)
        a = np.array([1.0,
                      (-2.0 * cos_w0) / a0,
                      (1.0 - alpha) / a0], dtype=np.float64)
        return b, a

    def _run_chain(self, data: np.ndarray, phase_t: float,
                   chain_idx: int, feedback: float,
                   depth: float = 1.0) -> np.ndarray:
        """Run N_STAGES allpass stages at staggered frequencies derived from phase_t."""
        n = len(data)
        # depth scales the sweep width: 0 = minimal sweep near F_MIN, 1 = full range
        log_range = (self._log_fmax - self._log_fmin) * max(0.05, depth)
        base_freq = math.exp(self._log_fmin + phase_t * log_range)
        # Feedback injection: taper the previous block's last sample with an
        # exponential decay across the block to avoid a constant DC-step which
        # causes ringing/buzzing through the allpass chain at block boundaries.
        if feedback > 0.001:
            _fb = self._fb_state[chain_idx]
            fb_sig_0 = math.tanh(float(_fb[0]) * 1.5) / 1.5
            fb_sig_1 = math.tanh(float(_fb[1]) * 1.5) / 1.5
            decay_idx = self._idx_f32[:n] if n <= len(self._idx_f32) else np.arange(n, dtype=np.float32)
            decay = np.exp(-decay_idx * np.float32(4.0 / n))
            fb_scale = np.float32(feedback * 0.75)
            inp = self._inp_buf[:n] if n <= len(self._inp_buf) else np.empty((n, 2), dtype=np.float32)
            inp[:] = data
            inp[:, 0] += fb_sig_0 * fb_scale * decay
            inp[:, 1] += fb_sig_1 * fb_scale * decay
        else:
            inp = data  # no copy needed; astype below creates a new array

        # Process all stages with both channels at once (axis=0) to avoid
        # per-channel astype round-trips.  Keep data in float64 for the
        # full allpass chain, then convert back once at the end.
        out_f64 = self._f64_buf[:n] if n <= len(self._f64_buf) else np.empty((n, 2), dtype=np.float64)
        out_f64[:] = inp
        for stage in range(self.N_STAGES):
            # Stagger stages logarithmically for a richer phaser comb
            f_stage = min(base_freq * math.exp(stage * 0.55), self.sr * 0.44)
            b, a = self._ap2_coeffs(f_stage, self.sr)
            out_f64, self._zi[chain_idx, stage] = lfilter(
                b, a, out_f64, axis=0, zi=self._zi[chain_idx, stage])
            # Flush denormals from filter state to prevent CPU spikes
            zi_stage = self._zi[chain_idx, stage]
            zi_stage[np.abs(zi_stage) < 1e-18] = 0.0
            # Early NaN/Inf detection — reset this stage before it corrupts
            # downstream stages
            if not np.all(np.isfinite(zi_stage)):
                self._zi[chain_idx, stage] = 0.0
                out_f64[:] = inp  # restart from clean input

        out_buf = self._out_f32_bufs[chain_idx]
        out = out_buf[:n] if n <= len(out_buf) else np.empty((n, 2), dtype=np.float32)
        out[:] = out_f64

        # Store last sample for next-block feedback; guard against NaN/Inf
        last = out[-1]
        if not np.all(np.isfinite(last)):
            last = np.zeros(2, dtype=np.float32)
            self._zi[chain_idx] = np.zeros((self.N_STAGES, 2, 2), dtype=np.float64)
        self._fb_state[chain_idx] = last
        return out

    def process(self, data: np.ndarray) -> np.ndarray:
        st = self.state
        if not st.enabled:
            return data
        mix = st.mix
        if mix < 0.001:
            return data

        n = len(data)
        dt = n / self.sr

        # Rate: knob 0→1 maps to 0.05–3 Hz
        rate_hz = 0.05 + st.rate * 2.95

        # SYNC: when on, both chains share the same rate.
        # When off, FALL uses a 0.75x rate so the two sweeps drift apart
        # without ever perfectly re-aligning — adds natural woozy movement.
        if st.sync:
            delta_rise = rate_hz * dt
            delta_fall = delta_rise
        else:
            delta_rise = rate_hz * dt
            delta_fall = rate_hz * 0.75 * dt

        # Mid-block LFO positions for coefficient stability across the block
        mid_rise = (self._phase_rise + delta_rise * 0.5) % 1.0
        mid_fall = (self._phase_fall - delta_fall * 0.5) % 1.0

        # Advance LFOs
        self._phase_rise = (self._phase_rise + delta_rise) % 1.0
        self._phase_fall = (self._phase_fall - delta_fall) % 1.0

        route = st.route
        feedback = st.feedback
        depth = st.depth

        if route == 1:      # RISE only
            wet = self._run_chain(data, mid_rise, 0, feedback, depth)
        elif route == 2:    # FALL only
            wet = self._run_chain(data, mid_fall, 1, feedback, depth)
        else:               # BOTH — default, stereo spread between chains
            rise_out = self._run_chain(data, mid_rise, 0, feedback, depth)
            fall_out = self._run_chain(data, mid_fall, 1, feedback, depth)

            # Stereo spread: RISE→left, FALL→right
            spread = st.spread
            rise_l = 1.0
            rise_r = max(0.0, 1.0 - spread)
            fall_l = max(0.0, 1.0 - spread)
            fall_r = 1.0

            if n > self._wet_buf_size:
                self._wet_buf = np.empty((n, 2), dtype=np.float32)
                self._wet_buf_size = n
            wet = self._wet_buf[:n]
            # Normalise by 0.5 (two chains summed)
            wet[:, 0] = (rise_out[:, 0] * rise_l + fall_out[:, 0] * fall_l) * 0.5
            wet[:, 1] = (rise_out[:, 1] * rise_r + fall_out[:, 1] * fall_r) * 0.5

        return data * np.float32(1.0 - mix) + wet * np.float32(mix)

    def reset(self):
        self._zi[:] = 0.0
        self._fb_state[:] = 0.0
        self._phase_rise = 0.0
        self._phase_fall = 0.5


class VHSProcessor:
    """VHS tape degradation: wobble, dropout, worn character, noise.

    VHS has strong pitch wobble from helical scan tracking, signal dropouts,
    high-frequency loss (worn tape), and broadband noise/static.
    """

    def __init__(self, sample_rate: int = SAMPLE_RATE, buffer_size: int = 512):
        self.sr = sample_rate
        self.state = VHSState()
        self._worn_lp = BiquadFilter(sample_rate)
        self._worn_lp.set_lowpass(8000.0, 0.707)
        self._wobble_phase = 0.0
        self._idx_f64 = np.arange(buffer_size, dtype=np.float64)
        self._idx_int = np.arange(buffer_size, dtype=np.intp)
        # Circular buffer for modulated-delay pitch wobble
        self._delay_buf = np.zeros((4096, 2), dtype=np.float32)
        self._buf_pos = 0
        # Dropout counter
        self._dropout_counter = 0
        self._dropout_gate = 1.0
        # Pre-generated noise buffer (avoids per-block RNG + alloc)
        _NL = 8192
        self._noise_buf = np.random.default_rng(99).standard_normal((_NL, 2)).astype(np.float32)
        self._noise_pos = 0
        # Per-instance RNG for stochastic effects (thread-safe)
        self._rng = np.random.default_rng()
        # Cache last worn value to avoid rebuilding filter every block
        self._last_worn: float = -1.0
        # Pre-allocated wet buffer (oversized so process() fallback is dead)
        self._wet_buf = np.empty((max(buffer_size, 4096), 2), dtype=np.float32)
        # Pre-allocated scratch buffers for pitch-wobble modulated read
        self._flutter_bufs = _FlutterReadBuffers(buffer_size)

    def process(self, data: np.ndarray) -> np.ndarray:
        if not self.state.enabled:
            return data
        n = len(data)
        mix = self.state.mix
        if mix < 0.001:
            return data

        wet = self._wet_buf[:n] if n <= len(self._wet_buf) else np.empty((n, 2), dtype=np.float32)
        wet[:] = data

        # Wobble: modulated-delay pitch wobble (VHS tracking instability)
        wobble = self.state.wobble
        buf = self._delay_buf
        buf_len = len(buf)
        wp = self._buf_pos
        w_idx = (wp + self._idx_int[:n]) % buf_len
        buf[w_idx] = wet
        if wobble > 0.01:
            # Mod depth in samples — exponential: usable at low, trashed at max
            mod_depth = wobble * (12.0 + wobble * 90.0)
            base_delay = mod_depth + 8.0
            idx = self._idx_f64[:n] if n <= len(self._idx_f64) else np.arange(n, dtype=np.float64)
            t = idx / self.sr
            ph = self._wobble_phase
            lfo = np.sin(2.0 * np.pi * 0.8 * (t + ph))
            lfo += 0.6 * np.sin(2.0 * np.pi * 3.1 * (t + ph))
            lfo += 0.35 * np.sin(2.0 * np.pi * 7.3 * (t + ph))
            lfo += 0.15 * np.sin(2.0 * np.pi * 13.7 * (t + ph))
            lfo = lfo / 2.1  # normalize to [-1, 1]
            delay_samps = base_delay + lfo * mod_depth
            read_pos = (wp + idx) - delay_samps
            _flutter_read_into(wet, buf, read_pos, buf_len, self._flutter_bufs)
            self._wobble_phase = math.fmod(self._wobble_phase + n / self.sr, 1000.0)
        self._buf_pos = (wp + n) % buf_len

        # Dropout: intermittent signal drops (simulated per-block)
        dropout = self.state.dropout
        if dropout > 0.01:
            self._dropout_counter += 1
            # Stochastic dropout: higher values = more frequent and deeper
            if self._rng.random() < dropout * 0.06:
                self._dropout_gate = dropout * 0.02 + self._rng.random() * 0.2
            else:
                self._dropout_gate = min(1.0, self._dropout_gate + 0.08)
            wet = wet * np.float32(self._dropout_gate)

        # Worn: high-frequency rolloff (simulating worn heads/tape)
        worn = self.state.worn
        if worn > 0.01:
            cutoff = 12000.0 - worn * 9000.0  # 12kHz down to 3kHz
            if abs(worn - self._last_worn) > 0.005:
                self._worn_lp.set_lowpass(max(500.0, cutoff), 0.707)
                self._last_worn = worn
            wet = self._worn_lp.process(wet)

        # Noise: broadband static
        noise_amt = self.state.noise
        if noise_amt > 0.01:
            p = self._noise_pos
            if p + n > len(self._noise_buf):
                p = 0
            noise = self._noise_buf[p:p + n] * np.float32(noise_amt * 0.02)
            self._noise_pos = p + n
            wet = wet + noise

        return data * (1.0 - mix) + wet * mix

    def reset(self):
        self._worn_lp.reset()
        self._delay_buf[:] = 0
        self._buf_pos = 0
        self._wobble_phase = 0.0
        self._dropout_counter = 0
        self._dropout_gate = 1.0
        self._noise_pos = 0
        self._last_worn = -1.0


class VinylProcessor:
    """Vinyl record emulation: crackle, pops, warmth, groove wear.

    Vintage record character from surface noise (crackle), random pops,
    low-end warmth (bass shelf boost + gentle saturation), and high-frequency
    loss from groove wear.
    """

    def __init__(self, sample_rate: int = SAMPLE_RATE, buffer_size: int = 512):
        self.sr = sample_rate
        self.state = VinylState()
        self._warmth_lp = BiquadFilter(sample_rate)
        self._warmth_lp.set_lowpass(400.0, 0.8)
        self._wear_lp = BiquadFilter(sample_rate)
        self._wear_lp.set_lowpass(14000.0, 0.707)
        # Modulated delay for warped-record wow
        self._delay_buf = np.zeros((4096, 2), dtype=np.float32)
        self._buf_pos = 0
        self._wow_phase = 0.0
        self._idx_f64 = np.arange(buffer_size, dtype=np.float64)
        self._idx_int = np.arange(buffer_size, dtype=np.intp)
        # Pop scheduling
        self._samples_until_pop = int(sample_rate * 0.5)
        # Pre-generated noise buffers (avoids per-block RNG + alloc)
        _NL = 8192
        _rng = np.random.default_rng(77)
        self._noise_buf = _rng.standard_normal((_NL, 2)).astype(np.float32)
        self._gate_buf = _rng.random(_NL).astype(np.float32)
        self._noise_pos = 0
        # Per-instance RNG for stochastic effects (thread-safe)
        self._rng = np.random.default_rng()
        # Cache last filter param values
        self._last_wear: float = -1.0
        self._last_warmth_freq: float = -1.0
        # Pre-allocated wet buffer (oversized so process() fallback is dead)
        self._wet_buf = np.empty((max(buffer_size, 4096), 2), dtype=np.float32)
        # Pre-allocated scratch buffers for pitch-wow modulated read
        self._flutter_bufs = _FlutterReadBuffers(buffer_size)

    def process(self, data: np.ndarray) -> np.ndarray:
        if not self.state.enabled:
            return data
        n = len(data)
        mix = self.state.mix
        if mix < 0.001:
            return data

        # Gate all additive noise on input signal presence.
        # Crackle/pops are injected on top of the signal — they must not
        # fire on a silent/unrouted bus.
        has_signal = float(np.abs(data[:n]).max()) > 1e-5

        wet = self._wet_buf[:n] if n <= len(self._wet_buf) else np.empty((n, 2), dtype=np.float32)
        wet[:] = data

        # Wow: slow warped-record pitch modulation (driven by warmth knob)
        warmth = self.state.warmth
        buf = self._delay_buf
        buf_len = len(buf)
        wp = self._buf_pos
        w_idx = (wp + self._idx_int[:n]) % buf_len
        buf[w_idx] = wet
        if warmth > 0.01:
            # Slow wow: 0.5Hz primary, depth scales with warmth
            mod_depth = warmth * (20.0 + warmth * 80.0)
            base_delay = mod_depth + 8.0
            idx = self._idx_f64[:n] if n <= len(self._idx_f64) else np.arange(n, dtype=np.float64)
            t = idx / self.sr
            ph = self._wow_phase
            lfo = np.sin(2.0 * np.pi * 0.5 * (t + ph))
            lfo += 0.3 * np.sin(2.0 * np.pi * 1.3 * (t + ph))
            lfo = lfo / 1.3  # normalize to [-1, 1]
            delay_samps = base_delay + lfo * mod_depth
            read_pos = (wp + idx) - delay_samps
            _flutter_read_into(wet, buf, read_pos, buf_len, self._flutter_bufs)
            self._wow_phase = math.fmod(self._wow_phase + n / self.sr, 1000.0)
        self._buf_pos = (wp + n) % buf_len

        # Warmth: bass shelf boost + heavier vinyl saturation
        if warmth > 0.01:
            warmth_freq = 300.0 + warmth * 200.0
            if abs(warmth_freq - self._last_warmth_freq) > 2.0:
                self._warmth_lp.set_lowpass(warmth_freq, 0.9)
                self._last_warmth_freq = warmth_freq
            bass = self._warmth_lp.process(wet)
            wet = wet + bass * warmth * (0.8 + warmth * 1.0)
            # Heavier vinyl saturation
            drive = 1.0 + warmth * 3.5
            np.multiply(wet, drive, out=wet)
            np.tanh(wet, out=wet)
            wet *= np.float32(1.0 / math.tanh(drive))

        # Wear: high-frequency rolloff (old grooves lose treble)
        wear = self.state.wear
        if wear > 0.01:
            cutoff = 14000.0 - wear * 10000.0  # 14kHz down to 4kHz
            if abs(wear - self._last_wear) > 0.005:
                self._wear_lp.set_lowpass(max(1000.0, cutoff), 0.707)
                self._last_wear = wear
            wet = self._wear_lp.process(wet)

        # Crackle: continuous surface noise — only when signal is present
        crackle = self.state.crackle
        if crackle > 0.01 and has_signal:
            p = self._noise_pos
            if p + n > len(self._noise_buf):
                p = 0
            raw_noise = self._noise_buf[p:p + n]
            # Denser crackle with more presence
            gate = (self._gate_buf[p:p + n] < crackle * 0.5)
            crackle_sig = raw_noise * gate[:, np.newaxis] * np.float32(crackle * 0.12)
            self._noise_pos = p + n
            wet = wet + crackle_sig

        # Pops: random transient clicks — only when signal is present
        pops = self.state.pops
        if pops > 0.01:
            self._samples_until_pop -= n
            if self._samples_until_pop <= 0:
                if has_signal:
                    pop_pos = min(n - 1, max(0, int(self._rng.random() * n)))
                    pop_amp = 0.08 + pops * 0.25
                    pop_sign = 1.0 if self._rng.random() > 0.5 else -1.0
                    wet[pop_pos, 0] += np.float32(pop_amp * pop_sign)
                    wet[pop_pos, 1] += np.float32(pop_amp * pop_sign)
                    # Wider pop (couple adjacent samples)
                    if pop_pos + 1 < n:
                        wet[pop_pos + 1] += np.float32(pop_amp * pop_sign * 0.5)
                    if pop_pos + 2 < n:
                        wet[pop_pos + 2] += np.float32(pop_amp * pop_sign * 0.2)
                # Always reschedule the timer so pops fire promptly when signal returns
                avg_gap = max(0.03, 1.5 - pops * 1.3)
                self._samples_until_pop = int(
                    self.sr * avg_gap * (0.5 + self._rng.random())
                )

        return data * (1.0 - mix) + wet * mix

    def reset(self):
        self._warmth_lp.reset()
        self._wear_lp.reset()
        self._delay_buf[:] = 0
        self._buf_pos = 0
        self._wow_phase = 0.0
        self._noise_pos = 0
        self._samples_until_pop = int(self.sr * 0.5)
        self._last_wear = -1.0
        self._last_warmth_freq = -1.0

class CompressorProcessor:
    """Feedforward stereo compressor with sidechain support.

    Supports self-keying or external sidechain from group buses.
    Includes sidechain HP/LP filters for frequency-selective compression.
    """

    def __init__(self, sample_rate: int = SAMPLE_RATE):
        self.sr = sample_rate
        self.state = CompressorState()
        self._sc_hp = BiquadFilter(sample_rate)
        self._sc_hp.set_highpass(20.0, 0.707)
        # Envelope follower state
        self._env = 0.0
        # Gain reduction in dB for metering
        self.gr_db = 0.0
        # Pre-allocated buffers
        from mlrvp.config import BUFFER_SIZE as _BS
        self._buf_size = _BS
        self._gain = np.ones(_BS, dtype=np.float32)
        self._sc_buf = np.zeros((_BS, 2), dtype=np.float32)

    def process(self, data: np.ndarray, sidechain: np.ndarray | None = None) -> np.ndarray:
        """Process audio through the compressor.

        Args:
            data: stereo audio to compress.
            sidechain: optional external sidechain signal. If None, uses data.
        """
        if not self.state.enabled:
            self.gr_db = 0.0
            return data

        n = len(data)
        st = self.state

        # Sidechain signal — copy into pre-allocated buffer to avoid allocation
        sc_src = sidechain if sidechain is not None else data
        if n != self._buf_size:
            self._buf_size = n
            self._gain = np.ones(n, dtype=np.float32)
            self._sc_buf = np.zeros((n, 2), dtype=np.float32)
        self._sc_buf[:n] = sc_src[:n]
        sc = self._sc_buf[:n]

        # Apply sidechain filters
        if getattr(st, 'thrust_enabled', True) and st.sc_hp_freq > 25.0:
            self._sc_hp.set_highpass(st.sc_hp_freq, 0.707)
            sc = self._sc_hp.process(sc)

        # Compute level per sample (mono sum for detection)
        sc_mono = (np.abs(sc[:, 0]) + np.abs(sc[:, 1])) * 0.5

        # Convert threshold
        thresh_lin = 10.0 ** (st.threshold / 20.0)
        ratio = max(1.0, st.ratio)

        # Makeup gain
        makeup_lin = 10.0 ** (st.makeup_gain / 20.0)

        # Envelope attack/release coefficients
        att_ms = max(0.1, st.attack)
        rel_ms = max(1.0, st.release)
        alpha_a = np.exp(-1.0 / (att_ms * 0.001 * self.sr))
        alpha_r = np.exp(-1.0 / (rel_ms * 0.001 * self.sr))

        # Process sample-by-sample envelope + gain reduction.
        gain = self._gain
        gain[:n] = 1.0

        if _HAS_NUMBA:
            env, gr_db = _compressor_jit(
                sc_mono, n, gain, thresh_lin, ratio,
                float(alpha_a), float(alpha_r), self._env,
            )
            self._env = env
            self.gr_db = gr_db
        else:
            env = self._env
            gr_sum = 0.0
            _thresh = thresh_lin

            for i in range(n):
                level = float(sc_mono[i])
                if level > env:
                    env = alpha_a * env + (1.0 - alpha_a) * level
                else:
                    env = alpha_r * env + (1.0 - alpha_r) * level

                if env > _thresh:
                    g = (_thresh / env) ** (1.0 - 1.0 / ratio)
                    gr_sum += math.log10(max(g, 1e-10)) * 20.0
                    gain[i] = float(g)

            self._env = env
            self.gr_db = gr_sum / max(n, 1)

        # Apply gain reduction + makeup
        return data * gain[:n, np.newaxis] * np.float32(makeup_lin)

    def reset(self):
        self._sc_hp.reset()
        self._env = 0.0
        self.gr_db = 0.0
        self._gain[:] = 1.0
        self._sc_buf[:] = 0


# ------------------------------------------------------------------ #
# Glue Compressor (SSL G-Bus inspired feedback compressor)           #
# ------------------------------------------------------------------ #

class GlueCompressorProcessor:
    """SSL G-Bus style feedback glue compressor.

    Features a soft knee, stepped attack/ratio/release options, and a
    program-dependent auto-release mode — tuned for bus-glue duties.
    """

    _RATIOS = [2.0, 4.0, 10.0]
    _ATTACKS_MS = [0.1, 0.3, 1.0, 3.0, 10.0, 30.0]
    _RELEASES_MS = [100.0, 300.0, 600.0]   # index 3 = auto
    _KNEE_DB = 6.0
    _HPF_FREQS = [0.0, 30.0, 60.0, 105.0, 125.0, 185.0]

    def __init__(self, sample_rate: int = SAMPLE_RATE):
        self.sr = sample_rate
        self.state = GlueCompressorState()
        self._env = 0.0
        self.gr_db = 0.0
        self._hpf = BiquadFilter(sample_rate)
        self._hpf_idx_cached = -1
        # Pre-allocated gain buffer
        from mlrvp.config import BUFFER_SIZE as _BS
        self._buf_size = _BS
        self._gain = np.ones(_BS, dtype=np.float32)

    def process(self, data: np.ndarray) -> np.ndarray:
        if not self.state.enabled:
            self.gr_db = 0.0
            return data

        n = len(data)
        st = self.state

        ratio = self._RATIOS[max(0, min(2, st.ratio_idx))]
        attack_ms = self._ATTACKS_MS[max(0, min(5, st.attack_idx))]
        auto_release = (st.release_idx == 3)
        release_ms = self._RELEASES_MS[max(0, min(2, st.release_idx))] if not auto_release else 300.0

        thresh_lin = 10.0 ** (st.threshold / 20.0)
        makeup_lin = 10.0 ** (st.makeup_gain / 20.0)

        # Precompute sr coefficient used in every exp() call
        _sr_ms = 0.001 * self.sr
        alpha_a = math.exp(-1.0 / (attack_ms * _sr_ms))
        alpha_r = math.exp(-1.0 / (release_ms * _sr_ms))

        # Soft-knee boundaries (linear domain, no log in loop)
        half_knee = self._KNEE_DB / 2.0
        knee_lower_lin = thresh_lin * (10.0 ** (-half_knee / 20.0))
        knee_upper_lin = thresh_lin * (10.0 ** (half_knee / 20.0))
        knee_range_lin = max(knee_upper_lin - knee_lower_lin, 1e-10)

        inv_ratio = 1.0 / ratio

        # Mono sum for detection (with optional sidechain HPF)
        hpf_idx = max(0, min(5, st.hpf_idx))
        if hpf_idx > 0:
            if hpf_idx != self._hpf_idx_cached:
                self._hpf.set_highpass(self._HPF_FREQS[hpf_idx], 0.707)
                self._hpf_idx_cached = hpf_idx
            sc_data = self._hpf.process(data)
        else:
            sc_data = data
        sc_mono = (np.abs(sc_data[:, 0]) + np.abs(sc_data[:, 1])) * 0.5

        if n != self._buf_size:
            self._buf_size = n
            self._gain = np.ones(n, dtype=np.float32)
        gain = self._gain
        gain[:n] = 1.0

        if _HAS_NUMBA:
            env, gr_db = _glue_jit(
                sc_mono, n, gain, thresh_lin, inv_ratio,
                alpha_a, alpha_r,
                knee_lower_lin, knee_upper_lin, knee_range_lin,
                auto_release, _sr_ms, self._env,
            )
            self._env = env
            self.gr_db = gr_db
        else:
            env = self._env
            gr_sum = 0.0

            for i in range(n):
                level = float(sc_mono[i])

                if level > env:
                    env = alpha_a * env + (1.0 - alpha_a) * level
                else:
                    if auto_release:
                        gr_ratio = env / thresh_lin
                        auto_rel_ms = max(50.0, 600.0 / max(gr_ratio, 1.0))
                        alpha_r_auto = math.exp(-1.0 / (auto_rel_ms * _sr_ms))
                        env = alpha_r_auto * env + (1.0 - alpha_r_auto) * level
                    else:
                        env = alpha_r * env + (1.0 - alpha_r) * level

                if env <= knee_lower_lin:
                    g = 1.0
                elif env >= knee_upper_lin:
                    g = (thresh_lin / env) ** (1.0 - inv_ratio)
                else:
                    t = (env - knee_lower_lin) / knee_range_lin
                    full_g = (thresh_lin / env) ** (1.0 - inv_ratio)
                    g = 1.0 + (full_g - 1.0) * t * t

                gain[i] = float(g)
                if g < 1.0:
                    gr_sum += math.log10(g) * 20.0

            self._env = env
            self.gr_db = gr_sum / max(n, 1)

        # Flush denormals from envelope to prevent idle noise
        if abs(self._env) < 1e-18:
            self._env = 0.0

        return data * gain[:n, np.newaxis] * np.float32(makeup_lin)

    def reset(self):
        self._hpf.reset()
        self._env = 0.0
        self.gr_db = 0.0
        self._hpf_idx_cached = -1
        self._gain[:] = 1.0


# ------------------------------------------------------------------ #
# Hall / Room / Chamber Reverb                                        #
# ------------------------------------------------------------------ #

def _circ_read(buf, idx, n, sz, out=None):
    """Block-read *n* stereo samples from a circular buffer."""
    end = idx + n
    if end <= sz:
        if out is not None:
            out[:n] = buf[idx:end]
            return out[:n]
        return buf[idx:end].copy()
    split = sz - idx
    if out is None:
        out = np.empty((n, 2), dtype=np.float32)
    out[:split] = buf[idx:sz]
    out[split:n] = buf[:end - sz]
    return out[:n]


def _circ_write(buf, idx, data, sz):
    """Block-write stereo samples into a circular buffer."""
    n = len(data)
    end = idx + n
    if end <= sz:
        buf[idx:end] = data
    else:
        split = sz - idx
        buf[idx:sz] = data[:split]
        buf[:end - sz] = data[split:]


def _circ_write_f64(buf, idx, data, sz):
    """Block-write stereo samples into a float64 circular buffer."""
    n = len(data)
    end = idx + n
    if end <= sz:
        buf[idx:end] = data
    else:
        split = sz - idx
        buf[idx:sz] = data[:split]
        buf[:end - sz] = data[split:]


class _CombFilter:
    """Feedback comb filter with one-pole damping — block-processed.

    Uses scipy lfilter (C) for the IIR damping and numpy slicing for
    circular-buffer I/O.  Zero Python per-sample loops.
    """
    __slots__ = ('_buf', '_size', '_idx', '_fb', '_damp_zi', '_b', '_a', '_rd_buf')

    def __init__(self, delay_samples: int):
        self._size = delay_samples
        self._buf = np.zeros((delay_samples, 2), dtype=np.float32)
        self._idx = 0
        self._fb = np.float32(0.5)
        self._rd_buf = np.empty((delay_samples, 2), dtype=np.float32)
        # One-pole IIR: y[n] = (1-d)*x[n] + d*y[n-1]  — float32 to avoid astype
        self._b = np.array([0.5], dtype=np.float32)
        self._a = np.array([1.0, -0.5], dtype=np.float32)
        self._damp_zi = np.zeros((1, 2), dtype=np.float32)

    def set_feedback(self, fb: float):
        self._fb = np.float32(fb)

    def set_damp(self, damp: float):
        self._b[0] = 1.0 - damp
        self._a[1] = -damp

    def process(self, inp: np.ndarray) -> np.ndarray:
        n = len(inp)
        sz = self._size
        idx = self._idx

        if sz >= n:
            # Common path: delay > buffer — single-block read/write
            delayed = _circ_read(self._buf, idx, n, sz, out=self._rd_buf)
            damped, self._damp_zi = lfilter(
                self._b, self._a, delayed, axis=0, zi=self._damp_zi)
            self._damp_zi[np.abs(self._damp_zi) < 1e-18] = 0.0
            _circ_write(self._buf, idx,
                        inp + damped * self._fb, sz)
            self._idx = (idx + n) % sz
            return delayed

        # Rare fallback: delay < buffer — process in delay-sized chunks
        out = np.empty_like(inp)
        pos = 0
        while pos < n:
            cn = min(sz, n - pos)
            delayed = _circ_read(self._buf, idx, cn, sz, out=self._rd_buf)
            damped, self._damp_zi = lfilter(
                self._b, self._a, delayed, axis=0, zi=self._damp_zi)
            self._damp_zi[np.abs(self._damp_zi) < 1e-18] = 0.0
            _circ_write(self._buf, idx,
                        inp[pos:pos + cn] + damped * self._fb, sz)
            out[pos:pos + cn] = delayed
            idx = (idx + cn) % sz
            pos += cn
        self._idx = idx
        return out

    def reset(self):
        self._buf[:] = 0.0
        self._idx = 0
        self._damp_zi[:] = 0.0


class _AllpassFilter:
    """Schroeder allpass — block-processed in delay-sized chunks.

    For delays >= buffer size a single numpy pass is used; otherwise
    the buffer is split into delay-sized chunks (typically 1-3 iterations
    instead of 512 per-sample iterations).
    """
    __slots__ = ('_buf', '_size', '_idx', '_fb', '_rd_buf', '_flush_count')

    def __init__(self, delay_samples: int, feedback: float = 0.5):
        self._size = delay_samples
        self._buf = np.zeros((delay_samples, 2), dtype=np.float32)
        self._idx = 0
        self._fb = np.float32(feedback)
        self._rd_buf = np.empty((delay_samples, 2), dtype=np.float32)
        self._flush_count = 0

    def process(self, inp: np.ndarray) -> np.ndarray:
        n = len(inp)
        sz = self._size
        fb = self._fb
        idx = self._idx

        if sz >= n:
            # Single-block fast path
            delayed = _circ_read(self._buf, idx, n, sz, out=self._rd_buf)
            _circ_write(self._buf, idx, inp + delayed * fb, sz)
            self._idx = (idx + n) % sz
            self._maybe_flush()
            return delayed - inp * fb

        # Chunked path for small delays
        out = np.empty_like(inp)
        pos = 0
        while pos < n:
            cn = min(sz, n - pos)
            delayed = _circ_read(self._buf, idx, cn, sz, out=self._rd_buf)
            inp_c = inp[pos:pos + cn]
            _circ_write(self._buf, idx, inp_c + delayed * fb, sz)
            out[pos:pos + cn] = delayed - inp_c * fb
            idx = (idx + cn) % sz
            pos += cn
        self._idx = idx
        self._maybe_flush()
        return out

    def _maybe_flush(self):
        self._flush_count += 1
        if self._flush_count >= 100:
            self._flush_count = 0
            self._buf[np.abs(self._buf) < 1e-18] = 0.0

    def flush_denormals(self):
        self._buf[np.abs(self._buf) < 1e-18] = 0.0

    def reset(self):
        self._buf[:] = 0.0
        self._idx = 0


# Separately-tuned, prime-based comb delay lines per room type (at 44100 Hz).
# Prime lengths prevent harmonic relationships that cause metallic ringing.
_COMBS_HALL    = [1997, 2153, 2311, 2357, 1877, 2039, 1723, 1559]  # concert hall
_COMBS_ROOM    = [967,  1031, 1097, 1163, 1237, 1303, 1373, 1451]  # live room
_COMBS_CHAMBER = [499,  557,  613,  659,  709,  769,  823,  883]   # echo chamber
_COMBS_STUDIO  = [211,  251,  307,  349,  397,  433,  479,  523]   # studio live room

_APASSES_HALL    = [541, 449, 347, 233]
_APASSES_ROOM    = [389, 307, 211, 149]
_APASSES_CHAMBER = [211, 167, 127, 83]
_APASSES_STUDIO  = [113, 89,  67,  43]

# Per-type feedback range, damping range, and base pre-delay (ms)
_TYPE_FB_MIN   = {0: 0.40, 1: 0.35, 2: 0.30, 3: 0.20}
_TYPE_FB_RANGE = {0: 0.52, 1: 0.48, 2: 0.45, 3: 0.40}
_TYPE_DAMP_MIN = {0: 0.08, 1: 0.12, 2: 0.18, 3: 0.04}
_TYPE_DAMP_RNG = {0: 0.55, 1: 0.52, 2: 0.48, 3: 0.36}
_TYPE_PRE_MS   = {0: 35.0, 1: 15.0, 2:  8.0, 3:  3.0}


class HallReverbProcessor:
    """Schroeder-style reverb with Hall / Room / Chamber / Studio modes.

    Each mode uses separately-tuned, prime-based delay lines (no harmonic
    relationships → no metallic ringing).  Knob changes (decay, diffusion,
    mix, room_size) update instantly with NO glitch because the filters are
    never rebuilt mid-stream.  Only a room-type switch causes a rebuild,
    which is expected to produce a timbral change.

    room_size controls the pre-delay length (larger = more ms before tail)
    and gives a subtle feedback boost for a larger-space impression.
    """

    _TYPE_COMBS = {
        0: _COMBS_HALL,
        1: _COMBS_ROOM,
        2: _COMBS_CHAMBER,
        3: _COMBS_STUDIO,
    }
    _TYPE_APASSES = {
        0: _APASSES_HALL,
        1: _APASSES_ROOM,
        2: _APASSES_CHAMBER,
        3: _APASSES_STUDIO,
    }

    def __init__(self, sample_rate: int):
        self.sr = sample_rate
        self.state = HallReverbState()
        self._sr_ratio = sample_rate / 44100.0

        # Pre-delay buffer (max ~200 ms supports even hall mode at room_size=1)
        max_pd = int(0.2 * sample_rate) + 1
        self._pd_buf = np.zeros((max_pd, 2), dtype=np.float32)
        self._pd_size = max_pd
        self._pd_idx = 0

        # Build filter banks for initial room type
        self._combs: list[_CombFilter] = []
        self._allpasses: list[_AllpassFilter] = []
        self._rebuild(room_type=0)
        self._last_room_type = 0
        # Pre-allocated wet buffer
        from mlrvp.config import BUFFER_SIZE as _BS
        self._wet_size = _BS
        self._wet = np.zeros((_BS, 2), dtype=np.float32)
        # Pre-allocated predelay read buffer
        self._pd_rd_buf = np.empty((_BS, 2), dtype=np.float32)
        self._pd_rd_size = _BS

    def _rebuild(self, room_type: int):
        """Rebuild comb+allpass banks for a new room type only."""
        rt = max(0, min(room_type, 3))
        self._combs = [
            _CombFilter(max(4, int(dl * self._sr_ratio)))
            for dl in self._TYPE_COMBS[rt]
        ]
        self._allpasses = [
            _AllpassFilter(max(4, int(dl * self._sr_ratio)), 0.5)
            for dl in self._TYPE_APASSES[rt]
        ]

    def process(self, data: np.ndarray) -> np.ndarray:
        st = self.state
        if not st.enabled:
            return data

        rt = max(0, min(st.room_type, 3))

        # Rebuild ONLY when room type switches — never on knob tweaks
        if rt != self._last_room_type:
            self._rebuild(rt)
            self._last_room_type = rt

        n = len(data)

        # Pre-delay: scales with room_size to give depth sensation
        # Hall: up to 35*2.5 = 87ms; Room: up to 37ms; Chamber: up to 20ms; Studio: 7ms
        pd_ms = _TYPE_PRE_MS[rt] * (0.5 + st.room_size * 2.0)
        pd_samples = max(0, min(self._pd_size - 1, int(pd_ms * 0.001 * self.sr)))
        _circ_write(self._pd_buf, self._pd_idx, data, self._pd_size)
        rd = (self._pd_idx - pd_samples) % self._pd_size
        if n > self._pd_rd_size:
            self._pd_rd_buf = np.empty((n, 2), dtype=np.float32)
            self._pd_rd_size = n
        pre = _circ_read(self._pd_buf, rd, n, self._pd_size, out=self._pd_rd_buf)
        self._pd_idx = (self._pd_idx + n) % self._pd_size

        # Feedback: room_size adds a small boost (larger space = longer decay)
        fb_min = _TYPE_FB_MIN[rt]
        fb = fb_min + _TYPE_FB_RANGE[rt] * st.decay * (0.88 + st.room_size * 0.12)
        fb = min(fb, 0.97)

        # Damping: high diffusion = well-scattered energy, low damping = brighter tail
        damp = _TYPE_DAMP_MIN[rt] + _TYPE_DAMP_RNG[rt] * (1.0 - st.diffusion)

        for c in self._combs:
            c.set_feedback(fb)
            c.set_damp(damp)

        # Parallel combs summed (normalised to prevent gain stacking)
        if n > self._wet_size:
            self._wet_size = n
            self._wet = np.zeros((n, 2), dtype=np.float32)
        wet = self._wet
        wet[:n] = 0
        for c in self._combs:
            wet[:n] += c.process(pre)
        wet[:n] *= np.float32(1.0 / len(self._combs))

        # Series allpasses spread the energy in time for dense diffusion
        for ap in self._allpasses:
            wet[:n] = ap.process(wet[:n])

        mix = st.mix
        return data * np.float32(1.0 - mix) + wet[:n] * np.float32(mix)

    def reset(self):
        self._pd_buf[:] = 0
        self._pd_idx = 0
        for c in self._combs:
            c.reset()
        for ap in self._allpasses:
            ap.reset()
        self._wet[:] = 0


# ------------------------------------------------------------------ #
# Plate / Spring Reverb                                               #
# ------------------------------------------------------------------ #

_PLATE_COMBS = [1051, 1153, 1259, 1373, 1493, 1621]
_PLATE_ALLPASSES = [307, 359, 461, 181]


class PlateReverbProcessor:
    """Plate / spring reverb — bright, dense tail with damping control."""

    def __init__(self, sample_rate: int):
        self.sr = sample_rate
        self.state = PlateReverbState()
        self._sr_ratio = sample_rate / 44100.0

        # Pre-delay circular buffer (max ~200 ms)
        max_pd = int(0.2 * sample_rate) + 1
        self._pd_buf = np.zeros((max_pd, 2), dtype=np.float32)
        self._pd_size = max_pd
        self._pd_idx = 0

        # Comb + allpass banks
        self._combs = [_CombFilter(max(4, int(dl * self._sr_ratio)))
                       for dl in _PLATE_COMBS]
        self._allpasses = [_AllpassFilter(max(4, int(dl * self._sr_ratio)), 0.6)
                           for dl in _PLATE_ALLPASSES]
        # Pre-allocated wet buffer
        from mlrvp.config import BUFFER_SIZE as _BS
        self._wet_size = _BS
        self._wet = np.zeros((_BS, 2), dtype=np.float32)
        # Pre-allocated predelay read buffer
        self._pd_rd_buf = np.empty((_BS, 2), dtype=np.float32)
        self._pd_rd_size = _BS

    def process(self, data: np.ndarray) -> np.ndarray:
        st = self.state
        if not st.enabled:
            return data

        n = len(data)

        # Block pre-delay using circular buffer slicing
        pd_samples = max(0, min(self._pd_size - 1,
                                int(st.pre_delay * 0.001 * self.sr)))
        buf = self._pd_buf
        pd_sz = self._pd_size
        idx = self._pd_idx

        # Write current data into pre-delay buffer
        _circ_write(buf, idx, data, pd_sz)
        # Read delayed samples (offset back by pd_samples)
        rd_start = (idx - pd_samples) % pd_sz
        if n > self._pd_rd_size:
            self._pd_rd_buf = np.empty((n, 2), dtype=np.float32)
            self._pd_rd_size = n
        pre = _circ_read(buf, rd_start, n, pd_sz, out=self._pd_rd_buf)
        self._pd_idx = (idx + n) % pd_sz

        # Set comb parameters
        fb = 0.4 + st.decay * 0.55  # 0.4 – 0.95
        damp = st.damping * 0.7     # higher damping = darker
        for c in self._combs:
            c.set_feedback(fb)
            c.set_damp(damp)

        # Parallel combs
        if n > self._wet_size:
            self._wet_size = n
            self._wet = np.zeros((n, 2), dtype=np.float32)
        wet = self._wet
        wet[:n] = 0
        for c in self._combs:
            wet[:n] += c.process(pre)
        wet[:n] *= np.float32(1.0 / len(self._combs))

        # Series allpasses
        for ap in self._allpasses:
            wet[:n] = ap.process(wet[:n])

        mix = st.mix
        return data * np.float32(1.0 - mix) + wet[:n] * np.float32(mix)

    def reset(self):
        self._pd_buf[:] = 0
        self._pd_idx = 0
        for c in self._combs:
            c.reset()
        for ap in self._allpasses:
            ap.reset()
        self._wet[:] = 0


# ═══════════════════════════════════════════════════════════════════════════════
# Noise Gate — Drawmer DS201 model
# State machine: OPEN → HOLD → CLOSING → CLOSED
# Features: key filter sidechain, hysteresis, variable range
# ═══════════════════════════════════════════════════════════════════════════════

class NoiseGateProcessor:
    """Drawmer DS201-style frequency-conscious noise gate.

    State machine with four phases:
      OPEN    — signal above threshold, gate fully open
      HOLD    — signal dropped below threshold, holding open for hold_ms
      CLOSING — hold expired, fading toward closed (release envelope)
      CLOSED  — gate attenuated by range_db

    Key filter: optional LP or HP on the sidechain before envelope detection,
    enabling frequency-selective gating (e.g. gate on kick but not hi-hat).

    Hysteresis: close threshold is 6 dB below open threshold to prevent
    chatter near the threshold boundary.
    """

    _GATE_OPEN    = 0
    _GATE_HOLD    = 1
    _GATE_CLOSING = 2
    _GATE_CLOSED  = 3

    _HYSTERESIS_DB = 6.0  # close threshold is 6 dB below open threshold

    def __init__(self, sample_rate: int = SAMPLE_RATE, buffer_size: int = 512):
        self.sr = sample_rate
        self.state = NoiseGateState()
        self._gate_state = self._GATE_CLOSED
        self._gate_gain = 0.0          # current gate attenuation 0.0–1.0
        self._env = 0.0                # envelope follower level (linear)
        self._hold_counter = 0         # samples remaining in hold phase
        # Key filter biquad state (2 channels × 2 delay elements)
        self._kf_z1 = np.zeros(2, dtype=np.float64)
        self._kf_z2 = np.zeros(2, dtype=np.float64)
        self._kf_b = np.array([1.0, 0.0, 0.0], dtype=np.float64)
        self._kf_a = np.array([1.0, 0.0, 0.0], dtype=np.float64)
        self._kf_zi = np.zeros((2, 2), dtype=np.float64)
        self._last_kf_type = -1
        self._last_kf_freq = -1.0
        # Pre-allocated sidechain copy buffer
        self._sc_buf = np.empty((buffer_size, 2), dtype=np.float32)
        self._sc_buf_size = buffer_size
        # Pre-allocated output buffer
        self._out_buf = np.empty((buffer_size, 2), dtype=np.float32)
        self._out_buf_size = buffer_size
        # Pre-allocated sidechain peak buffers
        self._sc_abs0 = np.empty(buffer_size, dtype=np.float32)
        self._sc_abs1 = np.empty(buffer_size, dtype=np.float32)
        self._sc_peaks = np.empty(buffer_size, dtype=np.float32)
        self._sc_peak_size = buffer_size
        # Pre-allocated per-sample gain array (was np.empty per block)
        self._gain_arr = np.empty(buffer_size, dtype=np.float32)
        self._gain_arr_size = buffer_size

    def _update_key_filter(self, ftype: int, freq: float):
        """Recalculate key filter biquad coefficients if params changed."""
        if ftype == self._last_kf_type and abs(freq - self._last_kf_freq) < 0.5:
            return
        self._last_kf_type = ftype
        self._last_kf_freq = freq
        if ftype == 0:  # Off
            self._kf_b[:] = [1.0, 0.0, 0.0]
            self._kf_a[:] = [1.0, 0.0, 0.0]
            return
        freq = max(25.0, min(freq, self.sr * 0.45))
        w0 = 2.0 * math.pi * freq / self.sr
        cos_w0 = math.cos(w0)
        sin_w0 = math.sin(w0)
        alpha = sin_w0 / (2.0 * 0.707)
        if ftype == 1:  # Low-pass
            b0 = (1.0 - cos_w0) / 2.0
            b1 = 1.0 - cos_w0
            b2 = (1.0 - cos_w0) / 2.0
        else:  # High-pass
            b0 = (1.0 + cos_w0) / 2.0
            b1 = -(1.0 + cos_w0)
            b2 = (1.0 + cos_w0) / 2.0
        a0 = 1.0 + alpha
        self._kf_b[:] = [b0 / a0, b1 / a0, b2 / a0]
        self._kf_a[:] = [1.0, (-2.0 * cos_w0) / a0, (1.0 - alpha) / a0]

    def _apply_key_filter(self, sidechain: np.ndarray) -> np.ndarray:
        """Apply biquad key filter to sidechain signal via lfilter (C)."""
        if self._last_kf_type == 0:
            return sidechain
        self._kf_zi[0] = self._kf_z1
        self._kf_zi[1] = self._kf_z2
        filtered, zi_new = lfilter(
            self._kf_b, self._kf_a, sidechain, axis=0, zi=self._kf_zi)
        self._kf_z1[:] = zi_new[0]
        self._kf_z2[:] = zi_new[1]
        # Flush denormals from filter state
        self._kf_z1[np.abs(self._kf_z1) < 1e-12] = 0.0
        self._kf_z2[np.abs(self._kf_z2) < 1e-12] = 0.0
        sidechain[:] = filtered
        return sidechain

    def process(self, data: np.ndarray) -> np.ndarray:
        st = self.state
        if not st.enabled:
            return data

        n = len(data)
        if n == 0:
            return data

        # Key filter on sidechain copy (lfilter in C)
        self._update_key_filter(st.key_filter, st.key_freq)
        if n > self._sc_buf_size:
            self._sc_buf = np.empty((n, 2), dtype=np.float32)
            self._sc_buf_size = n
        sc = self._sc_buf[:n]
        np.copyto(sc, data)
        sc = self._apply_key_filter(sc)

        # Thresholds (linear)
        thresh_open = 10.0 ** (st.threshold / 20.0)
        thresh_close = 10.0 ** ((st.threshold - self._HYSTERESIS_DB) / 20.0)
        min_gain = 10.0 ** (st.range_db / 20.0)

        det_attack_ms = max(0.1, st.attack * 0.1)
        det_release_ms = 50.0
        att_coeff = math.exp(-1.0 / (self.sr * det_attack_ms * 0.001))
        rel_coeff = math.exp(-1.0 / (self.sr * det_release_ms * 0.001))
        gate_attack_coeff = math.exp(-1.0 / max(1.0, self.sr * st.attack * 0.001))
        gate_release_coeff = math.exp(-1.0 / max(1.0, self.sr * st.release * 0.001))
        hold_samples = int(self.sr * st.hold * 0.001)

        # Pre-compute sidechain peaks (vectorised, pre-allocated)
        if n > self._sc_peak_size:
            self._sc_abs0 = np.empty(n, dtype=np.float32)
            self._sc_abs1 = np.empty(n, dtype=np.float32)
            self._sc_peaks = np.empty(n, dtype=np.float32)
            self._sc_peak_size = n
        np.abs(sc[:n, 0], out=self._sc_abs0[:n])
        np.abs(sc[:n, 1], out=self._sc_abs1[:n])
        sc_peaks = self._sc_peaks[:n]
        np.maximum(self._sc_abs0[:n], self._sc_abs1[:n], out=sc_peaks)
        # Guard against NaN/Inf from corrupted upstream audio
        sc_peaks[~np.isfinite(sc_peaks)] = 0.0

        env = self._env
        gate_gain = self._gate_gain
        gate_state = self._gate_state
        hold_counter = self._hold_counter

        # Constants for state machine
        OPEN = self._GATE_OPEN
        HOLD = self._GATE_HOLD
        CLOSING = self._GATE_CLOSING
        CLOSED = self._GATE_CLOSED

        # Build gain array in tight scalar loop (no numpy indexing per sample)
        if n > self._gain_arr_size:
            self._gain_arr = np.empty(n, dtype=np.float32)
            self._gain_arr_size = n
        gain_arr = self._gain_arr[:n]

        for i in range(n):
            peak = float(sc_peaks[i])
            if peak > env:
                env = att_coeff * env + (1.0 - att_coeff) * peak
            else:
                env = rel_coeff * env + (1.0 - rel_coeff) * peak

            if gate_state == CLOSED:
                if env >= thresh_open:
                    gate_state = OPEN
            elif gate_state == OPEN:
                if env < thresh_close:
                    gate_state = HOLD
                    hold_counter = hold_samples
            elif gate_state == HOLD:
                if env >= thresh_open:
                    gate_state = OPEN
                else:
                    hold_counter -= 1
                    if hold_counter <= 0:
                        gate_state = CLOSING
            elif gate_state == CLOSING:
                if env >= thresh_open:
                    gate_state = OPEN

            target = 1.0 if (gate_state == OPEN or gate_state == HOLD) else min_gain
            if target > gate_gain:
                gate_gain = gate_attack_coeff * gate_gain + (1.0 - gate_attack_coeff) * target
            else:
                gate_gain = gate_release_coeff * gate_gain + (1.0 - gate_release_coeff) * target

            gain_arr[i] = gate_gain

        self._env = env
        self._gate_gain = gate_gain
        self._gate_state = gate_state
        self._hold_counter = hold_counter

        # Apply gain vectorised — use pre-allocated output buffer
        n = len(data)
        if n > self._out_buf_size:
            self._out_buf = np.empty((n, 2), dtype=np.float32)
            self._out_buf_size = n
        out = self._out_buf[:n]
        out[:] = data
        out[:, 0] *= gain_arr
        out[:, 1] *= gain_arr
        return out

    def reset(self):
        self._gate_state = self._GATE_CLOSED
        self._gate_gain = 0.0
        self._env = 0.0
        self._hold_counter = 0
        self._kf_z1[:] = 0.0
        self._kf_z2[:] = 0.0


# ═══════════════════════════════════════════════════════════════════════════════
# Tremolo — Demeter TRM-1 Optical Tremolo
# ═══════════════════════════════════════════════════════════════════════════════

class TremoloProcessor:
    """Demeter TRM-1-style optical tremolo with sine/tri/square LFO.

    Features:
      - Three LFO shapes: sine (smooth), triangle (linear), square (choppy)
      - Three stereo modes: mono (both channels same), stereo (90° offset),
        ping-pong (180° alternate)
      - Vectorized per-block processing using numpy
    """

    def __init__(self, sample_rate: int = SAMPLE_RATE, buffer_size: int = 512):
        self.sr = sample_rate
        self.state = TremoloState()
        self._phase = 0.0  # LFO phase accumulator [0, 1)
        # Pre-allocated output buffer
        self._out_buf = np.empty((buffer_size, 2), dtype=np.float32)
        self._out_buf_size = buffer_size

    def process(self, data: np.ndarray) -> np.ndarray:
        st = self.state
        if not st.enabled:
            return data

        n = len(data)
        if n == 0:
            return data

        rate = max(0.5, min(15.0, st.rate))
        depth = max(0.0, min(1.0, st.depth))
        mix = max(0.0, min(1.0, st.mix))

        if depth < 1e-6 or mix < 1e-6:
            return data

        # Build phase array for this block
        phase_inc = rate / self.sr
        phases_l = np.mod(self._phase + np.arange(n, dtype=np.float64) * phase_inc, 1.0)
        self._phase = float(np.mod(self._phase + n * phase_inc, 1.0))

        # Stereo offset
        if st.stereo_mode == 1:      # Stereo 90°
            phases_r = (phases_l + 0.25) % 1.0
        elif st.stereo_mode == 2:     # Ping-pong 180°
            phases_r = (phases_l + 0.5) % 1.0
        else:                         # Mono
            phases_r = phases_l

        # Generate amplitude modulation based on shape
        amp_l = self._shape_lfo(phases_l, st.shape, depth)
        amp_r = self._shape_lfo(phases_r, st.shape, depth)

        # Apply
        if n > self._out_buf_size:
            self._out_buf = np.empty((n, 2), dtype=np.float32)
            self._out_buf_size = n
        out = self._out_buf[:n]
        wet_l = data[:, 0] * amp_l
        wet_r = data[:, 1] * amp_r
        out[:, 0] = data[:, 0] * (1.0 - mix) + wet_l * mix
        out[:, 1] = data[:, 1] * (1.0 - mix) + wet_r * mix
        return out

    @staticmethod
    def _shape_lfo(phases: np.ndarray, shape: int, depth: float) -> np.ndarray:
        """Generate amplitude modulation array from LFO phase array."""
        if shape == 0:  # Sine
            return 1.0 - depth * (0.5 + 0.5 * np.sin(2.0 * np.pi * phases))
        elif shape == 1:  # Triangle
            return 1.0 - depth * (1.0 - np.abs(2.0 * (phases % 1.0) - 1.0))
        else:  # Square
            return np.where(phases < 0.5, 1.0, 1.0 - depth)

    def reset(self):
        self._phase = 0.0


class AutoWahProcessor:
    """Mutron III-style envelope follower + resonant filter (auto-wah)."""

    def __init__(self, sample_rate: int = SAMPLE_RATE, buffer_size: int = 512):
        self.state = AutoWahState()
        self._sr = sample_rate

        # Envelope follower state (per-channel)
        self._env = np.zeros(2, dtype=np.float64)

        # Biquad lfilter state: shape (2, 2) for order-2 × 2 channels
        self._zi = np.zeros((2, 2), dtype=np.float64)

        # Cached biquad b/a arrays for lfilter
        self._b_arr = np.zeros(3, dtype=np.float64)
        self._a_arr = np.array([1.0, 0.0, 0.0], dtype=np.float64)
        self._last_cutoff = -1.0
        self._last_ftype = -1

        # Frequency range
        self._min_freq = 80.0
        self._max_freq = 8000.0

        # Pre-allocated float64 buffers for per-block .astype() elimination
        self._rect_f64 = np.empty((buffer_size, 2), dtype=np.float64)
        self._rect_f64_size = buffer_size
        self._out_f64 = np.empty((buffer_size, 2), dtype=np.float64)
        self._out_f64_size = buffer_size
        # Pre-allocated per-sample cutoff buffer (was np.empty per block)
        self._cutoffs = np.empty(buffer_size, dtype=np.float64)
        self._cutoffs_size = buffer_size
        # Pre-allocated float32 output buffer (eliminates np.ascontiguousarray alloc)
        self._out_f32 = np.empty((buffer_size, 2), dtype=np.float32)
        self._out_f32_size = buffer_size

    def _calc_biquad(self, cutoff_hz: float, ftype: int, q: float):
        """Recalculate biquad coefficients for LP/BP/HP."""
        cutoff_hz = max(20.0, min(cutoff_hz, self._sr * 0.45))
        w0 = 2.0 * math.pi * cutoff_hz / self._sr
        sin_w0 = math.sin(w0)
        cos_w0 = math.cos(w0)
        alpha = sin_w0 / (2.0 * max(q, 0.1))

        if ftype == 0:  # LP
            b0 = (1.0 - cos_w0) / 2.0
            b1 = 1.0 - cos_w0
            b2 = (1.0 - cos_w0) / 2.0
        elif ftype == 1:  # BP
            b0 = alpha
            b1 = 0.0
            b2 = -alpha
        else:  # HP
            b0 = (1.0 + cos_w0) / 2.0
            b1 = -(1.0 + cos_w0)
            b2 = (1.0 + cos_w0) / 2.0

        a0 = 1.0 + alpha
        a1 = -2.0 * cos_w0 / a0
        a2 = (1.0 - alpha) / a0
        self._b_arr[:] = b0 / a0, b1 / a0, b2 / a0
        self._a_arr[:] = 1.0, a1, a2
        self._last_cutoff = cutoff_hz
        self._last_ftype = ftype

    def process(self, data: np.ndarray) -> np.ndarray:
        if not self.state.enabled:
            return data

        s = self.state
        n = data.shape[0]
        mix = s.mix
        sensitivity = max(0.001, s.sensitivity)

        # Envelope coefficients (fast attack, slower release)
        att_ms = 1.0
        rel_ms = 80.0
        att_coeff = math.exp(-1.0 / (att_ms * 0.001 * self._sr))
        rel_coeff = math.exp(-1.0 / (rel_ms * 0.001 * self._sr))

        # Resonance Q mapped from peak (0–1 → Q 0.5–15)
        q = 0.5 + s.peak * 14.5

        # 1. Compute envelope for entire block (per-sample, lightweight)
        env0 = float(self._env[0])
        env1 = float(self._env[1])
        if n > self._cutoffs_size:
            self._cutoffs = np.empty(n, dtype=np.float64)
            self._cutoffs_size = n
        cutoffs = self._cutoffs[:n]
        min_f = self._min_freq
        max_f = self._max_freq
        f_range = max_f - min_f
        mode_up = (s.mode == 0)

        # Pre-compute rectified input for both channels
        if n > self._rect_f64_size:
            self._rect_f64 = np.empty((n, 2), dtype=np.float64)
            self._rect_f64_size = n
        rect_all = self._rect_f64[:n]
        np.abs(data, out=rect_all, casting='unsafe')
        rect_all *= (sensitivity * 4.0)

        for i in range(n):
            rect0 = rect_all[i, 0]
            rect1 = rect_all[i, 1]
            if rect0 > env0:
                env0 = att_coeff * env0 + (1.0 - att_coeff) * rect0
            else:
                env0 = rel_coeff * env0 + (1.0 - rel_coeff) * rect0
            if rect1 > env1:
                env1 = att_coeff * env1 + (1.0 - att_coeff) * rect1
            else:
                env1 = rel_coeff * env1 + (1.0 - rel_coeff) * rect1
            avg_env = min((env0 + env1) * 0.5, 1.0)
            if mode_up:
                cutoffs[i] = min_f + avg_env * f_range
            else:
                cutoffs[i] = max_f - avg_env * f_range
        self._env[0] = env0
        self._env[1] = env1

        # 2. Process biquad in sub-blocks (lfilter in C per chunk)
        CHUNK = 32
        if n > self._out_f64_size:
            self._out_f64 = np.empty((n, 2), dtype=np.float64)
            self._out_f64_size = n
        out = self._out_f64[:n]
        np.copyto(out, data, casting='unsafe')
        pos = 0
        ftype = s.filter_type
        while pos < n:
            end = min(pos + CHUNK, n)
            cutoff = float(cutoffs[(pos + end) // 2])
            if abs(cutoff - self._last_cutoff) > 1.0 or self._last_ftype != ftype:
                self._calc_biquad(cutoff, ftype, q)
            out[pos:end], self._zi = lfilter(
                self._b_arr, self._a_arr, out[pos:end], axis=0, zi=self._zi)
            pos = end
        self._zi[np.abs(self._zi) < 1e-12] = 0.0

        if n > self._out_f32_size:
            self._out_f32 = np.empty((n, 2), dtype=np.float32)
            self._out_f32_size = n
        out_f32 = self._out_f32[:n]
        np.copyto(out_f32, out, casting='unsafe')
        if mix < 0.999:
            return data * (1.0 - mix) + out_f32 * mix
        return out_f32

    def reset(self):
        self._env[:] = 0.0
        self._zi[:] = 0.0
        self._last_cutoff = -1.0
        self._last_ftype = -1


class FETLimiterProcessor:
    """Urei 1176LN-style FET peak limiter / compressor.

    Fixed threshold with input-driven compression.  Ultra-fast attack,
    program-dependent release, 4 ratios + All-Buttons-In mode.
    """

    # 7 attack positions — 0 = fastest (20 µs), 6 = slowest (800 µs)
    _ATTACK_S = (0.00002, 0.00005, 0.0001, 0.0003, 0.001, 0.003, 0.08)
    # 5 release positions — 0 = fastest (50 ms), 4 = slowest (1.1 s)
    _RELEASE_S = (0.05, 0.1, 0.3, 0.7, 1.1)
    # Ratio table — index 4 = ABI (All Buttons In)
    _RATIOS = (4.0, 8.0, 12.0, 20.0, -1.0)  # -1 sentinel for ABI
    # Fixed threshold in dBFS — the 1176 has a fixed threshold
    _THRESHOLD_DB = -20.0

    def __init__(self, sample_rate: int = SAMPLE_RATE):
        self.state = FETLimiterState()
        self._sr = sample_rate

        # Envelope state per channel
        self._env = np.zeros(2, dtype=np.float64)
        # Public gain reduction readout (dB, negative)
        self.gr_db: float = 0.0
        # Pre-allocated driven signal buffer
        from mlrvp.config import BUFFER_SIZE as _BS
        self._driven_buf = np.empty((_BS, 2), dtype=np.float32)
        self._driven_size = _BS
        # Pre-allocated envelope/abs buffers (was np.empty/np.abs per block)
        self._env_arr = np.empty(_BS, dtype=np.float64)
        self._abs_l = np.empty(_BS, dtype=np.float64)
        self._abs_r = np.empty(_BS, dtype=np.float64)
        self._fet_buf_size = _BS

    def process(self, data: np.ndarray) -> np.ndarray:
        if not self.state.enabled:
            self.gr_db = 0.0
            return data

        s = self.state
        n = data.shape[0]

        # Map controls
        input_db = s.input_gain * 40.0
        input_lin = 10.0 ** (input_db / 20.0)
        output_db = s.output_gain * 40.0 - 20.0
        output_lin = np.float32(10.0 ** (output_db / 20.0))

        # Pre-compute input levels (vectorised) into pre-allocated buffer
        if n > self._driven_size:
            self._driven_buf = np.empty((n, 2), dtype=np.float32)
            self._driven_size = n
        driven = self._driven_buf[:n]
        np.multiply(data, np.float32(input_lin), out=driven)

        # Silence gate — check the GAINED signal so upstream residue
        # that the input-gain knob amplifies is caught reliably.
        # −80 dBFS post-gain is well below audibility.
        peak = np.max(np.abs(driven))
        if peak < 1e-4:  # ≈ −80 dBFS post-gain
            self._env[:] = 0.0
            self.gr_db = 0.0
            driven[:] = 0.0
            return driven

        # Timing coefficients
        att_s = self._ATTACK_S[min(s.attack_idx, 6)]
        rel_s = self._RELEASE_S[min(s.release_idx, 4)]
        att_coeff = math.exp(-1.0 / max(att_s * self._sr, 1.0))
        rel_coeff = math.exp(-1.0 / max(rel_s * self._sr, 1.0))

        ratio_idx = min(s.ratio_idx, 4)
        is_abi = ratio_idx == 4
        ratio = 20.0 if is_abi else self._RATIOS[ratio_idx]

        threshold_db = self._THRESHOLD_DB
        knee_db = 2.0 if is_abi else 6.0
        half_knee = knee_db / 2.0
        slope = 1.0 - 1.0 / ratio
        abi_mult = 1.15 if is_abi else 1.0
        if n > self._fet_buf_size:
            self._env_arr = np.empty(n, dtype=np.float64)
            self._abs_l = np.empty(n, dtype=np.float64)
            self._abs_r = np.empty(n, dtype=np.float64)
            self._fet_buf_size = n
        abs_l = self._abs_l[:n]
        abs_r = self._abs_r[:n]
        np.abs(driven[:, 0], out=abs_l, casting='unsafe')
        np.abs(driven[:, 1], out=abs_r, casting='unsafe')

        # Envelope follower — sequential (IIR), but tight scalar loop
        # only computing envelopes, no log/gain computation per sample
        env_arr = self._env_arr[:n]
        env0 = float(self._env[0])
        env1 = float(self._env[1])

        for i in range(n):
            pl = float(abs_l[i])
            pr = float(abs_r[i])
            if pl > env0:
                env0 = att_coeff * env0 + (1.0 - att_coeff) * pl
            else:
                env0 = rel_coeff * env0 + (1.0 - rel_coeff) * pl
            if pr > env1:
                env1 = att_coeff * env1 + (1.0 - att_coeff) * pr
            else:
                env1 = rel_coeff * env1 + (1.0 - rel_coeff) * pr
            env_arr[i] = max(env0, env1)

        self._env[0] = env0
        self._env[1] = env1
        self._env[np.abs(self._env) < 1e-18] = 0.0

        # Vectorised gain computation (log/pow in numpy)
        env_arr = np.maximum(env_arr, 1e-30)
        level_db = 20.0 * np.log10(env_arr)
        over_db = level_db - threshold_db

        gr_db = np.where(
            over_db <= -half_knee,
            0.0,
            np.where(
                over_db >= half_knee,
                over_db * slope,
                ((over_db + half_knee) ** 2) / (2.0 * knee_db) * slope
            )
        ) * abi_mult

        gr_lin = np.float32(10.0 ** (-gr_db / 20.0))

        # Apply gain + output (vectorised)
        out = driven * gr_lin[:, np.newaxis] * output_lin

        # ABI saturation (vectorised)
        if is_abi:
            out = np.tanh(out * 1.2) / np.float32(1.2)

        self.gr_db = -float(np.max(gr_db))
        return out

    def reset(self):
        self._env[:] = 0.0
        self.gr_db = 0.0


class RotaryProcessor:
    """Neo Ventilator II-style rotary speaker emulation.

    Crossover splits into horn (high) and rotor (low) paths.
    Each path gets independent AM + Doppler pitch modulation with
    speed-ramping transitions between Stop / Slow / Fast.
    """

    _CROSSOVER_HZ = 800.0
    _HORN_SLOW = 0.7     # Hz — Neo Ventilator II spec
    _HORN_FAST = 6.7
    _ROTOR_SLOW = 0.7
    _ROTOR_FAST = 5.9
    _DOPPLER_DEPTH = 0.003  # 0.3% pitch modulation

    def __init__(self, sample_rate: int = SAMPLE_RATE):
        self.state = RotaryState()
        self._sr = sample_rate

        # LFO phases
        self._horn_phase = 0.0
        self._rotor_phase = 0.0

        # Current Hz (ramped toward target)
        self._horn_hz = 0.0
        self._rotor_hz = 0.0

        # Crossover biquad coefficients (for lfilter)
        self._lp_b, self._lp_a, self._hp_b, self._hp_a = self._calc_crossover()
        # lfilter zi: shape (2, 2) — [filter_order, channels]
        self._xo_lp_zi = np.zeros((2, 2), dtype=np.float64)
        self._xo_hp_zi = np.zeros((2, 2), dtype=np.float64)

        # Doppler delay line — 1024 to support write-all-then-read
        self._dmax = 1024
        self._horn_buf = np.zeros((self._dmax, 2), dtype=np.float64)
        self._rotor_buf = np.zeros((self._dmax, 2), dtype=np.float64)
        self._horn_wp = 0
        self._rotor_wp = 0
        # Pre-allocated crossover input buffer
        from mlrvp.config import BUFFER_SIZE as _BS
        self._f64_buf = np.zeros((_BS, 2), dtype=np.float64)
        # Pre-allocated output buffer (eliminates np.empty_like per block)
        self._out_f64_buf = np.empty((_BS, 2), dtype=np.float64)

    def _calc_crossover(self):
        """Return (lp_b, lp_a, hp_b, hp_a) for crossover biquad filters."""
        w0 = 2.0 * math.pi * self._CROSSOVER_HZ / self._sr
        cos_w0 = math.cos(w0)
        sin_w0 = math.sin(w0)
        alpha = sin_w0 / (2.0 * 0.707)
        a0 = 1.0 + alpha
        a1 = -2.0 * cos_w0
        a2 = 1.0 - alpha
        # LP
        b0 = (1.0 - cos_w0) / 2.0
        b1 = 1.0 - cos_w0
        b2 = b0
        lp_b = np.array([b0/a0, b1/a0, b2/a0], dtype=np.float64)
        lp_a = np.array([1.0, a1/a0, a2/a0], dtype=np.float64)
        # HP
        b0h = (1.0 + cos_w0) / 2.0
        b1h = -(1.0 + cos_w0)
        b2h = b0h
        hp_b = np.array([b0h/a0, b1h/a0, b2h/a0], dtype=np.float64)
        hp_a = np.array([1.0, a1/a0, a2/a0], dtype=np.float64)
        return lp_b, lp_a, hp_b, hp_a

    def process(self, data: np.ndarray) -> np.ndarray:
        if not self.state.enabled:
            return data

        s = self.state
        n = data.shape[0]
        two_pi = 2.0 * math.pi

        # Target speeds
        if s.speed == 0:
            target_horn = 0.0
            target_rotor = 0.0
        elif s.speed == 1:
            target_horn = self._HORN_SLOW
            target_rotor = self._ROTOR_SLOW
        else:
            target_horn = self._HORN_FAST
            target_rotor = self._ROTOR_FAST

        # Ramp coefficient
        ramp_s = 0.2 + s.acceleration * 2.8
        rc = math.exp(-1.0 / max(ramp_s * self._sr, 1.0))

        horn_lvl = s.horn_level
        rotor_lvl = s.rotor_level
        mix_wet = s.mix

        # --- Speed ramp: closed-form exponential decay ---
        powers = rc ** np.arange(1, n + 1, dtype=np.float64)
        horn_hz = target_horn + (self._horn_hz - target_horn) * powers
        rotor_hz = target_rotor + (self._rotor_hz - target_rotor) * powers
        self._horn_hz = float(horn_hz[-1])
        self._rotor_hz = float(rotor_hz[-1])

        # --- LFO phases: cumulative sum of per-sample increments ---
        horn_inc = two_pi * horn_hz / self._sr
        rotor_inc = two_pi * rotor_hz / self._sr
        horn_phases = self._horn_phase + np.cumsum(horn_inc)
        rotor_phases = self._rotor_phase + np.cumsum(rotor_inc)
        self._horn_phase = float(horn_phases[-1]) % two_pi
        self._rotor_phase = float(rotor_phases[-1]) % two_pi

        # Pre-compute sin for AM + Doppler (both channels)
        horn_sin = np.sin(horn_phases)
        rotor_sin = np.sin(rotor_phases)

        # --- Crossover: vectorised via lfilter ---
        if n > self._f64_buf.shape[0]:
            self._f64_buf = np.zeros((n, 2), dtype=np.float64)
        data_f64 = self._f64_buf[:n]
        np.copyto(data_f64, data, casting='unsafe')
        lp_out, self._xo_lp_zi = lfilter(
            self._lp_b, self._lp_a, data_f64, axis=0, zi=self._xo_lp_zi)
        hp_out, self._xo_hp_zi = lfilter(
            self._hp_b, self._hp_a, data_f64, axis=0, zi=self._xo_hp_zi)
        # Flush denormals from crossover filter state
        self._xo_lp_zi[np.abs(self._xo_lp_zi) < 1e-18] = 0.0
        self._xo_hp_zi[np.abs(self._xo_hp_zi) < 1e-18] = 0.0

        # --- Write crossover outputs into delay buffers (vectorised) ---
        wp = self._horn_wp
        _circ_write_f64(self._horn_buf, wp, hp_out, self._dmax)
        _circ_write_f64(self._rotor_buf, wp, lp_out, self._dmax)

        # Positions in delay buffer for each sample
        positions = (wp + np.arange(n, dtype=np.int64)) % self._dmax
        dmax = self._dmax
        dd100 = self._DOPPLER_DEPTH * 100.0

        # --- Horn: Doppler read + AM (both channels vectorised) ---
        if n > self._out_f64_buf.shape[0]:
            self._out_f64_buf = np.empty((n, 2), dtype=np.float64)
        out = self._out_f64_buf[:n]
        positions_f64 = positions.astype(np.float64)
        for ch in range(2):
            ph_off = 0.0 if ch == 0 else math.pi
            sin_ch = np.sin(horn_phases + ph_off) if ch == 1 else horn_sin

            # AM
            horn_am = 1.0 + 0.5 * sin_ch

            # Doppler fractional delay
            horn_delay = 128.0 + 64.0 * sin_ch * dd100
            rd = positions_f64 - horn_delay
            idx0 = rd.astype(np.int64) % dmax
            idx1 = (idx0 + 1) % dmax
            frac = rd - np.floor(rd)
            horn_sig = (self._horn_buf[idx0, ch] * (1.0 - frac) +
                        self._horn_buf[idx1, ch] * frac)

            # Rotor: same pattern
            rsin_ch = np.sin(rotor_phases + ph_off) if ch == 1 else rotor_sin
            rotor_am = 1.0 + 0.3 * rsin_ch
            rotor_delay = 128.0 + 64.0 * rsin_ch * dd100
            rd_r = positions_f64 - rotor_delay
            idx0r = rd_r.astype(np.int64) % dmax
            idx1r = (idx0r + 1) % dmax
            frac_r = rd_r - np.floor(rd_r)
            rotor_sig = (self._rotor_buf[idx0r, ch] * (1.0 - frac_r) +
                         self._rotor_buf[idx1r, ch] * frac_r)

            wet = horn_sig * horn_am * horn_lvl + rotor_sig * rotor_am * rotor_lvl
            out[:, ch] = data_f64[:, ch] * (1.0 - mix_wet) + wet * mix_wet

        # Advance write pointer
        self._horn_wp = (wp + n) % dmax
        self._rotor_wp = self._horn_wp

        return out.astype(np.float32)

    def reset(self):
        self._horn_phase = 0.0
        self._rotor_phase = 0.0
        self._horn_hz = 0.0
        self._rotor_hz = 0.0
        self._xo_lp_zi[:] = 0.0
        self._xo_hp_zi[:] = 0.0
        self._horn_buf[:] = 0.0
        self._rotor_buf[:] = 0.0
        self._horn_wp = 0
        self._rotor_wp = 0


# ── Aural Exciter (Aphex Type C) ────────────────────────────────────────────

class ExciterProcessor:
    """Aphex Aural Exciter Type C — harmonic enhancement.

    High path: 2nd-order HPF at `tune` → tanh soft-clip → blend at `mix`.
    Lo path:   1st-order HPF at `lo_tune` → allpass phase shift → blend at `lo_mix`.
    Output:    makeup gain in dB.
    """

    def __init__(self, sample_rate: int = 44100):
        self.sample_rate = sample_rate
        self.state = ExciterState()
        # Hi-path HPF state (2nd-order biquad, per channel)
        self._hi_b = np.zeros(3)
        self._hi_a = np.zeros(3)
        self._hi_zi = None
        self._last_tune = -1.0
        # Lo-path HPF state (1st-order, per channel)
        self._lo_b = np.zeros(2)
        self._lo_a = np.zeros(2)
        self._lo_zi = None
        # Lo-path allpass state (1st-order, per channel)
        self._ap_b = np.zeros(2)
        self._ap_a = np.zeros(2)
        self._ap_zi = None
        self._last_lo_tune = -1.0
        # Pre-allocated output buffer (eliminates work.copy() per block)
        from mlrvp.config import BUFFER_SIZE as _BS
        self._out_buf = np.empty((_BS, 2), dtype=np.float32)
        self._out_buf_size = _BS
        self._recalc_hi(self.state.tune)
        self._recalc_lo(self.state.lo_tune)

    def _recalc_hi(self, freq: float):
        """Recalculate 2nd-order HPF coefficients for the harmonics path."""
        sr = self.sample_rate
        freq = max(20.0, min(sr * 0.45, freq))
        w0 = 2.0 * np.pi * freq / sr
        alpha = np.sin(w0) / (2.0 * 0.707)
        cos_w0 = np.cos(w0)
        b0 = (1.0 + cos_w0) / 2.0
        b1 = -(1.0 + cos_w0)
        b2 = (1.0 + cos_w0) / 2.0
        a0 = 1.0 + alpha
        a1 = -2.0 * cos_w0
        a2 = 1.0 - alpha
        self._hi_b[:] = [b0 / a0, b1 / a0, b2 / a0]
        self._hi_a[:] = [1.0, a1 / a0, a2 / a0]
        self._last_tune = freq

    def _recalc_lo(self, freq: float):
        """Recalculate lo-path HPF and allpass coefficients."""
        sr = self.sample_rate
        freq = max(20.0, min(sr * 0.45, freq))
        # 1st-order HPF: y = x - x1 + R*y1, R = 1 - 2*pi*f/sr
        R = 1.0 - (2.0 * np.pi * freq / sr)
        self._lo_b[:] = [1.0, -1.0]
        self._lo_a[:] = [1.0, -R]
        # 1st-order allpass for ~90° phase shift at freq
        # H(z) = (a + z^-1) / (1 + a*z^-1), a = (tan(pi*f/sr) - 1) / (tan(pi*f/sr) + 1)
        t = np.tan(np.pi * freq / sr)
        a_coeff = (t - 1.0) / (t + 1.0)
        self._ap_b[:] = [a_coeff, 1.0]
        self._ap_a[:] = [1.0, a_coeff]
        self._last_lo_tune = freq

    def process(self, data: np.ndarray) -> np.ndarray:
        if not self.state.enabled:
            return data
        st = self.state

        # Recalc filters if parameters changed
        if abs(st.tune - self._last_tune) > 0.5:
            self._recalc_hi(st.tune)
        if abs(st.lo_tune - self._last_lo_tune) > 0.5:
            self._recalc_lo(st.lo_tune)

        # data layout: (samples,) mono or (samples, channels) stereo
        is_mono = data.ndim == 1
        if is_mono:
            work = data[:, np.newaxis]          # (samples, 1)
        else:
            work = data                          # (samples, channels)
        n_ch = work.shape[1]

        # Init filter states if needed (one zi vector per channel)
        if self._hi_zi is None or self._hi_zi.shape[0] != n_ch:
            self._hi_zi = np.zeros((n_ch, 2))
        if self._lo_zi is None or self._lo_zi.shape[0] != n_ch:
            self._lo_zi = np.zeros((n_ch, 1))
        if self._ap_zi is None or self._ap_zi.shape[0] != n_ch:
            self._ap_zi = np.zeros((n_ch, 1))

        n_samp = work.shape[0]
        if n_samp > self._out_buf_size or self._out_buf.shape[1] < n_ch:
            self._out_buf = np.empty((n_samp, n_ch), dtype=np.float32)
            self._out_buf_size = n_samp
        out = self._out_buf[:n_samp, :n_ch]
        out[:] = work
        hi_mix = float(st.mix)
        lo_mix = float(st.lo_mix)

        # ── Hi harmonics path ──
        if hi_mix > 1e-6:
            for ch in range(n_ch):
                filtered, self._hi_zi[ch] = lfilter(
                    self._hi_b, self._hi_a, work[:, ch], zi=self._hi_zi[ch])
                # Soft-clip to generate harmonics
                harmonics = np.tanh(filtered * 2.0) * 0.5
                out[:, ch] += harmonics * hi_mix

        # ── Lo enhancement path ──
        if lo_mix > 1e-6:
            for ch in range(n_ch):
                # HPF to isolate low-mid content
                lo_hp, self._lo_zi[ch] = lfilter(
                    self._lo_b, self._lo_a, work[:, ch], zi=self._lo_zi[ch])
                # Allpass for phase shift
                lo_ap, self._ap_zi[ch] = lfilter(
                    self._ap_b, self._ap_a, lo_hp, zi=self._ap_zi[ch])
                out[:, ch] += lo_ap * lo_mix

        # Flush denormals and NaN/Inf from filter state
        _DN = np.float64(1e-18)
        if self._hi_zi is not None:
            mask = np.abs(self._hi_zi) < _DN
            mask |= ~np.isfinite(self._hi_zi)
            self._hi_zi[mask] = 0.0
        if self._lo_zi is not None:
            mask = np.abs(self._lo_zi) < _DN
            mask |= ~np.isfinite(self._lo_zi)
            self._lo_zi[mask] = 0.0
        if self._ap_zi is not None:
            mask = np.abs(self._ap_zi) < _DN
            mask |= ~np.isfinite(self._ap_zi)
            self._ap_zi[mask] = 0.0

        # ── Output makeup gain ──
        gain_db = max(-6.0, min(6.0, float(st.output)))
        if abs(gain_db) > 0.01:
            out *= 10.0 ** (gain_db / 20.0)

        if is_mono:
            return out[:, 0]
        return out

    def reset(self):
        self._hi_zi = None
        self._lo_zi = None
        self._ap_zi = None


class TransientShaperProcessor:
    """SPL Transient Designer 4 — attack/sustain envelope shaper.

    Two parallel envelope followers (fast + slow).  Transient = fast - slow.
    Attack gain = 1 + attack_shape * transient.  Sustain uses the inverse.
    """

    def __init__(self, sample_rate: int = 44100, buffer_size: int = 512):
        self.sample_rate = sample_rate
        self.state = TransientShaperState()
        # Fast envelope: 0.5ms attack, 4ms release
        self._fast_att = np.exp(-1.0 / (sample_rate * 0.0005))
        self._fast_rel = np.exp(-1.0 / (sample_rate * 0.004))
        # Slow envelope: 50ms attack, 300ms release
        self._slow_att = np.exp(-1.0 / (sample_rate * 0.050))
        self._slow_rel = np.exp(-1.0 / (sample_rate * 0.300))
        # Per-channel envelope state
        self._fast_env = np.zeros(2, dtype=np.float64)
        self._slow_env = np.zeros(2, dtype=np.float64)
        # Pre-allocated work buffers
        self._work_f64 = np.zeros((buffer_size, 2), dtype=np.float64)
        self._result_f32 = np.zeros((buffer_size, 2), dtype=np.float32)
        # Pre-allocated envelope follower buffers
        self._fast_env_buf = np.zeros((buffer_size, 2), dtype=np.float64)
        self._slow_env_buf = np.zeros((buffer_size, 2), dtype=np.float64)
        self._fe_out = np.zeros(buffer_size, dtype=np.float64)
        self._se_out = np.zeros(buffer_size, dtype=np.float64)
        # Pre-allocated gain computation buffers
        self._gain_buf = np.empty((buffer_size, 2), dtype=np.float64)
        self._trans_buf = np.empty((buffer_size, 2), dtype=np.float64)
        self._gain_buf_size = buffer_size

    def process(self, data: np.ndarray) -> np.ndarray:
        if not self.state.enabled:
            return data
        st = self.state
        is_mono = data.ndim == 1
        n = len(data)
        if is_mono:
            self._work_f64[:n, 0] = data
            self._work_f64[:n, 1] = 0.0
            work = self._work_f64[:n]
        else:
            np.copyto(self._work_f64[:n], data, casting='unsafe')
            work = self._work_f64[:n]

        n_samples, n_ch = work.shape
        fast_att = self._fast_att
        fast_rel = self._fast_rel
        slow_att = self._slow_att
        slow_rel = self._slow_rel
        attack_shape = float(st.attack_shape)
        sustain_shape = float(st.sustain_shape)

        # Compute per-sample rectified input
        rect = np.abs(work)

        # Envelope follower — Numba JIT or Python fallback
        fast_env = self._fast_env_buf[:n_samples]
        slow_env = self._slow_env_buf[:n_samples]

        if _HAS_NUMBA:
            _transient_env_jit(
                rect, n_samples, n_ch,
                fast_att, fast_rel, slow_att, slow_rel,
                self._fast_env, self._slow_env,
                fast_env, slow_env)
        else:
            for ch in range(n_ch):
                fe = self._fast_env[ch]
                se = self._slow_env[ch]
                r = rect[:, ch]
                fe_out = self._fe_out[:n_samples]
                se_out = self._se_out[:n_samples]
                for i in range(n_samples):
                    s = r[i]
                    if s > fe:
                        fe = fast_att * fe + (1.0 - fast_att) * s
                    else:
                        fe = fast_rel * fe + (1.0 - fast_rel) * s
                    if s > se:
                        se = slow_att * se + (1.0 - slow_att) * s
                    else:
                        se = slow_rel * se + (1.0 - slow_rel) * s
                    fe_out[i] = fe
                    se_out[i] = se
                self._fast_env[ch] = fe
                self._slow_env[ch] = se
                fast_env[:, ch] = fe_out
                slow_env[:, ch] = se_out

        # Stereo link: average L/R envelopes
        if st.stereo_link and n_ch == 2:
            fast_avg = (fast_env[:, 0] + fast_env[:, 1]) * 0.5
            slow_avg = (slow_env[:, 0] + slow_env[:, 1]) * 0.5
            fast_env[:, 0] = fast_env[:, 1] = fast_avg
            slow_env[:, 0] = slow_env[:, 1] = slow_avg

        # Normalise transient/sustain signals relative to the slow envelope
        # so they represent *proportional* change — not tiny absolute diffs.
        # Add small floor to avoid division by zero.
        env_floor = 1e-6
        if n_samples > self._gain_buf_size:
            self._gain_buf = np.empty((n_samples, 2), dtype=np.float64)
            self._trans_buf = np.empty((n_samples, 2), dtype=np.float64)
            self._gain_buf_size = n_samples
        gain = self._gain_buf[:n_samples]
        transient = self._trans_buf[:n_samples]
        np.maximum(slow_env, env_floor, out=gain)  # use gain as temp for norm

        # Transient = (fast - slow) / slow — large during sharp attacks
        np.subtract(fast_env, slow_env, out=transient)
        np.divide(transient, gain, out=transient)
        np.clip(transient, 0.0, 1.0, out=transient)

        # Gain shaping: ±1 maps to ±12 dB of boost/cut
        _RANGE_DB = 12.0
        _LOG10_20 = 0.11512925464970229  # log(10)/20
        # gain = exp(((attack_shape * transient) + (sustain_shape * (1-transient))) * RANGE * LOG10_20)
        np.multiply(transient, attack_shape * _RANGE_DB * _LOG10_20, out=gain)
        # sustain = (1 - transient) * sustain_shape * RANGE * LOG10_20
        # Add in-place: gain += sustain_shape * (1-transient) * RANGE * LOG10_20
        sus_coeff = sustain_shape * _RANGE_DB * _LOG10_20
        if abs(sus_coeff) > 1e-9:
            # transient is still valid, compute (1-transient) contribution
            gain += sus_coeff
            gain -= sus_coeff * transient
        np.exp(gain, out=gain)
        np.clip(gain, 0.05, 8.0, out=gain)

        out = work * gain

        # Output makeup gain
        gain_db = max(-6.0, min(6.0, float(st.output)))
        if abs(gain_db) > 0.01:
            out *= 10.0 ** (gain_db / 20.0)

        np.copyto(self._result_f32[:n], out, casting='unsafe')
        result = self._result_f32[:n]
        if is_mono:
            return result[:, 0]
        return result

    def reset(self):
        self._fast_env[:] = 0.0
        self._slow_env[:] = 0.0


# ---------------------------------------------------------------------------
#  Harmonizer  (Eventide H3000 style dual pitch-shifter)
# ---------------------------------------------------------------------------

class HarmonizerProcessor:
    """Wrong Lane — granular overlap-add dual-voice pitch shifter.

    Two voices, each with two overlapping read heads (50 % overlap,
    Hann window).  Read pointers advance at ``pitch_ratio`` speed;
    at each grain boundary the finishing head re-syncs to the write
    cursor so the stream stays click-free.
    """

    GRAIN = 2048               # grain length in samples (~46 ms @ 44 100)
    HOP   = GRAIN // 2         # 50 % overlap

    def __init__(self, sr: int = SAMPLE_RATE, max_channels: int = 2):
        self.sr = sr
        self._max_ch = max_channels
        self.state = HarmonizerState()

        # Pre-computed Hann window (endpoints are exactly 0)
        self._win = np.hanning(self.GRAIN).astype(np.float32)

        # Ring buffer: max delay (250 ms) + 4 grains headroom
        self._blen = int(0.25 * sr) + 4 * self.GRAIN
        self._buf = np.zeros((self._blen, max_channels), dtype=np.float32)
        self._wr = 0                       # write position (wrapping)

        # 2 voices × 2 read heads  —  [voice][head]
        # _rd = fractional read position (may be negative; wrapped in interp)
        # _ph = grain phase counter (0 … GRAIN-1)
        self._rd = [[0.0, 0.0], [0.0, 0.0]]
        self._ph = [[0, self.HOP], [0, self.HOP]]
        self._inited = False

        # Feedback buffer (large enough for any reasonable block size)
        self._fb = np.zeros((4096, max_channels), dtype=np.float32)
        # Pre-allocated interpolation buffers (grow-only)
        self._interp_size = 4096
        self._interp_arange = np.arange(4096, dtype=np.float64)
        self._interp_positions = np.empty(4096, dtype=np.float64)
        self._interp_fl = np.empty(4096, dtype=np.int64)
        self._interp_frac = np.empty((4096, 1), dtype=np.float32)
        self._interp_out = np.empty((4096, max_channels), dtype=np.float32)
        # Pre-allocated process()-level buffers (grow-only)
        # Replace per-block: data.copy(), np.zeros((n,ch)) per voice,
        # np.arange(wr, wr+n) % blen, audio*win temporaries.
        from mlrvp.config import BUFFER_SIZE as _BS
        self._proc_size = _BS
        self._work_buf = np.empty((_BS, max_channels), dtype=np.float32)
        self._voice_out_a = np.empty((_BS, max_channels), dtype=np.float32)
        self._voice_out_b = np.empty((_BS, max_channels), dtype=np.float32)
        self._grain_tmp = np.empty((_BS, max_channels), dtype=np.float32)
        self._idx_base = np.arange(_BS, dtype=np.int64)
        self._idx_buf = np.empty(_BS, dtype=np.int64)
        self._result_buf = np.empty((_BS, max_channels), dtype=np.float32)

    def _ensure_proc_bufs(self, n: int):
        if n > self._proc_size:
            self._proc_size = n
            mc = self._max_ch
            self._work_buf = np.empty((n, mc), dtype=np.float32)
            self._voice_out_a = np.empty((n, mc), dtype=np.float32)
            self._voice_out_b = np.empty((n, mc), dtype=np.float32)
            self._grain_tmp = np.empty((n, mc), dtype=np.float32)
            self._idx_base = np.arange(n, dtype=np.int64)
            self._idx_buf = np.empty(n, dtype=np.int64)
            self._result_buf = np.empty((n, mc), dtype=np.float32)

    def _ensure_interp_bufs(self, n):
        if n > self._interp_size:
            self._interp_size = n
            self._interp_arange = np.arange(n, dtype=np.float64)
            self._interp_positions = np.empty(n, dtype=np.float64)
            self._interp_fl = np.empty(n, dtype=np.int64)
            self._interp_frac = np.empty((n, 1), dtype=np.float32)
            self._interp_out = np.empty((n, self._max_ch), dtype=np.float32)

    # ---- helpers -----------------------------------------------------------

    @staticmethod
    def _pitch_ratio(semitones: int, cents: int) -> float:
        return 2.0 ** ((semitones + cents / 100.0) / 12.0)

    def _interp_read(self, start: float, ratio: float,
                     n: int, channels: int):
        """Vectorised linear-interpolation read from ring buffer.

        Returns ``(audio[n, channels], new_read_pos)``.
        """
        self._ensure_interp_bufs(n)
        blen = self._blen
        buf = self._buf
        positions = self._interp_positions[:n]
        np.multiply(self._interp_arange[:n], ratio, out=positions)
        positions += start
        fl = self._interp_fl[:n]
        np.floor(positions, out=self._interp_positions[:n])  # temp reuse ok
        fl[:] = self._interp_positions[:n]
        frac = self._interp_frac[:n]
        frac[:, 0] = positions - self._interp_positions[:n]
        i0 = fl % blen
        i1 = (fl + 1) % blen
        # Advanced-index both points, sliced to actual channel count
        s0 = buf[i0, :channels]    # (n, channels)
        s1 = buf[i1, :channels]
        out = self._interp_out[:n, :channels]
        np.subtract(s1, s0, out=out)
        out *= frac
        out += s0
        return out, float(positions[-1] + ratio)

    # ---- per-voice overlap-add ---------------------------------------------

    def _process_voice(self, vi: int, ratio: float, delay: int,
                       n_samples: int, channels: int,
                       out: np.ndarray) -> np.ndarray:
        """Process one voice through its two overlapping Hann-windowed grains.

        Writes into pre-allocated ``out`` (zeroed on entry).
        """
        out.fill(0.0)
        wr = self._wr         # write cursor (already advanced past input)
        grain_tmp = self._grain_tmp[:n_samples, :channels]

        for h in range(2):
            phase = self._ph[vi][h]
            rd    = self._rd[vi][h]
            end_phase = phase + n_samples

            if end_phase <= self.GRAIN:
                # No grain boundary inside this block
                audio, new_rd = self._interp_read(rd, ratio,
                                                  n_samples, channels)
                win = self._win[phase:end_phase, np.newaxis]
                np.multiply(audio, win, out=grain_tmp)
                out += grain_tmp
                self._rd[vi][h] = new_rd
                if end_phase == self.GRAIN:
                    # Exactly at boundary — resync for next call
                    self._ph[vi][h] = 0
                    self._rd[vi][h] = float(wr - self.GRAIN - delay)
                else:
                    self._ph[vi][h] = end_phase
            else:
                # Grain boundary falls within this block
                first  = self.GRAIN - phase        # samples to finish grain
                second = n_samples - first         # samples in new grain

                # ── Finish current grain ──
                a1, _ = self._interp_read(rd, ratio, first, channels)
                w1 = self._win[phase:self.GRAIN, np.newaxis]
                tmp1 = grain_tmp[:first]
                np.multiply(a1, w1, out=tmp1)
                out[:first] += tmp1

                # ── Re-sync head to write cursor AT THE BOUNDARY POINT ──
                # The write cursor at sample 'first' was wr - second
                sync_rd = float(wr - second - self.GRAIN - delay)
                a2, new_rd = self._interp_read(sync_rd, ratio,
                                               second, channels)
                w2 = self._win[:second, np.newaxis]
                tmp2 = grain_tmp[:second]
                np.multiply(a2, w2, out=tmp2)
                out[first:] += tmp2

                self._ph[vi][h] = second
                self._rd[vi][h] = new_rd

        return out

    # ---- main entry --------------------------------------------------------

    def process(self, data: np.ndarray) -> np.ndarray:
        st = self.state
        if not st.enabled:
            return data

        is_mono = data.ndim == 1
        n = data.shape[0]
        ch = 1 if is_mono else data.shape[1]
        self._ensure_proc_bufs(n)
        work = self._work_buf[:n, :ch]
        if is_mono:
            work[:, 0] = data
        else:
            np.copyto(work, data)

        # ── Write input (+ feedback) into ring buffer ──
        blen = self._blen
        fb_amt = max(0.0, min(0.9, float(st.feedback)))
        idx_buf = self._idx_buf[:n]
        np.add(self._idx_base[:n], self._wr, out=idx_buf)
        np.mod(idx_buf, blen, out=idx_buf)
        idx = idx_buf
        if fb_amt > 0.001:
            self._buf[idx] = work + fb_amt * self._fb[:n, :ch]
        else:
            self._buf[idx] = work
        self._wr = (self._wr + n) % blen

        # ── Lazy-init read heads on first call ──
        if not self._inited:
            da = int(st.voice_a_delay_ms * 0.001 * self.sr)
            db = int(st.voice_b_delay_ms * 0.001 * self.sr)
            for hh in range(2):
                self._rd[0][hh] = float(self._wr - self.GRAIN - da)
                self._rd[1][hh] = float(self._wr - self.GRAIN - db)
            self._inited = True

        # ── Per-voice processing ──
        ratio_a = self._pitch_ratio(st.voice_a_semitones, st.voice_a_cents)
        ratio_b = self._pitch_ratio(st.voice_b_semitones, st.voice_b_cents)

        # Algorithm modes modify effective ratios:
        #   0 = DUAL   — both voices as-is (independent intervals)
        #   1 = UP     — both voices shift up (abs of semitones)
        #   2 = DOWN   — both voices shift down (negative abs)
        #   3 = PTCH   — pitched delay: unity pitch, delay + feedback only
        algo = int(st.algorithm)
        if algo == 1:       # UP — force both positive
            ratio_a = self._pitch_ratio(abs(st.voice_a_semitones),
                                        abs(st.voice_a_cents))
            ratio_b = self._pitch_ratio(abs(st.voice_b_semitones),
                                        abs(st.voice_b_cents))
        elif algo == 2:     # DOWN — force both negative
            ratio_a = self._pitch_ratio(-abs(st.voice_a_semitones),
                                        -abs(st.voice_a_cents))
            ratio_b = self._pitch_ratio(-abs(st.voice_b_semitones),
                                        -abs(st.voice_b_cents))
        elif algo == 3:     # PTCH — unity pitch, delay-only
            ratio_a = 1.0
            ratio_b = 1.0

        da = int(st.voice_a_delay_ms * 0.001 * self.sr)
        db = int(st.voice_b_delay_ms * 0.001 * self.sr)

        va = self._process_voice(0, ratio_a, da, n, ch, self._voice_out_a[:n, :ch])
        vb = self._process_voice(1, ratio_b, db, n, ch, self._voice_out_b[:n, :ch])

        # wet = (va + vb) * 0.5  — accumulate in-place into va to avoid allocs
        wet = va
        wet += vb
        wet *= 0.5

        # ── Store wet for feedback next block ──
        self._fb[:n, :ch] = wet

        mix = max(0.0, min(1.0, float(st.mix)))
        result = self._result_buf[:n, :ch]
        np.multiply(np.float32(1.0 - mix), work, out=result)
        result += wet * np.float32(mix)
        if is_mono:
            return result[:, 0]
        return result

    def reset(self):
        self._buf[:] = 0.0
        self._wr = 0
        self._rd = [[0.0, 0.0], [0.0, 0.0]]
        self._ph = [[0, self.HOP], [0, self.HOP]]
        self._inited = False
        self._fb[:] = 0.0


# ──────────────────────────────────────────────────────────────────────────────
# Stereo Widener  (Aphex 124A M-S)
# ──────────────────────────────────────────────────────────────────────────────
class StereoWidenerProcessor:
    """M-S stereo width control with low-mono crossover."""

    def __init__(self, sample_rate: int = SAMPLE_RATE, buffer_size: int = 512):
        self.sr = sample_rate
        from mlrvp.config import StereoWidenerState
        self.state = StereoWidenerState()
        # LP filter on the side channel — everything below low_mono_freq
        # gets removed from S to keep bass mono.
        self._side_lp = BiquadFilter(sample_rate)
        self._side_lp.set_lowpass(120.0, 0.707)
        self._last_lm_freq: float = 120.0
        # Pre-allocated stereo buffer for side-channel LP filter
        self._s_stereo = np.empty((buffer_size, 2), dtype=np.float32)
        self._s_stereo_size = buffer_size
        # Pre-allocated output buffer
        self._out = np.empty((buffer_size, 2), dtype=np.float32)
        self._out_size = buffer_size

    def process(self, data: np.ndarray) -> np.ndarray:
        st = self.state
        if not st.enabled:
            return data

        width = st.width
        center = st.center

        # Nothing to do when unity width and unity center
        if abs(width - 1.0) < 0.001 and abs(center - 1.0) < 0.001:
            return data

        # M-S encode
        L = data[:, 0]
        R = data[:, 1] if data.shape[1] > 1 else L
        M = (L + R) * 0.5
        S = (L - R) * 0.5

        # Scale mid / side
        M = M * center
        S = S * width

        # Low-mono: remove low-frequency content from side channel
        lm = st.low_mono_freq
        if lm > 25.0:
            if abs(lm - self._last_lm_freq) > 0.5:
                self._side_lp.set_lowpass(lm, 0.707)
                self._last_lm_freq = lm
            # Filter the side to get lows, subtract to keep only highs in S
            n = len(S)
            if n > self._s_stereo_size:
                self._s_stereo = np.empty((n, 2), dtype=np.float32)
                self._s_stereo_size = n
            s_stereo = self._s_stereo[:n]
            s_stereo[:, 0] = S
            s_stereo[:, 1] = S
            s_lo = self._side_lp.process(s_stereo)[:, 0]
            S = S - s_lo

        # M-S decode
        n = len(data)
        if n > self._out_size:
            self._out = np.empty((n, 2), dtype=np.float32)
            self._out_size = n
        out = self._out[:n]
        out[:, 0] = M + S
        if data.shape[1] > 1:
            out[:, 1] = M - S
        return out

    def reset(self):
        self._side_lp.reset()
        self._last_lm_freq = 120.0


# ──────────────────────────────────────────────────────────────────────────────
# Frequency Shifter  (Eventide H949 / Bode 1630)
# ──────────────────────────────────────────────────────────────────────────────

def _make_hilbert_fir(M: int = 64) -> np.ndarray:
    """Design a 2M+1 tap FIR Hilbert transform with Hann window.

    Returns the filter kernel.  Group delay = M samples.
    """
    h = np.zeros(2 * M + 1, dtype=np.float64)
    for k in range(-M, M + 1):
        if k % 2 != 0:
            h[k + M] = 2.0 / (np.pi * k)
    h *= np.hanning(2 * M + 1)
    return h

# Pre-compute the kernel once at module load
# 257 taps (M=128): ~-45 dB sideband rejection, 2.9 ms latency @ 44.1 kHz
_HILBERT_M = 128
_HILBERT_FIR = _make_hilbert_fir(_HILBERT_M)


class FreqShifterProcessor:
    """Single-sideband frequency shifter via FIR Hilbert transform.

    Q-path = FIR Hilbert of input (via lfilter, C-level).
    I-path = input delayed by M samples to match FIR group delay.
    SSB: out = I·cos(ωt) − Q·sin(ωt)   →  upper sideband only.
    """

    def __init__(self, sample_rate: int = SAMPLE_RATE):
        from scipy.signal import lfilter as _lf, lfilter_zi as _lzi
        self._lfilter = _lf
        self.sr = sample_rate
        from mlrvp.config import FreqShifterState
        self.state = FreqShifterState()
        self._M = _HILBERT_M
        self._fir = _HILBERT_FIR
        # FIR filter state for Q-path (shape: (ntaps-1, nchan))
        self._zi_q = _lzi(self._fir, 1.0).reshape(-1, 1) * np.zeros((1, 2))
        # I-path delay line: ring buffer of M samples × 2 channels
        self._delay_buf = np.zeros((self._M, 2), dtype=np.float64)
        self._osc_phase = 0.0
        self._fb_buf = None
        # Pre-allocated I-path output buffer
        self._i_out = np.zeros((4096, 2), dtype=np.float64)
        # Pre-allocated input float64 conversion buffer
        self._x_f64 = np.zeros((4096, 2), dtype=np.float64)
        self._x_f64_size = 4096
        # Pre-allocated wet + feedback buffers
        self._wet_buf = np.empty((4096, 2), dtype=np.float32)
        self._fb_f64_buf = np.zeros((4096, 2), dtype=np.float64)
        self._wet_size = 4096
        # Pre-allocated oscillator buffers
        self._osc_phases = np.empty(4096, dtype=np.float64)
        self._cos_osc = np.empty(4096, dtype=np.float64)
        self._sin_osc = np.empty(4096, dtype=np.float64)
        self._osc_arange = np.arange(4096, dtype=np.float64)
        self._osc_buf_size = 4096

    def process(self, data: np.ndarray) -> np.ndarray:
        st = self.state
        if not st.enabled:
            return data
        shift = st.shift_hz
        mix = st.mix
        if abs(shift) < 0.01 and st.feedback < 0.001:
            if abs(mix - 1.0) < 0.001:
                return data
            if mix < 0.001:
                return data

        lfilter = self._lfilter
        n = len(data)
        nchan = data.shape[1] if data.ndim == 2 else 1
        dry = data

        if n > self._x_f64_size:
            self._x_f64 = np.zeros((n, 2), dtype=np.float64)
            self._x_f64_size = n
        x = self._x_f64[:n, :nchan]
        np.copyto(x, data, casting='unsafe')

        # Mix full-block feedback from previous iteration
        fb_gain = min(0.9, max(0.0, st.feedback))
        if fb_gain > 0.001 and self._fb_buf is not None:
            fb = self._fb_buf
            if fb.shape[0] >= n:
                x += fb[:n, :nchan] * fb_gain
            else:
                x[:fb.shape[0]] += fb[:, :nchan] * fb_gain

        # Q-path: FIR Hilbert transform (C-level lfilter)
        Q_out, self._zi_q = lfilter(
            self._fir, 1.0, x, axis=0, zi=self._zi_q[:, :nchan]
        )

        # I-path: delay by M samples using pre-allocated buffer
        M = self._M
        i_out = self._i_out[:n, :nchan]
        if n <= M:
            i_out[:] = self._delay_buf[:n, :nchan]
        else:
            i_out[:M] = self._delay_buf[:, :nchan]
            i_out[M:] = x[:n - M]
        # Save the last M samples for next block
        if n >= M:
            self._delay_buf[:, :nchan] = x[n - M:]
        else:
            self._delay_buf[:-n, :nchan] = self._delay_buf[n:, :nchan]
            self._delay_buf[-n:, :nchan] = x
        I_out = i_out

        # Oscillator — pre-allocated buffers
        if n > self._osc_buf_size:
            self._osc_phases = np.empty(n, dtype=np.float64)
            self._cos_osc = np.empty(n, dtype=np.float64)
            self._sin_osc = np.empty(n, dtype=np.float64)
            self._osc_arange = np.arange(n, dtype=np.float64)
            self._osc_buf_size = n
        phase_inc = 2.0 * np.pi * shift / self.sr
        phases = self._osc_phases[:n]
        np.multiply(self._osc_arange[:n], phase_inc, out=phases)
        phases += self._osc_phase
        self._osc_phase = float(phases[-1] + phase_inc) % (2.0 * np.pi)
        cos_osc = self._cos_osc[:n]
        sin_osc = self._sin_osc[:n]
        np.cos(phases, out=cos_osc)
        np.sin(phases, out=sin_osc)

        # SSB: upper sideband = I·cos − Q·sin
        if n > self._wet_size:
            self._wet_buf = np.empty((n, 2), dtype=np.float32)
            self._fb_f64_buf = np.zeros((n, 2), dtype=np.float64)
            self._wet_size = n
        if nchan >= 2:
            wet = self._wet_buf[:n]
            wet[:, 0] = (I_out[:, 0] * cos_osc - Q_out[:, 0] * sin_osc).astype(np.float32)
            wet[:, 1] = (I_out[:, 1] * cos_osc - Q_out[:, 1] * sin_osc).astype(np.float32)
        else:
            wet = (I_out[:, 0] * cos_osc - Q_out[:, 0] * sin_osc).astype(np.float32).reshape(-1, 1)

        # Store full wet block for feedback next iteration (reuse pre-allocated buffer)
        fb = self._fb_f64_buf[:n, :nchan]
        np.copyto(fb, wet[:, :nchan], casting='unsafe')
        self._fb_buf = fb

        return dry * (1.0 - mix) + wet * mix

    def reset(self):
        self._zi_q[:] = 0.0
        self._delay_buf[:] = 0.0
        self._osc_phase = 0.0
        self._fb_buf = None


# ──────────────────────────────────────────────────────────────────────────────
# Ring Modulator  ("Ring Leader")
# ──────────────────────────────────────────────────────────────────────────────

class RingModProcessor:
    """Classic ring modulator — multiplies audio by a carrier oscillator.

    Below ~20 Hz the effect is tremolo; above that it generates
    metallic sum-and-difference sidebands.  Three carrier shapes:
    sine (smooth), triangle (softer harmonics), square (harsh).
    """

    def __init__(self, sample_rate: int = SAMPLE_RATE):
        self.sr = sample_rate
        from mlrvp.config import RingModState
        self.state = RingModState()
        self._phase = 0.0
        # Pre-allocated work buffers (grow-only)
        self._buf_size = 512
        self._phases = np.empty(self._buf_size, dtype=np.float64)
        self._carrier = np.empty(self._buf_size, dtype=np.float32)
        self._mod = np.empty(self._buf_size, dtype=np.float32)
        self._arange = np.arange(self._buf_size, dtype=np.float64)

    def _ensure_bufs(self, n):
        if n > self._buf_size:
            self._buf_size = n
            self._phases = np.empty(n, dtype=np.float64)
            self._carrier = np.empty(n, dtype=np.float32)
            self._mod = np.empty(n, dtype=np.float32)
            self._arange = np.arange(n, dtype=np.float64)

    def process(self, data: np.ndarray) -> np.ndarray:
        st = self.state
        if not st.enabled:
            return data

        freq = max(0.1, min(5000.0, float(st.freq_hz)))
        depth = max(0.0, min(1.0, float(st.depth)))
        mix = max(0.0, min(1.0, float(st.mix)))

        if depth < 0.001 or mix < 0.001:
            return data

        n = len(data)
        if n <= 0:
            return data
        self._ensure_bufs(n)
        phase_inc = 2.0 * np.pi * freq / self.sr
        phases = self._phases[:n]
        np.multiply(self._arange[:n], phase_inc, out=phases)
        phases += self._phase
        self._phase = float(phases[-1] + phase_inc) % (2.0 * np.pi)

        carrier = self._carrier[:n]
        shape = int(st.shape)
        if shape == 1:
            # Triangle: fold sawtooth
            t = phases / (2.0 * np.pi) % 1.0
            np.subtract(2.0 * t, 1.0, out=phases[:n])  # reuse as temp
            np.abs(phases[:n], out=phases[:n])
            np.multiply(phases[:n], 2.0, out=phases[:n])
            np.subtract(phases[:n], 1.0, out=phases[:n])
            carrier[:] = phases[:n]
        elif shape == 2:
            # Square: sign of sine
            np.sin(phases, out=phases)
            np.sign(phases, out=phases)
            carrier[:] = phases
        else:
            # Sine (default)
            np.sin(phases, out=phases)
            carrier[:] = phases

        # depth: 0 = unmodulated, 1 = full ring-mod
        mod = self._mod[:n]
        np.multiply(carrier, depth, out=mod)
        mod += (1.0 - depth)

        if data.ndim == 2:
            wet = data * mod[:, np.newaxis]
        else:
            wet = data * mod

        return data * (1.0 - mix) + wet * mix

    def reset(self):
        self._phase = 0.0


# ─── Simple EQ ────────────────────────────────────────────────────────────────

class SimpleEQProcessor:
    """Golden Ears — 3-band Pultec-style parametric EQ.

    Three biquad shelving filters: low shelf, mid bell, high shelf.
    Each band provides ±12 dB of gain. Minimal CPU: just 3 biquad passes.
    """

    def __init__(self, sample_rate: int = SAMPLE_RATE, buffer_size: int = 512):
        self.sr = sample_rate
        from mlrvp.config import SimpleEQState
        self.state = SimpleEQState()
        # Biquad coefficients [b0, b1, b2, a1, a2] for low, mid, high
        self._coeffs = [np.zeros(5, dtype=np.float64) for _ in range(3)]
        # Pre-split b/a arrays for lfilter (avoid per-call allocation)
        self._b = [np.zeros(3, dtype=np.float64) for _ in range(3)]
        self._a = [np.array([1.0, 0.0, 0.0], dtype=np.float64) for _ in range(3)]
        # Per-band lfilter state: shape (2, 2) for order-2 biquad × 2 channels
        self._zi = [np.zeros((2, 2), dtype=np.float64) for _ in range(3)]
        self._dirty = True
        self._last_params = (0.0, 0.0, 0.0, 200.0, 4000.0)
        self._update_coeffs()
        # Pre-allocated f64 work buffer
        self._f64_buf = np.zeros((buffer_size, 2), dtype=np.float64)

    def _update_coeffs(self):
        """Recompute biquad coefficients from current state."""
        st = self.state
        params = (st.low, st.mid, st.high, st.low_freq, st.high_freq)
        if params == self._last_params and not self._dirty:
            return
        self._last_params = params
        self._dirty = False

        sr = self.sr
        # Low shelf
        self._coeffs[0] = self._low_shelf(
            max(60.0, min(500.0, st.low_freq)),
            max(-12.0, min(12.0, st.low)), sr
        )
        # Mid bell (peaking) at geometric mean of low_freq and high_freq
        mid_freq = math.sqrt(st.low_freq * st.high_freq)
        self._coeffs[1] = self._peaking(
            mid_freq, max(-12.0, min(12.0, st.mid)), 0.7, sr
        )
        # High shelf
        self._coeffs[2] = self._high_shelf(
            max(2000.0, min(12000.0, st.high_freq)),
            max(-12.0, min(12.0, st.high)), sr
        )
        # Split into b/a arrays for lfilter
        for i in range(3):
            c = self._coeffs[i]
            self._b[i][:] = c[0], c[1], c[2]
            self._a[i][:] = 1.0, c[3], c[4]

    @staticmethod
    def _low_shelf(freq: float, gain_db: float, sr: int) -> np.ndarray:
        A = 10.0 ** (gain_db / 40.0)
        w0 = 2.0 * math.pi * freq / sr
        cs = math.cos(w0)
        sn = math.sin(w0)
        alpha = sn / (2.0 * 0.707)
        b0 = A * ((A + 1) - (A - 1) * cs + 2 * math.sqrt(A) * alpha)
        b1 = 2 * A * ((A - 1) - (A + 1) * cs)
        b2 = A * ((A + 1) - (A - 1) * cs - 2 * math.sqrt(A) * alpha)
        a0 = (A + 1) + (A - 1) * cs + 2 * math.sqrt(A) * alpha
        a1 = -2 * ((A - 1) + (A + 1) * cs)
        a2 = (A + 1) + (A - 1) * cs - 2 * math.sqrt(A) * alpha
        return np.array([b0/a0, b1/a0, b2/a0, a1/a0, a2/a0], dtype=np.float64)

    @staticmethod
    def _high_shelf(freq: float, gain_db: float, sr: int) -> np.ndarray:
        A = 10.0 ** (gain_db / 40.0)
        w0 = 2.0 * math.pi * freq / sr
        cs = math.cos(w0)
        sn = math.sin(w0)
        alpha = sn / (2.0 * 0.707)
        b0 = A * ((A + 1) + (A - 1) * cs + 2 * math.sqrt(A) * alpha)
        b1 = -2 * A * ((A - 1) + (A + 1) * cs)
        b2 = A * ((A + 1) + (A - 1) * cs - 2 * math.sqrt(A) * alpha)
        a0 = (A + 1) - (A - 1) * cs + 2 * math.sqrt(A) * alpha
        a1 = 2 * ((A - 1) - (A + 1) * cs)
        a2 = (A + 1) - (A - 1) * cs - 2 * math.sqrt(A) * alpha
        return np.array([b0/a0, b1/a0, b2/a0, a1/a0, a2/a0], dtype=np.float64)

    @staticmethod
    def _peaking(freq: float, gain_db: float, q: float, sr: int) -> np.ndarray:
        A = 10.0 ** (gain_db / 40.0)
        w0 = 2.0 * math.pi * freq / sr
        cs = math.cos(w0)
        sn = math.sin(w0)
        alpha = sn / (2.0 * q)
        b0 = 1 + alpha * A
        b1 = -2 * cs
        b2 = 1 - alpha * A
        a0 = 1 + alpha / A
        a1 = -2 * cs
        a2 = 1 - alpha / A
        return np.array([b0/a0, b1/a0, b2/a0, a1/a0, a2/a0], dtype=np.float64)

    def process(self, data: np.ndarray) -> np.ndarray:
        st = self.state
        if not st.enabled:
            return data
        mix = max(0.0, min(1.0, st.mix))
        if mix < 0.001:
            return data
        self._update_coeffs()

        # Process all 3 bands with lfilter (C-level biquad, no Python loops)
        n = len(data)
        if n > self._f64_buf.shape[0]:
            self._f64_buf = np.zeros((n, 2), dtype=np.float64)
        out = self._f64_buf[:n]
        np.copyto(out, data, casting='unsafe')
        for band in range(3):
            out, self._zi[band] = lfilter(
                self._b[band], self._a[band], out, axis=0, zi=self._zi[band])
            self._zi[band][np.abs(self._zi[band]) < 1e-18] = 0.0

        out = np.ascontiguousarray(out, dtype=np.float32)
        if mix < 0.999:
            return data * (1.0 - mix) + out * mix
        return out

    def reset(self):
        for zi in self._zi:
            zi[:] = 0.0
        self._dirty = True
        self._last_params = None


# ─── Ping-Pong Delay ─────────────────────────────────────────────────────────

class PingPongDelayProcessor:
    """Table Tennis — stereo ping-pong delay with independent L/R tap times.

    True stereo bouncing: L tap → R tap → L tap → …
    PING controls the left tap time, PONG the right tap time.
    When linked, both follow the master time knob.
    Each repeat alternates channels, with feedback damping LP filter.
    Max delay 2 seconds, up to ~95% feedback for long tails.
    """

    def __init__(self, sample_rate: int = SAMPLE_RATE, buffer_size: int = 512):
        self.sr = sample_rate
        from mlrvp.config import PingPongDelayState
        self.state = PingPongDelayState()
        max_samples = int(2.0 * sample_rate)  # 2s max
        self._buf_l = np.zeros(max_samples, dtype=np.float32)
        self._buf_r = np.zeros(max_samples, dtype=np.float32)
        self._write_pos = 0
        self._buf_len = max_samples
        # LP filter state for damping
        self._lp_l = 0.0
        self._lp_r = 0.0
        # Pre-allocated filter coefficient arrays
        self._lp_b = np.array([1.0], dtype=np.float64)
        self._lp_a = np.array([1.0, 0.0], dtype=np.float64)
        # Pre-allocated read/output buffers (grow-only)
        self._work_size = buffer_size
        self._tap_l = np.empty(buffer_size, dtype=np.float32)
        self._tap_r = np.empty(buffer_size, dtype=np.float32)
        self._out = np.empty((buffer_size, 2), dtype=np.float32)

    def _ensure_work_bufs(self, n):
        if n > self._work_size:
            self._work_size = n
            self._tap_l = np.empty(n, dtype=np.float32)
            self._tap_r = np.empty(n, dtype=np.float32)
            self._out = np.empty((n, 2), dtype=np.float32)

    def process(self, data: np.ndarray) -> np.ndarray:
        st = self.state
        if not st.enabled:
            return data
        mix = max(0.0, min(1.0, st.mix))
        if mix < 0.001:
            return data

        n = len(data)
        stereo = data.ndim == 2

        if st.ping_pong_link:
            ping_ms = max(10.0, min(2000.0, st.time_ms))
            pong_ms = ping_ms
        else:
            ping_ms = max(10.0, min(2000.0, st.ping_ms))
            pong_ms = max(10.0, min(2000.0, st.pong_ms))

        delay_l = min(int(ping_ms * self.sr / 1000.0), self._buf_len - 1)
        delay_r = min(int(pong_ms * self.sr / 1000.0), self._buf_len - 1)
        fb = np.float32(max(0.0, min(0.95, st.feedback)))
        damp = max(0.0, min(1.0, st.damping))
        spread = max(0.0, min(1.0, st.spread))
        lp_coeff = 1.0 - damp * 0.7

        wp = self._write_pos
        bl = self._buf_len

        if delay_l >= n and delay_r >= n:
            # Fast vectorised path — both delays > blocksize
            self._ensure_work_bufs(n)
            # Read taps into pre-allocated buffers
            rd_l_start = (wp - delay_l) % bl
            rd_r_start = (wp - delay_r) % bl
            tap_l = self._tap_l[:n]
            tap_r = self._tap_r[:n]
            self._circ_read_into(self._buf_l, rd_l_start, n, tap_l)
            self._circ_read_into(self._buf_r, rd_r_start, n, tap_r)

            # LP damping via lfilter (one-pole IIR in C)
            self._lp_b[0] = lp_coeff
            self._lp_a[1] = -(1.0 - lp_coeff)
            lp_l_out, _ = lfilter(self._lp_b, self._lp_a, tap_l.astype(np.float64),
                                  zi=np.array([(1.0 - lp_coeff) * self._lp_l]))
            lp_r_out, _ = lfilter(self._lp_b, self._lp_a, tap_r.astype(np.float64),
                                  zi=np.array([(1.0 - lp_coeff) * self._lp_r]))
            self._lp_l = float(lp_l_out[-1])
            self._lp_r = float(lp_r_out[-1])
            lp_l_f32 = lp_l_out.astype(np.float32)
            lp_r_f32 = lp_r_out.astype(np.float32)

            # Write with ping-pong feedback
            if stereo:
                in_l = data[:, 0]
                in_r = data[:, 1]
            else:
                in_l = data
                in_r = data
            write_l = in_l + fb * lp_r_f32
            write_r = in_r + fb * lp_l_f32
            self._circ_write_mono(self._buf_l, wp, write_l, n)
            self._circ_write_mono(self._buf_r, wp, write_r, n)

            # Stereo spread + mix (vectorised)
            sp_hi = np.float32(0.5 + 0.5 * spread)
            sp_lo = np.float32(0.5 - 0.5 * spread)
            wet_l = tap_l * sp_hi + tap_r * sp_lo
            wet_r = tap_r * sp_hi + tap_l * sp_lo

            mix_f32 = np.float32(mix)
            if stereo:
                out = self._out[:n]
                out[:, 0] = data[:, 0] * (1.0 - mix_f32) + wet_l * mix_f32
                out[:, 1] = data[:, 1] * (1.0 - mix_f32) + wet_r * mix_f32
            else:
                out = data * (1.0 - mix_f32) + (wet_l + wet_r) * np.float32(0.5 * mix)

            self._write_pos = (wp + n) % bl
            return out

        # Fallback: per-sample for very short delays (rare)
        out = data.copy()
        lp_l = self._lp_l
        lp_r = self._lp_r
        for i in range(n):
            rd_l = (wp - delay_l) % bl
            rd_r = (wp - delay_r) % bl
            tap_l = self._buf_l[rd_l]
            tap_r = self._buf_r[rd_r]
            lp_l = lp_l + lp_coeff * (tap_l - lp_l)
            lp_r = lp_r + lp_coeff * (tap_r - lp_r)
            in_l = float(data[i, 0]) if stereo else float(data[i])
            in_r = float(data[i, 1]) if stereo else float(data[i])
            self._buf_l[wp] = in_l + fb * lp_r
            self._buf_r[wp] = in_r + fb * lp_l
            sp_hi = 0.5 + 0.5 * spread
            sp_lo = 0.5 - 0.5 * spread
            wet_l = tap_l * sp_hi + tap_r * sp_lo
            wet_r = tap_r * sp_hi + tap_l * sp_lo
            if stereo:
                out[i, 0] = data[i, 0] * (1.0 - mix) + wet_l * mix
                out[i, 1] = data[i, 1] * (1.0 - mix) + wet_r * mix
            else:
                out[i] = data[i] * (1.0 - mix) + (wet_l + wet_r) * 0.5 * mix
            wp = (wp + 1) % bl
        self._write_pos = wp
        self._lp_l = lp_l
        self._lp_r = lp_r
        return out

    @staticmethod
    def _circ_read_mono(buf, idx, n):
        sz = len(buf)
        end = idx + n
        if end <= sz:
            return buf[idx:end].copy()
        split = sz - idx
        out = np.empty(n, dtype=buf.dtype)
        out[:split] = buf[idx:sz]
        out[split:] = buf[:end - sz]
        return out

    @staticmethod
    def _circ_read_into(buf, idx, n, out):
        """Read n samples from circular buffer into pre-allocated out array."""
        sz = len(buf)
        end = idx + n
        if end <= sz:
            out[:n] = buf[idx:end]
        else:
            split = sz - idx
            out[:split] = buf[idx:sz]
            out[split:] = buf[:end - sz]

    @staticmethod
    def _circ_write_mono(buf, idx, data, n):
        sz = len(buf)
        end = idx + n
        if end <= sz:
            buf[idx:end] = data
        else:
            split = sz - idx
            buf[idx:sz] = data[:split]
            buf[:end - sz] = data[split:]

    def reset(self):
        self._buf_l[:] = 0.0
        self._buf_r[:] = 0.0
        self._write_pos = 0
        self._lp_l = 0.0
        self._lp_r = 0.0


# ─── Shimmer Reverb ──────────────────────────────────────────────────────────

class ShimmerReverbProcessor:
    """Stained Glass — shimmer reverb with octave-shifted feedback.

    Architecture:
      input → pre-delay → 4 parallel combs (LP-damped feedback + shimmer)
            → 2 series allpasses → soft-clip → output

    Shimmer: the *previous* block's allpass output is pitch-shifted and
    blended into every comb's feedback path.  Over successive blocks the
    pitch keeps shifting up, creating the rising ethereal tail.
    Pitch shifter uses a block-level dual-head crossfaded reader —
    fully vectorised (numpy), no per-sample OLA or history buffer.
    """

    _COMB_TIMES = (0.0297, 0.0371, 0.0411, 0.0437)
    _AP_TIMES   = (0.0050, 0.0017)
    _AP_G       = 0.5

    def __init__(self, sample_rate: int = SAMPLE_RATE, buffer_size: int = 512):
        self.sr = sample_rate
        from mlrvp.config import ShimmerReverbState
        self.state = ShimmerReverbState()

        # Pre-delay ring buffer (max 100 ms)
        pd_max = int(0.1 * sample_rate)
        self._pd_buf = np.zeros((pd_max, 2), dtype=np.float32)
        self._pd_len = pd_max
        self._pd_wp = 0

        # 4 comb filters (circular buffers, stereo)
        self._comb_lens = [int(sample_rate * t) for t in self._COMB_TIMES]
        self._combs = [np.zeros((cl, 2), dtype=np.float32)
                       for cl in self._comb_lens]
        self._comb_wp = [0] * 4
        # LP state as plain Python floats (avoids numpy overhead in loop)
        self._comb_lp_l = [0.0] * 4
        self._comb_lp_r = [0.0] * 4

        # 2 all-pass filters (circular buffers, stereo)
        self._ap_lens = [int(sample_rate * t) for t in self._AP_TIMES]
        self._aps = [np.zeros((al, 2), dtype=np.float32)
                     for al in self._ap_lens]
        self._ap_wp = [0] * 2

        # Pitch-shifter phase (dual-head block reader, no history buffer)
        self._ps_phase = 0.0

        # Shimmer feedback from previous block (pre-alloc to max size)
        self._shimmer_fb_buf = np.zeros((max(buffer_size, 4096), 2), dtype=np.float32)
        self._shimmer_fb_len = max(buffer_size, 4096)

        # Pre-allocated buffers for comb LP filter (avoid per-block allocs)
        self._lp_zi_buf = np.zeros((1, 2), dtype=np.float64)
        self._delayed_f64 = np.zeros((buffer_size, 2), dtype=np.float64)
        self._lp_out_f32 = np.zeros((buffer_size, 2), dtype=np.float32)
        self._lp_buf_size = buffer_size
        # Pre-allocated circ-read buffer for predelay, combs, allpass
        self._circ_rd_buf = np.empty((buffer_size, 2), dtype=np.float32)
        self._circ_rd_size = buffer_size
        # Pre-allocated comb-sum and LP coefficient arrays
        self._comb_sum = np.zeros((buffer_size, 2), dtype=np.float32)
        self._comb_sum_size = buffer_size
        self._lp_b = np.array([1.0], dtype=np.float64)
        self._lp_a = np.array([1.0, 0.0], dtype=np.float64)
        # Pre-allocated pitch-shift work buffers
        self._ps_buf_size = buffer_size
        self._ps_positions = np.empty(buffer_size, dtype=np.float64)
        self._ps_pos_a = np.empty(buffer_size, dtype=np.float64)
        self._ps_pos_b = np.empty(buffer_size, dtype=np.float64)
        self._ps_ia = np.empty(buffer_size, dtype=np.int32)
        self._ps_ib = np.empty(buffer_size, dtype=np.int32)
        self._ps_fa = np.empty((buffer_size, 1), dtype=np.float32)
        self._ps_fb = np.empty((buffer_size, 1), dtype=np.float32)
        self._ps_t = np.empty(buffer_size, dtype=np.float32)
        self._ps_w = np.empty((buffer_size, 1), dtype=np.float32)
        self._ps_result = np.empty((buffer_size, 2), dtype=np.float32)
        self._ps_arange = np.arange(buffer_size, dtype=np.float64)

    def _ensure_ps_bufs(self, n):
        if n > self._ps_buf_size:
            self._ps_buf_size = n
            self._ps_positions = np.empty(n, dtype=np.float64)
            self._ps_pos_a = np.empty(n, dtype=np.float64)
            self._ps_pos_b = np.empty(n, dtype=np.float64)
            self._ps_ia = np.empty(n, dtype=np.int32)
            self._ps_ib = np.empty(n, dtype=np.int32)
            self._ps_fa = np.empty((n, 1), dtype=np.float32)
            self._ps_fb = np.empty((n, 1), dtype=np.float32)
            self._ps_t = np.empty(n, dtype=np.float32)
            self._ps_w = np.empty((n, 1), dtype=np.float32)
            self._ps_result = np.empty((n, 2), dtype=np.float32)
            self._ps_arange = np.arange(n, dtype=np.float64)

    # ------------------------------------------------------------------
    def process(self, data: np.ndarray) -> np.ndarray:
        st = self.state
        if not st.enabled:
            return data
        mix = max(0.0, min(1.0, st.mix))
        if mix < 0.001:
            return data

        n = len(data)
        decay    = max(0.0, min(1.0, st.decay))
        shimmer  = max(0.0, min(1.0, st.shimmer))
        damping  = max(0.0, min(1.0, st.damping))
        pd_ms    = max(0.0, min(100.0, st.pre_delay_ms))
        pd_samp  = max(1, min(int(pd_ms * self.sr / 1000.0),
                               self._pd_len - 1))
        pitch_ratio = 2.0 ** (int(st.pitch) / 12.0)

        fb = 0.70 + decay * 0.25          # 0.70–0.95
        lp_c = 1.0 - damping * 0.7        # LP coefficient
        use_shimmer = shimmer > 0.01

        shim_g = shimmer * (1.0 - fb) * 0.7 if use_shimmer else 0.0

        # Grow shimmer feedback buffer if needed (rare)
        if n > self._shimmer_fb_len:
            self._shimmer_fb_buf = np.zeros((n, 2), dtype=np.float32)
            self._shimmer_fb_len = n
        shimmer_fb = self._shimmer_fb_buf[:n]
        if use_shimmer:
            shimmer_fb *= 0.998
        sfb = shimmer_fb

        # Pre-delay — vectorised write-then-read
        _circ_write(self._pd_buf, self._pd_wp, data, self._pd_len)
        rd = (self._pd_wp - pd_samp) % self._pd_len
        if n > self._circ_rd_size:
            self._circ_rd_buf = np.empty((n, 2), dtype=np.float32)
            self._circ_rd_size = n
        pd_out = _circ_read(self._pd_buf, rd, n, self._pd_len, out=self._circ_rd_buf)
        self._pd_wp = (self._pd_wp + n) % self._pd_len

        # ---- Comb bank — vectorised (all delays > blocksize) ----------
        if n > self._comb_sum_size:
            self._comb_sum = np.zeros((n, 2), dtype=np.float32)
            self._comb_sum_size = n
        comb_sum = self._comb_sum[:n]
        comb_sum[:] = 0.0
        fb_f32 = np.float32(fb)
        lp_b = self._lp_b
        lp_a = self._lp_a
        lp_b[0] = lp_c
        lp_a[1] = -(1.0 - lp_c)

        if n > self._lp_buf_size:
            self._delayed_f64 = np.zeros((n, 2), dtype=np.float64)
            self._lp_out_f32 = np.zeros((n, 2), dtype=np.float32)
            self._lp_buf_size = n

        for c in range(4):
            wp = self._comb_wp[c]
            cl = self._comb_lens[c]

            # Block-read delayed output (raw tap for summing)
            delayed = _circ_read(self._combs[c], wp, n, cl, out=self._circ_rd_buf)
            comb_sum += delayed

            # LP filter the delayed signal (one-pole IIR via lfilter in C)
            self._lp_zi_buf[0, 0] = (1.0 - lp_c) * self._comb_lp_l[c]
            self._lp_zi_buf[0, 1] = (1.0 - lp_c) * self._comb_lp_r[c]
            self._delayed_f64[:n] = delayed
            lp_out, _ = lfilter(
                lp_b, lp_a, self._delayed_f64[:n], axis=0, zi=self._lp_zi_buf)
            self._comb_lp_l[c] = float(lp_out[-1, 0])
            self._comb_lp_r[c] = float(lp_out[-1, 1])

            # Write: input + LP'd feedback + shimmer, clamped to [-3, 3]
            self._lp_out_f32[:n] = lp_out
            write_data = pd_out + self._lp_out_f32[:n] * fb_f32
            if use_shimmer:
                write_data = write_data + sfb * np.float32(shim_g)
            np.clip(write_data, -3.0, 3.0, out=write_data)
            _circ_write(self._combs[c], wp, write_data, cl)
            self._comb_wp[c] = (wp + n) % cl

        comb_sum *= np.float32(0.25)

        # Flush LP denormals
        for c in range(4):
            if abs(self._comb_lp_l[c]) < 1e-18:
                self._comb_lp_l[c] = 0.0
            if abs(self._comb_lp_r[c]) < 1e-18:
                self._comb_lp_r[c] = 0.0

        # ---- All-pass chain (series) — chunked vectorised -------------
        ap_data = comb_sum
        for a in range(2):
            ap_data = self._allpass_block(a, ap_data, n)

        # Soft-clip → shimmer feedback → output
        wet = np.tanh(ap_data)
        if use_shimmer:
            self._shimmer_fb_buf[:n] = self._pitch_shift_block(
                wet, pitch_ratio, n)
        else:
            shimmer_fb[:] = 0.0

        return data * (1.0 - mix) + wet * mix

    # ------------------------------------------------------------------
    # All-pass block — chunked vectorised
    # ------------------------------------------------------------------
    def _allpass_block(self, idx, data, n):
        al = self._ap_lens[idx]
        buf = self._aps[idx]
        wp = self._ap_wp[idx]
        g = np.float32(self._AP_G)
        out = np.empty_like(data)
        pos = 0
        while pos < n:
            cn = min(al, n - pos)
            delayed = _circ_read(buf, wp, cn, al, out=self._circ_rd_buf)
            inp = data[pos:pos + cn]
            vn = inp + delayed * g
            _circ_write(buf, wp, vn, al)
            out[pos:pos + cn] = delayed - vn * g
            wp = (wp + cn) % al
            pos += cn
        self._ap_wp[idx] = wp
        return out

    # ------------------------------------------------------------------
    # Block-level dual-head pitch shifter (no history buffer)
    # ------------------------------------------------------------------
    def _pitch_shift_block(self, block, ratio, n):
        """Pitch-shift *block* by reading it at *ratio* speed with two
        crossfaded read heads.  Reads only from *block* (no history
        buffer), so energy cannot accumulate across blocks."""
        if n < 2:
            return block.copy()
        self._ensure_ps_bufs(n)
        half = n // 2

        positions = self._ps_positions[:n]
        np.multiply(self._ps_arange[:n], ratio, out=positions)
        positions += self._ps_phase
        pos_a = self._ps_pos_a[:n]
        np.mod(positions, n, out=pos_a)
        pos_b = self._ps_pos_b[:n]
        np.add(positions, half, out=pos_b)
        np.mod(pos_b, n, out=pos_b)

        # Linear interpolation for head A
        ia = self._ps_ia[:n]
        np.floor(pos_a, out=self._ps_positions[:n])  # reuse as temp
        ia[:] = self._ps_positions[:n].astype(np.int32) % n
        fa = self._ps_fa[:n]
        np.subtract(pos_a, self._ps_positions[:n], out=fa[:, 0])
        sa = block[ia] * (1.0 - fa) + block[(ia + 1) % n] * fa

        # Linear interpolation for head B
        ib = self._ps_ib[:n]
        np.floor(pos_b, out=self._ps_positions[:n])
        ib[:] = self._ps_positions[:n].astype(np.int32) % n
        fb_frac = self._ps_fb[:n]
        np.subtract(pos_b, self._ps_positions[:n], out=fb_frac[:, 0])
        sb = block[ib] * (1.0 - fb_frac) + block[(ib + 1) % n] * fb_frac

        # Cosine crossfade between the two heads
        t = self._ps_t[:n]
        np.mod(positions[:n], half, out=self._ps_pos_a[:n])  # reuse as temp
        np.divide(self._ps_pos_a[:n], half, out=self._ps_positions[:n])
        t[:] = self._ps_positions[:n]
        w = self._ps_w[:n]
        np.multiply(t, np.pi, out=t)
        np.cos(t, out=t)
        w[:, 0] = 0.5 + 0.5 * t
        result = self._ps_result[:n]
        result[:] = sa * w + sb * (1.0 - w)

        self._ps_phase = float(np.mod(self._ps_phase + n * ratio, n))
        return result

    # ------------------------------------------------------------------
    def reset(self):
        self._pd_buf[:] = 0.0
        self._pd_wp = 0
        for c in self._combs:
            c[:] = 0.0
        self._comb_wp = [0] * 4
        self._comb_lp_l = [0.0] * 4
        self._comb_lp_r = [0.0] * 4
        for a in self._aps:
            a[:] = 0.0
        self._ap_wp = [0] * 2
        self._ps_phase = 0.0
        self._shimmer_fb_buf[:] = 0.0


# ─── Lo-Fi Radio ─────────────────────────────────────────────────────────────

class LoFiRadioProcessor:
    """Tin Can — lo-fi radio / telephone bandpass filter with noise and breakup.

    Emulates AM radio, telephone, or transistor speaker using:
      - HP + LP biquad bandpass to restrict bandwidth
      - Optional soft-clip speaker distortion with cabinet resonance
      - Bandpass-filtered noise (noise follows the same HP/LP as the signal)
      - Input gain reduction to emulate weak signal / bad reception

    Presets have dramatically different character:
      AM Radio   — 500–3000 Hz, mid-heavy, crackly
      Telephone  — 700–2800 Hz, narrow, honky, thin
      Transistor — 150–6000 Hz, wider but still lo-fi, warmer
      Custom     — user-defined lo/hi cut
    """

    def __init__(self, sample_rate: int = SAMPLE_RATE, buffer_size: int = 512):
        self.sr = sample_rate
        from mlrvp.config import LoFiRadioState
        self.state = LoFiRadioState()
        # HP and LP biquad lfilter state: shape (2, n_ch) for order-2
        self._zi_nch = 2
        self._hp_zi = np.zeros((2, 2), dtype=np.float64)
        self._lp_zi = np.zeros((2, 2), dtype=np.float64)
        # Noise filter states (separate so noise goes through same bandpass)
        self._nhp_zi = np.zeros((2, 2), dtype=np.float64)
        self._nlp_zi = np.zeros((2, 2), dtype=np.float64)
        # Pre-split b/a arrays for lfilter
        self._hp_b = np.zeros(3, dtype=np.float64)
        self._hp_a = np.array([1.0, 0.0, 0.0], dtype=np.float64)
        self._lp_b = np.zeros(3, dtype=np.float64)
        self._lp_a = np.array([1.0, 0.0, 0.0], dtype=np.float64)
        self._last_lo = 0.0
        self._last_hi = 0.0
        self._rng = np.random.default_rng(42)
        # Pre-allocated circular noise buffer (avoids per-block RNG alloc)
        _NL = 8192
        self._noise_buf = self._rng.standard_normal((_NL, 2)).astype(np.float32)
        self._noise_len = _NL
        self._noise_pos = 0
        # Pre-allocated float64 biquad conversion buffer
        self._f64_buf = np.empty((buffer_size, 2), dtype=np.float64)
        self._f64_buf_size = buffer_size
        self._update_filters(500.0, 3000.0)

    # Band presets: (lo_cut, hi_cut) — made more dramatically different
    _PRESETS = {
        0: (500.0,  3000.0),   # AM Radio — narrow mid-band, classic crackle
        1: (700.0,  2800.0),   # Telephone — very narrow, honky
        2: (150.0,  6000.0),   # Transistor — wider, warmer vintage speaker
    }

    def _update_filters(self, lo: float, hi: float):
        if lo == self._last_lo and hi == self._last_hi:
            return
        self._last_lo = lo
        self._last_hi = hi
        # Use slightly higher Q for more dramatic bandpass character
        c = self._biquad_hp(max(20.0, lo), self.sr, q=1.0)
        self._hp_b[:] = c[0], c[1], c[2]
        self._hp_a[:] = 1.0, c[3], c[4]
        c = self._biquad_lp(min(16000.0, hi), self.sr, q=1.0)
        self._lp_b[:] = c[0], c[1], c[2]
        self._lp_a[:] = 1.0, c[3], c[4]

    @staticmethod
    def _biquad_hp(freq: float, sr: int, q: float = 0.707) -> np.ndarray:
        w0 = 2.0 * math.pi * freq / sr
        cs = math.cos(w0)
        alpha = math.sin(w0) / (2.0 * q)
        b0 = (1 + cs) / 2
        b1 = -(1 + cs)
        b2 = (1 + cs) / 2
        a0 = 1 + alpha
        a1 = -2 * cs
        a2 = 1 - alpha
        return np.array([b0/a0, b1/a0, b2/a0, a1/a0, a2/a0], dtype=np.float64)

    @staticmethod
    def _biquad_lp(freq: float, sr: int, q: float = 0.707) -> np.ndarray:
        w0 = 2.0 * math.pi * freq / sr
        cs = math.cos(w0)
        alpha = math.sin(w0) / (2.0 * q)
        b0 = (1 - cs) / 2
        b1 = 1 - cs
        b2 = (1 - cs) / 2
        a0 = 1 + alpha
        a1 = -2 * cs
        a2 = 1 - alpha
        return np.array([b0/a0, b1/a0, b2/a0, a1/a0, a2/a0], dtype=np.float64)

    def _apply_biquad(self, data: np.ndarray, b: np.ndarray, a: np.ndarray,
                      zi: np.ndarray):
        """Apply biquad filter using lfilter — returns (filtered_data, new_zi)."""
        n = len(data)
        n_ch = data.shape[1] if data.ndim == 2 else 1
        if n > self._f64_buf_size or (data.ndim == 2 and self._f64_buf.shape[1] != n_ch):
            self._f64_buf = np.empty((n, n_ch), dtype=np.float64)
            self._f64_buf_size = n
        buf = self._f64_buf[:n]
        np.copyto(buf, data, casting='unsafe')
        out, zi_new = lfilter(b, a, buf, axis=0, zi=zi)
        zi_new[np.abs(zi_new) < 1e-18] = 0.0
        return np.ascontiguousarray(out, dtype=np.float32), zi_new

    def process(self, data: np.ndarray) -> np.ndarray:
        st = self.state
        if not st.enabled:
            return data
        mix = max(0.0, min(1.0, st.mix))
        if mix < 0.001:
            return data

        # Ensure zi arrays match channel count
        n_ch = data.shape[1] if data.ndim == 2 else 1
        if n_ch != self._zi_nch:
            self._hp_zi = np.zeros((2, n_ch), dtype=np.float64)
            self._lp_zi = np.zeros((2, n_ch), dtype=np.float64)
            self._nhp_zi = np.zeros((2, n_ch), dtype=np.float64)
            self._nlp_zi = np.zeros((2, n_ch), dtype=np.float64)
            self._zi_nch = n_ch

        n = len(data)

        # Determine cutoff frequencies from preset or custom
        if st.band in self._PRESETS:
            lo, hi = self._PRESETS[st.band]
        else:
            lo = max(20.0, min(2000.0, st.lo_cut))
            hi = max(500.0, min(16000.0, st.hi_cut))
        self._update_filters(lo, hi)

        # Apply bandpass (HP then LP) to signal via lfilter
        wet, self._hp_zi = self._apply_biquad(
            data, self._hp_b, self._hp_a, self._hp_zi)
        wet, self._lp_zi = self._apply_biquad(
            wet, self._lp_b, self._lp_a, self._lp_zi)

        # Speaker distortion (soft clip with cabinet resonance)
        dist = max(0.0, min(1.0, st.distortion))
        if dist > 0.01:
            drive = 1.0 + dist * 8.0
            wet = np.tanh(wet * drive) / np.tanh(np.float32(drive))

        # Bandpass-filtered noise — noise goes through the same HP/LP as
        # the signal, so it sounds like in-band static rather than full-
        # spectrum white noise.  Amplitude is very subtle by default.
        noise_amt = max(0.0, min(1.0, st.noise))
        if noise_amt > 0.001:
            # Read from pre-allocated circular noise buffer (zero alloc)
            pos = self._noise_pos
            nl = self._noise_len
            if pos + n <= nl:
                raw_noise = self._noise_buf[pos:pos + n]
                self._noise_pos = pos + n if pos + n < nl else 0
            else:
                # Wrap around
                first = nl - pos
                raw_noise = np.empty((n, 2), dtype=np.float32)
                raw_noise[:first] = self._noise_buf[pos:]
                raw_noise[first:] = self._noise_buf[:n - first]
                self._noise_pos = n - first
            if data.ndim == 1:
                raw_noise = raw_noise[:, 0]
            # Filter noise through the same bandpass
            filt_noise, self._nhp_zi = self._apply_biquad(
                raw_noise, self._hp_b, self._hp_a, self._nhp_zi)
            filt_noise, self._nlp_zi = self._apply_biquad(
                filt_noise, self._lp_b, self._lp_a, self._nlp_zi)
            # Very subtle scaling: noise_amt 0→1 maps to 0→0.008 amplitude
            wet = wet + filt_noise * (noise_amt * 0.008)

        return data * (1.0 - mix) + wet * mix

    def reset(self):
        self._hp_zi[:] = 0.0
        self._lp_zi[:] = 0.0
        self._nhp_zi[:] = 0.0
        self._nlp_zi[:] = 0.0
        self._last_lo = 0.0
        self._last_hi = 0.0


# ─── Auto-Pan ────────────────────────────────────────────────────────────────

class AutoPanProcessor:
    """Pendulum — stereo auto-panner with configurable LFO.

    Modulates the stereo field position using sine/tri/square LFO.
    The left and right channels are gain-modulated in opposition to create
    rhythmic stereo movement. Different from tremolo — this moves the image
    left-to-right rather than just modulating volume.
    """

    def __init__(self, sample_rate: int = SAMPLE_RATE, buffer_size: int = 512):
        self.sr = sample_rate
        from mlrvp.config import AutoPanState
        self.state = AutoPanState()
        self._phase = 0.0
        # Pre-allocated work buffers (grow-only)
        self._buf_size = buffer_size
        self._phases = np.empty(buffer_size, dtype=np.float64)
        self._pan = np.empty(buffer_size, dtype=np.float64)
        self._theta = np.empty(buffer_size, dtype=np.float64)
        self._gain_l = np.empty(buffer_size, dtype=np.float32)
        self._gain_r = np.empty(buffer_size, dtype=np.float32)
        self._wet = np.empty((buffer_size, 2), dtype=np.float32)
        self._arange = np.arange(buffer_size, dtype=np.float64)

    def _ensure_bufs(self, n):
        if n > self._buf_size:
            self._buf_size = n
            self._phases = np.empty(n, dtype=np.float64)
            self._pan = np.empty(n, dtype=np.float64)
            self._theta = np.empty(n, dtype=np.float64)
            self._gain_l = np.empty(n, dtype=np.float32)
            self._gain_r = np.empty(n, dtype=np.float32)
            self._wet = np.empty((n, 2), dtype=np.float32)
            self._arange = np.arange(n, dtype=np.float64)

    def process(self, data: np.ndarray) -> np.ndarray:
        st = self.state
        if not st.enabled:
            return data

        n = len(data)
        if n == 0:
            return data

        rate = max(0.1, min(20.0, st.rate))
        depth = max(0.0, min(1.0, st.depth))
        mix = max(0.0, min(1.0, st.mix))

        if depth < 1e-6 or mix < 1e-6:
            return data

        self._ensure_bufs(n)

        # Generate LFO — pan position from -1 (left) to +1 (right)
        phase_inc = rate / self.sr
        phases = self._phases[:n]
        np.multiply(self._arange[:n], phase_inc, out=phases)
        phases += self._phase
        np.mod(phases, 1.0, out=phases)
        self._phase = float(np.mod(self._phase + n * phase_inc, 1.0))

        pan = self._pan[:n]
        shape = st.shape
        if shape == 0:       # Sine
            np.multiply(phases, 2.0 * np.pi, out=pan)
            np.sin(pan, out=pan)
        elif shape == 1:     # Triangle
            np.subtract(np.multiply(phases, 2.0), 1.0, out=pan)
            np.abs(pan, out=pan)
            np.multiply(pan, 2.0, out=pan)
            np.subtract(pan, 1.0, out=pan)
        else:                # Square
            pan[:] = np.where(phases < 0.5, -1.0, 1.0)

        # Convert pan position to L/R gains using constant-power panning
        theta = self._theta[:n]
        np.multiply(pan, depth, out=theta)
        theta += 1.0
        theta *= 0.5 * (np.pi * 0.5)
        gain_l = self._gain_l[:n]
        gain_r = self._gain_r[:n]
        np.cos(theta, out=theta)  # reuse theta for cos
        gain_l[:] = theta
        # Recompute theta for sin
        np.multiply(pan, depth, out=theta)
        theta += 1.0
        theta *= 0.5 * (np.pi * 0.5)
        np.sin(theta, out=theta)
        gain_r[:] = theta

        if data.ndim == 2:
            wet = self._wet[:n]
            np.multiply(data[:, 0], gain_l, out=wet[:, 0])
            np.multiply(data[:, 1], gain_r, out=wet[:, 1])
        else:
            wet = data * (gain_l + gain_r) * 0.5

        return data * (1.0 - mix) + wet * mix

    def reset(self):
        self._phase = 0.0


# ─── Fuzz ─────────────────────────────────────────────────────────────────────

class FuzzProcessor:
    """Buzz Saw — Big Muff-style fuzz/distortion.

    Models a classic transistor fuzz circuit:
      1. Input gain stage (sustain → compression via soft-clip pre-amp)
      2. Hard-clip fuzz stage with asymmetric diode clipping
      3. Tone stack (variable LP/HP crossfade)
      4. Noise gate to tame idle hiss
    Much more aggressive than tube overdrive — square-wave destruction territory.
    """

    def __init__(self, sample_rate: int = SAMPLE_RATE, buffer_size: int = 512):
        self.sr = sample_rate
        from mlrvp.config import FuzzState
        self.state = FuzzState()
        # Tone filter state for lfilter: shape (1, n_ch) for 1st-order
        self._tone_zi = np.zeros((1, 2), dtype=np.float64)
        self._tone_nch = 2
        # Pre-allocated buffers (grow-only, same pattern as all other processors)
        _sz = max(buffer_size, 4096)
        self._wet_buf = np.zeros((_sz, 2), dtype=np.float32)
        self._wet_buf_size = _sz
        self._f64_buf = np.zeros((_sz, 2), dtype=np.float64)
        self._f64_buf_size = _sz
        self._lp_buf = np.zeros((_sz, 2), dtype=np.float32)
        self._lp_buf_size = _sz
        self._out_f32 = np.zeros((_sz, 2), dtype=np.float32)
        self._out_f32_size = _sz

    def _ensure_bufs(self, n: int, n_ch: int) -> None:
        sz = n * n_ch
        if n > self._wet_buf_size or n_ch != self._wet_buf.shape[1] if self._wet_buf.ndim == 2 else True:
            self._wet_buf = np.zeros((n, n_ch), dtype=np.float32)
            self._wet_buf_size = n
        if n > self._f64_buf_size or n_ch != self._f64_buf.shape[1] if self._f64_buf.ndim == 2 else True:
            self._f64_buf = np.zeros((n, n_ch), dtype=np.float64)
            self._f64_buf_size = n
        if n > self._lp_buf_size or n_ch != self._lp_buf.shape[1] if self._lp_buf.ndim == 2 else True:
            self._lp_buf = np.zeros((n, n_ch), dtype=np.float32)
            self._lp_buf_size = n
        if n > self._out_f32_size or n_ch != self._out_f32.shape[1] if self._out_f32.ndim == 2 else True:
            self._out_f32 = np.zeros((n, n_ch), dtype=np.float32)
            self._out_f32_size = n

    def process(self, data: np.ndarray) -> np.ndarray:
        st = self.state
        if not st.enabled:
            return data
        mix = max(0.0, min(1.0, st.mix))
        if mix < 0.001:
            return data

        n = len(data)
        # Ensure zi matches channel count
        n_ch = data.shape[1] if data.ndim == 2 else 1
        if n_ch != self._tone_nch:
            self._tone_zi = np.zeros((1, n_ch), dtype=np.float64)
            self._tone_nch = n_ch

        self._ensure_bufs(n, n_ch)
        wet = self._wet_buf[:n]
        f64 = self._f64_buf[:n]
        lp_f32 = self._lp_buf[:n]
        out = self._out_f32[:n]

        gain = max(0.0, min(1.0, st.gain))
        tone = max(0.0, min(1.0, st.tone))
        sustain = max(0.0, min(1.0, st.sustain))
        gate_thresh = max(0.0, min(0.1, st.gate))

        # Drive amount: 0–1 → 1–100x gain
        drive = np.float32(1.0 + gain * 99.0)

        np.copyto(wet, data, casting='unsafe')  # float32 copy of input

        # Stage 1: Sustain — soft-clip compression before fuzz (in-place)
        if sustain > 0.01:
            comp_gain = np.float32(1.0 + sustain * 4.0)
            wet *= comp_gain
            np.tanh(wet, out=wet)

        # Stage 2: Fuzz — hard clip with asymmetric diodes (in-place where possible)
        wet *= drive
        # Asymmetric clipping: positive at +0.7, negative at -0.5
        pos_mask = wet > 0
        wet[pos_mask] = np.tanh(wet[pos_mask] * 2.0) * 0.7
        wet[~pos_mask] = np.tanh(wet[~pos_mask] * 3.0) * 0.5

        # Stage 3: Tone stack — one-pole LP, then blend LP/HP
        lp_coeff = 0.05 + (1.0 - tone) * 0.4
        b = np.array([lp_coeff], dtype=np.float64)
        a = np.array([1.0, -(1.0 - lp_coeff)], dtype=np.float64)
        np.copyto(f64, wet, casting='unsafe')
        lp_f64, self._tone_zi = lfilter(b, a, f64, axis=0, zi=self._tone_zi)
        self._tone_zi[np.abs(self._tone_zi) < 1e-18] = 0.0
        np.copyto(lp_f32, lp_f64, casting='unsafe')
        # out = lp*(1-tone) + (wet-lp)*tone  — in-place via out buffer
        t = np.float32(tone)
        np.multiply(lp_f32, np.float32(1.0 - tone), out=out)
        out += (wet - lp_f32) * t

        # Stage 4: Noise gate (in-place on out)
        if gate_thresh > 0.001:
            env = np.abs(out)
            if out.ndim == 2:
                env = np.max(env, axis=1, keepdims=True)
            gate_mask = np.where(env > gate_thresh, 1.0, env / max(gate_thresh, 1e-6))
            out *= gate_mask.astype(np.float32)

        # Normalize output level and mix (in-place)
        out *= np.float32(0.7)
        return data * np.float32(1.0 - mix) + out * np.float32(mix)

    def reset(self):
        self._tone_zi[:] = 0.0


# ─── Granular Freeze ─────────────────────────────────────────────────────────

class GranularFreezeProcessor:
    """Snow Globe — granular freeze with scatter, pitch spread, and freeze toggle.

    Architecture:
      input → write to ring buffer (unless frozen) →
      N grain read heads (Hann-windowed) at scattered positions →
      optional per-grain pitch randomisation via linear-interp resampling →
      overlap-add → mix with dry signal.
    """

    _NUM_GRAINS = 12

    def __init__(self, sample_rate: int = SAMPLE_RATE, buffer_size: int = 512):
        self.sr = sample_rate
        from mlrvp.config import GranularFreezeState
        self.state = GranularFreezeState()
        # Ring buffer: ~4 seconds stereo
        buf_len = int(4.0 * sample_rate)
        self._buf = np.zeros((buf_len, 2), dtype=np.float32)
        self._buf_len = buf_len
        self._write_pos = 0
        # Per-grain state: phase (0–1 through grain), start offset, pitch ratio
        self._grain_phase = np.zeros(self._NUM_GRAINS, dtype=np.float64)
        self._grain_start = np.zeros(self._NUM_GRAINS, dtype=np.int64)
        self._grain_pitch = np.ones(self._NUM_GRAINS, dtype=np.float64)
        # Stagger grains evenly in phase so they overlap from the start
        for i in range(self._NUM_GRAINS):
            self._grain_phase[i] = i / self._NUM_GRAINS
        self._rng = np.random.default_rng(42)
        # Pre-allocated wet output buffer
        self._gf_wet = np.zeros((max(buffer_size, 4096), 2), dtype=np.float32)
        self._gf_wet_size = max(buffer_size, 4096)
        # Pre-allocated mono→stereo buffer
        self._mono_stereo = np.zeros((max(buffer_size, 4096), 2), dtype=np.float32)
        self._mono_stereo_size = max(buffer_size, 4096)

    def process(self, data: np.ndarray) -> np.ndarray:
        st = self.state
        if not st.enabled:
            return data
        mix = max(0.0, min(1.0, st.mix))
        if mix < 0.001:
            return data

        n = len(data)
        stereo = data.ndim == 2 and data.shape[1] >= 2

        # Write input into ring buffer (unless frozen) — vectorised
        if not st.freeze:
            wp = self._write_pos
            end = wp + n
            if stereo:
                src = data[:, :2]
            else:
                if n > self._mono_stereo_size:
                    self._mono_stereo = np.zeros((n, 2), dtype=np.float32)
                    self._mono_stereo_size = n
                self._mono_stereo[:n, 0] = data[:n].ravel()[:n]
                self._mono_stereo[:n, 1] = data[:n].ravel()[:n]
                src = self._mono_stereo[:n]
            if end <= self._buf_len:
                self._buf[wp:end] = src
            else:
                first = self._buf_len - wp
                self._buf[wp:self._buf_len] = src[:first]
                self._buf[:end - self._buf_len] = src[first:]
            self._write_pos = end % self._buf_len

        # Grain parameters
        grain_ms = 5.0 + st.grain_size * 495.0  # 5–500 ms
        grain_samples = max(64, int(grain_ms * 0.001 * self.sr))
        scatter_range = int(st.scatter * self._buf_len * 0.5)
        spread = st.spread
        density = 0.2 + st.density * 0.8  # 0.2–1.0 = fewer→more overlap

        # Phase increment per sample: controls how fast each grain advances
        phase_inc = 1.0 / max(1, grain_samples)

        if n > self._gf_wet_size:
            self._gf_wet = np.zeros((n, 2), dtype=np.float32)
            self._gf_wet_size = n
        wet = self._gf_wet[:n]
        wet[:] = 0
        active_grains = max(2, int(self._NUM_GRAINS * density))
        two_pi = 2.0 * math.pi

        for g in range(active_grains):
            phase = float(self._grain_phase[g])
            g_start = int(self._grain_start[g])
            g_pitch = float(self._grain_pitch[g])

            # Process in vectorised segments between grain re-triggers
            offset = 0
            while offset < n:
                # Check if we need to re-trigger
                if phase >= 1.0:
                    phase -= 1.0
                    scatter_off = int(self._rng.integers(-scatter_range, scatter_range + 1)) if scatter_range > 0 else 0
                    g_start = (self._write_pos - grain_samples + scatter_off) % self._buf_len
                    if spread > 0.01:
                        semi = self._rng.uniform(-spread * 12.0, spread * 12.0)
                        g_pitch = 2.0 ** (semi / 12.0)
                    else:
                        g_pitch = 1.0

                # How many samples until next re-trigger?
                samples_to_trigger = max(1, int(math.ceil((1.0 - phase) / phase_inc)))
                seg_len = min(samples_to_trigger, n - offset)

                # Vectorised Hann window + read for this segment
                phases = phase + np.arange(seg_len, dtype=np.float64) * phase_inc
                windows = np.float32(0.5) - np.float32(0.5) * np.cos(two_pi * phases).astype(np.float32)
                read_positions = g_start + phases * (grain_samples * g_pitch)
                indices = read_positions.astype(np.int64) % self._buf_len

                wet[offset:offset + seg_len, 0] += self._buf[indices, 0] * windows
                wet[offset:offset + seg_len, 1] += self._buf[indices, 1] * windows

                phase = float(phases[-1]) + phase_inc
                offset += seg_len

            self._grain_phase[g] = phase
            self._grain_start[g] = g_start
            self._grain_pitch[g] = g_pitch

        # Normalise
        if active_grains > 1:
            wet *= np.float32(2.0 / active_grains)

        if stereo:
            return data * np.float32(1.0 - mix) + wet * np.float32(mix)
        else:
            mono_wet = (wet[:, 0] + wet[:, 1]) * 0.5
            return data * (1.0 - mix) + mono_wet * mix

    def reset(self):
        self._buf[:] = 0.0
        self._write_pos = 0
        for i in range(self._NUM_GRAINS):
            self._grain_phase[i] = i / self._NUM_GRAINS
        self._grain_start[:] = 0
        self._grain_pitch[:] = 1.0


# ─── Gated Reverb ────────────────────────────────────────────────────────────

# Comb/allpass tunings for gated reverb (slightly detuned from hall to
# give a punchier, snappier character)
_GATE_COMBS = [1117, 1201, 1327, 1447, 1553, 1663, 1789, 1907]
_GATE_APASSES = [241, 307, 397, 509]


class GatedReverbProcessor:
    """Gate Crasher — gated reverb with optional reverse tail.

    GATE mode: hall reverb whose wet tail is cut by a time-triggered
    gate envelope.  After the input signal crosses the internal threshold,
    the reverb runs for GATE duration, then a shaped window (SLOPE) closes it.

    REVERSE mode: reverb output is written into a fixed-length buffer and
    played back in reverse, creating a swell-before-the-beat effect.
    """

    def __init__(self, sample_rate: int = SAMPLE_RATE, buffer_size: int = 512):
        self.sr = sample_rate
        from mlrvp.config import GatedReverbState
        self.state = GatedReverbState()
        sr_ratio = sample_rate / 44100.0

        # Comb + allpass banks (same Schroeder topology as HallReverbProcessor)
        self._combs = [_CombFilter(max(4, int(dl * sr_ratio)))
                       for dl in _GATE_COMBS]
        self._allpasses = [_AllpassFilter(max(4, int(dl * sr_ratio)), 0.5)
                           for dl in _GATE_APASSES]

        # Pre-delay circular buffer (max ~100 ms)
        max_pd = int(0.1 * sample_rate) + 1
        self._pd_buf = np.zeros((max_pd, 2), dtype=np.float32)
        self._pd_size = max_pd
        self._pd_idx = 0
        # Pre-allocated predelay read buffer
        self._pd_rd_buf = np.empty((buffer_size, 2), dtype=np.float32)
        self._pd_rd_size = buffer_size

        # Gate envelope state
        self._gate_open = False
        self._gate_countdown = 0  # samples remaining in gate-open phase
        self._gate_env = 1.0      # current envelope value (1=open, 0=closed)

        # Reverse mode buffer (max 3 seconds)
        rev_len = int(3.0 * sample_rate)
        self._rev_buf = np.zeros((rev_len, 2), dtype=np.float32)
        self._rev_len = rev_len
        self._rev_write = 0
        self._rev_read = 0
        self._rev_filled = 0  # how many samples have been written this cycle

        # Threshold for gate trigger
        self._trigger_thresh = 0.01

        # Pre-allocated gate envelope and wet buffers
        self._env_buf = np.empty(buffer_size, dtype=np.float32)
        self._gate_wet = np.empty((buffer_size, 2), dtype=np.float32)
        self._gate_buf_size = buffer_size
        self._levels_buf = np.empty(buffer_size, dtype=np.float32)
        # Pre-allocated reverb accumulation buffer (eliminates np.zeros per block)
        self._reverb_out = np.zeros((buffer_size, 2), dtype=np.float32)
        self._reverb_out_size = buffer_size

    def _ensure_gate_bufs(self, n):
        if n > self._gate_buf_size:
            self._gate_buf_size = n
            self._env_buf = np.empty(n, dtype=np.float32)
            self._gate_wet = np.empty((n, 2), dtype=np.float32)
            self._levels_buf = np.empty(n, dtype=np.float32)
        if n > self._reverb_out_size:
            self._reverb_out = np.zeros((n, 2), dtype=np.float32)
            self._reverb_out_size = n

    def process(self, data: np.ndarray) -> np.ndarray:
        st = self.state
        if not st.enabled:
            return data
        mix = max(0.0, min(1.0, st.mix))
        if mix < 0.001:
            return data

        n = len(data)
        stereo = data.ndim == 2 and data.shape[1] >= 2

        # Ensure stereo for internal processing
        if stereo:
            work = data
        else:
            work = np.column_stack((data, data))

        # Pre-delay
        pd_ms = st.pre_delay * 100.0  # 0–100 ms
        pd_samples = max(0, min(self._pd_size - 1, int(pd_ms * 0.001 * self.sr)))
        _circ_write(self._pd_buf, self._pd_idx, work, self._pd_size)
        rd = (self._pd_idx - pd_samples) % self._pd_size
        if n > self._pd_rd_size:
            self._pd_rd_buf = np.empty((n, 2), dtype=np.float32)
            self._pd_rd_size = n
        pre = _circ_read(self._pd_buf, rd, n, self._pd_size, out=self._pd_rd_buf)
        self._pd_idx = (self._pd_idx + n) % self._pd_size

        # Reverb: room controls feedback
        fb = 0.5 + st.room * 0.45  # 0.5–0.95
        damp = 0.3  # fixed moderate damping for punchy character
        for c in self._combs:
            c.set_feedback(fb)
            c.set_damp(damp)

        # Parallel combs → series allpasses
        self._ensure_gate_bufs(n)
        reverb_out = self._reverb_out[:n]
        reverb_out[:] = 0
        for c in self._combs:
            reverb_out += c.process(pre)
        reverb_out *= np.float32(1.0 / len(self._combs))
        for ap in self._allpasses:
            reverb_out = ap.process(reverb_out)

        # Gate duration in samples
        gate_dur_s = 0.05 + st.gate_time * 2.95  # 50 ms – 3 s
        gate_dur_samples = int(gate_dur_s * self.sr)
        slope = max(0.01, st.slope)

        if st.mode == 0:
            # GATE mode — apply time-triggered gate envelope
            wet = self._apply_gate(work, reverb_out, n, gate_dur_samples, slope, True)
        else:
            # REVERSE mode — collect reverb, play back reversed
            wet = self._apply_reverse(work, reverb_out, n, gate_dur_samples, True)

        if stereo:
            return data * np.float32(1.0 - mix) + wet * np.float32(mix)
        else:
            mono_wet = (wet[:, 0] + wet[:, 1]) * 0.5 if wet.ndim == 2 else wet
            return data * (1.0 - mix) + mono_wet * mix

    def _apply_gate(self, dry, reverb, n, gate_dur, slope, stereo):
        """Gate mode: let reverb through for gate_dur samples after trigger."""
        self._ensure_gate_bufs(n)
        # Pre-compute input levels vectorised
        levels = self._levels_buf[:n]
        if stereo:
            np.maximum(np.abs(dry[:n, 0]), np.abs(dry[:n, 1]), out=levels)
        else:
            np.abs(dry[:n].ravel(), out=levels)

        # Build envelope
        fade_samples = max(1, int(slope * gate_dur * 0.5))
        fade_dec = 1.0 / fade_samples
        env = self._env_buf[:n]
        thresh = self._trigger_thresh

        if _HAS_NUMBA:
            go, cd, ge = _gate_env_jit(
                levels, n, gate_dur, fade_dec, thresh,
                self._gate_open, self._gate_countdown, self._gate_env,
                env)
            self._gate_open = go
            self._gate_countdown = cd
            self._gate_env = ge
        else:
            gate_open = self._gate_open
            countdown = self._gate_countdown
            gate_env = self._gate_env
            for i in range(n):
                if levels[i] > thresh and not gate_open:
                    gate_open = True
                    countdown = gate_dur
                    gate_env = 1.0
                if gate_open:
                    countdown -= 1
                    if countdown <= 0:
                        gate_env -= fade_dec
                        if gate_env <= 0.0:
                            gate_env = 0.0
                            gate_open = False
                env[i] = gate_env
            self._gate_open = gate_open
            self._gate_countdown = countdown
            self._gate_env = gate_env

        # Vectorised multiply
        wet = self._gate_wet[:n]
        np.multiply(reverb[:n, 0], env, out=wet[:, 0])
        np.multiply(reverb[:n, 1], env, out=wet[:, 1])
        return wet

    def _apply_reverse(self, dry, reverb, n, gate_dur, stereo):
        """Reverse mode: accumulate reverb tail, play it backward."""
        wet = np.zeros((n, 2), dtype=np.float32)
        tail_len = min(gate_dur, self._rev_len)

        # Pre-compute input levels for trigger detection
        if stereo:
            levels = np.maximum(np.abs(dry[:n, 0]), np.abs(dry[:n, 1]))
        else:
            levels = np.abs(dry[:n].ravel())

        thresh = self._trigger_thresh
        i = 0
        while i < n:
            if self._rev_filled == 0:
                # Idle — find next trigger using vectorised search
                above = np.where(levels[i:n] > thresh)[0]
                if len(above) == 0:
                    break  # no trigger in rest of block
                i += int(above[0])
                self._rev_write = 0
                self._rev_read = 0
                self._rev_filled = 1

            if 0 < self._rev_filled <= tail_len:
                # Writing phase — bulk copy reverb into reverse buffer
                to_write = min(tail_len + 1 - self._rev_write, n - i)
                if to_write > 0:
                    rw = self._rev_write
                    self._rev_buf[rw:rw + to_write] = reverb[i:i + to_write]
                    self._rev_write = rw + to_write
                    self._rev_filled = self._rev_write
                    i += to_write
                continue

            if self._rev_filled > tail_len:
                # Reading phase — vectorised Hann window and reverse indexing
                to_read = min(tail_len - self._rev_read, n - i)
                if to_read > 0:
                    rr = self._rev_read
                    read_indices = (self._rev_write - 1 - (rr + np.arange(to_read))) % self._rev_len
                    t_vals = (rr + np.arange(to_read, dtype=np.float64)) / tail_len
                    windows = np.float32(0.5) - np.float32(0.5) * np.cos(
                        2.0 * math.pi * t_vals).astype(np.float32)
                    wet[i:i + to_read, 0] = self._rev_buf[read_indices, 0] * windows
                    wet[i:i + to_read, 1] = self._rev_buf[read_indices, 1] * windows
                    self._rev_read += to_read
                    i += to_read
                if self._rev_read >= tail_len:
                    self._rev_filled = 0
                    self._rev_read = 0
                continue

            i += 1  # safety fallback

        return wet

    def reset(self):
        for c in self._combs:
            c.reset()
        for ap in self._allpasses:
            ap.reset()
        self._pd_buf[:] = 0.0
        self._pd_idx = 0
        self._gate_open = False
        self._gate_countdown = 0
        self._gate_env = 1.0
        self._rev_buf[:] = 0.0
        self._rev_write = 0
        self._rev_read = 0
        self._rev_filled = 0


# ─── Stutter / Glitch Buffer Looper ─────────────────────────────────────────

class StutterProcessor:
    """Broken Record — stutter/glitch buffer looper.

    When triggered, captures a short slice of live audio into a fixed buffer
    and loops it on repeat.  SIZE controls capture length, RATE controls
    repeat speed (tempo-synced beat divisions), CHAOS adds random position
    jumps.  When not triggered, audio passes through untouched.
    """

    SYNC_DIVISIONS = [
        0.125,      # 1/32
        1.0 / 6,    # 1/16t (triplet)
        0.25,       # 1/16
        1.0 / 3,    # 1/8t (triplet)
        0.5,        # 1/8
        2.0 / 3,    # 1/4t (triplet)
        0.75,       # 3/16
        1.0,        # 1/4
        4.0 / 3,    # 1/3
        1.5,        # 3/8
        2.0,        # 1/2
        3.0,        # 3/4
        4.0,        # 1 bar
    ]

    def __init__(self, sample_rate: int = SAMPLE_RATE, buffer_size: int = 512):
        self.sr = sample_rate
        self.tempo = 120.0
        from mlrvp.config import StutterState
        self.state = StutterState()
        # Capture buffer — max ~2 seconds stereo
        max_samples = int(2.0 * sample_rate)
        self._buf = np.zeros((max_samples, 2), dtype=np.float32)
        self._buf_len = max_samples
        self._captured_len = 0    # actual captured length in samples
        self._read_pos = 0        # current playback position in captured buffer
        self._was_triggered = False  # edge detection for trigger
        self._filling = False         # True while still capturing into buffer
        self._fill_pos = 0            # next write position during capture
        self._target_cap = 0          # target capture length in samples
        self._rng = np.random.default_rng(77)
        # Crossfade state
        self._xfade_pos = 0
        self._xfade_len = 0
        # Pre-allocated fast-path sample buffers
        self._s0_buf = np.empty(buffer_size, dtype=np.float32)
        self._s1_buf = np.empty(buffer_size, dtype=np.float32)
        self._s_buf_size = buffer_size
        # Pre-allocated output buffer
        self._out_buf = np.empty((buffer_size, 2), dtype=np.float32)
        self._out_buf_size = buffer_size

    def set_tempo(self, tempo: float):
        self.tempo = max(20.0, min(300.0, tempo))

    def _sync_to_samples(self, idx: int) -> int:
        """Convert a sync division index to a sample count at current tempo."""
        idx = max(0, min(idx, len(self.SYNC_DIVISIONS) - 1))
        beats = self.SYNC_DIVISIONS[idx]
        sec = beats * 60.0 / self.tempo
        return max(1, min(int(sec * self.sr), self._buf_len))

    def process(self, data: np.ndarray) -> np.ndarray:
        st = self.state
        if not st.enabled:
            return data

        triggered = st.trigger
        mix = max(0.0, min(1.0, st.mix))

        # If not triggered, pass through
        if not triggered:
            self._was_triggered = False
            self._captured_len = 0
            self._filling = False
            self._fill_pos = 0
            return data

        n = len(data)
        stereo = data.ndim == 2 and data.shape[1] >= 2

        # Capture length from SIZE: log scale 10–2000 ms
        size_ms = 10.0 * (200.0 ** st.size)  # 10–2000 ms
        capture_len = min(int(size_ms * 0.001 * self.sr), self._buf_len)

        # Repeat rate from RATE: tempo-synced beat division
        repeat_len = self._sync_to_samples(int(st.rate))

        # On rising edge of trigger — start capturing audio
        if not self._was_triggered:
            self._was_triggered = True
            self._filling = True
            self._fill_pos = 0
            self._target_cap = capture_len
            self._captured_len = 0
            self._read_pos = 0

        # Continue filling capture buffer across multiple process() calls
        if self._filling:
            remaining = self._target_cap - self._fill_pos
            to_copy = min(remaining, n)
            if to_copy > 0:
                if stereo:
                    self._buf[self._fill_pos:self._fill_pos + to_copy, :] = data[:to_copy, :2]
                else:
                    self._buf[self._fill_pos:self._fill_pos + to_copy, 0] = data[:to_copy]
                    self._buf[self._fill_pos:self._fill_pos + to_copy, 1] = data[:to_copy]
                self._fill_pos += to_copy
            if self._fill_pos >= self._target_cap:
                self._captured_len = self._target_cap
                self._filling = False

        # If nothing captured yet, pass through
        if self._captured_len < 1:
            return data

        chaos = max(0.0, min(1.0, st.chaos))
        smear = max(0.001, min(1.0, st.smear))
        cap_len = self._captured_len
        xfade_samples = max(1, int(smear * min(repeat_len, cap_len) * 0.25))

        if n > self._out_buf_size:
            self._out_buf = np.empty((n, 2), dtype=np.float32)
            self._out_buf_size = n
        out = self._out_buf[:n]
        out[:] = data

        if chaos <= 0.01:
            # Vectorised path — no random jumps
            raw_pos = (self._read_pos + np.arange(n, dtype=np.int64)) % repeat_len
            positions = raw_pos % cap_len

            # Base reads from capture buffer (pre-allocated)
            if n > self._s_buf_size:
                self._s0_buf = np.empty(n, dtype=np.float32)
                self._s1_buf = np.empty(n, dtype=np.float32)
                self._s_buf_size = n
            s0 = self._s0_buf[:n]
            s1 = self._s1_buf[:n]
            np.take(self._buf[:, 0], positions, out=s0)
            np.take(self._buf[:, 1], positions, out=s1)

            # Crossfade at loop boundary
            dist_to_end = cap_len - positions
            need_xfade = dist_to_end <= xfade_samples
            if np.any(need_xfade):
                xf_idx = np.where(need_xfade)[0]
                t = dist_to_end[xf_idx].astype(np.float32) / xfade_samples
                wrap_pos = (xfade_samples - dist_to_end[xf_idx]) % cap_len
                s0[xf_idx] = self._buf[positions[xf_idx], 0] * t + self._buf[wrap_pos, 0] * (1.0 - t)
                s1[xf_idx] = self._buf[positions[xf_idx], 1] * t + self._buf[wrap_pos, 1] * (1.0 - t)

            if stereo:
                out[:, 0] = data[:, 0] * (1.0 - mix) + s0 * mix
                out[:, 1] = data[:, 1] * (1.0 - mix) + s1 * mix
            else:
                out[:] = data[:] * (1.0 - mix) + (s0 + s1) * 0.5 * mix

            self._read_pos = int((self._read_pos + n) % repeat_len)
        else:
            # Scalar path with chaos — per-sample RNG jumps required
            for i in range(n):
                if self._rng.random() < chaos * 0.01:
                    self._read_pos = int(self._rng.integers(0, cap_len))

                pos = self._read_pos % cap_len
                dist_to_end = cap_len - pos
                if dist_to_end <= xfade_samples:
                    t = dist_to_end / xfade_samples
                    wrap_pos = (xfade_samples - dist_to_end) % cap_len
                    s0 = self._buf[pos, 0] * t + self._buf[wrap_pos, 0] * (1.0 - t)
                    s1 = self._buf[pos, 1] * t + self._buf[wrap_pos, 1] * (1.0 - t)
                else:
                    s0 = self._buf[pos, 0]
                    s1 = self._buf[pos, 1]

                if stereo:
                    out[i, 0] = data[i, 0] * (1.0 - mix) + s0 * mix
                    out[i, 1] = data[i, 1] * (1.0 - mix) + s1 * mix
                else:
                    out[i] = data[i] * (1.0 - mix) + (s0 + s1) * 0.5 * mix

                self._read_pos += 1
                if self._read_pos >= repeat_len:
                    self._read_pos = 0

        return out

    def reset(self):
        self._buf[:] = 0.0
        self._captured_len = 0
        self._read_pos = 0
        self._was_triggered = False
        self._filling = False
        self._fill_pos = 0
        self._target_cap = 0


# ===================================================================
# External plugin host — "Foreign Exchange" (VST3 / AU)
# ===================================================================

import base64
import threading
from collections import deque

_HAS_PEDALBOARD = False
try:
    import pedalboard as _pb
    _HAS_PEDALBOARD = True
except ImportError:
    pass


class StowawayProcessor:
    """Host a single external VST3/AU plugin within the FX chain.

    - Zero-allocation passthrough when no plugin is loaded.
    - Thread-safe param updates via lock-free deque.
    - Wet/dry mix blend.
    """

    def __init__(self, sample_rate: int = 44100, buffer_size: int = 512):
        self._sr = sample_rate
        self._buf_size = buffer_size
        self._plugin = None          # pedalboard.Plugin | None
        self._loaded = False
        self._enabled = True
        self._wet_dry: float = 1.0   # 0 = fully dry, 1 = fully wet
        self._lock = threading.Lock()
        self._param_queue: deque = deque(maxlen=64)

        # Pre-allocated passthrough buffer (grow-only)
        self._passthru_buf = np.empty((buffer_size, 2), dtype=np.float32)
        # Pre-allocated wet buffer for mix blending
        self._wet_buf = np.empty((buffer_size, 2), dtype=np.float32)

    # ---- Loading / unloading (call from MAIN thread) ----

    _LOAD_TIMEOUT = 15.0  # seconds — passed to pedalboard initialization_timeout

    def load(self, path: str, fmt: str = "vst3") -> bool:
        """Load a plugin from disk.  Returns True on success.

        **Must be called from the main thread** so that the plugin's
        native GUI (``show_editor``) works afterwards.  Native Windows
        error-mode dialogs are suppressed via SetErrorMode.
        """
        if not _HAS_PEDALBOARD:
            return False

        from mlrvp.audio.plugin_scanner import suppress_plugin_dialogs, resolve_vst3_binary
        import logging
        _log = logging.getLogger(__name__)

        # Fully release the previous plugin before loading a new one.
        # Some VST3 plugins hold exclusive native resources that prevent
        # a second instance from initialising while the first is alive.
        self.unload()

        # Resolve bundle directories to their inner binary (pedalboard
        # cannot load a VST3 bundle directory, only the actual DLL).
        if fmt == "vst3":
            path = resolve_vst3_binary(path)

        try:
            with suppress_plugin_dialogs():
                plugin = _pb.load_plugin(
                    path, initialization_timeout=self._LOAD_TIMEOUT,
                )
        except Exception as exc:
            _log.error("StowawayProcessor.load failed for %s: %s", path, exc)
            return False

        with self._lock:
            self._plugin = plugin
            self._loaded = True
        return True

    def unload(self) -> None:
        """Remove the currently loaded plugin."""
        with self._lock:
            old = self._plugin
            self._plugin = None
            self._loaded = False
        if old is not None:
            try:
                old.reset()
            except Exception:
                pass
            # Force release native resources now instead of waiting for GC.
            try:
                del old
            except Exception:
                pass
        # Drain pending param changes
        self._param_queue.clear()

    # ---- DSP (call from DSP thread ONLY) ----

    def process(self, data: np.ndarray) -> np.ndarray:
        """Process an audio block through the hosted plugin."""
        if not self._loaded or not self._enabled:
            return data

        n = data.shape[0]
        plugin = self._plugin
        if plugin is None:
            return data

        # Drain queued param changes (lock-free reads from deque)
        while self._param_queue:
            try:
                name, value = self._param_queue.popleft()
                param = getattr(plugin, name, None)
                if param is not None:
                    setattr(plugin, name, value)
            except IndexError:
                break
            except Exception:
                pass

        # Process through pedalboard
        try:
            wet = plugin.process(
                data, sample_rate=self._sr, reset=False
            )
        except Exception:
            return data

        # Wet/dry blend
        mix = self._wet_dry
        if mix >= 0.999:
            return wet

        # Grow wet_buf if needed
        if self._wet_buf.shape[0] < n:
            self._wet_buf = np.empty((n, 2), dtype=np.float32)

        np.multiply(wet[:n], mix, out=self._wet_buf[:n])
        # Grow passthru_buf BEFORE the multiply to avoid wasted temporaries
        if self._passthru_buf.shape[0] < n:
            self._passthru_buf = np.empty((n, 2), dtype=np.float32)
        np.multiply(data[:n], 1.0 - mix, out=self._passthru_buf[:n])
        np.add(self._wet_buf[:n], self._passthru_buf[:n],
               out=self._wet_buf[:n])
        return self._wet_buf[:n]

    # ---- Parameter access (call from main/GUI thread) ----

    def set_param(self, name: str, value: float) -> None:
        """Queue a parameter change (applied at start of next process())."""
        self._param_queue.append((name, value))

    def get_params(self) -> dict[str, float]:
        """Read all plugin parameters.  Main thread only."""
        plugin = self._plugin
        if plugin is None:
            return {}
        result = {}
        try:
            for name in plugin.parameters.keys():
                param = getattr(plugin, name, None)
                if isinstance(param, (int, float)):
                    result[name] = float(param)
        except Exception:
            pass
        return result

    def get_param_names(self) -> list[str]:
        """Return ordered list of parameter names.  Main thread only."""
        plugin = self._plugin
        if plugin is None:
            return []
        try:
            return list(plugin.parameters.keys())
        except Exception:
            return []

    # ---- Editor (call from main thread ONLY) ----

    def show_editor(self, close_event=None) -> bool:
        """Open the plugin's native GUI editor.  Returns False if unavailable.

        *close_event* may be a ``threading.Event``; calling ``.set()``
        on it from another thread will close the editor window.
        ``show_editor`` **blocks** until the user (or the event) closes
        the window.  Must be called from the main thread.
        """
        plugin = self._plugin
        if plugin is None:
            return False
        if hasattr(plugin, "show_editor"):
            try:
                plugin.show_editor(close_event)
                return True
            except Exception as exc:
                import logging
                logging.getLogger(__name__).warning(
                    "show_editor failed: %s", exc,
                )
                return False
        return False

    # ---- State persistence ----

    def get_state_b64(self) -> str:
        """Return plugin state as a base64 string, or empty string."""
        plugin = self._plugin
        if plugin is None:
            return ""
        try:
            raw = bytes(plugin.raw_state)
            return base64.b64encode(raw).decode("ascii")
        except Exception:
            return ""

    def set_state_b64(self, state_b64: str) -> bool:
        """Restore plugin state from a base64 string."""
        plugin = self._plugin
        if plugin is None or not state_b64:
            return False
        try:
            raw = base64.b64decode(state_b64)
            plugin.raw_state = raw
            return True
        except Exception:
            return False

    def reset(self):
        """Reset the hosted plugin (if loaded)."""
        plugin = self._plugin
        if plugin is not None:
            try:
                plugin.reset()
            except Exception:
                pass
