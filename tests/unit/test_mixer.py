"""Tests for mlrvp.audio.mixer.Mixer — group summing, routing, master output."""
import numpy as np
import pytest

from mlrvp.audio.mixer import Mixer


def const_audio(num_frames: int, value: float = 1.0) -> np.ndarray:
    return np.full((num_frames, 2), value, dtype=np.float32)


class TestGroupSumming:
    def test_channels_in_same_group_sum(self):
        mx = Mixer()
        outputs = [(0, const_audio(64, 0.3)), (0, const_audio(64, 0.2))]
        main, *_ = mx.process_groups(outputs, 64)
        np.testing.assert_allclose(main, const_audio(64, 0.5), atol=1e-6)

    def test_different_groups_both_reach_main(self):
        mx = Mixer()
        outputs = [(0, const_audio(64, 0.3)), (1, const_audio(64, 0.2))]
        main, *_ = mx.process_groups(outputs, 64)
        np.testing.assert_allclose(main, const_audio(64, 0.5), atol=1e-6)

    def test_out_of_range_group_index_is_clamped(self):
        mx = Mixer()
        outputs = [(999, const_audio(64, 0.5))]
        # Should clamp into the last valid group rather than raise/crash.
        main, *_ = mx.process_groups(outputs, 64)
        np.testing.assert_allclose(main, const_audio(64, 0.5), atol=1e-6)

    def test_empty_channel_list_yields_silence(self):
        mx = Mixer()
        main, *_ = mx.process_groups([], 64)
        assert np.all(main == 0.0)

    def test_zero_frames_returns_empty_arrays(self):
        mx = Mixer()
        main, aux, fx1, fx2, fx3, fx4 = mx.process_groups([], 0)
        assert len(main) == 0


class TestMute:
    def test_muted_group_does_not_reach_main(self):
        mx = Mixer()
        mx.groups[0].mute = True
        outputs = [(0, const_audio(64, 1.0))]
        first, *_ = mx.process_groups(outputs, 64)
        assert first[0, 0] > 0.9 and first[-1, 0] == 0.0   # fades out, no click
        main, *_ = mx.process_groups(outputs, 64)
        assert np.all(main == 0.0)

    def test_unmuted_group_still_passes(self):
        mx = Mixer()
        mx.groups[0].mute = False
        outputs = [(0, const_audio(64, 1.0))]
        main, *_ = mx.process_groups(outputs, 64)
        assert np.any(main != 0.0)


class TestVolume:
    def test_volume_scales_group_output(self):
        mx = Mixer()
        mx.groups[0].volume = 0.5
        # Prime smoothing so the ramp doesn't interfere with the assertion.
        mx.reset_smooth_volumes()
        outputs = [(0, const_audio(64, 1.0))]
        main, *_ = mx.process_groups(outputs, 64)
        np.testing.assert_allclose(main, const_audio(64, 0.5), atol=1e-5)

    def test_reset_smooth_volumes_snaps_immediately(self):
        mx = Mixer()
        mx.groups[0].volume = 0.1
        mx.reset_smooth_volumes()
        outputs = [(0, const_audio(64, 1.0))]
        main, *_ = mx.process_groups(outputs, 64)
        # With smoothing reset, the very first block should already be at
        # the target volume rather than ramping in from 1.0.
        np.testing.assert_allclose(main, const_audio(64, 0.1), atol=1e-3)


class TestFxRouting:
    @pytest.mark.parametrize("route,bus_index", [(1, 2), (2, 3), (3, 4), (4, 5)])
    def test_full_send_routes_entirely_to_target_bus(self, route, bus_index):
        mx = Mixer()
        mx.groups[0].fx_route = route
        mx.groups[0].send = 1.0  # 100% wet
        mx.reset_smooth_volumes()
        outputs = [(0, const_audio(64, 1.0))]
        result = mx.process_groups(outputs, 64)
        main = result[0]
        fx_bus = result[bus_index]
        assert np.all(main == 0.0)
        assert np.any(fx_bus != 0.0)

    def test_partial_send_splits_between_main_and_fx(self):
        mx = Mixer()
        mx.groups[0].fx_route = 1
        mx.groups[0].send = 0.5
        mx.reset_smooth_volumes()
        outputs = [(0, const_audio(64, 1.0))]
        main, aux, fx1, fx2, fx3, fx4 = mx.process_groups(outputs, 64)
        np.testing.assert_allclose(main, const_audio(64, 0.5), atol=1e-5)
        np.testing.assert_allclose(fx1, const_audio(64, 0.5), atol=1e-5)

    def test_route_off_sends_only_to_main(self):
        mx = Mixer()
        mx.groups[0].fx_route = 0
        mx.reset_smooth_volumes()
        outputs = [(0, const_audio(64, 1.0))]
        main, aux, fx1, fx2, fx3, fx4 = mx.process_groups(outputs, 64)
        np.testing.assert_allclose(main, const_audio(64, 1.0), atol=1e-5)
        assert np.all(fx1 == 0.0)


