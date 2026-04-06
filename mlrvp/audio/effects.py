"""DSP effects for mlrVp: delay, filters, modulation, etc."""

import math
import random
import numpy as np
from scipy.signal import lfilter
from mlrvp.config import (
    SAMPLE_RATE, DelayState, FilterState, FilterBState, TapeDelayState, ModState,
    ReelToReelState, CassetteTapeState, VHSState, VinylState, CompressorState,
    HallReverbState, PlateReverbState, GlueCompressorState, EtherDriftState,
)


class BiquadFilter:
    """Simple biquad filter for LP/HP in the delay feedback path."""

    def __init__(self, sample_rate: int = SAMPLE_RATE):
        self.sr = sample_rate
        self.b = np.array([1.0, 0.0, 0.0], dtype=np.float64)
        self.a = np.array([1.0, 0.0, 0.0], dtype=np.float64)
        # lfilter state for both channels at once: shape (2, 2) = (order, channels)
        self._zi = np.zeros((2, 2), dtype=np.float64)

    def set_lowpass(self, freq: float, q: float = 0.707):
        freq = max(20.0, min(freq, self.sr * 0.49))
        w0 = 2 * np.pi * freq / self.sr
        alpha = np.sin(w0) / (2 * q)
        cos_w0 = np.cos(w0)
        a0 = 1 + alpha
        self.b = np.array([(1 - cos_w0) / 2, 1 - cos_w0, (1 - cos_w0) / 2], dtype=np.float64) / a0
        self.a = np.array([1.0, (-2 * cos_w0) / a0, (1 - alpha) / a0], dtype=np.float64)

    def set_highpass(self, freq: float, q: float = 0.707):
        freq = max(20.0, min(freq, self.sr * 0.49))
        w0 = 2 * np.pi * freq / self.sr
        alpha = np.sin(w0) / (2 * q)
        cos_w0 = np.cos(w0)
        a0 = 1 + alpha
        self.b = np.array([(1 + cos_w0) / 2, -(1 + cos_w0), (1 + cos_w0) / 2], dtype=np.float64) / a0
        self.a = np.array([1.0, (-2 * cos_w0) / a0, (1 - alpha) / a0], dtype=np.float64)

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

    def process(self, data: np.ndarray) -> np.ndarray:
        """Process stereo data using scipy lfilter — both channels at once."""
        filtered, self._zi = lfilter(
            self.b, self.a, data.astype(np.float64), axis=0, zi=self._zi)
        return filtered.astype(np.float32)

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

        # Read delayed block from circular buffer
        read_start = (self.write_pos - delay_samples) % self.max_delay_samples
        if read_start + num_frames <= self.max_delay_samples:
            delayed = self.buffer[read_start:read_start + num_frames].copy()
        else:
            # Wrap around
            first = self.max_delay_samples - read_start
            delayed = np.concatenate([
                self.buffer[read_start:self.max_delay_samples],
                self.buffer[:num_frames - first]
            ])

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


