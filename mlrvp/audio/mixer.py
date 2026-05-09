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
        self.aux_mute = True
        self.aux_fx_route = 0  # 0=OFF (main), 1=FX1, 2=FX2, 3=FX3, 4=FX4
        self.master_volume = 1.0

        # Peak levels per group (updated each process call)
        self.group_peaks = [0.0] * NUM_GROUPS

        # Pre-allocated buffers to avoid per-callback allocation.
        # Allocate at a generous max size so dynamic buffer size changes
        # never trigger reallocation on the audio thread.
        _MAX_BUF = max(BUFFER_SIZE, 4096)
        self._buf_size = _MAX_BUF
        self._group_buses = [np.zeros((_MAX_BUF, 2), dtype=np.float32) for _ in range(NUM_GROUPS)]
        self._group_audio = [np.zeros((_MAX_BUF, 2), dtype=np.float32) for _ in range(NUM_GROUPS)]
        self._main_bus = np.zeros((_MAX_BUF, 2), dtype=np.float32)
        self._aux_send = np.zeros((_MAX_BUF, 2), dtype=np.float32)
        self._fx1_bus = np.zeros((_MAX_BUF, 2), dtype=np.float32)
        self._fx2_bus = np.zeros((_MAX_BUF, 2), dtype=np.float32)
        self._fx3_bus = np.zeros((_MAX_BUF, 2), dtype=np.float32)
        self._fx4_bus = np.zeros((_MAX_BUF, 2), dtype=np.float32)
        # Temp buffer for wet/dry routing (avoids per-group temporaries)
        self._route_tmp = np.zeros((_MAX_BUF, 2), dtype=np.float32)
        # Temp buffer for peak calculation (avoids per-block np.abs alloc)
        self._abs_tmp = np.zeros((_MAX_BUF, 2), dtype=np.float32)
        # Pre-allocated list for group_buses views (avoids list-comp per block)
        self._group_bus_views = [gb[:_MAX_BUF] for gb in self._group_buses]
        # Pre-allocated result list (avoids tuple creation per block)
        self._result = [None, None, None, None, None, None]
        # Volume smoothing: DSP-side current gain per group (ramps toward group.volume)
        # Prevents clicks when volume jumps suddenly (e.g. grid button press in mix mode)
        self._smooth_volumes = [1.0] * NUM_GROUPS
        # Pre-allocated 1-D ramp buffer — reused each callback, avoids allocation
        self._vol_ramp = np.zeros(_MAX_BUF, dtype=np.float32)

    def process_groups(self, channel_outputs: list[tuple[int, np.ndarray]],
                       num_frames: int) -> list:
        """Mix channel outputs into group buses.

        Args:
            channel_outputs: list of (group_index, stereo_audio) tuples
            num_frames: number of audio frames

        Returns:
            (main_bus, aux_send_bus, fx1_bus, fx2_bus, fx3_bus, fx4_bus) - all stereo arrays
        """
        # Re-allocate only if buffer size exceeds pre-allocated max
        if num_frames <= 0:
            return (self._main_bus[:0], self._aux_send[:0], self._fx1_bus[:0],
                    self._fx2_bus[:0], self._fx3_bus[:0], self._fx4_bus[:0])
        if num_frames > self._buf_size:
            self._buf_size = num_frames
            self._group_buses = [np.zeros((num_frames, 2), dtype=np.float32) for _ in range(NUM_GROUPS)]
            self._group_audio = [np.zeros((num_frames, 2), dtype=np.float32) for _ in range(NUM_GROUPS)]
            self._main_bus = np.zeros((num_frames, 2), dtype=np.float32)
            self._aux_send = np.zeros((num_frames, 2), dtype=np.float32)
            self._fx1_bus = np.zeros((num_frames, 2), dtype=np.float32)
            self._fx2_bus = np.zeros((num_frames, 2), dtype=np.float32)
            self._fx3_bus = np.zeros((num_frames, 2), dtype=np.float32)
            self._fx4_bus = np.zeros((num_frames, 2), dtype=np.float32)
            self._route_tmp = np.zeros((num_frames, 2), dtype=np.float32)
            self._abs_tmp = np.zeros((num_frames, 2), dtype=np.float32)
            self._group_bus_views = [gb[:num_frames] for gb in self._group_buses]

        # Update pre-allocated view list (no list allocation)
        group_buses = self._group_bus_views
        for _gi in range(NUM_GROUPS):
            group_buses[_gi] = self._group_buses[_gi][:num_frames]
        main_bus = self._main_bus[:num_frames]
        aux_send = self._aux_send[:num_frames]
        fx1_bus = self._fx1_bus[:num_frames]
        fx2_bus = self._fx2_bus[:num_frames]
        fx3_bus = self._fx3_bus[:num_frames]
        fx4_bus = self._fx4_bus[:num_frames]

        for gb in group_buses:
            gb[:] = 0
        main_bus[:] = 0
        aux_send[:] = 0
        fx1_bus[:] = 0
        fx2_bus[:] = 0
        fx3_bus[:] = 0
        fx4_bus[:] = 0

        # Sum channels into their assigned groups
        for group_idx, audio in channel_outputs:
            g = max(0, min(group_idx, NUM_GROUPS - 1))
            group_buses[g] += audio

        for i, (bus, group) in enumerate(zip(group_buses, self.groups)):
            if group.mute:
                self.group_peaks[i] = self.group_peaks[i] * 0.95
                self._group_audio[i][:num_frames] = 0
                continue

            # Apply group volume — ramp from previous value to target over this
            # buffer so sudden jumps (e.g. mix-mode button press) don't click.
            scaled = self._group_audio[i][:num_frames]
            current_vol = self._smooth_volumes[i]
            target_vol = group.volume
            if abs(target_vol - current_vol) < 1e-4:
                # Stable — fast scalar multiply
                self._smooth_volumes[i] = target_vol
                np.multiply(bus, target_vol, out=scaled)
            else:
                # Ramp linearly from current to target over this buffer
                ramp = self._vol_ramp[:num_frames]
                step = (target_vol - current_vol) / max(num_frames - 1, 1)
                np.multiply(np.arange(num_frames, dtype=np.float32), step, out=ramp)
                ramp += current_vol
                np.multiply(bus, ramp[:, np.newaxis], out=scaled)
                self._smooth_volumes[i] = target_vol

            # Track peak level (with decay) — use pre-allocated tmp for max
            abs_tmp = self._abs_tmp[:num_frames]
            np.abs(scaled, out=abs_tmp)
            peak = float(abs_tmp.max())
            if not np.isfinite(peak):
                peak = 0.0
            self.group_peaks[i] = max(self.group_peaks[i] * 0.95, peak)

            # Route to FX bus or main using send fader as wet/dry mix
            fx_route = group.fx_route
            if fx_route == 1:
                wet = group.send  # send fader = wet amount
                dry = 1.0 - wet
                tmp = self._route_tmp[:num_frames]
                np.multiply(scaled, dry, out=tmp)
                main_bus += tmp
                np.multiply(scaled, wet, out=tmp)
                fx1_bus += tmp
            elif fx_route == 2:
                wet = group.send
                dry = 1.0 - wet
                tmp = self._route_tmp[:num_frames]
                np.multiply(scaled, dry, out=tmp)
                main_bus += tmp
                np.multiply(scaled, wet, out=tmp)
                fx2_bus += tmp
            elif fx_route == 3:
                wet = group.send
                dry = 1.0 - wet
                tmp = self._route_tmp[:num_frames]
                np.multiply(scaled, dry, out=tmp)
                main_bus += tmp
                np.multiply(scaled, wet, out=tmp)
                fx3_bus += tmp
            elif fx_route == 4:
                wet = group.send
                dry = 1.0 - wet
                tmp = self._route_tmp[:num_frames]
                np.multiply(scaled, dry, out=tmp)
                main_bus += tmp
                np.multiply(scaled, wet, out=tmp)
                fx4_bus += tmp
            else:
                main_bus += scaled

            # Aux send (post-fader, only when no FX routing)
            if self.aux_send_level > 0.0 and fx_route == 0:
                tmp = self._route_tmp[:num_frames]
                np.multiply(scaled, group.send * self.aux_send_level, out=tmp)
                aux_send += tmp

        r = self._result
        r[0] = main_bus; r[1] = aux_send
        r[2] = fx1_bus; r[3] = fx2_bus; r[4] = fx3_bus; r[5] = fx4_bus
        return r

    def apply_master(self, main_bus: np.ndarray, aux_return: np.ndarray) -> np.ndarray:
        """Apply master volume and sum in aux return.

        Args:
            main_bus: main stereo bus from groups
            aux_return: processed aux return (from delay effect)

        Returns:
            Final stereo output
        """
        # In-place: main_bus += aux_return * aux_volume, then *= master
        if self.aux_volume != 0.0:
            n = len(main_bus)
            tmp = self._route_tmp[:n]
            np.multiply(aux_return, self.aux_volume, out=tmp)
            main_bus += tmp
        main_bus *= self.master_volume

        # Soft clip to prevent harsh clipping
        np.clip(main_bus, -1.0, 1.0, out=main_bus)
        return main_bus
