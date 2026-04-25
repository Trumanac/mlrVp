"""Audio analysis helpers — BPM and musical key detection.

Uses librosa (already a project dependency). Both functions cap audio load
duration to keep scans responsive on large libraries.
"""

from __future__ import annotations

import os
from typing import Optional

import numpy as np


# Krumhansl-Schmuckler key profiles (major + minor).
# These are the canonical key-finding profiles from cognitive musicology.
_KS_MAJOR = np.array(
    [6.35, 2.23, 3.48, 2.33, 4.38, 4.09, 2.52, 5.19, 2.39, 3.66, 2.29, 2.88],
    dtype=np.float64,
)
_KS_MINOR = np.array(
    [6.33, 2.68, 3.52, 5.38, 2.60, 3.53, 2.54, 4.75, 3.98, 2.69, 3.34, 3.17],
    dtype=np.float64,
)
_PITCH_NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]

# Maximum seconds of audio to load per analysis. Keeps scans fast.
_MAX_LOAD_DURATION = 60.0


def _load_audio(filepath: str):
    """Load mono audio capped to _MAX_LOAD_DURATION. Returns (y, sr) or None."""
    if not filepath or not os.path.isfile(filepath):
        return None
    try:
        import librosa  # imported lazily — analysis is optional at startup

        y, sr = librosa.load(
            filepath,
            sr=None,
            mono=True,
            duration=_MAX_LOAD_DURATION,
        )
        if y is None or len(y) == 0:
            return None
        return y, sr
    except Exception:
        return None


def detect_bpm(filepath: str) -> Optional[float]:
    """Estimate BPM for an audio file. Returns rounded float or None on failure."""
    loaded = _load_audio(filepath)
    if loaded is None:
        return None
    y, sr = loaded
    try:
        import librosa

        tempo, _beats = librosa.beat.beat_track(y=y, sr=sr)
        # librosa may return an ndarray scalar
        if hasattr(tempo, "item"):
            tempo = tempo.item()
        bpm = float(tempo)
        if not np.isfinite(bpm) or bpm <= 0:
            return None
        return round(bpm, 1)
    except Exception:
        return None


def detect_key(filepath: str) -> Optional[str]:
    """Estimate musical key for an audio file.

    Returns a short string like "C maj" or "F# min", or None on failure.
    Uses the Krumhansl-Schmuckler algorithm on a CQT chroma vector.
    """
    loaded = _load_audio(filepath)
    if loaded is None:
        return None
    y, sr = loaded
    try:
        import librosa

        chroma = librosa.feature.chroma_cqt(y=y, sr=sr)
        if chroma is None or chroma.size == 0:
            return None
        # Average chroma energy across time -> 12-element pitch class vector
        pitch_vec = chroma.mean(axis=1)
        if not np.any(pitch_vec):
            return None

        # Correlate with all 24 key profiles (12 major + 12 minor rotations)
        best_score = -np.inf
        best_key = None
        for tonic in range(12):
            major_profile = np.roll(_KS_MAJOR, tonic)
            minor_profile = np.roll(_KS_MINOR, tonic)
            score_maj = float(np.corrcoef(pitch_vec, major_profile)[0, 1])
            score_min = float(np.corrcoef(pitch_vec, minor_profile)[0, 1])
            # corrcoef returns NaN if either input has zero variance
            # (e.g. constant-valued pitch_vec). Exclude NaN scores so they
            # don't poison the best-key comparison via the always-False
            # behaviour of `NaN > x`.
            if not np.isfinite(score_maj):
                score_maj = -np.inf
            if not np.isfinite(score_min):
                score_min = -np.inf
            if score_maj > best_score:
                best_score = score_maj
                best_key = f"{_PITCH_NAMES[tonic]} maj"
            if score_min > best_score:
                best_score = score_min
                best_key = f"{_PITCH_NAMES[tonic]} min"
        return best_key
    except Exception:
        return None


def detect_bpm_and_key(filepath: str) -> tuple[Optional[float], Optional[str]]:
    """Convenience: load audio once and return both BPM and key."""
    loaded = _load_audio(filepath)
    if loaded is None:
        return None, None
    y, sr = loaded

    bpm: Optional[float] = None
    key: Optional[str] = None

    try:
        import librosa

        tempo, _ = librosa.beat.beat_track(y=y, sr=sr)
        if hasattr(tempo, "item"):
            tempo = tempo.item()
        bpm_val = float(tempo)
        if np.isfinite(bpm_val) and bpm_val > 0:
            bpm = round(bpm_val, 1)
    except Exception:
        bpm = None

    try:
        import librosa

        chroma = librosa.feature.chroma_cqt(y=y, sr=sr)
        if chroma is not None and chroma.size > 0:
            pitch_vec = chroma.mean(axis=1)
            if np.any(pitch_vec):
                best_score = -np.inf
                for tonic in range(12):
                    major_profile = np.roll(_KS_MAJOR, tonic)
                    minor_profile = np.roll(_KS_MINOR, tonic)
                    score_maj = float(np.corrcoef(pitch_vec, major_profile)[0, 1])
                    score_min = float(np.corrcoef(pitch_vec, minor_profile)[0, 1])
                    if not np.isfinite(score_maj):
                        score_maj = -np.inf
                    if not np.isfinite(score_min):
                        score_min = -np.inf
                    if score_maj > best_score:
                        best_score = score_maj
                        key = f"{_PITCH_NAMES[tonic]} maj"
                    if score_min > best_score:
                        best_score = score_min
                        key = f"{_PITCH_NAMES[tonic]} min"
    except Exception:
        key = None

    return bpm, key