class ChannelFilter:
    """Simple filter for per-channel use. Sweepable LP/HP."""

    def __init__(self, sample_rate: int = SAMPLE_RATE):
        self.sr = sample_rate
        self.lp = BiquadFilter(sample_rate)
        self.hp = BiquadFilter(sample_rate)
        self._filter_val = 0.0

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

    def process(self, data: np.ndarray) -> np.ndarray:
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
            out = np.tanh(out * drive_amt) / np.tanh(drive_amt)
        # Output gain (±12 dB)
        out_db = (self.state.output_gain - 0.5) * 24.0
        if abs(out_db) > 0.05:
            out = out * (10.0 ** (out_db / 20.0))
        return out


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

    def update_from_state(self, state: FilterBState):
        self.state = state

    def process(self, data: np.ndarray) -> np.ndarray:
        _tanh = math.tanh          # local alias — avoids attribute lookup per call
        n = len(data)
        if n != self._buf_size:
            self._buf_size = n
            self._out = np.empty((n, 2), dtype=np.float32)
        out = self._out
        cutoff = max(20.0, min(self.state.cutoff, self.sr * 0.49))
        # Scale resonance to [0, 3.8] — keeps headroom below self-oscillation
        resonance = self.state.resonance * 3.8
        drive = 1.0 + self.state.drive * 3.0
        do_drive = drive > 1.01

        # One-pole coefficient (bilinear approximation) — use math.exp/pi
        g = 1.0 - math.exp(-2.0 * math.pi * cutoff / self.sr)

        for ch in range(2):
            # Python list: O(1) indexed access without numpy __getitem__ overhead
            x_list = data[:, ch].tolist()
            y0 = float(self._y[0, ch])
            y1 = float(self._y[1, ch])
            y2 = float(self._y[2, ch])
            y3 = float(self._y[3, ch])
            fb = float(self._fb[ch])
            result = [0.0] * n

            # Seed the cached tanh values for the initial stage states.
            # Each ty_k is kept up-to-date at end of every iteration so the
            # next iteration's tanh(y_k) lookup is free — no redundant call.
            ty0 = _tanh(y0)
            ty1 = _tanh(y1)
            ty2 = _tanh(y2)
            ty3 = _tanh(y3)

            for i in range(n):
                xn = x_list[i]
                if do_drive:
                    xn = _tanh(xn * drive)
                xn -= resonance * fb

                # 4-pole ladder — 5 tanh calls per sample (down from 9)
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
            fb_signal = np.tanh(fb_signal * drive) / np.float32(max(math.tanh(drive), 0.01))

        # Write input + feedback to buffer
        write_data = input_data + fb_signal
        np.clip(write_data, -4.0, 4.0, out=write_data)
        self.buffer[write_positions] = write_data

        self.write_pos = (self.write_pos + n) % self.max_delay_samples

        output = input_data + delayed_sum * gain

        # Built-in spring reverb (allpass chain)
        spring_mix = self.state.spring_mix
        if spring_mix > 0.01:
            spring = output.copy()
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
        # Per-stage, per-channel allpass state: [[x_prev_L, x_prev_R], ...]
        self._ap_x = [[0.0, 0.0] for _ in range(stages)]
        self._ap_y = [[0.0, 0.0] for _ in range(stages)]
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

        rps = self.rate / self.sr          # LFO phase increment per sample
        phase = self._phase
        # Cap feedback — allpass gain is 1, loop gain > ~0.88 causes ringing
        feedback = min(abs(self.feedback), 0.88)
        stages = self.stages

        # Local references to avoid per-iteration attribute lookups
        ap_x = self._ap_x
        ap_y = self._ap_y
        fb0 = self._fb0
        fb1 = self._fb1
        _pi2 = math.pi * 2.0

        for i in range(n):
            # LFO → allpass coefficient c in (0.05, 0.95)
            lfo = math.sin(_pi2 * phase) * self.depth
            c = 0.5 + lfo * 0.4
            if c < 0.05:
                c = 0.05
            elif c > 0.95:
                c = 0.95
            mc = -c
            phase += rps

            # Left channel
            x0 = float(data[i, 0]) + fb0 * feedback
            for s in range(stages):
                xp = ap_x[s][0];  yp = ap_y[s][0]
                y = mc * x0 + xp + c * yp
                ap_x[s][0] = x0;  ap_y[s][0] = y
                x0 = y
            fb0 = x0

            # Right channel
            x1 = float(data[i, 1]) + fb1 * feedback
            for s in range(stages):
                xp = ap_x[s][1];  yp = ap_y[s][1]
                y = mc * x1 + xp + c * yp
                ap_x[s][1] = x1;  ap_y[s][1] = y
                x1 = y
            fb1 = x1

            out[i, 0] = float(data[i, 0]) * 0.5 + x0 * 0.5
            out[i, 1] = float(data[i, 1]) * 0.5 + x1 * 0.5

        if phase >= 1.0:
            phase -= int(phase)
        self._phase = phase
        self._fb0 = fb0
        self._fb1 = fb1
        return out


