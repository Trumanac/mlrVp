"""Tests for mlrvp.transport.pattern.PatternRecorder."""
import pytest

from mlrvp.transport.pattern import PatternRecorder


class TestRecordingLifecycle:
    def test_toggle_record_starts_recording(self):
        pr = PatternRecorder(index=0, length_beats=4)
        pr.toggle_record(current_beat=0.0)
        assert pr.recording is True
        assert pr.playing is False

    def test_toggle_record_again_stops_and_autoplays(self):
        pr = PatternRecorder(index=0, length_beats=4)
        pr.toggle_record(current_beat=0.0)
        pr.toggle_record(current_beat=2.0)
        assert pr.recording is False
        assert pr.playing is True

    def test_recording_clears_previous_events(self):
        pr = PatternRecorder(index=0, length_beats=4)
        pr.toggle_record(current_beat=0.0)
        pr.record_event(current_beat=0.5, row=1, column=0)
        pr.toggle_record(current_beat=2.0)  # stop -> has 1 event
        assert pr.has_events
        pr.toggle_record(current_beat=0.0)  # start again -> should clear
        assert not pr.has_events

    def test_stop_clears_both_flags(self):
        pr = PatternRecorder(index=0)
        pr.toggle_record(0.0)
        pr.stop()
        assert pr.recording is False
        assert pr.playing is False

    def test_clear_removes_events_and_stops(self):
        pr = PatternRecorder(index=0)
        pr.toggle_record(0.0)
        pr.record_event(0.5, row=0, column=0)
        pr.clear()
        assert not pr.has_events
        assert pr.recording is False
        assert pr.playing is False


class TestRecordEvent:
    def test_ignored_when_not_recording(self):
        pr = PatternRecorder(index=0)
        pr.record_event(current_beat=0.5, row=0, column=0)
        assert not pr.has_events

    def test_position_recorded_relative_to_start_beat(self):
        pr = PatternRecorder(index=0, length_beats=4)
        pr.toggle_record(current_beat=10.0)  # start recording at beat 10
        pr.record_event(current_beat=10.5, row=2, column=3)
        assert pr.events[0].beat_position == pytest.approx(0.5)
        assert pr.events[0].row == 2
        assert pr.events[0].column == 3

    def test_position_wraps_within_pattern_length(self):
        pr = PatternRecorder(index=0, length_beats=4)
        pr.toggle_record(current_beat=0.0)
        pr.record_event(current_beat=5.0, row=0, column=0)  # 1 beat past a 4-beat loop
        assert pr.events[0].beat_position == pytest.approx(1.0)

    def test_event_cap_trims_oldest_batch(self):
        pr = PatternRecorder(index=0, length_beats=1000)
        pr.toggle_record(current_beat=0.0)
        for i in range(10001):
            pr.record_event(current_beat=float(i % 999), row=0, column=0)
        # Should have trimmed the oldest 1000 once the 10000 cap was hit,
        # never growing unboundedly.
        assert len(pr.events) < 10001


class TestRecordAutomation:
    def test_records_automation_event(self):
        pr = PatternRecorder(index=0, length_beats=4)
        pr.toggle_record(current_beat=0.0)
        pr.record_automation(current_beat=1.0, param_path="ch0.gain", value=0.75)
        assert pr.events[0].event_type == "automation"
        assert pr.events[0].param_path == "ch0.gain"
        assert pr.events[0].value == 0.75