class TestAuxSend:
    def test_aux_send_only_when_no_fx_route(self):
        mx = Mixer()
        mx.groups[0].fx_route = 0
        mx.groups[0].send = 1.0
        mx.aux_send_level = 1.0
        mx.reset_smooth_volumes()
        outputs = [(0, const_audio(64, 1.0))]
        main, aux, *_ = mx.process_groups(outputs, 64)
        assert np.any(aux != 0.0)

    def test_no_aux_send_when_fx_routed(self):
        mx = Mixer()
        mx.groups[0].fx_route = 1
        mx.groups[0].send = 1.0
        mx.aux_send_level = 1.0
        mx.reset_smooth_volumes()
        outputs = [(0, const_audio(64, 1.0))]
        main, aux, *_ = mx.process_groups(outputs, 64)
        assert np.all(aux == 0.0)


class TestGroupPeaks:
    def test_peak_tracks_loudest_block(self):
        mx = Mixer()
        mx.reset_smooth_volumes()
        outputs = [(0, const_audio(64, 0.8))]
        mx.process_groups(outputs, 64)
        assert mx.group_peaks[0] == pytest.approx(0.8, abs=1e-4)

    def test_peak_decays_after_silence(self):
        mx = Mixer()
        mx.reset_smooth_volumes()
        mx.process_groups([(0, const_audio(64, 1.0))], 64)
        loud_peak = mx.group_peaks[0]
        for _ in range(20):
            mx.process_groups([], 64)
        assert mx.group_peaks[0] < loud_peak

    def test_nan_input_does_not_poison_peak(self):
        mx = Mixer()
        mx.reset_smooth_volumes()
        bad = np.full((64, 2), np.nan, dtype=np.float32)
        mx.process_groups([(0, bad)], 64)
        assert np.isfinite(mx.group_peaks[0])


class TestApplyMaster:
    def test_master_volume_scales_output(self):
        mx = Mixer()
        mx.master_volume = 0.5
        aux_return = np.zeros((64, 2), dtype=np.float32)
        ramp = mx.apply_master(const_audio(64, 0.4), aux_return)
        # Ramps from the previous volume over one block (no click) ...
        assert ramp[0, 0] == pytest.approx(0.4) and ramp[-1, 0] == pytest.approx(0.2)
        assert np.all(np.diff(ramp[:, 0]) <= 0)
        # ... then holds the new level
        out = mx.apply_master(const_audio(64, 0.4), aux_return)
        np.testing.assert_allclose(out, const_audio(64, 0.2), atol=1e-5)

    def test_output_is_clipped_to_unit_range(self):
        mx = Mixer()
        mx.master_volume = 1.0
        main = const_audio(64, 5.0)  # way over 0dBFS
        aux_return = np.zeros((64, 2), dtype=np.float32)
        out = mx.apply_master(main, aux_return)
        assert np.all(out <= 1.0)
        assert np.all(out >= -1.0)

    def test_aux_return_is_summed_in(self):
        mx = Mixer()
        mx.aux_volume = 1.0
        main = const_audio(64, 0.2)
        aux_return = const_audio(64, 0.1)
        out = mx.apply_master(main, aux_return)
        np.testing.assert_allclose(out, const_audio(64, 0.3), atol=1e-5)

    def test_zero_aux_volume_skips_aux_entirely(self):
        mx = Mixer()
        mx.aux_volume = 0.0
        main = const_audio(64, 0.2)
        aux_return = const_audio(64, 99.0)  # would blow the mix up if added
        out = mx.apply_master(main, aux_return)
        np.testing.assert_allclose(out, const_audio(64, 0.2), atol=1e-5)


class TestBufferGrowth:
    def test_process_groups_handles_larger_block_than_preallocated(self):
        mx = Mixer()
        big = 8192  # larger than the internal 4096 preallocation
        outputs = [(0, const_audio(big, 0.5))]
        main, *_ = mx.process_groups(outputs, big)
        assert main.shape == (big, 2)
        np.testing.assert_allclose(main, const_audio(big, 0.5), atol=1e-5)
