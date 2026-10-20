"""GUI tests for TransportView, driven through the TransportPage POM wrapper."""
import pytest

from tests.gui.pages.transport_page import TransportPage

pytestmark = pytest.mark.gui


class TestTempo:
    def test_default_bpm(self, qtbot):
        page = TransportPage(qtbot)
        assert page.get_bpm() == pytest.approx(120.0)

    def test_set_bpm_updates_display(self, qtbot):
        page = TransportPage(qtbot)
        page.set_bpm(145.5)
        assert page.get_bpm() == pytest.approx(145.5)

    def test_set_bpm_emits_tempo_changed(self, qtbot):
        page = TransportPage(qtbot)
        blocker = page.run_and_capture_signal(
            page.widget.tempo_changed, lambda: page.set_bpm(90.0)
        )
        assert blocker.args[0] == pytest.approx(90.0)

    def test_bpm_is_clamped_to_widget_range(self, qtbot):
        page = TransportPage(qtbot)
        page.set_bpm(5.0)  # below the 20-300 range enforced by the spinbox
        assert page.get_bpm() == pytest.approx(20.0)
        page.set_bpm(9999.0)
        assert page.get_bpm() == pytest.approx(300.0)

    def test_tempo_up_button_increments(self, qtbot):
        page = TransportPage(qtbot)
        page.set_bpm(120.0)
        page.click_tempo_up()
        assert page.get_bpm() > 120.0

    def test_tempo_down_button_decrements(self, qtbot):
        page = TransportPage(qtbot)
        page.set_bpm(120.0)
        page.click_tempo_down()
        assert page.get_bpm() < 120.0

    def test_sync_button_emits_request(self, qtbot):
        page = TransportPage(qtbot)
        page.run_and_capture_signal(page.widget.sync_bpm_requested, page.click_sync)


class TestGroupStop:
    def test_click_group_stop_emits_index(self, qtbot):
        page = TransportPage(qtbot)
        blocker = page.run_and_capture_signal(
            page.widget.group_stop_requested, lambda: page.click_group_stop(3)
        )
        assert blocker.args[0] == 3

    def test_click_all_stop_emits_signal(self, qtbot):
        page = TransportPage(qtbot)
        page.run_and_capture_signal(page.widget.stop_all_requested, page.click_all_stop)

    @pytest.mark.parametrize("group_index", range(8))
    def test_each_group_stop_button_reports_its_own_index(self, qtbot, group_index):
        page = TransportPage(qtbot)
        blocker = page.run_and_capture_signal(
            page.widget.group_stop_requested, lambda: page.click_group_stop(group_index)
        )
        assert blocker.args[0] == group_index


class TestGridMode:
    def test_click_emits_cycle_request(self, qtbot):
        page = TransportPage(qtbot)
        page.run_and_capture_signal(
            page.widget.grid_mode_cycle_requested, page.click_grid_mode
        )


class TestPresetControls:
    def test_new_button_emits_request(self, qtbot):
        page = TransportPage(qtbot)
        page.run_and_capture_signal(page.widget.preset_new_requested, page.click_new)

    def test_prev_button_emits_request(self, qtbot):
        page = TransportPage(qtbot)
        page.run_and_capture_signal(page.widget.preset_prev_requested, page.click_preset_prev)

    def test_next_button_emits_request(self, qtbot):
        page = TransportPage(qtbot)
        page.run_and_capture_signal(page.widget.preset_next_requested, page.click_preset_next)

    def test_save_button_emits_request(self, qtbot):
        page = TransportPage(qtbot)
        page.run_and_capture_signal(page.widget.preset_save_requested, page.click_save)
