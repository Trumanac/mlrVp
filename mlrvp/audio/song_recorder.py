"""Song Recorder — stereo master-output loopback recorder with tape-speed pitch control.

Records the post-master DSP output (loopback mode always on) into a growable buffer,
applies real-time soft-clip limiting, and on finalise resamples to emulate tape varispeed:

    tape_speed = 1.0  →  unity (no pitch change)
    tape_speed < 1.0  →  recorded slow  →  plays back faster  →  pitch UP
    tape_speed > 1.0  →  recorded fast  →  plays back slower  →  pitch DOWN

    formula: out_sr = int(in_sr * tape_speed)
    resampy.resample(data, in_sr, out_sr)  writes out_sr frames per second of audio,
    which a player reading at in_sr will play back at tape_speed rate.

The finalised SampleBuffer is available via `.result` for in-panel playback and export.
Playback is driven by a QTimer on the GUI thread feeding a sounddevice OutputStream.
"""

from __future__ import annotations

import logging
import threading
from datetime import datetime
from typing import Callable, Optional

import numpy as np
import sounddevice as sd

from mlrvp.audio.sample import SampleBuffer
from mlrvp.config import SAMPLE_RATE

log = logging.getLogger(__name__)

# Hard cap: 60 minutes stereo float32 @ 44100 ≈ 1.2 GB — refuse to allocate beyond this
MAX_FREE_SECONDS = 3600.0
_CHUNK_FRAMES = 4096          # free-length chunk size
LIMITER_THRESHOLD = 0.891     # −1 dBFS soft-clip knee