class TestPlayback:
    def _recorded_pattern(self, events_at):
        """Build a stopped-recording PatternRecorder with events at the given beats."""
        pr = PatternRecorder(index=0, length_beats=4)
        pr.toggle_record(current_beat=0.0)
        for beat in events_at:
            pr.record_event(current_beat=beat, row=0, column=1)
        pr.toggle_record(current_beat=4.0)  # stop -> auto-play
        return pr

    def test_trigger_callback_fires_when_position_crosses_event(self):
        pr = self._recorded_pattern([1.0])
        fired = []
        pr.set_trigger_callback(lambda row, col, etype: fired.append((row, col, etype)))
        pr._start_beat = 0.0  # re-anchor playback start for a clean test
        pr.update(current_beat=0.5)   # establishes last_pos, no fire yet
        pr.update(current_beat=1.5)   # crosses beat 1.0 -> should fire
        assert fired == [(0, 1, "trigger")]

    def test_no_double_fire_for_same_event(self):
        pr = self._recorded_pattern([1.0])
        fired = []
        pr.set_trigger_callback(lambda row, col, etype: fired.append((row, col, etype)))
        pr._start_beat = 0.0
        pr.update(current_beat=0.5)
        pr.update(current_beat=1.5)
        pr.update(current_beat=1.6)  # still past 1.0, shouldn't refire
        assert len(fired) == 1

    def test_wraparound_fires_event_near_loop_end(self):
        # Event at beat 3.9 in a 4-beat loop; position moves from 3.5 -> 0.5
        # (wrapping past the loop boundary) and should still fire it.
        pr = self._recorded_pattern([3.9])
        fired = []
        pr.set_trigger_callback(lambda row, col, etype: fired.append((row, col, etype)))
        pr._start_beat = 0.0
        pr.update(current_beat=3.5)
        pr.update(current_beat=4.5)  # wraps to pos=0.5, crossing 3.9
        assert fired == [(0, 1, "trigger")]

    def test_automation_callback_used_for_automation_events(self):
        pr = PatternRecorder(index=0, length_beats=4)
        pr.toggle_record(current_beat=0.0)
        pr.record_automation(current_beat=1.0, param_path="p", value=0.5)
        pr.toggle_record(current_beat=4.0)
        pr._start_beat = 0.0

        trig_fired = []
        auto_fired = []
        pr.set_trigger_callback(lambda row, col, etype: trig_fired.append(1))
        pr.set_automation_callback(lambda path, val: auto_fired.append((path, val)))
        pr.update(current_beat=0.5)
        pr.update(current_beat=1.5)
        assert auto_fired == [("p", 0.5)]
        assert trig_fired == []

    def test_toggle_play_off_stops_firing(self):
        pr = self._recorded_pattern([1.0])
        fired = []
        pr.set_trigger_callback(lambda row, col, etype: fired.append(1))
        pr.toggle_play(current_beat=4.0)  # was auto-playing -> turn off
        assert pr.playing is False
        pr.update(current_beat=5.0)
        assert fired == []


class TestAutoStop:
    def test_recording_auto_stops_after_length_reached(self):
        pr = PatternRecorder(index=0, length_beats=4)
        pr.toggle_record(current_beat=0.0)
        pr.record_event(current_beat=1.0, row=0, column=0)
        pr.update(current_beat=4.5)  # past the 4-beat recording window
        assert pr.recording is False
        assert pr.playing is True  # has events -> auto-plays

    def test_auto_stop_with_no_events_does_not_autoplay(self):
        pr = PatternRecorder(index=0, length_beats=4)
        pr.toggle_record(current_beat=0.0)
        pr.update(current_beat=4.5)
        assert pr.recording is False
        assert pr.playing is False

    def test_state_change_callback_fires_on_autostop(self):
        pr = PatternRecorder(index=0, length_beats=4)
        pr.toggle_record(current_beat=0.0)
        pr.record_event(current_beat=1.0, row=0, column=0)
        changes = []
        pr.set_state_change_callback(lambda idx, state: changes.append((idx, state)))
        pr.update(current_beat=4.5)
        assert changes == [(0, "playing")]


class TestProgress:
    def test_progress_is_zero_when_not_playing(self):
        pr = PatternRecorder(index=0, length_beats=4)
        assert pr.progress == 0.0

    def test_progress_reflects_playback_position(self):
        pr = PatternRecorder(index=0, length_beats=4)
        # update() only advances _last_playback_pos when there's at least
        # one event and a trigger callback registered — an event-less
        # pattern has nothing to play back, so progress would stay at 0.
        pr.toggle_record(current_beat=0.0)
        pr.record_event(current_beat=0.0, row=0, column=0)
        pr.toggle_record(current_beat=4.0)  # stop -> auto-play
        pr.set_trigger_callback(lambda row, col, etype: None)
        pr._start_beat = 0.0
        pr.update(current_beat=1.0)  # 1 of 4 beats -> 25%
        assert pr.progress == pytest.approx(0.25)


class TestSetLength:
    def test_set_length_clamps_to_minimum_one(self):
        pr = PatternRecorder(index=0, length_beats=4)
        pr.set_length(0)
        assert pr.length_beats == 1

    def test_set_length_updates(self):
        pr = PatternRecorder(index=0, length_beats=4)
        pr.set_length(16)
        assert pr.length_beats == 16
