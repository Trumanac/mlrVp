"""Live audio recorder for mlrVp.

Captures input (or main-output loopback) into a SampleBuffer, with support for:
- Beat/bar quantize start (driven externally via clock callbacks)
- Free-length recording (no fixed bar count, stop when told)
- Pre-record input gain
- Input level metering (peak + RMS, polled by GUI)
- Completion callback fired when buffer fills (non-loop)
- Pre-roll ring buffer for retroactive capture (separate class)
"""

import threading

import numpy as np
from typing import Callable, Optional

from mlrvp.config import SAMPLE_RATE, RecorderState
from mlrvp.audio.sample import SampleBuffer, create_empty_buffer

# Hard cap for free-length recording to prevent unbounded memory growth.
# 5 minutes stereo float32 @ 44.1kHz ≈ 100 MB.
MAX_FREE_LENGTH_SECONDS = 300.0


class Recorder:
    """Records live audio input into sample buffers.

    Thread-safety: process() runs on the audio worker thread; arm/start/stop
    are called from the GUI/clock thread. All mutable state is guarded by
    _lock; large allocations happen outside the lock.
    """

    def __init__(self, sample_rate: int = SAMPLE_RATE):
        self.sr = sample_rate
        self.state = RecorderState()
        self._buffer: Optional[SampleBuffer] = None
        self._write_pos: int = 0
        self._target_frames: int = 0   # 0 == free-length (record until stopped)
        self._recording: bool = False
        self._armed: bool = False
        self._free_length: bool = False
        self._lock = threading.Lock()
        # Pre-allocated thru buffer to avoid per-block .copy()
        self._thru_buf = np.zeros((max(512, sample_rate // 50), 2), dtype=np.float32)
        # Free-length growable buffer chunks (assembled on stop())
        self._free_chunks: list = []
        self._free_total_frames: int = 0
        # Input level metering (updated each block, read by GUI)
        self._input_peak: float = 0.0
        self._input_rms: float = 0.0
        # Loopback flag - when True, engine routes main output here
        self._use_loopback: bool = False
        # Completion callback fired on the audio thread when buffer fills
        self._complete_cb: Optional[Callable[[], None]] = None

    # ------------------------------------------------------------------ control

    def arm(self, tempo: float, bars: int = 4, beats: int = 1, free_length: bool = False):
        """Arm the recorder for the next start(). Pre-allocates the buffer.

        free_length=True: skip allocation, record into growable chunks until stop().
        """
        if free_length:
            with self._lock:
                self._buffer = None
                self._target_frames = 0
                self._write_pos = 0
                self._free_chunks = []
                self._free_total_frames = 0
                self._free_length = True
                self._armed = True
                self._recording = False
            return

        if tempo <= 0:
            return
        beats_total = bars * 4 + beats
        duration_sec = beats_total * (60.0 / tempo)
        new_buf = create_empty_buffer(duration_sec, num_channels=2, sample_rate=self.sr)
        with self._lock:
            self._buffer = new_buf
            self._target_frames = new_buf.num_frames
            self._write_pos = 0
            self._free_length = False
            self._free_chunks = []
            self._free_total_frames = 0
            self._armed = True
            self._recording = False

    def start(self):
        """Start recording (call after arming)."""
        with self._lock:
            if self._armed:
                self._recording = True
                self._armed = False
                self.state.recording = True

    def stop(self) -> Optional[SampleBuffer]:
        """Stop recording and return the captured buffer.

        Fixed-length: returns the buffer cropped to write_pos.
        Free-length: assembles chunks into a single SampleBuffer.
        """
        with self._lock:
            self._recording = False
            self.state.recording = False
            self._armed = False
            free_length = self._free_length
            chunks = self._free_chunks
            total = self._free_total_frames
            buf = self._buffer
            wp = self._write_pos
            self._free_chunks = []
            self._free_total_frames = 0

        if free_length:
            if total <= 0 or not chunks:
                return None
            data = np.concatenate(chunks, axis=0)
            return SampleBuffer(
                data=data,
                sample_rate=self.sr,
                num_frames=int(data.shape[0]),
                num_channels=int(data.shape[1]),
                duration_sec=float(data.shape[0]) / float(self.sr),
            )

        if buf is None or wp <= 0:
            return None
        if wp < buf.num_frames:
            cropped = buf.data[:wp].copy()
            return SampleBuffer(
                data=cropped,
                sample_rate=self.sr,
                num_frames=wp,
                num_channels=int(cropped.shape[1]),
                duration_sec=float(wp) / float(self.sr),
            )
        return buf

    def cancel(self):
        """Discard the current recording without producing a buffer."""
        with self._lock:
            self._recording = False
            self.state.recording = False
            self._armed = False
            self._buffer = None
            self._free_chunks = []
            self._free_total_frames = 0
            self._write_pos = 0

    def set_complete_callback(self, cb: Optional[Callable[[], None]]):
        """Register a callback fired (on audio thread) when buffer fills."""
        with self._lock:
            self._complete_cb = cb

    # ------------------------------------------------------------------ process

    def process(self, input_data: np.ndarray, input_trim: float = 1.0) -> Optional[np.ndarray]:
        """Write incoming audio to the recording buffer.

        Args:
            input_data: stereo float32 array (frames, channels)
            input_trim: pre-recording gain multiplier (post-monitor, pre-write)

        Returns:
            Thru audio if monitoring is enabled, else None.
            Thru is always the un-trimmed input.
        """
        n = len(input_data)
        # Update input metering (cheap, always on) - use np.abs+max+mean directly
        # without intermediate left/right slice copies
        if n > 0:
            try:
                # np.abs+max returns scalar without intermediate array if we
                # use .max() on the original; this is a O(n) read with no alloc
                peak = float(np.max(np.abs(input_data)))
                rms = float(np.sqrt(np.mean(np.square(input_data, dtype=np.float32))))
            except (ValueError, FloatingPointError):
                peak = 0.0
                rms = 0.0
            # Smooth peak with a slow decay (~hold for VU display)
            self._input_peak = max(peak, self._input_peak * 0.7)
            self._input_rms = rms

        with self._lock:
            recording = self._recording
            buffer = self._buffer
            write_pos = self._write_pos
            target_frames = self._target_frames
            free_length = self._free_length
            do_thru = self.state.thru
            do_loop = self.state.loop
            complete_cb = self._complete_cb

        # Build thru output (un-trimmed) if monitoring
        if do_thru and n > 0:
            if n > len(self._thru_buf):
                self._thru_buf = np.zeros((n, 2), dtype=np.float32)
            thru_output = self._thru_buf[:n]
            thru_output[:] = input_data
        else:
            thru_output = None

        if not recording or n == 0:
            return thru_output

        if input_trim != 1.0:
            trimmed = input_data * float(input_trim)
        else:
            trimmed = input_data

        fired_complete = False
        max_free_frames = int(MAX_FREE_LENGTH_SECONDS * self.sr)

        if free_length:
            # Allocate the chunk OUTSIDE the lock (astype may copy)
            chunk = trimmed.astype(np.float32, copy=True)
            with self._lock:
                if self._recording and self._free_length:
                    self._free_chunks.append(chunk)
                    self._free_total_frames += int(chunk.shape[0])
                    # Auto-stop on cap to prevent unbounded growth
                    if self._free_total_frames >= max_free_frames:
                        self._recording = False
                        self.state.recording = False
                        fired_complete = True
        else:
            if buffer is None:
                return thru_output
            # Perform slice write INSIDE the lock so stop()/cancel() cannot
            # swap the buffer mid-write (otherwise we corrupt freed memory).
            with self._lock:
                if self._recording and not self._free_length and self._buffer is buffer:
                    frames_to_write = min(n, target_frames - write_pos)
                    new_write_pos = write_pos
                    if frames_to_write > 0:
                        buf = buffer.data
                        if trimmed.shape[1] >= 2:
                            buf[write_pos:write_pos + frames_to_write] = trimmed[:frames_to_write, :2]
                        else:
                            buf[write_pos:write_pos + frames_to_write, 0] = trimmed[:frames_to_write, 0]
                            buf[write_pos:write_pos + frames_to_write, 1] = trimmed[:frames_to_write, 0]
                        new_write_pos = write_pos + frames_to_write
                    self._write_pos = new_write_pos
                    if new_write_pos >= target_frames:
                        if do_loop:
                            self._write_pos = 0
                        else:
                            self._recording = False
                            self.state.recording = False
                            fired_complete = True

        # Fire completion outside the lock; log exceptions instead of swallowing
        if fired_complete and complete_cb is not None:
            try:
                complete_cb()
            except Exception:
                import logging
                logging.getLogger(__name__).exception("Recorder complete callback raised")

        return thru_output

    # ------------------------------------------------------------------ properties

    @property
    def recording(self) -> bool:
        return self._recording

    @property
    def armed(self) -> bool:
        return self._armed

    @property
    def progress(self) -> float:
        """Recording progress 0.0 - 1.0. Returns 0 for free-length."""
        with self._lock:
            tf = self._target_frames
            wp = self._write_pos
            free = self._free_length
        if free or tf == 0:
            return 0.0
        return min(1.0, wp / tf)

    @property
    def free_length_frames(self) -> int:
        with self._lock:
            return int(self._free_total_frames)

    @property
    def buffer(self) -> Optional[SampleBuffer]:
        return self._buffer

    @property
    def input_peak(self) -> float:
        return float(self._input_peak)

    @property
    def input_rms(self) -> float:
        return float(self._input_rms)

    @property
    def use_loopback(self) -> bool:
        return self._use_loopback

    @use_loopback.setter
    def use_loopback(self, v: bool):
        self._use_loopback = bool(v)


class PreRollBuffer:
    """Circular ring buffer that always holds the last N seconds of audio.

    Runs unconditionally on the audio thread (cheap), enabling the
    "Capture Last X seconds" workflow.
    """

    def __init__(self, sample_rate: int, max_seconds: float = 30.0, num_channels: int = 2):
        self.sr = int(sample_rate)
        self.num_channels = int(num_channels)
        self._capacity = int(self.sr * float(max_seconds))
        if self._capacity < self.sr:
            self._capacity = self.sr
        self._ring = np.zeros((self._capacity, self.num_channels), dtype=np.float32)
        self._write_pos: int = 0
        self._filled: int = 0
        self._lock = threading.Lock()

    @property
    def capacity_seconds(self) -> float:
        return self._capacity / float(self.sr)

    @property
    def filled_seconds(self) -> float:
        with self._lock:
            return self._filled / float(self.sr)

    def process(self, input_data: np.ndarray):
        """Append input to ring (always-on, cheap)."""
        if input_data is None:
            return
        n = int(input_data.shape[0])
        if n <= 0:
            return
        if input_data.shape[1] < self.num_channels:
            data = np.repeat(input_data[:, :1], self.num_channels, axis=1)
        else:
            data = input_data[:, :self.num_channels]

        with self._lock:
            wp = self._write_pos
            cap = self._capacity
            if n >= cap:
                # Larger than ring: keep only the last `cap` frames
                self._ring[:] = data[-cap:]
                self._write_pos = 0
                self._filled = cap
                return
            end = wp + n
            if end <= cap:
                self._ring[wp:end] = data
            else:
                first = cap - wp
                self._ring[wp:cap] = data[:first]
                self._ring[0:n - first] = data[first:]
            self._write_pos = (wp + n) % cap
            self._filled = min(cap, self._filled + n)

    def capture_last(self, seconds: float) -> Optional[SampleBuffer]:
        """Return the most recent `seconds` of audio as a SampleBuffer."""
        n = int(self.sr * float(seconds))
        if n <= 0:
            return None
        with self._lock:
            cap = self._capacity
            available = self._filled
            wp = self._write_pos
            n = min(n, available)
            if n <= 0:
                return None
            start = (wp - n) % cap
            if start + n <= cap:
                data = self._ring[start:start + n].copy()
            else:
                first = cap - start
                data = np.concatenate(
                    [self._ring[start:cap], self._ring[0:n - first]], axis=0
                )
        return SampleBuffer(
            data=data.astype(np.float32, copy=False),
            sample_rate=self.sr,
            num_frames=int(data.shape[0]),
            num_channels=int(data.shape[1]),
            duration_sec=float(data.shape[0]) / float(self.sr),
        )

    def reset(self):
        with self._lock:
            self._ring.fill(0.0)
            self._write_pos = 0
            self._filled = 0
"""Live audio recorder for mlrVp."""