class Chorus:
    """Simple chorus effect using modulated delay (block-vectorised)."""

    def __init__(self, sample_rate: int = SAMPLE_RATE, buffer_size: int = 512):
        self.sr = sample_rate
        self.rate = 1.0
        self.depth = 0.5
        self.mix = 0.5
        self._phase = 0.0
        max_delay = int(0.03 * sample_rate)  # 30ms max
        self._buf = np.zeros((max_delay + 1, 2), dtype=np.float32)
        self._buf_len = max_delay + 1
        self._write_pos = 0
        # Pre-allocated index arrays (reused every callback)
        self._idx_f64 = np.arange(buffer_size, dtype=np.float64)
        self._idx_intp = np.arange(buffer_size, dtype=np.intp)

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
        d0 = np.clip(delay_f.astype(np.intp), 1, self._buf_len - 2)
        frac = (delay_f - d0).astype(np.float32)[:, np.newaxis]  # (n, 1)

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
        d0   = np.clip(delay_f.astype(np.intp), 1, self._buf_len - 2)
        frac = (delay_f - d0).astype(np.float32)[:, np.newaxis]   # (n, 1) for stereo

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

    def process(self, data: np.ndarray) -> np.ndarray:
        st = self.state
        if not st.enabled:
            return data
        mix = st.mix
        if mix < 0.001:
            return data

        n = len(data)
        wet = data.copy()

        # --- Sample-rate reduction (zero-order hold) ---
        # reduction=0 → passthrough; reduction=1 → hold every ~32 samples
        hold_len = max(1, int(1.0 + st.sample_rate_reduction * 31))
        if hold_len > 1:
            # Vectorized ZOH: repeat every hold_len-th sample across the block
            # Build a selector index: [0,0,...,hold_len,hold_len,...,2*hold_len,...]
            idx = self._idx_int[:n] if n <= len(self._idx_int) else np.arange(n, dtype=np.intp)
            selector = idx // hold_len * hold_len
            selector = np.clip(selector, 0, n - 1)
            wet = wet[selector]

        # --- Bit depth reduction ---
        bits = max(1, min(int(st.bit_depth), 32))
        if bits < 32:
            levels = float(2 ** (bits - 1))
            wet = np.round(wet * levels) / levels
            np.clip(wet, -1.0, 1.0, out=wet)

        return data * np.float32(1.0 - mix) + wet * np.float32(mix)

    def reset(self):
        pass


