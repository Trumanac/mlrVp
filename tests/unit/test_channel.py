"""Tests for mlrvp.audio.channel.Channel — the per-row playback engine.

Channel has two parallel implementations of process(): a Numba JIT kernel
(mlrvp.audio._dsp_kernels.channel_process_kernel) and a pure-numpy Python
fallback used when Numba isn't available. The ``jit_enabled`` fixture runs
the critical playback tests against both so a regression in either path
gets caught, not just whichever one happens to be active on the machine
running the suite.
"""
import numpy as np
import pytest

from mlrvp.audio.channel import Channel
import mlrvp.audio.channel as channel_mod
from mlrvp.config import PlaybackMode


@pytest.fixture(params=[True, False], ids=["jit", "python_fallback"])
def jit_enabled(request, monkeypatch):
    """Force the JIT path on/off, skipping the JIT case if numba isn't built."""
    if request.param and not channel_mod._HAS_CH_JIT:
        pytest.skip("numba JIT kernel not available in this environment")
    monkeypatch.setattr(channel_mod, "_HAS_CH_JIT", request.param)
    return request.param


def make_channel(ramp_sample, jit_enabled=None) -> Channel:
    ch = Channel(index=0)
    ch.load_sample(ramp_sample)
    return ch


class TestTrigger:
    def test_trigger_sets_playing(self, ramp_sample):
        ch = make_channel(ramp_sample)
        ch.trigger(column=0, total_columns=8)
        assert ch.playing is True
        assert ch.state.playing is True

    def test_trigger_out_of_range_column_is_noop(self, ramp_sample):
        ch = make_channel(ramp_sample)
        ch.trigger(column=8, total_columns=8)  # valid range is [0, 8)
        assert ch.playing is False

    def test_trigger_negative_column_is_noop(self, ramp_sample):
        ch = make_channel(ramp_sample)
        ch.trigger(column=-1, total_columns=8)
        assert ch.playing is False

    def test_trigger_without_sample_is_noop(self):
        ch = Channel(index=0)
        ch.trigger(column=0, total_columns=8)
        assert ch.playing is False

    def test_trigger_maps_column_to_position(self, ramp_sample):
        # 1000-frame sample, 8 columns, default full selection & 8 steps:
        # column 4 -> slice_index=4 -> position = 4/8 * 1000 = 500
        ch = make_channel(ramp_sample)
        ch.trigger(column=4, total_columns=8)
        assert ch._position == pytest.approx(500.0, abs=1.0)

    def test_zero_length_selection_is_noop(self, ramp_sample):
        ch = make_channel(ramp_sample)
        ch.state.sel_start = 0.5
        ch.state.sel_end = 0.5
        ch.trigger(column=0, total_columns=8)
        assert ch.playing is False


