"""GUI tests for ChannelStrip, driven through the ChannelStripPage POM wrapper.

Marked `gui` (see pyproject.toml) — these build real Qt widgets under the
offscreen platform plugin (forced in conftest.py), so they're slower and
more environment-sensitive than the pure-logic tests in tests/unit/.
"""
import pytest

from mlrvp.config import ChannelState, PlaybackMode

from tests.gui.pages.channel_strip_page import ChannelStripPage

pytestmark = pytest.mark.gui


class TestMuteSoloStop:
    def test_starts_unmuted(self, qtbot):
        page = ChannelStripPage(qtbot)
        assert page.is_muted() is False

    def test_click_mute_toggles_checked_state(self, qtbot):
        page = ChannelStripPage(qtbot)
        page.click_mute()
        assert page.is_muted() is True
        page.click_mute()
        assert page.is_muted() is False

    def test_click_solo_toggles_checked_state(self, qtbot):
        page = ChannelStripPage(qtbot)
        page.click_solo()
        assert page.is_solo() is True

    def test_mute_emits_param_changed_with_row_and_value(self, qtbot):
        page = ChannelStripPage(qtbot, row=3)
        blocker = page.run_and_capture_signal(
            page.widget.param_changed, page.click_mute
        )
        row, name, value = blocker.args
        assert row == 3
        assert name == "mute"
        assert value is True

    def test_stop_click_does_not_raise(self, qtbot):
        # STOP has no associated checkable state to assert on directly here
        # (its effect is wired up by app.py to the audio engine) — this is
        # a smoke test that the button exists and is clickable.
        page = ChannelStripPage(qtbot)
        page.click_stop()


class TestGroupSelection:
    def test_defaults_to_group_zero(self, qtbot):
        page = ChannelStripPage(qtbot)
        assert page.get_selected_group() == 0

    def test_click_group_button_selects_it_exclusively(self, qtbot):
        page = ChannelStripPage(qtbot)
        page.click_group(3)
        assert page.get_selected_group() == 3

    def test_group_click_emits_param_changed(self, qtbot):
        page = ChannelStripPage(qtbot)
        blocker = page.run_and_capture_signal(
            page.widget.param_changed, lambda: page.click_group(2)
        )
        row, name, value = blocker.args
        assert name == "group"
        assert value == 2


class TestModeCombo:
    @pytest.mark.parametrize("mode", list(PlaybackMode))
    def test_set_mode_updates_combo_label(self, qtbot, mode):
        page = ChannelStripPage(qtbot)
        page.set_mode(mode)
        assert page.get_mode_label() == ChannelStripPage.MODE_LABELS[mode]

    def test_default_mode_is_loop(self, qtbot):
        page = ChannelStripPage(qtbot)
        assert page.get_mode_label() == "LOOP"


class TestSetState:
    def test_apply_state_updates_mute_button(self, qtbot):
        page = ChannelStripPage(qtbot)
        state = ChannelState(mute=True)
        page.apply_state(state)
        assert page.is_muted() is True

    def test_apply_state_updates_group_selection(self, qtbot):
        page = ChannelStripPage(qtbot)
        state = ChannelState(group=5)
        page.apply_state(state)
        assert page.get_selected_group() == 5

    def test_apply_state_updates_mode(self, qtbot):
        page = ChannelStripPage(qtbot)
        state = ChannelState(mode=PlaybackMode.SEQ)
        page.apply_state(state)
        assert page.get_mode_label() == "SEQ"

    def test_apply_state_updates_gain_fader(self, qtbot):
        page = ChannelStripPage(qtbot)
        state = ChannelState(gain=1.0)  # unity gain -> fader position 0.8
        page.apply_state(state)
        assert page.get_gain_fader_position() == pytest.approx(0.8, abs=0.01)


class TestSampleLoadSignal:
    def test_emit_sample_load_forwards_row_and_path(self, qtbot):
        page = ChannelStripPage(qtbot, row=7)
        blocker = page.run_and_capture_signal(
            page.widget.sample_load_requested,
            lambda: page.emit_sample_load("/tmp/kick.wav"),
        )
        row, path = blocker.args
        assert row == 7
        assert path == "/tmp/kick.wav"