# ---------------------------------------------------------------------------
#  Tube Overdrive (Hot Snake)
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

    def process(self, data: np.ndarray) -> np.ndarray:
        st = self.state
        if not st.enabled:
            return data
        mix = st.mix
        if mix < 0.001:
            return data

        # --- Input drive ---
        drive = np.float32(1.0 + st.drive * 8.0)  # 1x to 9x pre-gain
        wet = data * drive

        # --- Bias offset (asymmetric → even harmonics) ---
        bias_offset = np.float32((st.tube_bias - 0.5) * 0.15)  # ±0.075 offset
        wet = wet + bias_offset

        # --- Polynomial soft clip: tanh-like but slightly asymmetric ---
        # x / (1 + |x|) gives smooth saturation with gentle knee
        wet = wet / (np.float32(1.0) + np.abs(wet))
        wet = wet - np.float32(bias_offset * 0.5)   # partial bias compensation to re-center

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

    def process(self, data: np.ndarray) -> np.ndarray:
        if not self.state.enabled:
            return data
        n = len(data)
        mix = self.state.mix
        if mix < 0.001:
            return data

        wet = data.copy()

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
            rd_floor = np.floor(read_pos).astype(np.int64)
            rd_frac = (read_pos - rd_floor).astype(np.float32)
            rd_i = rd_floor % buf_len
            rd_j = (rd_floor + 1) % buf_len
            wet = buf[rd_i] * (1.0 - rd_frac[:, np.newaxis]) + buf[rd_j] * rd_frac[:, np.newaxis]
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
            wet = np.tanh(wet * drive) / np.float32(math.tanh(drive))

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

    def process(self, data: np.ndarray) -> np.ndarray:
        if not self.state.enabled:
            return data
        n = len(data)
        mix = self.state.mix
        if mix < 0.001:
            return data

        wet = data.copy()

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
            rd_floor = np.floor(read_pos).astype(np.int64)
            rd_frac = (read_pos - rd_floor).astype(np.float32)
            rd_i = rd_floor % buf_len
            rd_j = (rd_floor + 1) % buf_len
            wet = buf[rd_i] * (1.0 - rd_frac[:, np.newaxis]) + buf[rd_j] * rd_frac[:, np.newaxis]
            self._warp_phase = math.fmod(self._warp_phase + n / self.sr, 1000.0)
        self._buf_pos = (wp + n) % buf_len

        # Saturation: cassette tape has more aggressive midrange saturation
        sat = self.state.saturation
        if sat > 0.01:
            drive = 1.0 + sat * 5.0
            wet = np.tanh(wet * drive) / np.float32(math.tanh(drive))

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
            wet = np.tanh(wet * punch_drive) / np.float32(math.tanh(punch_drive))

        # Hiss: additive tape hiss (band-limited noise) — gated on signal presence
        hiss = self.state.hiss
        has_signal = max(abs(float(data[0, 0])), abs(float(data[n >> 1, 0])), abs(float(data[-1, 0]))) > 1e-5
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
            inp = data.copy()
            inp[:, 0] += fb_sig_0 * fb_scale * decay
            inp[:, 1] += fb_sig_1 * fb_scale * decay
        else:
            inp = data  # no copy needed; astype below creates a new array

        # Process all stages with both channels at once (axis=0) to avoid
        # per-channel astype round-trips.  Keep data in float64 for the
        # full allpass chain, then convert back once at the end.
        out_f64 = inp.astype(np.float64)
        for stage in range(self.N_STAGES):
            # Stagger stages logarithmically for a richer phaser comb
            f_stage = min(base_freq * math.exp(stage * 0.55), self.sr * 0.44)
            b, a = self._ap2_coeffs(f_stage, self.sr)
            out_f64, self._zi[chain_idx, stage] = lfilter(
                b, a, out_f64, axis=0, zi=self._zi[chain_idx, stage])

        out = out_f64.astype(np.float32)

        # Store last sample for next-block feedback; guard against NaN/Inf
        last = out[-1]
        if not np.all(np.isfinite(last)):
            last = np.zeros(2, dtype=np.float32)
            self._zi[chain_idx] = 0.0  # reset chain state on blow-up
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

            wet = np.empty_like(data)
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
        # Cache last worn value to avoid rebuilding filter every block
        self._last_worn: float = -1.0

    def process(self, data: np.ndarray) -> np.ndarray:
        if not self.state.enabled:
            return data
        n = len(data)
        mix = self.state.mix
        if mix < 0.001:
            return data

        wet = data.copy()

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
            rd_floor = np.floor(read_pos).astype(np.int64)
            rd_frac = (read_pos - rd_floor).astype(np.float32)
            rd_i = rd_floor % buf_len
            rd_j = (rd_floor + 1) % buf_len
            wet = buf[rd_i] * (1.0 - rd_frac[:, np.newaxis]) + buf[rd_j] * rd_frac[:, np.newaxis]
            self._wobble_phase = math.fmod(self._wobble_phase + n / self.sr, 1000.0)
        self._buf_pos = (wp + n) % buf_len

        # Dropout: intermittent signal drops (simulated per-block)
        dropout = self.state.dropout
        if dropout > 0.01:
            self._dropout_counter += 1
            # Stochastic dropout: higher values = more frequent and deeper
            if random.random() < dropout * 0.06:
                self._dropout_gate = dropout * 0.02 + random.random() * 0.2
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
        # Cache last filter param values
        self._last_wear: float = -1.0
        self._last_warmth_freq: float = -1.0

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
        has_signal = max(abs(float(data[0, 0])), abs(float(data[n >> 1, 0])), abs(float(data[-1, 0]))) > 1e-5

        wet = data.copy()

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
            rd_floor = np.floor(read_pos).astype(np.int64)
            rd_frac = (read_pos - rd_floor).astype(np.float32)
            rd_i = rd_floor % buf_len
            rd_j = (rd_floor + 1) % buf_len
            wet = buf[rd_i] * (1.0 - rd_frac[:, np.newaxis]) + buf[rd_j] * rd_frac[:, np.newaxis]
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
            wet = np.tanh(wet * drive) / np.float32(math.tanh(drive))

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
                    pop_pos = min(n - 1, max(0, int(random.random() * n)))
                    pop_amp = 0.08 + pops * 0.25
                    pop_sign = 1.0 if random.random() > 0.5 else -1.0
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
                    self.sr * avg_gap * (0.5 + random.random())
                )

        return data * (1.0 - mix) + wet * mix

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
        # Gain reduction is computed purely multiplicatively (no log in hot path):
        #   gain_lin = (thresh_lin / env) ^ (1 - 1/ratio)   when env > thresh
        gain = self._gain
        gain[:n] = 1.0
        env = self._env
        gr_sum = 0.0
        _thresh = thresh_lin

        for i in range(n):
            level = float(sc_mono[i])
            # Envelope follower
            if level > env:
                env = alpha_a * env + (1.0 - alpha_a) * level
            else:
                env = alpha_r * env + (1.0 - alpha_r) * level

            # Gain computer — multiplicative form, no repeated log10 calls
            if env > _thresh:
                g = (_thresh / env) ** (1.0 - 1.0 / ratio)
                gr_sum += math.log10(max(g, 1e-10)) * 20.0
                gain[i] = float(g)

        self._env = env
        self.gr_db = gr_sum / max(n, 1)

        # Apply gain reduction + makeup
        return data * gain[:n, np.newaxis] * np.float32(makeup_lin)


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
        env = self._env
        gr_sum = 0.0

        for i in range(n):
            level = float(sc_mono[i])

            if level > env:
                env = alpha_a * env + (1.0 - alpha_a) * level
            else:
                if auto_release:
                    # Program-dependent: faster release when heavily compressing
                    # thresh_lin >= 10^(-30/20) >> 1e-10, no need to guard
                    gr_ratio = env / thresh_lin
                    auto_rel_ms = max(50.0, 600.0 / max(gr_ratio, 1.0))
                    alpha_r_auto = math.exp(-1.0 / (auto_rel_ms * _sr_ms))
                    env = alpha_r_auto * env + (1.0 - alpha_r_auto) * level
                else:
                    env = alpha_r * env + (1.0 - alpha_r) * level

            # Soft-knee gain computer (no log in hot path)
            if env <= knee_lower_lin:
                g = 1.0
            elif env >= knee_upper_lin:
                g = (thresh_lin / env) ** (1.0 - inv_ratio)
            else:
                t = (env - knee_lower_lin) / knee_range_lin
                full_g = (thresh_lin / env) ** (1.0 - inv_ratio)
                g = 1.0 + (full_g - 1.0) * t * t   # quadratic knee

            gain[i] = float(g)
            # Only call log10 when actual gain reduction is present
            if g < 1.0:
                gr_sum += math.log10(g) * 20.0

        self._env = env
        self.gr_db = gr_sum / max(n, 1)

        return data * gain[:n, np.newaxis] * np.float32(makeup_lin)


