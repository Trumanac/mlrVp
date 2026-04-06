"""Mixer for mlrVp — group summing, aux send, and master output."""

import numpy as np
from mlrvp.config import NUM_GROUPS, GroupState, BUFFER_SIZE


class Mixer:
    """Mixer with group buses, aux send, and master output.

    Each channel is assigned to a group (0-3).
    Groups sum their channels, then pass through volume/mute/send.
    Aux send goes to the delay effect.
    Master sums all groups + processed aux return.
    """

    def __init__(self):
        self.groups = [GroupState() for _ in range(NUM_GROUPS)]
        self.aux_volume = 1.0
        self.aux_send_level = 1.0
        self.aux_mute = False
        self.aux_fx_route = 0  # 0=OFF (main), 1=FX1, 2=FX2
        self.master_volume = 1.0

        # Peak levels per group (updated each process call)
        self.group_peaks = [0.0] * NUM_GROUPS

        # Pre-allocated buffers to avoid per-callback allocation
        self._buf_size = BUFFER_SIZE
        self._group_buses = [np.zeros((BUFFER_SIZE, 2), dtype=np.float32) for _ in range(NUM_GROUPS)]
        self._group_audio = [np.zeros((BUFFER_SIZE, 2), dtype=np.float32) for _ in range(NUM_GROUPS)]
        self._main_bus = np.zeros((BUFFER_SIZE, 2), dtype=np.float32)
        self._aux_send = np.zeros((BUFFER_SIZE, 2), dtype=np.float32)
        self._fx1_bus = np.zeros((BUFFER_SIZE, 2), dtype=np.float32)
        self._fx2_bus = np.zeros((BUFFER_SIZE, 2), dtype=np.float32)

    def process_groups(self, channel_outputs: list[tuple[int, np.ndarray]],
                       num_frames: int) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
        """Mix channel outputs into group buses.

        Args:
            channel_outputs: list of (group_index, stereo_audio) tuples
            num_frames: number of audio frames

        Returns:
            (main_bus, aux_send_bus, fx1_bus, fx2_bus) - all stereo arrays
        """
        # Re-allocate if buffer size changed
        if num_frames != self._buf_size:
            self._buf_size = num_frames
            self._group_buses = [np.zeros((num_frames, 2), dtype=np.float32) for _ in range(NUM_GROUPS)]
            self._group_audio = [np.zeros((num_frames, 2), dtype=np.float32) for _ in range(NUM_GROUPS)]
            self._main_bus = np.zeros((num_frames, 2), dtype=np.float32)
            self._aux_send = np.zeros((num_frames, 2), dtype=np.float32)
            self._fx1_bus = np.zeros((num_frames, 2), dtype=np.float32)
            self._fx2_bus = np.zeros((num_frames, 2), dtype=np.float32)

        # Zero pre-allocated buffers
        for gb in self._group_buses:
            gb[:] = 0
        self._main_bus[:] = 0
        self._aux_send[:] = 0
        self._fx1_bus[:] = 0
        self._fx2_bus[:] = 0

        group_buses = self._group_buses
        main_bus = self._main_bus
        aux_send = self._aux_send
        fx1_bus = self._fx1_bus
        fx2_bus = self._fx2_bus

        # Sum channels into their assigned groups
        for group_idx, audio in channel_outputs:
            g = max(0, min(group_idx, NUM_GROUPS - 1))
            group_buses[g] += audio

        for i, (bus, group) in enumerate(zip(group_buses, self.groups)):
            if group.mute:
                self.group_peaks[i] = self.group_peaks[i] * 0.95
                continue

            # Apply group volume in-place into pre-allocated buffer
            scaled = self._group_audio[i]
            np.multiply(bus, group.volume, out=scaled)

            # Track peak level (with decay) — use pre-allocated tmp for max
            peak = float(np.abs(scaled).max())
            self.group_peaks[i] = max(self.group_peaks[i] * 0.95, peak)

            # Route to FX bus or main using send fader as wet/dry mix
            fx_route = getattr(group, 'fx_route', 0)
            if fx_route == 1:
                wet = group.send  # send fader = wet amount
                dry = 1.0 - wet
                main_bus += scaled * dry
                fx1_bus += scaled * wet
            elif fx_route == 2:
                wet = group.send
                dry = 1.0 - wet
                main_bus += scaled * dry
                fx2_bus += scaled * wet
            else:
                main_bus += scaled

            # Aux send (post-fader, only when no FX routing)
            if self.aux_send_level > 0.0 and fx_route == 0:
                aux_send += scaled * group.send * self.aux_send_level

        return main_bus, aux_send, fx1_bus, fx2_bus

    def apply_master(self, main_bus: np.ndarray, aux_return: np.ndarray) -> np.ndarray:
        """Apply master volume and sum in aux return.

        Args:
            main_bus: main stereo bus from groups
            aux_return: processed aux return (from delay effect)

        Returns:
            Final stereo output
        """
        output = main_bus + aux_return * self.aux_volume
        output *= self.master_volume

        # Soft clip to prevent harsh clipping
        np.clip(output, -1.0, 1.0, out=output)
        return output