class SongRecorder:
    """Stereo master-output recorder with varispeed tape emulation.

    Thread-safety: process() runs on the audio worker thread.
    All other methods are called from the GUI/app thread and are lock-protected.
    """

    def __init__(self, sample_rate: int = SAMPLE_RATE):
        self.sr = sample_rate
        self._lock = threading.Lock()

        # State flags
        self._armed: bool = False
        self._recording: bool = False

        # Recording buffer (growable chunks)
        self._chunks: list[np.ndarray] = []
        self._total_frames: int = 0
        self._punch_in_frame: int = 0   # frame index where current recording started

        # Config
        self.tape_speed: float = 1.0
        self.auto_level: bool = True
        self.loop: bool = False

        # Result
        self.result: Optional[SampleBuffer] = None

        # Metering (polled by GUI)
        self._peak_l: float = 0.0
        self._peak_r: float = 0.0
        # Playback metering (separate so GUI can show output level during playback)
        self._pb_peak_l: float = 0.0
        self._pb_peak_r: float = 0.0

        # Completion callback (fired on audio thread)
        self._complete_cb: Optional[Callable[[], None]] = None

        # Playback
        self._pb_stream: Optional[sd.OutputStream] = None
        self._pb_fpos: float = 0.0   # fractional frame position for vari-speed
        self._pb_volume: float = 1.0  # 0.0–1.0 playback volume scalar
        self._scrubbing: bool = False      # True while waveform is being dragged
        self._scrub_target_fpos: float = -1.0  # latest drag target (frame index)
        self._ff_rw_speed: float = 0.0            # target speed from FF/RW (0 = not active)
        self._transport_smooth_speed: float = 1.0  # EMA-smoothed playback step
        self._pb_playing: bool = False
        self._pb_stream_alive: bool = False  # True only while callback is actively firing
        self._pb_stop_pending: bool = False  # True = fade out this block then CallbackStop
        self._pb_lock = threading.Lock()

    # ------------------------------------------------------------------ control

    def arm(self) -> None:
        """Arm: ready to start on next trigger.  Does NOT clear existing recording."""
        with self._lock:
            self._recording = False
            self._armed = True

    def start(self) -> None:
        """Begin recording from current tape position (must be armed first)."""
        with self._lock:
            if self._armed:
                self._armed = False
                self._punch_in_frame = int(self._pb_fpos)
                self._chunks = []
                self._total_frames = 0
                self._recording = True

    def start_recording_now(self) -> None:
        """Immediate record from current tape position without arming."""
        with self._lock:
            self._punch_in_frame = int(self._pb_fpos)
            self._chunks = []
            self._total_frames = 0
            self._armed = False
            self._recording = True

    def stop(self) -> Optional[SampleBuffer]:
        """Stop, splice new audio into existing tape, return updated SampleBuffer."""
        with self._lock:
            if not self._recording and not self._armed:
                return self.result
            self._recording = False
            self._armed = False
            chunks = list(self._chunks)
            total = self._total_frames
            punch_in = self._punch_in_frame
            existing = self.result

        if total <= 0 or not chunks:
            return existing or self.result

        new_audio = np.concatenate(chunks, axis=0)

        # Tape-speed pitch is already baked per-block during process().
        # Splice: keep everything before punch-in, then attach new audio.
        if existing is not None and existing.num_frames > 0 and punch_in > 0:
            pre = existing.data[:punch_in]
            raw = np.concatenate([pre, new_audio], axis=0)
        else:
            raw = new_audio

        buf = SampleBuffer(
            data=raw,
            sample_rate=self.sr,
            num_frames=int(raw.shape[0]),
            num_channels=int(raw.shape[1]),
            duration_sec=float(raw.shape[0]) / float(self.sr),
        )
        with self._lock:
            self.result = buf
        return buf

    def cancel(self) -> None:
        with self._lock:
            self._recording = False
            self._armed = False
            self._chunks = []
            self._total_frames = 0
            self._punch_in_frame = 0

    def clear(self) -> None:
        """Discard the current recording and result; stop playback."""
        self.stop_playback()
        with self._lock:
            self._recording = False
            self._armed = False
            self._chunks = []
            self._total_frames = 0
            self._punch_in_frame = 0
            self.result = None
            self._pb_peak_l = 0.0
            self._pb_peak_r = 0.0

    def set_complete_callback(self, cb: Optional[Callable[[], None]]) -> None:
        with self._lock:
            self._complete_cb = cb

    # ------------------------------------------------------------------ process (audio thread)

    def process(self, input_data: np.ndarray) -> None:
        """Called from the DSP thread with the post-master stereo output block.

        Applies soft-clip limiting (if auto_level) then writes to the rolling buffer.
        Metering is updated every block.
        """
        with self._lock:
            recording = self._recording

        n = len(input_data)
        if n == 0:
            return

        # Metering — always update even if not recording, so VU shows signal level.
        # Use nan-safe max so a transient NaN from upstream doesn't permanently
        # corrupt _peak_l/_peak_r (matches the pattern used by engine.py and mixer.py).
        if input_data.ndim == 2 and input_data.shape[1] >= 2:
            _pl = float(np.nanmax(np.abs(input_data[:, 0])))
            _pr = float(np.nanmax(np.abs(input_data[:, 1])))
        else:
            _pl = _pr = float(np.nanmax(np.abs(input_data)))
        if np.isfinite(_pl):
            self._peak_l = max(_pl, self._peak_l * 0.92)
        if np.isfinite(_pr):
            self._peak_r = max(_pr, self._peak_r * 0.92)

        if not recording:
            return

        # Make a copy so we don't mutate the engine's output buffer
        block = input_data.copy()

        # Real-time soft-clip limiter: x / (1 + |x|) applied above threshold
        if self.auto_level:
            abs_block = np.abs(block)
            mask = abs_block > LIMITER_THRESHOLD
            if mask.any():
                block[mask] = block[mask] / (1.0 + abs_block[mask])

        # Guard against NaN/Inf from upstream
        np.nan_to_num(block, copy=False, nan=0.0, posinf=0.0, neginf=0.0)

        # Tape-speed record resampling — applied live so knob changes during
        # recording are captured.  speed>1 stretches (stores more frames →
        # slower/lower pitch on 1x playback); speed<1 decimates (stores fewer
        # frames → faster/higher pitch on 1x playback).
        speed = max(0.01, float(self.tape_speed))
        if abs(speed - 1.0) > 0.001:
            n_out = max(1, round(n * speed))
            t_in = np.arange(n, dtype=np.float64)
            t_out = np.linspace(0.0, float(n - 1), n_out)
            l_ch = np.interp(t_out, t_in, block[:, 0])
            r_ch = np.interp(t_out, t_in, block[:, 1])
            block = np.column_stack([l_ch, r_ch]).astype(np.float32)
            n = n_out

        # Accumulate chunk under lock so stop() snapshot is always consistent.
        # The lock is released quickly (list.append + int add); no blocking I/O.
        cb = None
        with self._lock:
            if not self._recording:
                return  # stop() raced and cleared the flag — discard this block
            self._chunks.append(block)
            self._total_frames += n
            # Hard cap: stop recording if we exceed the maximum
            if self._total_frames >= int(MAX_FREE_SECONDS * self.sr):
                self._recording = False
                cb = self._complete_cb
        if cb is not None:
            cb()

    # ------------------------------------------------------------------ properties

    @property
    def armed(self) -> bool:
        return self._armed

    @property
    def recording(self) -> bool:
        return self._recording

    @property
    def armed_or_recording(self) -> bool:
        return self._armed or self._recording

    @property
    def input_peak_l(self) -> float:
        return self._peak_l

    @property
    def input_peak_r(self) -> float:
        return self._peak_r

    @property
    def elapsed_seconds(self) -> float:
        return self._total_frames / float(self.sr)

    @property
    def playback_peak_l(self) -> float:
        return self._pb_peak_l

    @property
    def playback_peak_r(self) -> float:
        return self._pb_peak_r

    @property
    def total_duration_sec(self) -> float:
        """Total length of the finalised recording (or current rec length if recording)."""
        if self.result is not None:
            if self._recording:
                # Punch-in: full length = pre-punch frames + new frames recorded so far
                return float(self._punch_in_frame + self._total_frames) / float(self.sr)
            return float(self.result.num_frames) / float(self.sr)
        return self.elapsed_seconds

    @property
    def playback_frames(self) -> int:
        """Current playback position in frames."""
        with self._pb_lock:
            return int(self._pb_fpos)

    @property
    def playback_seconds(self) -> float:
        return self.playback_frames / float(self.sr)

    # ------------------------------------------------------------------ waveform peaks

    def get_peaks(self, num_bins: int) -> Optional[np.ndarray]:
        """Return a downsampled mono peak envelope for waveform display.

        Pulls from `result.data` when finalised.  During an active recording
        (including punch-in over existing tape) it splices the pre-punch
        portion of the last result with the in-flight chunks so the waveform
        stays stable and grows smoothly from the punch point.
        Returns shape (num_bins,) of |max| per bin, or None.
        """
        if num_bins <= 0:
            return None

        with self._lock:
            recording = self._recording
            chunks_snapshot = list(self._chunks) if recording else None
            punch_in = self._punch_in_frame
            result_snap = self.result

        if recording:
            # Build the live composite: existing tape up to punch-in + new audio
            parts = []
            if result_snap is not None and punch_in > 0:
                parts.append(result_snap.data[:punch_in])
            if chunks_snapshot:
                new_audio = np.concatenate(chunks_snapshot, axis=0)
                parts.append(new_audio)
            if not parts:
                return None
            data = np.concatenate(parts, axis=0) if len(parts) > 1 else parts[0]
        elif result_snap is not None and result_snap.num_frames > 0:
            data = result_snap.data
        else:
            return None

        if data.shape[0] == 0:
            return None
        mono = data.mean(axis=1) if data.ndim == 2 else data
        n = mono.shape[0]
        bin_size = max(1, n // num_bins)
        usable = (n // bin_size) * bin_size
        if usable == 0:
            return np.array([float(np.abs(mono).max())], dtype=np.float32)
        peaks = np.abs(mono[:usable]).reshape(-1, bin_size).max(axis=1)
        return peaks.astype(np.float32)

    # ------------------------------------------------------------------ playback

    def play_pause(self) -> None:
        """Toggle playback of the finalised recording."""
        if self.result is None:
            return
        if self._pb_playing:
            self._pb_pause()
        else:
            self._pb_play()

    def rewind(self, seconds: float = 5.0) -> None:
        with self._pb_lock:
            self._pb_fpos = max(0.0, self._pb_fpos - seconds * self.sr)

    def fast_forward(self, seconds: float = 5.0) -> None:
        if self.result is None:
            return
        with self._pb_lock:
            self._pb_fpos = min(
                float(self.result.num_frames),
                self._pb_fpos + seconds * self.sr,
            )

    def seek(self, position: float) -> None:
        """Seek to position 0.0–1.0 within the recording."""
        if self.result is None:
            return
        with self._pb_lock:
            self._pb_fpos = max(0.0, min(1.0, position)) * self.result.num_frames

    def set_ff_rw_speed(self, speed: float) -> None:
        """Set FF/RW transport target speed (0.0 = normal play, positive = FF, negative = RW).

        Safe to call from any thread — atomic float write under CPython GIL.
        """
        self._ff_rw_speed = float(speed)

    def set_scrubbing(self, active: bool) -> None:
        """Enable / disable waveform-drag scrub mode.

        Safe to call from any thread — atomic bool write under CPython GIL.
        """
        self._scrubbing = bool(active)

    @property
    def scrubbing(self) -> bool:
        return self._scrubbing

    def set_pb_volume(self, volume: float) -> None:
        """Set playback volume scalar (0.0–1.0)."""
        self._pb_volume = max(0.0, min(1.0, float(volume)))

    def stop_playback(self) -> None:
        self._pb_pause()
        self._pb_stream_alive = False
        with self._pb_lock:
            self._pb_fpos = 0.0
        self._pb_peak_l = 0.0
        self._pb_peak_r = 0.0

    @property
    def playback_position(self) -> float:
        """Current playback position as a fraction 0.0–1.0."""
        if self.result is None or self.result.num_frames == 0:
            return 0.0
        with self._pb_lock:
            return self._pb_fpos / self.result.num_frames

    @property
    def is_playing(self) -> bool:
        return self._pb_playing

    def _pb_play(self) -> None:
        if self.result is None:
            return
        # Always clear any pending stop — we want to play.
        self._pb_stop_pending = False
        self._pb_playing = True
        # If the stream callback is actively firing, it will see _pb_playing=True
        # and continue — no need to rebuild.
        if self._pb_stream_alive:
            return
        # Stream is not alive — build or rebuild it.
        self._transport_smooth_speed = float(self.tape_speed)
        self._ff_rw_speed = 0.0
        buf = self.result.data      # shape (N, 2)
        n_frames = buf.shape[0]
        recorder = self             # closure ref so callback reads tape_speed live

        def _callback(outdata: np.ndarray, frames: int, _t, _status):
            with recorder._pb_lock:
                # Clamp fpos to [0, n_frames-1] so indices[0] is always in-range.
                fpos = max(0.0, min(float(n_frames - 1), recorder._pb_fpos))

            if recorder._scrubbing:
                target_step = recorder._ff_rw_speed
            else:
                ff = recorder._ff_rw_speed
                target_step = ff if ff != 0.0 else float(recorder.tape_speed)

            old_s = recorder._transport_smooth_speed
            new_s = old_s + (target_step - old_s) * 0.15   # EMA α=0.15 ≈ 300ms ramp
            recorder._transport_smooth_speed = new_s

            if abs(new_s) < 0.02:  # tape momentarily stopped
                outdata[:] = 0.0
                # Still consume stop-pending even at low speed.
                if recorder._pb_stop_pending:
                    recorder._pb_playing = False
                    recorder._pb_stop_pending = False
                    recorder._pb_stream_alive = False
                    raise sd.CallbackStop()
                return

            step = new_s

            # Fractional source indices for each output frame
            indices = fpos + np.arange(frames, dtype=np.float64) * step

            # Valid frames stay within [0, n_frames) — works for both directions.
            # upper bound is n_frames (not n_frames-1) so the last sample is
            # reachable; the np.clip below keeps int_i within [0, n_frames-2],
            # so next_i = int_i+1 <= n_frames-1 remains in-bounds.
            valid_mask = (indices >= 0) & (indices < n_frames)
            n_valid = int(valid_mask.sum())

            oc = min(2, outdata.shape[1])
            if n_valid > 0:
                vi = indices[:n_valid]
                # Defensive clip: guard against floating-point edge cases.
                int_i = np.clip(vi.astype(np.int64), 0, n_frames - 2)
                frac = (vi - int_i)[:, np.newaxis]          # (n_valid, 1)
                next_i = int_i + 1  # safe: int_i <= n_frames-2
                outdata[:n_valid, :oc] = (
                    buf[int_i, :oc] * (1.0 - frac) +
                    buf[next_i,  :oc] * frac
                ).astype(np.float32)
                outdata[:n_valid, :oc] *= recorder._pb_volume
            if n_valid < frames:
                outdata[n_valid:] = 0.0

            # Playback metering
            if n_valid > 0:
                pl = float(np.abs(outdata[:n_valid, 0]).max())
                pr = float(np.abs(outdata[:n_valid, 1]).max()) if oc >= 2 else pl
                recorder._pb_peak_l = max(pl, recorder._pb_peak_l * 0.85)
                recorder._pb_peak_r = max(pr, recorder._pb_peak_r * 0.85)

            with recorder._pb_lock:
                new_fpos = fpos + n_valid * step
                recorder._pb_fpos = max(0.0, min(float(n_frames), new_fpos))

            # EOF handling
            if n_valid < frames:
                if recorder._scrubbing:
                    pass  # hit tape edge during scrub — stay silent
                elif recorder.loop:
                    with recorder._pb_lock:
                        recorder._pb_fpos = float(n_frames - 1) if step < 0 else 0.0
                else:
                    recorder._pb_playing = False
                    recorder._pb_stream_alive = False
                    raise sd.CallbackStop()

            # Pending stop: one-block linear fade then stop cleanly.
            if recorder._pb_stop_pending:
                ramp = np.linspace(1.0, 0.0, frames, dtype=np.float32)[:, np.newaxis]
                outdata[:] *= ramp
                recorder._pb_playing = False
                recorder._pb_stop_pending = False
                recorder._pb_stream_alive = False
                raise sd.CallbackStop()

        try:
            if self._pb_stream is not None:
                try:
                    self._pb_stream.stop()
                    self._pb_stream.close()
                except Exception:
                    pass

            self._pb_stream = sd.OutputStream(
                samplerate=self.sr,
                channels=2,
                dtype="float32",
                blocksize=512,
                callback=_callback,
            )
            self._pb_stream.start()
            self._pb_stream_alive = True
        except Exception as exc:
            log.warning("SongRecorder playback error: %s", exc)
            self._pb_playing = False
            self._pb_stream_alive = False

    def _pb_pause(self) -> None:
        """Request a smooth fade-out.  If the stream is alive the callback applies
        a one-block linear fade then raises CallbackStop.  If the stream is already
        dead we just clear the playing flag — nothing to fade."""
        self._pb_playing = False
        if self._pb_stream_alive:
            self._pb_stop_pending = True

    # ------------------------------------------------------------------ export

    def generate_filename(self) -> str:
        """Return an auto-generated filename stem (no extension)."""
        return f"Recording_{datetime.now().strftime('%Y%m%d_%H%M%S')}"

    def export(self, filepath: str, tape_speed: Optional[float] = None) -> bool:
        """Write the recording to a WAV file, resampling at the given tape_speed.

        If tape_speed is None, uses self.tape_speed.
        Returns True on success.
        """
        try:
            import soundfile as sf
        except ImportError:
            log.error("soundfile not available; cannot export")
            return False

        with self._lock:
            chunks = list(self._chunks)
            total = self._total_frames
            speed = tape_speed if tape_speed is not None else self.tape_speed

        if total <= 0 or not chunks:
            # Fall back to pre-finalised result if available
            if self.result is not None:
                try:
                    sf.write(filepath, self.result.data, self.sr, subtype="PCM_24")
                    return True
                except Exception as exc:
                    log.error("SongRecorder export failed: %s", exc)
                    return False
            return False

        raw = np.concatenate(chunks, axis=0)

        if abs(speed - 1.0) > 0.001:
            try:
                import resampy
                out_sr = int(self.sr * speed)
                if out_sr > 0:
                    raw = resampy.resample(raw, self.sr, out_sr, axis=0).astype(np.float32)
            except Exception as exc:
                log.warning("SongRecorder export resample failed: %s", exc)

        try:
            sf.write(filepath, raw, self.sr, subtype="PCM_24")
            return True
        except Exception as exc:
            log.error("SongRecorder export failed: %s", exc)
            return False