# ------------------------------------------------------------------ #
# Hall / Room / Chamber Reverb                                        #
# ------------------------------------------------------------------ #

def _circ_read(buf, idx, n, sz):
    """Block-read *n* stereo samples from a circular buffer."""
    end = idx + n
    if end <= sz:
        return buf[idx:end].copy()
    split = sz - idx
    out = np.empty((n, 2), dtype=np.float32)
    out[:split] = buf[idx:sz]
    out[split:] = buf[:end - sz]
    return out


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


class _CombFilter:
    """Feedback comb filter with one-pole damping — block-processed.

    Uses scipy lfilter (C) for the IIR damping and numpy slicing for
    circular-buffer I/O.  Zero Python per-sample loops.
    """
    __slots__ = ('_buf', '_size', '_idx', '_fb', '_damp_zi', '_b', '_a')

    def __init__(self, delay_samples: int):
        self._size = delay_samples
        self._buf = np.zeros((delay_samples, 2), dtype=np.float32)
        self._idx = 0
        self._fb = np.float32(0.5)
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
            delayed = _circ_read(self._buf, idx, n, sz)
            damped, self._damp_zi = lfilter(
                self._b, self._a, delayed, axis=0, zi=self._damp_zi)
            _circ_write(self._buf, idx,
                        inp + damped * self._fb, sz)
            self._idx = (idx + n) % sz
            return delayed

        # Rare fallback: delay < buffer — process in delay-sized chunks
        out = np.empty_like(inp)
        pos = 0
        while pos < n:
            cn = min(sz, n - pos)
            delayed = _circ_read(self._buf, idx, cn, sz)
            damped, self._damp_zi = lfilter(
                self._b, self._a, delayed, axis=0, zi=self._damp_zi)
            _circ_write(self._buf, idx,
                        inp[pos:pos + cn] + damped * self._fb, sz)
            out[pos:pos + cn] = delayed
            idx = (idx + cn) % sz
            pos += cn
        self._idx = idx
        return out


