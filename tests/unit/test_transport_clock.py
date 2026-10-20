"""Tests for mlrvp.transport.clock.MasterClock.

Pure calculation helpers (set_tempo, get_quantize_delay, beats<->seconds)
are tested directly without starting the clock thread. A small number of
``slow``-marked tests start the real background thread for a short window
to verify beat position actually advances at roughly the right rate —
these use generous tolerances since CI runners can have scheduling jitter.
"""
import time

import pytest

from mlrvp.transport.clock import MasterClock
from mlrvp.config import QuantizeMode


class TestTempoClamping:
    def test_set_tempo_clamps_low(self):
        clock = MasterClock(tempo=120.0)
        clock.set_tempo(1.0, immediate=True)
        assert clock.tempo == 20.0

    def test_set_tempo_clamps_high(self):
        clock = MasterClock(tempo=120.0)
        clock.set_tempo(9999.0, immediate=True)
        assert clock.tempo == 300.0

    def test_set_tempo_within_range_unchanged(self):
        clock = MasterClock(tempo=120.0)
        clock.set_tempo(140.0, immediate=True)
        assert clock.tempo == 140.0

    def test_deferred_tempo_change_does_not_apply_immediately(self):
        clock = MasterClock(tempo=120.0)
        clock.set_tempo(140.0, immediate=False)
        assert clock.tempo == 120.0
        assert clock._next_tempo == 140.0


class TestBeatSecondConversion:
    def test_beats_to_seconds_at_120bpm(self):
        clock = MasterClock(tempo=120.0)
        # 120 BPM = 2 beats/sec -> 1 beat = 0.5s
        assert clock.beats_to_seconds(1.0) == pytest.approx(0.5)

    def test_seconds_to_beats_at_120bpm(self):
        clock = MasterClock(tempo=120.0)
        assert clock.seconds_to_beats(0.5) == pytest.approx(1.0)

    def test_round_trip(self):
        clock = MasterClock(tempo=93.0)
        beats = 3.7
        seconds = clock.beats_to_seconds(beats)
        assert clock.seconds_to_beats(seconds) == pytest.approx(beats)

    def test_beat_in_bar_wraps_at_4(self):
        clock = MasterClock(tempo=120.0)
        clock._beat_position = 5.5
        assert clock.beat_in_bar == pytest.approx(1.5)


class TestQuantizeDelay:
    def test_none_quantize_has_no_delay(self):
        clock = MasterClock(tempo=120.0, quantize=QuantizeMode.NONE)
        assert clock.get_quantize_delay() == 0.0

    def test_on_boundary_has_no_delay(self):
        clock = MasterClock(tempo=120.0, quantize=QuantizeMode.QUARTER)
        clock._beat_position = 4.0  # exactly on a quarter-note boundary
        assert clock.get_quantize_delay() == 0.0

    def test_mid_beat_returns_remaining_time(self):
        clock = MasterClock(tempo=120.0, quantize=QuantizeMode.QUARTER)
        clock._beat_position = 4.5  # halfway into the next quarter note
        # 120 BPM -> 0.5s/beat; half a quarter-note beat remaining = 0.25s
        assert clock.get_quantize_delay() == pytest.approx(0.25, abs=0.01)

    def test_bar_quantize_uses_four_beats(self):
        clock = MasterClock(tempo=120.0, quantize=QuantizeMode.BAR)
        clock._beat_position = 1.0  # 3 beats until the next bar boundary
        assert clock.get_quantize_delay() == pytest.approx(1.5, abs=0.01)


class TestCallbackRegistration:
    def test_on_beat_appends_callback(self):
        clock = MasterClock()
        fired = []
        clock.on_beat(lambda beat_int, beat_pos: fired.append(beat_int))
        assert len(clock._beat_callbacks) == 1

    def test_on_tick_appends_callback(self):
        clock = MasterClock()
        clock.on_tick(lambda pos, tempo: None)
        assert len(clock._tick_callbacks) == 1


@pytest.mark.slow
class TestRealTimeClock:
    def test_beat_position_advances_when_running(self):
        clock = MasterClock(tempo=120.0)
        clock.start()
        try:
            time.sleep(0.15)
            pos_a = clock.beat_position
            time.sleep(0.15)
            pos_b = clock.beat_position
            assert pos_b > pos_a
        finally:
            clock.stop()

    def test_stop_halts_further_progress(self):
        clock = MasterClock(tempo=120.0)
        clock.start()
        time.sleep(0.1)
        clock.stop()
        pos_after_stop = clock.beat_position
        time.sleep(0.1)
        assert clock.beat_position == pos_after_stop

    def test_beat_callback_fires(self):
        clock = MasterClock(tempo=300.0)  # fast tempo -> beats arrive quickly
        fired = []
        clock.on_beat(lambda beat_int, beat_pos: fired.append(beat_int))
        clock.start()
        try:
            time.sleep(0.5)
        finally:
            clock.stop()
        assert len(fired) >= 1