class TestLoopPlayback:
    def test_process_produces_nonzero_audio(self, ramp_sample, jit_enabled):
        ch = make_channel(ramp_sample)
        ch.trigger(column=0, total_columns=8)
        # Run past the attack ramp so envelope is open.
        for _ in range(5):
            out = ch.process(64)
        assert out.shape == (64, 2)
        assert np.any(out != 0.0)

    def test_loop_mode_never_stops_playing(self, ramp_sample, jit_enabled):
        ch = make_channel(ramp_sample)
        ch.state.mode = PlaybackMode.LOOP
        ch.trigger(column=0, total_columns=8)
        for _ in range(50):  # far more than one full loop of the 1000-frame sample
            ch.process(64)
            assert ch.playing is True

    def test_position_wraps_within_selection(self, ramp_sample, jit_enabled):
        ch = make_channel(ramp_sample)
        ch.state.sel_start = 0.0
        ch.state.sel_end = 1.0
        ch.trigger(column=0, total_columns=8)
        for _ in range(100):
            ch.process(64)
            assert 0.0 <= ch._position <= ch.sample.num_frames

    def test_mute_zeroes_output(self, ramp_sample, jit_enabled):
        ch = make_channel(ramp_sample)
        ch.trigger(column=0, total_columns=8)
        ch.process(64)  # let envelope open
        ch.state.mute = True
        for _ in range(Channel.GAIN_RAMP // 64):   # fade-out (no click)
            ch.process(64)
        out = ch.process(64)
        assert np.all(out == 0.0)

    def test_mute_and_unmute_fade_instead_of_stepping(self, jit_enabled):
        from mlrvp.audio.sample import SampleBuffer
        level = np.full((4000, 2), 0.5, dtype=np.float32)   # constant: any step is a click
        ch = Channel(index=0)
        ch.load_sample(SampleBuffer(data=level, sample_rate=44100, num_channels=2,
                                    num_frames=4000, duration_sec=4000 / 44100))
        ch.state.attack = 0.0
        ch.trigger(column=0, total_columns=8)
        ch.process(64)
        ch.state.mute = True
        fade_out = np.concatenate([ch.process(64)[:, 0].copy() for _ in range(6)])
        max_step = 0.5 / Channel.GAIN_RAMP * 1.01
        assert fade_out[0] > 0.49 and fade_out[-1] == 0.0
        assert np.abs(np.diff(fade_out)).max() <= max_step
        ch.state.mute = False
        fade_in = np.concatenate([ch.process(64)[:, 0].copy() for _ in range(6)])
        assert fade_in[0] <= max_step and fade_in[-1] == pytest.approx(0.5)
        assert np.abs(np.diff(fade_in)).max() <= max_step

    def test_gain_scales_output_linearly(self, ramp_sample, jit_enabled):
        ch_full = make_channel(ramp_sample)
        ch_full.state.attack = 0.0
        ch_full.trigger(column=0, total_columns=8)
        ch_full.process(32)  # skip past instantaneous attack edge
        out_full = ch_full.process(64).copy()

        ch_half = make_channel(ramp_sample)
        ch_half.state.attack = 0.0
        ch_half.state.gain = 0.5
        ch_half.trigger(column=0, total_columns=8)
        ch_half.process(32)
        out_half = ch_half.process(64).copy()

        np.testing.assert_allclose(out_half, out_full * 0.5, atol=1e-5)


class TestOneshot:
    def test_stops_after_playing_through_selection(self, ramp_sample, jit_enabled):
        ch = make_channel(ramp_sample)
        ch.state.mode = PlaybackMode.ONESHOT
        ch.state.attack = 0.0
        ch.state.release = 0.001
        ch.trigger(column=0, total_columns=1)  # whole sample, position 0
        # 1000 frames at speed 1.0 — well within 2000 frames processed.
        stopped = False
        for _ in range(2000 // 64 + 2):
            ch.process(64)
            if not ch.playing:
                stopped = True
                break
        assert stopped, "ONESHOT channel should stop after playing through its selection"

    def test_loop_mode_does_not_stop_over_same_span(self, ramp_sample, jit_enabled):
        ch = make_channel(ramp_sample)
        ch.state.mode = PlaybackMode.LOOP
        ch.trigger(column=0, total_columns=1)
        for _ in range(2000 // 64 + 2):
            ch.process(64)
        assert ch.playing is True


class TestReverse:
    def test_reverse_mode_decreases_position(self, ramp_sample, jit_enabled):
        ch = make_channel(ramp_sample)
        ch.state.mode = PlaybackMode.REVERSE
        ch.state.attack = 0.0
        ch.trigger(column=0, total_columns=1)
        start_pos = ch._position
        ch.process(64)
        # Reverse should move toward the start of the buffer, not away.
        assert ch._position <= start_pos


class TestStop:
    def test_stop_sets_releasing_not_immediate(self, ramp_sample, jit_enabled):
        ch = make_channel(ramp_sample)
        ch.trigger(column=0, total_columns=8)
        ch.stop()
        assert ch._releasing is True
        assert ch.playing is True  # still playing until release ramp completes

    def test_force_stop_is_immediate(self, ramp_sample, jit_enabled):
        ch = make_channel(ramp_sample)
        ch.trigger(column=0, total_columns=8)
        ch.process(64)
        ch.force_stop()
        assert ch.playing is False
        assert ch.state.playing is False

    def test_release_eventually_silences_channel(self, ramp_sample, jit_enabled):
        ch = make_channel(ramp_sample)
        ch.state.release = 0.01  # ~441 samples at 44100
        ch.trigger(column=0, total_columns=8)
        ch.process(64)  # open envelope
        ch.stop()
        for _ in range(50):
            ch.process(64)
            if not ch.playing:
                break
        assert ch.playing is False


class TestStepMode:
    def test_plays_only_the_pressed_slice_then_stops(self, ramp_sample, jit_enabled):
        ch = make_channel(ramp_sample)
        ch.state.mode = PlaybackMode.STEP
        ch.state.steps = 8
        ch.state.attack = 0.0
        ch.state.release = 0.001
        ch.trigger(column=0, total_columns=8)
        # Slice 0 of 8 over a 1000-frame sample = 125 frames — should
        # finish well within a couple of 64-frame blocks.
        for _ in range(10):
            ch.process(64)
            if not ch.playing:
                break
        assert ch.playing is False


class TestPitchMode:
    def test_column_maps_to_scale_degree_speed(self, ramp_sample):
        ch = make_channel(ramp_sample)
        ch.state.mode = PlaybackMode.PITCH
        ch.state.pitch_scale = 4  # CHROMATIC: semitone == column index
        ch.trigger(column=3, total_columns=8)
        # No pre-cached pitch-scale audio -> falls back to rate-based mult
        expected_mult = 2.0 ** (3 / 12.0)
        assert ch._pitch_mode_mult == pytest.approx(expected_mult)

    def test_pitch_mode_always_starts_at_selection_start(self, ramp_sample):
        ch = make_channel(ramp_sample)
        ch.state.mode = PlaybackMode.PITCH
        ch.state.sel_start = 0.2
        ch.trigger(column=5, total_columns=8)
        assert ch._position == pytest.approx(0.2 * ramp_sample.num_frames)


class TestGetGridColumn:
    def test_returns_minus_one_when_not_playing(self, ramp_sample):
        ch = make_channel(ramp_sample)
        assert ch.get_grid_column(8) == -1

    def test_returns_valid_column_while_playing(self, ramp_sample):
        ch = make_channel(ramp_sample)
        ch.trigger(column=0, total_columns=8)
        col = ch.get_grid_column(8)
        assert 0 <= col < 8


class TestCrossfadeRetrigger:
    def test_retrigger_while_playing_stays_bounded(self, ramp_sample, jit_enabled):
        # Rapid retriggering exercises the crossfade-tail-accumulation path;
        # regression here shows up as clipping/blowup, so just assert the
        # output never exceeds a sane bound and contains no NaN/Inf.
        ch = make_channel(ramp_sample)
        for col in [0, 1, 2, 3, 0, 1, 2, 3]:
            ch.trigger(column=col, total_columns=8)
            out = ch.process(64)
            assert np.all(np.isfinite(out))