class _AllpassFilter:
    """Schroeder allpass — block-processed in delay-sized chunks.

    For delays >= buffer size a single numpy pass is used; otherwise
    the buffer is split into delay-sized chunks (typically 1-3 iterations
    instead of 512 per-sample iterations).
    """
    __slots__ = ('_buf', '_size', '_idx', '_fb')

    def __init__(self, delay_samples: int, feedback: float = 0.5):
        self._size = delay_samples
        self._buf = np.zeros((delay_samples, 2), dtype=np.float32)
        self._idx = 0
        self._fb = np.float32(feedback)

    def process(self, inp: np.ndarray) -> np.ndarray:
        n = len(inp)
        sz = self._size
        fb = self._fb
        idx = self._idx

        if sz >= n:
            # Single-block fast path
            delayed = _circ_read(self._buf, idx, n, sz)
            _circ_write(self._buf, idx, inp + delayed * fb, sz)
            self._idx = (idx + n) % sz
            return delayed - inp * fb

        # Chunked path for small delays
        out = np.empty_like(inp)
        pos = 0
        while pos < n:
            cn = min(sz, n - pos)
            delayed = _circ_read(self._buf, idx, cn, sz)
            inp_c = inp[pos:pos + cn]
            _circ_write(self._buf, idx, inp_c + delayed * fb, sz)
            out[pos:pos + cn] = delayed - inp_c * fb
            idx = (idx + cn) % sz
            pos += cn
        self._idx = idx
        return out


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
        pre = _circ_read(self._pd_buf, rd, n, self._pd_size)
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
        if n != self._wet_size:
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
        pre = _circ_read(buf, rd_start, n, pd_sz)
        self._pd_idx = (idx + n) % pd_sz

        # Set comb parameters
        fb = 0.4 + st.decay * 0.55  # 0.4 – 0.95
        damp = st.damping * 0.7     # higher damping = darker
        for c in self._combs:
            c.set_feedback(fb)
            c.set_damp(damp)

        # Parallel combs
        if n != self._wet_size:
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
        return data * np.float32(1.0 - mix) + wet * np.float32(mix)
