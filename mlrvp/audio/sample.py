"""Sample loading and buffer management for mlrVp."""

import numpy as np
import soundfile as sf
from pathlib import Path
from dataclasses import dataclass
from typing import Optional

from mlrvp.config import SAMPLE_RATE


@dataclass
class SampleBuffer:
    """A loaded audio sample stored as a numpy array."""
    data: np.ndarray           # shape: (num_frames, num_channels)
    sample_rate: int
    num_frames: int
    num_channels: int
    filename: str = ""
    filepath: str = ""
    duration_sec: float = 0.0

    @property
    def duration_samples(self) -> int:
        return self.num_frames


def load_sample(filepath: str, target_sr: int = SAMPLE_RATE) -> Optional[SampleBuffer]:
    """Load an audio file and resample to target sample rate.

    Supports WAV, AIFF, FLAC, OGG, MP3 (via soundfile/libsndfile).
    """
    path = Path(filepath)
    if not path.exists():
        return None

    # Guard against absurdly large files that would exhaust RAM
    _MAX_FILE_BYTES = 500 * 1024 * 1024  # 500 MB
    try:
        if path.stat().st_size > _MAX_FILE_BYTES:
            return None
    except OSError:
        return None

    try:
        data, sr = sf.read(str(path), dtype='float32', always_2d=True)
    except Exception:
        return None

    # Validate sample rate
    if sr <= 0 or target_sr <= 0:
        return None

    # Resample if needed
    if len(data) == 0:
        return None
    if sr != target_sr:
        try:
            import resampy
            data = resampy.resample(data, sr, target_sr, axis=0).astype(np.float32)
        except ImportError:
            # Fallback: simple linear interpolation
            ratio = target_sr / sr
            new_length = max(1, int(len(data) * ratio))
            indices = np.linspace(0, len(data) - 1, new_length)
            int_indices = indices.astype(int)
            frac = (indices - int_indices).reshape(-1, 1).astype(np.float32)
            int_indices_clipped = np.minimum(int_indices + 1, len(data) - 1)
            data = (data[int_indices] * (1 - frac) + data[int_indices_clipped] * frac).astype(np.float32)
        sr = target_sr

    num_frames, num_channels = data.shape
    return SampleBuffer(
        data=data,
        sample_rate=sr,
        num_frames=num_frames,
        num_channels=num_channels,
        filename=path.name,
        filepath=str(path),
        duration_sec=num_frames / sr,
    )


def create_empty_buffer(duration_sec: float, num_channels: int = 2,
                        sample_rate: int = SAMPLE_RATE) -> SampleBuffer:
    """Create an empty buffer for live recording."""
    num_frames = int(duration_sec * sample_rate)
    data = np.zeros((num_frames, num_channels), dtype=np.float32)
    return SampleBuffer(
        data=data,
        sample_rate=sample_rate,
        num_frames=num_frames,
        num_channels=num_channels,
        filename="recording",
        duration_sec=duration_sec,
    )


def get_waveform_overview(sample: SampleBuffer, num_points: int = 512) -> np.ndarray:
    """Generate a waveform overview for display.

    Returns array of shape (num_points, 2) with [min, max] per segment.
    Uses the first channel (or mono mix) for display.
    """
    if sample.num_channels > 1:
        mono = sample.data[:, 0]  # left channel only for display
    else:
        mono = sample.data[:, 0]

    if len(mono) < num_points:
        # Pad short samples
        points = np.zeros(num_points)
        points[:len(mono)] = mono
        mono = points

    chunk_size = max(1, len(mono) // num_points)
    # Truncate to evenly divisible length for reshape
    usable = chunk_size * num_points
    reshaped = mono[:usable].reshape(num_points, chunk_size)
    overview = np.zeros((num_points, 2), dtype=np.float32)
    overview[:, 0] = reshaped.min(axis=1)
    overview[:, 1] = reshaped.max(axis=1)
    return overview
