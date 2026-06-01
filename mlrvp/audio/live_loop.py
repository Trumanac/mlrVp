"""LIVE-mode looper recorder for mlrVp.

Each LiveLoopRecorder instance owns one in-progress live recording.
The audio engine holds at most one active recorder at a time
(single-row recording constraint).

States:
    EMPTY              - no buffer, idle
    ARMED              - buffer allocated, waiting for trigger boundary
    RECORDING          - actively writing input to a fresh buffer
    OVERDUBBING        - mixing input on top of existing playback buffer
    DONE               - recording complete; buffer ready for engine to retrieve

Threading model:
- arm() / start() / state transitions: called from app thread (GUI)
- process(): called from audio thread every block
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


# Internal recorder states (distinct from LiveLoopState.loop_state strings;
# this enum tracks ONLY the recorder hardware state, not the channel UI state).
class _RecState:
    EMPTY = "empty"
    ARMED = "armed"
    RECORDING = "recording"
    OVERDUBBING = "overdubbing"
    DONE = "done"


class LiveLoopRecorder:
    """Records live input audio into a SampleBuffer for one row.

    Lifecycle:
        rec = LiveLoopRecorder(row=2, sample_rate=44100)
        rec.arm(bars=4, tempo=120.0, overdub_buffer=None)   # allocates buffer
        rec.start()                                          # transitions to RECORDING
        ... audio thread calls rec.process(input, trim) every block ...
        when rec.is_done: buffer = rec.finalize()            # crossfade + return
    """

    def __init__(self, row: int, sample_rate: int = SAMPLE_RATE):
        self.row = row
        self.sr = sample_rate

        self._buffer: Optional[SampleBuffer] = None
        self._is_overdub: bool = False
        self._target_frames: int = 0
        self._write_pos: int = 0
        self._state: str = _RecState.EMPTY

        # thru monitoring (set by engine each block via process arg)
        self._thru: bool = False

        # Lock protects state transitions between app and audio thread
        self._lock = threading.Lock()

    # ── Properties ────────────────────────────────────────────────────────

    @property
    def state(self) -> str:
        return self._state

    @property
    def is_recording(self) -> bool:
        return self._state in (_RecState.RECORDING, _RecState.OVERDUBBING)

    @property
    def is_done(self) -> bool:
        return self._state == _RecState.DONE

    @property
    def is_overdub(self) -> bool:
        return self._is_overdub

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
            overdub_buffer: Optional[np.ndarray] = None,
            thru: bool = False) -> None:
        """Allocate buffer and arm for next start() call.

        bars, tempo determine buffer length: bars * 4 beats * (60/tempo) seconds.
        If overdub_buffer is provided, its data is copied as starting buffer
        contents and OVERDUBBING state is used (input mixes on top).
        """
        bars = max(1, min(32, int(bars)))
        tempo = max(20.0, min(300.0, float(tempo)))
        duration_sec = bars * 4 * (60.0 / tempo)

        # --- Allocate outside the lock to avoid blocking the audio thread.
        # The audio thread acquires self._lock on every process() call, so
        # holding it across a multi-MB malloc (fresh buffer) or large copy
        # (overdub) causes an audible dropout.  Only the final state swap
        # goes inside the lock (identical pattern to SampRecorder.arm()).
        if overdub_buffer is not None and overdub_buffer.size > 0:
            # Overdub: copy source buffer as starting contents, match its length
            src = overdub_buffer
            if src.ndim == 1:
                src = np.column_stack([src, src])
            elif src.shape[1] == 1:
                src = np.column_stack([src[:, 0], src[:, 0]])
            elif src.shape[1] > 2:
                src = src[:, :2]  # discard extra channels — buffer is always stereo

            num_frames = src.shape[0]
            buf_data = src.astype(np.float32, copy=True)
            new_buffer = SampleBuffer(
                data=buf_data,
                sample_rate=self.sr,
                num_frames=num_frames,
                num_channels=2,
                filename=f"live_row{self.row:02d}_overdub",
                duration_sec=num_frames / self.sr,
            )
            _is_overdub = True
            _target_frames = num_frames
        else:
            # Fresh recording
            new_buffer = create_empty_buffer(
                duration_sec, num_channels=2, sample_rate=self.sr
            )
            _is_overdub = False
            _target_frames = new_buffer.num_frames

        with self._lock:
            self._buffer = new_buffer
            self._is_overdub = _is_overdub
            self._target_frames = _target_frames
            self._write_pos = 0
            self._thru = bool(thru)
            self._state = _RecState.ARMED

    def start(self) -> None:
        """Transition from ARMED to RECORDING/OVERDUBBING."""
        with self._lock:
            if self._state != _RecState.ARMED:
                return
            self._state = (_RecState.OVERDUBBING if self._is_overdub
                           else _RecState.RECORDING)

    def cancel(self) -> None:
        """Abort recording, free the buffer."""
        with self._lock:
            self._buffer = None
            self._is_overdub = False
            self._target_frames = 0
            self._write_pos = 0
            self._state = _RecState.EMPTY

    # ── Audio-thread processing ───────────────────────────────────────────

    def process(self, input_data: np.ndarray, input_trim: float = 1.0) -> Optional[np.ndarray]:
        """Write input audio into the buffer (audio thread).

        Args:
            input_data: shape (num_frames, 2) float32 input audio
            input_trim: linear gain applied to input before writing/mixing

        Returns:
            Thru audio (input_data * trim) if thru monitoring is on, else None.
            Caller is responsible for adding it to the output mix.
        """
        # Apply trim OUTSIDE the lock — this is a pure compute on the
        # caller-provided array and never touches mutable recorder state.
        if input_trim != 1.0:
            trimmed = input_data * np.float32(input_trim)
        else:
            trimmed = input_data

        # Snapshot mutable state under the lock to avoid races with cancel()
        # and finalize() which may run on the GUI thread.  Only the minimal
        # state reads and the actual buffer write need the lock held.
        with self._lock:
            if self._state not in (_RecState.RECORDING, _RecState.OVERDUBBING):
                return None
            buf_obj = self._buffer
            if buf_obj is None:
                return None
            wp = self._write_pos
            target = self._target_frames
            is_overdub = self._state == _RecState.OVERDUBBING
            thru_on = self._thru

            frames_to_write = min(len(trimmed), target - wp)
            if frames_to_write <= 0:
                return trimmed if thru_on else None

            buf = buf_obj.data

            # Match channel count — view (no alloc) when input is stereo
            if trimmed.shape[1] >= 2:
                in_stereo = trimmed[:frames_to_write, :2]
            else:
                mono = trimmed[:frames_to_write, 0]
                in_stereo = np.column_stack([mono, mono])

            if is_overdub:
                # Mix input on top of existing buffer contents, then clamp so
                # repeated overdub layers can't push values outside ±1.0.
                buf[wp:wp + frames_to_write] += in_stereo
                np.clip(buf[wp:wp + frames_to_write], -1.0, 1.0,
                        out=buf[wp:wp + frames_to_write])
            else:
                # Fresh recording: overwrite
                buf[wp:wp + frames_to_write] = in_stereo

            self._write_pos = wp + frames_to_write

            # Auto-finalize when full
            if self._write_pos >= target:
                self._state = _RecState.DONE

            return trimmed if thru_on else None

    # ── Finalization ──────────────────────────────────────────────────────

    def finalize(self, fade_ms: float = 10.0) -> Optional[SampleBuffer]:
        """Apply loop-boundary crossfade and return the completed buffer.

        Should be called after is_done becomes True. Resets the recorder.
        Returns a NEW SampleBuffer whose length may be slightly shorter than
        the recording (trimmed by the crossfade length to keep the loop seamless).
        """
        # Snapshot the buffer reference and reset state under the lock so the
        # audio thread is never blocked waiting for the numpy crossfade work.
        with self._lock:
            if self._buffer is None:
                return None
            buf_data = self._buffer.data
            self._buffer = None
            self._is_overdub = False
            self._target_frames = 0
            self._write_pos = 0
            self._state = _RecState.EMPTY

        # Numpy operations happen outside the lock — audio thread can proceed.
        new_data = self._make_seamless_loop(buf_data, fade_ms)
        return SampleBuffer(
            data=new_data,
            sample_rate=self.sr,
            num_frames=new_data.shape[0],
            num_channels=new_data.shape[1],
            filename=f"live_row{self.row:02d}",
            duration_sec=new_data.shape[0] / self.sr,
        )

    def _make_seamless_loop(self, data: np.ndarray, fade_ms: float) -> np.ndarray:
        """Return new buffer with last N frames crossfaded into the first N
        and the trailing N frames truncated. Result loops seamlessly because
        the (new) tail end == (old) head end which now equals the new head start
        after the linear blend.

        Linear (constant-amplitude) ramps are used rather than equal-power
        because for a self-loop seam the head and tail are typically correlated
        and equal-power would cause a +3dB bump at the midpoint.
        """
        n = int(fade_ms * 0.001 * self.sr)
        if n <= 0 or data.shape[0] < n * 2:
            return data.copy()
        # Linear ramps — sum to 1.0 at every point
        ramp_in = np.linspace(0.0, 1.0, n, dtype=np.float32)
        ramp_out = 1.0 - ramp_in
        new_data = data[:-n].copy()
        # Blend old tail (data[-n:]) into start of new buffer (broadcast over channels)
        new_data[:n] = (data[-n:] * ramp_out[:, None]
                        + data[:n] * ramp_in[:, None])
        return new_data

    # ── Export ────────────────────────────────────────────────────────────

    @staticmethod
    def export(buffer: SampleBuffer, data_dir: Path | str, row: int,
               sample_rate: int = SAMPLE_RATE) -> str:
        """Write a SampleBuffer as 32-bit float WAV to data/slices/.

        Returns the absolute filepath of the saved file.
        """
        slices_dir = Path(data_dir) / "slices"
        slices_dir.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"live_row{row:02d}_{timestamp}.wav"
        filepath = slices_dir / filename
        sf.write(str(filepath), buffer.data, sample_rate, subtype="FLOAT")
        return str(filepath)
