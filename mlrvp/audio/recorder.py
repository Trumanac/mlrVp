"""Live audio recorder for mlrVp."""

import threading

import numpy as np
from typing import Optional

from mlrvp.config import SAMPLE_RATE, RecorderState
from mlrvp.audio.sample import SampleBuffer, create_empty_buffer


class Recorder:
    """Records live audio input into sample buffers.

    Supports recording to any channel's buffer, with configurable
    length in bars/beats, looping, and input monitoring (thru).
    """

    def __init__(self, sample_rate: int = SAMPLE_RATE):
        self.sr = sample_rate
        self.state = RecorderState()
        self._buffer: Optional[SampleBuffer] = None
        self._write_pos: int = 0
        self._target_frames: int = 0
        self._recording: bool = False
        self._armed: bool = False
        self._lock = threading.Lock()
        # Pre-allocated thru buffer to avoid per-block .copy()
        self._thru_buf = np.zeros((max(512, sample_rate // 50), 2), dtype=np.float32)

    def arm(self, tempo: float, bars: int = 4, beats: int = 1):
        """Arm the recorder for the next trigger. Creates the buffer."""
        if tempo <= 0:
            return
        with self._lock:
            beats_total = bars * 4 + beats  # bars in 4/4 time + extra beats
            duration_sec = beats_total * (60.0 / tempo)
            self._buffer = create_empty_buffer(duration_sec, num_channels=2, sample_rate=self.sr)
            self._target_frames = self._buffer.num_frames
            self._write_pos = 0
            self._armed = True
            self._recording = False

    def start(self):
        """Start recording (call after arming)."""
        with self._lock:
            if self._armed and self._buffer is not None:
                self._recording = True
                self._armed = False
                self.state.recording = True

    def stop(self) -> Optional[SampleBuffer]:
        """Stop recording and return the captured buffer."""
        with self._lock:
            self._recording = False
            self.state.recording = False
            self._armed = False
            result = self._buffer
            return result

    def process(self, input_data: np.ndarray) -> Optional[np.ndarray]:
        """Write incoming audio to the recording buffer.

        Args:
            input_data: stereo float32 array

        Returns:
            Thru audio if monitoring is enabled, else None
        """
        # Snapshot state under lock
        with self._lock:
            if not self._recording or self._buffer is None:
                if self.state.thru:
                    n = len(input_data)
                    if n > len(self._thru_buf):
                        self._thru_buf = np.zeros((n, 2), dtype=np.float32)
                    out = self._thru_buf[:n]
                    out[:] = input_data
                    return out
                return None
            buffer = self._buffer
            write_pos = self._write_pos
            target_frames = self._target_frames
            do_thru = self.state.thru
            do_loop = self.state.loop

        if do_thru:
            n = len(input_data)
            if n > len(self._thru_buf):
                self._thru_buf = np.zeros((n, 2), dtype=np.float32)
            thru_output = self._thru_buf[:n]
            thru_output[:] = input_data
        else:
            thru_output = None
        frames_to_write = min(len(input_data), target_frames - write_pos)

        # Bulk copy outside the lock — this is the expensive op
        if frames_to_write > 0:
            buf = buffer.data
            if input_data.shape[1] >= 2:
                buf[write_pos:write_pos + frames_to_write] = input_data[:frames_to_write, :2]
            else:
                buf[write_pos:write_pos + frames_to_write, 0] = input_data[:frames_to_write, 0]
                buf[write_pos:write_pos + frames_to_write, 1] = input_data[:frames_to_write, 0]
            write_pos += frames_to_write

        # Update position under lock
        with self._lock:
            self._write_pos = write_pos
            if write_pos >= target_frames:
                if do_loop:
                    self._write_pos = 0
                else:
                    self._recording = False
                    self.state.recording = False

        return thru_output

    @property
    def recording(self) -> bool:
        return self._recording

    @property
    def progress(self) -> float:
        """Recording progress 0.0 - 1.0."""
        if self._target_frames == 0:
            return 0.0
        return self._write_pos / self._target_frames

    @property
    def buffer(self) -> Optional[SampleBuffer]:
        return self._buffer
