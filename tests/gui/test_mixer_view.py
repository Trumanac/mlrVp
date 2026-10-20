"""GUI tests for MixerView, driven through the MixerPage POM wrapper."""
import pytest

from mlrvp.config import NUM_GROUPS
from tests.gui.pages.mixer_page import MixerPage

pytestmark = pytest.mark.gui


class TestGroupVolume:
    def test_dragging_fader_emits_group_volume_changed_with_gain(self, qtbot):
        page = MixerPage(qtbot)
        blocker = page.run_and_capture_signal(
            page.widget.group_volume_changed, lambda: page.drag_group_volume(2, 0.75)
        )
        assert blocker.args[0] == 2
        assert blocker.args[1] == pytest.approx(1.0, abs=0.01)  # 0.75 = unity position

    def test_dragging_fader_to_zero_reports_zero_gain(self, qtbot):
        page = MixerPage(qtbot)
        blocker = page.run_and_capture_signal(
            page.widget.group_volume_changed, lambda: page.drag_group_volume(0, 0.0)
        )
        assert blocker.args[1] == pytest.approx(0.0)

    @pytest.mark.parametrize("group", range(NUM_GROUPS))
    def test_each_group_reports_its_own_index(self, qtbot, group):
        page = MixerPage(qtbot)
        blocker = page.run_and_capture_signal(
            page.widget.group_volume_changed, lambda: page.drag_group_volume(group, 0.5)
        )
        assert blocker.args[0] == group


class TestGroupSend:
    def test_dragging_send_fader_emits_group_send_changed(self, qtbot):
        page = MixerPage(qtbot)
        blocker = page.run_and_capture_signal(
            page.widget.group_send_changed, lambda: page.drag_group_send(1, 0.4)
        )
        assert tuple(blocker.args) == (1, pytest.approx(0.4))


class TestGroupMute:
    def test_click_toggles_mute_state(self, qtbot):
        page = MixerPage(qtbot)
        assert page.is_group_muted(0) is False
        page.click_group_mute(0)
        assert page.is_group_muted(0) is True

    def test_click_emits_mute_toggled_with_group_index(self, qtbot):
        page = MixerPage(qtbot)
        blocker = page.run_and_capture_signal(
            page.widget.group_mute_toggled, lambda: page.click_group_mute(3)
        )
        assert tuple(blocker.args) == (3, True)


class TestGroupFXRoute:
    def test_default_route_is_off(self, qtbot):
        page = MixerPage(qtbot)
        assert page.group_fx_route_label(0) == "OFF"

    def test_click_cycles_through_fx_routes(self, qtbot):
        page = MixerPage(qtbot)
        page.click_group_fx_route(0)
        assert page.group_fx_route_label(0) == "FX1"
        page.click_group_fx_route(0)
        assert page.group_fx_route_label(0) == "FX2"

    def test_click_emits_fx_route_changed(self, qtbot):
        page = MixerPage(qtbot)
        blocker = page.run_and_capture_signal(
            page.widget.group_fx_route_changed, lambda: page.click_group_fx_route(2)
        )
        assert tuple(blocker.args) == (2, 1)

    def test_route_wraps_back_to_off_after_fx4(self, qtbot):
        page = MixerPage(qtbot)
        for _ in range(5):  # OFF -> FX1 -> FX2 -> FX3 -> FX4 -> OFF
            page.click_group_fx_route(0)
        assert page.group_fx_route_label(0) == "OFF"

    def test_set_fx_route_updates_display_without_signal(self, qtbot):
        page = MixerPage(qtbot)
        page.group_channel(0).set_fx_route(3)
        assert page.group_fx_route_label(0) == "FX3"


class TestGroupVisibility:
    def test_set_active_groups_hides_the_rest(self, qtbot):
        page = MixerPage(qtbot)
        page.set_active_groups(4)
        assert page.is_group_visible(3) is True
        assert page.is_group_visible(4) is False

    def test_set_group_level_does_not_raise(self, qtbot):
        page = MixerPage(qtbot)
        page.set_group_level(0, 0.5)  # smoke test — VU meters have no public getter


class TestAux:
    def test_default_aux_state(self, qtbot):
        page = MixerPage(qtbot)
        assert page.is_aux_muted() is False
        assert page.aux_fx_route_label() == "OFF"

    def test_drag_aux_volume_emits_gain(self, qtbot):
        page = MixerPage(qtbot)
        blocker = page.run_and_capture_signal(
            page.widget.aux_volume_changed, lambda: page.drag_aux_volume(0.75)
        )
        assert blocker.args[0] == pytest.approx(1.0, abs=0.01)

    def test_drag_aux_send_emits_raw_fader_position(self, qtbot):
        page = MixerPage(qtbot)
        blocker = page.run_and_capture_signal(
            page.widget.aux_send_changed, lambda: page.drag_aux_send(0.6)
        )
        assert blocker.args[0] == pytest.approx(0.6)

    def test_click_aux_mute_toggles_and_emits(self, qtbot):
        page = MixerPage(qtbot)
        blocker = page.run_and_capture_signal(
            page.widget.aux_mute_toggled, page.click_aux_mute
        )
        assert blocker.args[0] is True
        assert page.is_aux_muted() is True

    def test_click_aux_fx_route_cycles_and_emits(self, qtbot):
        page = MixerPage(qtbot)
        blocker = page.run_and_capture_signal(
            page.widget.aux_fx_route_changed, page.click_aux_fx_route
        )
        assert blocker.args[0] == 1
        assert page.aux_fx_route_label() == "FX1"

    def test_set_aux_send_updates_display_without_signal(self, qtbot):
        page = MixerPage(qtbot)
        page.widget.set_aux_send(0.3)
        assert page.widget._aux_send_fader.value == pytest.approx(0.3)

    def test_set_aux_mute_updates_display_without_signal(self, qtbot):
        page = MixerPage(qtbot)
        page.widget.set_aux_mute(True)
        assert page.is_aux_muted() is True

    def test_set_aux_fx_route_updates_display_without_signal(self, qtbot):
        page = MixerPage(qtbot)
        page.widget.set_aux_fx_route(4)
        assert page.aux_fx_route_label() == "FX4"


class TestMaster:
    def test_drag_master_volume_emits_gain(self, qtbot):
        page = MixerPage(qtbot)
        blocker = page.run_and_capture_signal(
            page.widget.master_volume_changed, lambda: page.drag_master_volume(0.75)
        )
        assert blocker.args[0] == pytest.approx(1.0, abs=0.01)

    def test_click_dac_toggles_and_emits(self, qtbot):
        page = MixerPage(qtbot)
        blocker = page.run_and_capture_signal(
            page.widget.dac_toggled, page.click_dac
        )
        assert blocker.args[0] is True

    def test_set_audio_enabled_true_unchecks_dac_button(self, qtbot):
        page = MixerPage(qtbot)
        page.set_audio_enabled(True)
        assert page.is_dac_engaged() is False

    def test_set_audio_enabled_false_checks_dac_button(self, qtbot):
        page = MixerPage(qtbot)
        page.set_audio_enabled(False)
        assert page.is_dac_engaged() is True

    def test_set_master_level_does_not_raise(self, qtbot):
        page = MixerPage(qtbot)
        page.widget.set_master_level(0.5, 0.6)

    def test_set_aux_level_does_not_raise(self, qtbot):
        page = MixerPage(qtbot)
        page.widget.set_aux_level(0.4)
