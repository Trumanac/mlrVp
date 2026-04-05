"""Live audio recorder for mlrVp."""

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

    def arm(self, tempo: float, bars: int = 4, beats: int = 1):
        """Arm the recorder for the next trigger. Creates the buffer."""
        beats_total = bars * 4 + beats  # bars in 4/4 time + extra beats
        duration_sec = beats_total * (60.0 / tempo)
        self._buffer = create_empty_buffer(duration_sec, num_channels=2, sample_rate=self.sr)
        self._target_frames = self._buffer.num_frames
        self._write_pos = 0
        self._armed = True
        self._recording = False

    def start(self):
        """Start recording (call after arming)."""
        if self._armed and self._buffer is not None:
            self._recording = True
            self._armed = False
            self.state.recording = True

    def stop(self) -> Optional[SampleBuffer]:
        """Stop recording and return the captured buffer."""
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
        if not self._recording or self._buffer is None:
            return input_data if self.state.thru else None

        thru_output = input_data.copy() if self.state.thru else None

        frames_to_write = min(len(input_data), self._target_frames - self._write_pos)

        if frames_to_write > 0:
            buf = self._buffer.data
            # Handle channel mismatch
            if input_data.shape[1] >= 2:
                buf[self._write_pos:self._write_pos + frames_to_write] = input_data[:frames_to_write, :2]
            else:
                buf[self._write_pos:self._write_pos + frames_to_write, 0] = input_data[:frames_to_write, 0]
                buf[self._write_pos:self._write_pos + frames_to_write, 1] = input_data[:frames_to_write, 0]

            self._write_pos += frames_to_write

        # Check if recording is complete
        if self._write_pos >= self._target_frames:
            if self.state.loop:
                self._write_pos = 0  # Loop recording
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
