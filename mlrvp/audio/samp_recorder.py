"""SAMP / resample recorder for mlrVp LIVE mode.

When a LIVE-mode channel's SOURCE is set to anything other than INPUT,
recordings are captured from an internal engine bus instead of the
hardware input. SampRecorder owns one in-progress bus capture for one row.

States:
    EMPTY      - no buffer, idle
    ARMED      - buffer allocated, waiting for trigger boundary
    RECORDING  - actively writing bus audio
    DONE       - recording complete; buffer ready to retrieve

Threading model:
- arm() / start() / cancel() / finalize(): called from app (GUI) thread
- process(): called from audio thread every block

This is intentionally simpler than LiveLoopRecorder: no overdub, no thru,
no input-trim. The bus tap already provides the desired post-FX signal.
"""

from __future__ import annotations

import datetime
import threading
from pathlib import Path
from typing import Optional

import numpy as np
import soundfile as sf

from mlrvp.config import SAMPLE_RATE
from mlrvp.audio.sample import SampleBuffer, create_empty_buffer


class _State:
    EMPTY = "empty"
    ARMED = "armed"
    RECORDING = "recording"
    DONE = "done"


class SampRecorder:
    """Records audio from an engine bus into a SampleBuffer for one row.

    Lifecycle:
        rec = SampRecorder(row=2, sample_rate=44100)
        rec.arm(bars=4, tempo=120.0, fade_ms=10, normalize=True)
        rec.start()                                # ARMED -> RECORDING
        ... audio thread calls rec.process(bus_data) every block ...
        when rec.is_done: buffer = rec.finalize()  # fade + normalize + return
    """

    def __init__(self, row: int, sample_rate: int = SAMPLE_RATE):
        self.row = row
        self.sr = sample_rate

        self._buffer: Optional[SampleBuffer] = None
        self._target_frames: int = 0
        self._write_pos: int = 0
        self._state: str = _State.EMPTY
        self._fade_ms: float = 10.0
        self._normalize: bool = True

        self._lock = threading.Lock()

    # ── Properties ────────────────────────────────────────────────────────

    @property
    def state(self) -> str:
        return self._state

    @property
    def is_recording(self) -> bool:
        return self._state == _State.RECORDING

    @property
    def is_done(self) -> bool:
        return self._state == _State.DONE

    @property
    def progress(self) -> float:
        """0.0-1.0 fraction of buffer filled."""
        if self._target_frames <= 0:
            return 0.0
        return min(1.0, self._write_pos / self._target_frames)

    @property
    def buffer(self) -> Optional[SampleBuffer]:
        return self._buffer

    # ── Setup ─────────────────────────────────────────────────────────────

    def arm(self, bars: int, tempo: float,
            fade_ms: float = 10.0, normalize: bool = True) -> None:
        """Allocate buffer and arm for next start() call.

        bars * 4 beats * (60/tempo) seconds of stereo float32 buffer is
        allocated. fade_ms and normalize are stored for finalize().
        """
        bars = max(1, min(32, int(bars)))
        tempo = max(20.0, min(300.0, float(tempo)))
        duration_sec = bars * 4 * (60.0 / tempo)

        # Allocate outside the lock — create_empty_buffer() zeros a potentially
        # large buffer (multi-MB) which would block process() on the audio thread
        # if held inside the lock (same issue fixed in Recorder.arm()).
        new_buf = create_empty_buffer(
            duration_sec, num_channels=2, sample_rate=self.sr
        )
        with self._lock:
            self._buffer = new_buf
            self._target_frames = new_buf.num_frames
            self._write_pos = 0
            self._fade_ms = float(fade_ms)
            self._normalize = bool(normalize)
            self._state = _State.ARMED

    def start(self) -> None:
        """Transition from ARMED to RECORDING."""
        with self._lock:
            if self._state != _State.ARMED:
                return
            self._state = _State.RECORDING

    def cancel(self) -> None:
        """Abort recording, free the buffer."""
        with self._lock:
            self._buffer = None
            self._target_frames = 0
            self._write_pos = 0
            self._state = _State.EMPTY

    # ── Audio-thread processing ───────────────────────────────────────────

    def process(self, bus_data: np.ndarray) -> None:
        """Write bus audio into the buffer (audio thread).

        bus_data: shape (num_frames, 2) float32 — pre-tapped engine bus.
        Auto-transitions to DONE when buffer fills.
        """
        with self._lock:
            if self._state != _State.RECORDING:
                return
            buf_obj = self._buffer
            if buf_obj is None:
                return
            wp = self._write_pos
            target = self._target_frames

            frames_to_write = min(len(bus_data), target - wp)
            if frames_to_write <= 0:
                return

            buf = buf_obj.data

            if bus_data.ndim == 1:
                mono = bus_data[:frames_to_write]
                buf[wp:wp + frames_to_write, 0] = mono
                buf[wp:wp + frames_to_write, 1] = mono
            elif bus_data.shape[1] >= 2:
                buf[wp:wp + frames_to_write] = bus_data[:frames_to_write, :2]
            else:
                mono = bus_data[:frames_to_write, 0]
                buf[wp:wp + frames_to_write, 0] = mono
                buf[wp:wp + frames_to_write, 1] = mono

            self._write_pos = wp + frames_to_write

            if self._write_pos >= target:
                self._state = _State.DONE

    # ── Finalization ──────────────────────────────────────────────────────

    def finalize(self) -> Optional[SampleBuffer]:
        """Apply fade in/out and (optionally) peak-normalize. Returns the
        completed buffer and resets the recorder. Should be called after
        is_done becomes True (or to retrieve a partial recording).
        """
        with self._lock:
            if self._buffer is None:
                return None
            buf_data = self._buffer.data
            written = self._write_pos
            fade_ms = self._fade_ms
            normalize = self._normalize
            self._buffer = None
            self._target_frames = 0
            self._write_pos = 0
            self._state = _State.EMPTY

        # Trim to actually-written frames (handles cancelled-mid-record case)
        if written <= 0:
            return None
        data = buf_data[:written].copy()

        # Apply fade-in / fade-out
        n = int(fade_ms * 0.001 * self.sr)
        if n > 0 and data.shape[0] >= n * 2:
            ramp = np.linspace(0.0, 1.0, n, dtype=np.float32).reshape(-1, 1)
            data[:n] *= ramp
            data[-n:] *= ramp[::-1]

        # Optional peak normalize (leaving 0.5 dB headroom)
        if normalize:
            peak = float(np.max(np.abs(data))) if data.size else 0.0
            if peak > 1e-6:
                data *= np.float32(0.94 / peak)

        return SampleBuffer(
            data=data,
            sample_rate=self.sr,
            num_frames=data.shape[0],
            num_channels=data.shape[1],
            filename=f"samp_row{self.row:02d}",
            duration_sec=data.shape[0] / self.sr,
        )

    # ── Export ────────────────────────────────────────────────────────────

    @staticmethod
    def export(buffer: SampleBuffer, data_dir: Path, row: int) -> str:
        """Write buffer to data/slices/samp_rowNN_TIMESTAMP.wav. Returns path."""
        slices_dir = Path(data_dir) / "slices"
        slices_dir.mkdir(parents=True, exist_ok=True)
        ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        path = slices_dir / f"samp_row{row:02d}_{ts}.wav"
        sf.write(str(path), buffer.data, buffer.sample_rate, subtype="FLOAT")
        return str(path)
