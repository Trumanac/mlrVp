"""Dedicated cue-preview audio service for the slicer tool.

Opens its own ``sounddevice.OutputStream`` so the slicer can play through a
different hardware device (e.g. headphones) without touching the main engine.
"""

import threading
import numpy as np
import sounddevice as sd
from typing import Optional


class SlicerPreviewService:
    """Stream-based slicer preview with independent device routing.

    * ``play()`` / ``stop()`` are safe to call from the Qt GUI thread.
    * The PortAudio callback only reads shared state behind ``_lock``.
    * The stream is opened lazily on ``play()`` and torn down on ``stop()``
      or device change so no resources are held while idle.
    """

    def __init__(self):
        self._stream: Optional[sd.OutputStream] = None
        self._buf: Optional[np.ndarray] = None  # float32, shape (N, ch)
        self._pos: int = 0
        self._loop: bool = False
        self._volume: float = 1.0
        self._device: int = -1          # -1 → system default
        self._channel_offset: int = 0   # first output channel index
        self._sr: int = 44100
        self._lock = threading.Lock()
        self._playing: bool = False
        # Pending loop region: swapped in when current loop iteration ends
        self._pending_buf: Optional[np.ndarray] = None
        self._buf_generation: int = 0  # incremented on each buffer swap

    # ── public API ────────────────────────────────────────────────

    @property
    def is_playing(self) -> bool:
        return self._playing

    @property
    def playback_progress(self) -> float:
        """Return 0.0–1.0 progress within the *original* (non-looped) segment."""
        with self._lock:
            if self._buf is None or len(self._buf) == 0:
                return 0.0
            return min(self._pos / len(self._buf), 1.0)

    @property
    def loop_progress(self) -> float:
        """Return 0.0–1.0 progress within the current loop iteration."""
        with self._lock:
            if self._buf is None or len(self._buf) == 0:
                return 0.0
            return (self._pos % len(self._buf)) / len(self._buf)

    @property
    def current_buf_len(self) -> int:
        """Length of the currently-playing buffer (samples)."""
        with self._lock:
            return len(self._buf) if self._buf is not None else 0

    @property
    def buf_generation(self) -> int:
        """Counter incremented each time a pending buffer is swapped in."""
        return self._buf_generation

    def set_volume(self, vol: float):
        """Thread-safe volume update; clamped 0–2."""
        self._volume = max(0.0, min(2.0, vol))

    def set_device(self, device: int, channel_offset: int = 0):
        """Change target device.  Stops any active playback."""
        was_playing = self._playing
        buf = None
        sr = self._sr
        loop = self._loop
        if was_playing:
            with self._lock:
                buf = self._buf
                sr = self._sr
                loop = self._loop
        self.stop()
        self._device = device
        self._channel_offset = channel_offset
        if was_playing and buf is not None:
            self.play(buf, sr, loop)

    def update_loop_segment(self, segment: np.ndarray):
        """Queue a new loop region; swapped in when the current iteration ends.

        Safe to call from the GUI thread while looping. If not currently
        playing or not looping, the pending buffer is silently ignored.
        """
        if segment is None or len(segment) == 0:
            return
        if segment.ndim == 1:
            segment = segment.reshape(-1, 1)
        segment = np.ascontiguousarray(segment, dtype=np.float32)
        with self._lock:
            self._pending_buf = segment

    def play(self, segment: np.ndarray, sr: int, loop: bool = False):
        """Start streaming *segment* on the configured cue device."""
        if segment is None or len(segment) == 0:
            return
        self.stop()
        # Ensure 2-D float32
        if segment.ndim == 1:
            segment = segment.reshape(-1, 1)
        segment = np.ascontiguousarray(segment, dtype=np.float32)

        with self._lock:
            self._buf = segment
            self._pos = 0
            self._loop = loop
            self._sr = sr
            self._pending_buf = None

        dev = self._device if self._device >= 0 else None
        ch_offset = self._channel_offset

        # Determine total output channels needed
        src_channels = segment.shape[1]
        out_channels = ch_offset + src_channels

        # Validate against device capability
        try:
            if dev is not None:
                info = sd.query_devices(dev, 'output')
            else:
                info = sd.query_devices(kind='output')
            max_ch = int(info['max_output_channels'])
            if out_channels > max_ch:
                # Clamp offset to fit
                ch_offset = max(0, max_ch - src_channels)
                self._channel_offset = ch_offset
                out_channels = ch_offset + src_channels
                if out_channels > max_ch:
                    out_channels = max_ch
        except Exception:
            out_channels = src_channels
            ch_offset = 0

        try:
            self._stream = sd.OutputStream(
                samplerate=sr,
                channels=out_channels,
                dtype='float32',
                device=dev,
                blocksize=512,
                callback=self._callback,
                finished_callback=self._on_finished,
            )
            self._stream.start()
            self._playing = True
        except Exception:
            self._playing = False
            self._stream = None
            raise

    def stop(self):
        """Tear down the stream (safe to call even when not playing)."""
        self._playing = False
        stream = self._stream
        self._stream = None
        if stream is not None:
            try:
                stream.stop()
                stream.close()
            except Exception:
                pass
        with self._lock:
            self._buf = None
            self._pos = 0
            self._pending_buf = None

    def close(self):
        """Alias for ``stop()`` — for use during application shutdown."""
        self.stop()

    # ── private PortAudio callback ────────────────────────────────

    def _callback(self, outdata: np.ndarray, frames: int,
                  time_info, status):
        with self._lock:
            buf = self._buf
            pos = self._pos
            loop = self._loop
            vol = self._volume
            ch_offset = self._channel_offset
            pending = self._pending_buf

        if buf is None:
            outdata[:] = 0
            return

        src_ch = buf.shape[1]
        remaining = len(buf) - pos

        if not loop and remaining <= 0:
            outdata[:] = 0
            raise sd.CallbackStop

        if loop:
            # Fill output by wrapping around the buffer
            written = 0
            while written < frames:
                chunk_start = pos % len(buf)
                # Check for pending buffer swap at loop boundary
                if chunk_start == 0 and pos > 0 and pending is not None:
                    with self._lock:
                        self._pending_buf = None
                        self._buf = pending
                        self._buf_generation += 1
                    buf = pending
                    src_ch = buf.shape[1]
                    pos = 0
                    chunk_start = 0
                    pending = None  # consumed
                avail = len(buf) - chunk_start
                need = frames - written
                n = min(avail, need)
                chunk = buf[chunk_start:chunk_start + n]
                dest_ch = min(src_ch, outdata.shape[1] - ch_offset)
                if dest_ch <= 0:
                    outdata[written:written + n] = 0
                else:
                    outdata[written:written + n, :] = 0
                    outdata[written:written + n, ch_offset:ch_offset + dest_ch] = chunk[:, :dest_ch]
                pos += n
                written += n
        else:
            n = min(remaining, frames)
            chunk = buf[pos:pos + n]
            dest_ch = min(src_ch, outdata.shape[1] - ch_offset)
            outdata[:] = 0
            if dest_ch > 0 and n > 0:
                outdata[:n, ch_offset:ch_offset + dest_ch] = chunk[:, :dest_ch]
            pos += n

        # Apply cue volume
        if vol != 1.0:
            outdata *= vol

        with self._lock:
            self._pos = pos

    def _on_finished(self):
        self._playing = False
