"""GUI tests for MidiSetupPanel (mlrvp.gui.views.setup_view), driven
through the SetupPage POM wrapper's ``.midi`` property.

Scope note: _on_grid_cc_ctx (right-click context menu on a grid CC cell)
opens a real modal QMenu via menu.exec() and isn't simulated here — same
reasoning as GridSetupPanel's per-cell menu. Its actual effect (emitting
grid_cc_cleared) is covered indirectly via _on_grid_cc_clear_all.
"""
import pytest

from tests.gui.pages.setup_page import SetupPage

pytestmark = pytest.mark.gui


def sample_mapping(param_path="channels.0.gain", **overrides):
    m = {"param_path": param_path, "label": "Ch1 Gain", "channel": 0, "cc": 20,
         "min_val": 0.0, "max_val": 1.0, "toggle": False}
    m.update(overrides)
    return m


class TestMidiOutputAndLearn:
    def test_output_device_change_emits_setting_changed(self, qtbot):
        page = SetupPage(qtbot)
        page.midi.set_midi_devices([], ["Output A", "Output B"])
        blocker = page.run_and_capture_signal(
            page.widget.midi_setting_changed,
            lambda: page.set_combo_index(page.midi._midi_out_combo, 2),
        )
        assert tuple(blocker.args) == ("midi_out_device", 1)  # -1 offset for "None"

    def test_output_device_none_emits_negative_one(self, qtbot):
        page = SetupPage(qtbot)
        page.midi.set_midi_devices([], ["Output A"])
        page.set_combo_index(page.midi._midi_out_combo, 1)
        blocker = page.run_and_capture_signal(
            page.widget.midi_setting_changed,
            lambda: page.set_combo_index(page.midi._midi_out_combo, 0),
        )
        assert tuple(blocker.args) == ("midi_out_device", -1)

    def test_learn_button_toggles_and_emits(self, qtbot):
        page = SetupPage(qtbot)
        blocker = page.run_and_capture_signal(
            page.widget.midi_learn_toggled, lambda: page.midi._learn_btn.setChecked(True)
        )
        assert blocker.args[0] is True

    def test_set_midi_learn_active_syncs_button_without_raising(self, qtbot):
        page = SetupPage(qtbot)
        page.widget.set_midi_learn_active(True)
        assert page.midi._learn_btn.isChecked() is True


class TestMidiInputRows:
    def test_add_input_creates_a_row(self, qtbot):
        page = SetupPage(qtbot)
        assert len(page.midi._midi_input_rows) == 0
        page.midi._on_add_input()
        assert len(page.midi._midi_input_rows) == 1

    def test_selecting_a_port_emits_input_added(self, qtbot):
        page = SetupPage(qtbot)
        page.midi.set_midi_devices(["Port A", "Port B"], [])
        page.midi._on_add_input()
        combo = page.midi._midi_input_rows[0]["combo"]
        blocker = page.run_and_capture_signal(
            page.widget.midi_input_added, lambda: page.set_combo_index(combo, 2)
        )
        assert blocker.args[0] == 1  # Port B -> index 1

    def test_switching_port_removes_old_and_adds_new(self, qtbot):
        page = SetupPage(qtbot)
        page.midi.set_midi_devices(["Port A", "Port B"], [])
        page.midi._on_add_input()
        combo = page.midi._midi_input_rows[0]["combo"]
        page.set_combo_index(combo, 1)  # Port A -> port 0

        removed, added = [], []
        page.widget.midi_input_removed.connect(removed.append)
        page.widget.midi_input_added.connect(added.append)
        page.set_combo_index(combo, 2)  # switch to Port B -> port 1
        assert removed == [0]
        assert added == [1]

    def test_remove_button_emits_removed_and_deletes_row(self, qtbot):
        page = SetupPage(qtbot)
        page.midi.set_midi_devices(["Port A"], [])
        page.midi._on_add_input()
        combo = page.midi._midi_input_rows[0]["combo"]
        page.set_combo_index(combo, 1)
        remove_btn = page.midi._midi_input_rows[0]["remove_btn"]
        blocker = page.run_and_capture_signal(
            page.widget.midi_input_removed, lambda: page.click(remove_btn)
        )
        assert blocker.args[0] == 0
        assert page.midi._midi_input_rows[0] is None

    def test_remove_button_on_unselected_row_does_not_emit(self, qtbot):
        page = SetupPage(qtbot)
        page.midi._on_add_input()
        remove_btn = page.midi._midi_input_rows[0]["remove_btn"]
        fired = []
        page.widget.midi_input_removed.connect(fired.append)
        page.click(remove_btn)
        assert fired == []


class TestRescanButton:
    def test_rescan_button_property_returns_the_widget(self, qtbot):
        page = SetupPage(qtbot)
        assert page.midi.rescan_button is page.midi._rescan_btn


class TestRefreshingDevicesWithExistingRows:
    def test_set_midi_devices_refreshes_existing_input_row_and_keeps_selection(self, qtbot):
        page = SetupPage(qtbot)
        page.midi.set_midi_devices(["Port A", "Port B"], [])
        page.midi._on_add_input()
        combo = page.midi._midi_input_rows[0]["combo"]
        page.set_combo_index(combo, 2)  # select "Port B"

        page.midi.set_midi_devices(["Port A", "Port B", "Port C"], [])
        assert combo.currentText() == "Port B"
        assert combo.count() == 4  # None + 3 ports

    def test_set_midi_devices_skips_removed_rows(self, qtbot):
        page = SetupPage(qtbot)
        page.midi.set_midi_devices(["Port A"], [])
        page.midi._on_add_input()
        page.click(page.midi._midi_input_rows[0]["remove_btn"])
        assert page.midi._midi_input_rows[0] is None
        page.midi.set_midi_devices(["Port A", "Port B"], [])  # must not raise


class TestMappingTable:
    def test_add_mapping_inserts_a_row(self, qtbot):
        page = SetupPage(qtbot)
        page.midi.add_mapping(sample_mapping())
        assert page.midi._map_table.rowCount() == 1
        assert page.midi._map_table.item(0, 0).text() == "Ch1 Gain"
        assert page.midi._map_table.item(0, 1).text() == "20"
        assert page.midi._map_table.item(0, 2).text() == "1"  # channel + 1

    def test_refresh_mappings_replaces_all_rows(self, qtbot):
        page = SetupPage(qtbot)
        page.midi.add_mapping(sample_mapping("a.b"))
        page.midi.refresh_mappings([sample_mapping("x.y"), sample_mapping("z.w")])
        assert page.midi._map_table.rowCount() == 2

    def test_min_max_spinbox_change_emits_range_changed(self, qtbot):
        page = SetupPage(qtbot)
        page.midi.add_mapping(sample_mapping())
        min_spin = page.midi._map_table.cellWidget(0, 3)
        blocker = page.run_and_capture_signal(
            page.widget.midi_mapping_range_changed, lambda: min_spin.setValue(0.25)
        )
        assert blocker.args[0] == "channels.0.gain"
        assert blocker.args[1] == pytest.approx(0.25)

    def test_toggle_checkbox_emits_toggle_changed(self, qtbot):
        page = SetupPage(qtbot)
        page.midi.add_mapping(sample_mapping())
        toggle_chk = page.midi._map_table.cellWidget(0, 5)
        blocker = page.run_and_capture_signal(
            page.widget.midi_mapping_toggle_changed, lambda: toggle_chk.setChecked(True)
        )
        assert tuple(blocker.args) == ("channels.0.gain", True)

    def test_delete_button_removes_row_and_emits(self, qtbot):
        page = SetupPage(qtbot)
        page.midi.add_mapping(sample_mapping())
        del_btn = page.midi._map_table.cellWidget(0, 6)
        blocker = page.run_and_capture_signal(
            page.widget.midi_mapping_removed, lambda: page.click(del_btn)
        )
        assert blocker.args[0] == "channels.0.gain"
        assert page.midi._map_table.rowCount() == 0

    def test_remove_mapping_row_does_not_emit(self, qtbot):
        page = SetupPage(qtbot)
        page.midi.add_mapping(sample_mapping())
        fired = []
        page.widget.midi_mapping_removed.connect(fired.append)
        page.midi.remove_mapping_row("channels.0.gain")
        assert fired == []
        assert page.midi._map_table.rowCount() == 0

    def test_clear_all_removes_every_row_and_emits_per_row(self, qtbot):
        page = SetupPage(qtbot)
        page.midi.add_mapping(sample_mapping("a.b"))
        page.midi.add_mapping(sample_mapping("c.d"))
        removed = []
        page.widget.midi_mapping_removed.connect(removed.append)
        page.click(page.midi._clear_all_btn)
        assert set(removed) == {"a.b", "c.d"}
        assert page.midi._map_table.rowCount() == 0


class TestTiltMapping:
    def test_set_tilt_targets_populates_combos(self, qtbot):
        page = SetupPage(qtbot)
        page.midi.set_tilt_targets([("channels.0.gain", "Ch1 Gain"), ("channels.1.gain", "Ch2 Gain")])
        combo = page.midi._tilt_combos[0]
        assert combo.count() == 3  # "None" + 2 targets
        assert combo.itemText(1) == "Ch1 Gain"

    def test_selecting_target_emits_tilt_mapping_changed(self, qtbot):
        page = SetupPage(qtbot)
        page.midi.set_tilt_targets([("channels.0.gain", "Ch1 Gain")])
        blocker = page.run_and_capture_signal(
            page.widget.midi_tilt_mapping_changed,
            lambda: page.set_combo_index(page.midi._tilt_combos[0], 1),
        )
        assert tuple(blocker.args) == (0, "channels.0.gain")

    def test_selecting_none_emits_empty_target(self, qtbot):
        page = SetupPage(qtbot)
        page.midi.set_tilt_targets([("channels.0.gain", "Ch1 Gain")])
        page.set_combo_index(page.midi._tilt_combos[1], 1)
        blocker = page.run_and_capture_signal(
            page.widget.midi_tilt_mapping_changed,
            lambda: page.set_combo_index(page.midi._tilt_combos[1], 0),
        )
        assert tuple(blocker.args) == (1, "")

    def test_min_spin_change_emits_range_changed_for_that_axis(self, qtbot):
        page = SetupPage(qtbot)
        blocker = page.run_and_capture_signal(
            page.widget.midi_tilt_range_changed,
            lambda: page.midi._tilt_min_spins[0].setValue(-0.5),
        )
        assert blocker.args[0] == 0
        assert blocker.args[1] == pytest.approx(-0.5)

    def test_sensitivity_spin_change_emits_sensitivity_changed(self, qtbot):
        page = SetupPage(qtbot)
        blocker = page.run_and_capture_signal(
            page.widget.midi_tilt_sensitivity_changed,
            lambda: page.midi._tilt_sensitivity_spins[1].setValue(8.0),
        )
        assert tuple(blocker.args) == (1, pytest.approx(8.0))

    def test_set_tilt_axis_state_round_trips_without_emitting(self, qtbot):
        page = SetupPage(qtbot)
        page.midi.set_tilt_targets([("channels.0.gain", "Ch1 Gain")])
        fired_range, fired_sens, fired_map = [], [], []
        page.widget.midi_tilt_range_changed.connect(lambda *a: fired_range.append(a))
        page.widget.midi_tilt_sensitivity_changed.connect(lambda *a: fired_sens.append(a))
        page.widget.midi_tilt_mapping_changed.connect(lambda *a: fired_map.append(a))
        page.midi.set_tilt_axis_state(0, "channels.0.gain", -0.2, 0.9, sensitivity=3.0)
        assert fired_range == fired_sens == fired_map == []
        assert page.midi._tilt_min_spins[0].value() == pytest.approx(-0.2)
        assert page.midi._tilt_max_spins[0].value() == pytest.approx(0.9)
        assert page.midi._tilt_sensitivity_spins[0].value() == pytest.approx(3.0)

    def test_set_tilt_axis_state_out_of_range_axis_is_a_noop(self, qtbot):
        page = SetupPage(qtbot)
        page.midi.set_tilt_axis_state(99, "channels.0.gain", 0.0, 1.0)  # must not raise

    def test_update_tilt_live_updates_label_text(self, qtbot):
        page = SetupPage(qtbot)
        page.midi.update_tilt_live(0.25, -0.75)
        assert "0.25" in page.midi._tilt_live_label.text()
        assert "-0.75" in page.midi._tilt_live_label.text()

    def test_set_tilt_visible_toggles_container(self, qtbot):
        page = SetupPage(qtbot)
        assert page.midi._tilt_container.isHidden() is True  # hidden by default
        page.midi.set_tilt_visible(True)
        assert page.midi._tilt_container.isHidden() is False


class TestControlRowActions:
    def test_set_control_row_actions_builds_expected_row_count(self, qtbot):
        page = SetupPage(qtbot)
        page.midi.set_control_row_actions(num_groups=4, num_patterns=2)
        # 4 group stops + 2 patterns + grid-mode-cycle + prev + next = 9
        assert len(page.midi._control_row_rows) == 9

    def test_learn_button_emits_control_row_learn_requested(self, qtbot):
        page = SetupPage(qtbot)
        page.midi.set_control_row_actions(num_groups=1, num_patterns=1)
        row = page.midi._control_row_rows[0]
        learn_btn = row["container"].findChildren(type(page.midi._clear_all_btn))[0]
        blocker = page.run_and_capture_signal(
            page.widget.midi_control_row_learn_requested, lambda: page.click(learn_btn)
        )
        assert blocker.args[0] == row["param_path"]

    def test_clear_button_emits_mapping_removed(self, qtbot):
        page = SetupPage(qtbot)
        page.midi.set_control_row_actions(num_groups=1, num_patterns=1)
        row = page.midi._control_row_rows[0]
        buttons = row["container"].findChildren(type(page.midi._clear_all_btn))
        clear_btn = buttons[1]  # [0]=Learn, [1]=Clear (added in that layout order)
        blocker = page.run_and_capture_signal(
            page.widget.midi_mapping_removed, lambda: page.click(clear_btn)
        )
        assert blocker.args[0] == row["param_path"]

    def test_update_control_row_assignment_sets_label_text(self, qtbot):
        page = SetupPage(qtbot)
        page.midi.set_control_row_actions(num_groups=1, num_patterns=0)
        path = page.midi._control_row_rows[0]["param_path"]
        page.midi.update_control_row_assignment(path, "CC10 Ch1")
        assert page.midi._control_row_rows[0]["assign_lbl"].text() == "CC10 Ch1"

    def test_update_control_row_assignment_empty_string_resets_dash(self, qtbot):
        page = SetupPage(qtbot)
        page.midi.set_control_row_actions(num_groups=1, num_patterns=0)
        path = page.midi._control_row_rows[0]["param_path"]
        page.midi.update_control_row_assignment(path, "CC10 Ch1")
        page.midi.update_control_row_assignment(path, "")
        assert page.midi._control_row_rows[0]["assign_lbl"].text() == "—"

    def test_toggle_button_shows_and_hides_panel(self, qtbot):
        page = SetupPage(qtbot)
        assert page.midi._control_row_panel.isHidden() is True
        page.midi._cr_toggle_btn.setChecked(True)
        page.midi._on_toggle_control_row(True)
        assert page.midi._control_row_panel.isHidden() is False


class TestGridCCMap:
    def test_default_grid_is_8x8(self, qtbot):
        page = SetupPage(qtbot)
        assert len(page.midi._gm_buttons) == 64

    def test_size_change_rebuilds_grid_and_emits(self, qtbot):
        page = SetupPage(qtbot)
        blocker = page.run_and_capture_signal(
            page.widget.midi_grid_cc_size_changed,
            lambda: page.midi._gm_rows_spin.setValue(4),
        )
        assert blocker.args[0] == 4
        assert len(page.midi._gm_buttons) == 4 * 8

    def test_set_grid_cc_size_does_not_emit(self, qtbot):
        page = SetupPage(qtbot)
        fired = []
        page.widget.midi_grid_cc_size_changed.connect(lambda *a: fired.append(a))
        page.midi.set_grid_cc_size(6, 6)
        assert fired == []
        assert len(page.midi._gm_buttons) == 36

    def test_clicking_a_cell_emits_learn_requested(self, qtbot):
        page = SetupPage(qtbot)
        btn = page.midi._gm_buttons[(2, 3)]
        blocker = page.run_and_capture_signal(
            page.widget.midi_grid_cc_learn_requested, lambda: page.click(btn)
        )
        assert tuple(blocker.args) == (2, 3)

    def test_update_assignment_clears_learning_state_for_that_cell(self, qtbot):
        page = SetupPage(qtbot)
        page.midi.set_grid_cc_learning(1, 1)
        page.midi.update_grid_cc_assignment(1, 1, channel=0, cc=5)
        assert page.midi._gm_learning_cell is None
        assert page.midi._gm_buttons[(1, 1)].text() == "CC5\nCh1"

    def test_update_assignment_changes_cell_text(self, qtbot):
        page = SetupPage(qtbot)
        page.midi.update_grid_cc_assignment(1, 1, channel=2, cc=44)
        btn = page.midi._gm_buttons[(1, 1)]
        assert btn.text() == "CC44\nCh3"

    def test_clear_assignment_resets_cell_text(self, qtbot):
        page = SetupPage(qtbot)
        page.midi.update_grid_cc_assignment(1, 1, channel=2, cc=44)
        page.midi.clear_grid_cc_assignment(1, 1)
        btn = page.midi._gm_buttons[(1, 1)]
        assert btn.text() == "R2\nC2"

    def test_set_learning_highlights_cell_and_clears_previous(self, qtbot):
        page = SetupPage(qtbot)
        page.midi.set_grid_cc_learning(0, 0)
        page.midi.set_grid_cc_learning(1, 1)
        assert page.midi._gm_buttons[(0, 0)].text() == "R1\nC1"
        assert page.midi._gm_buttons[(1, 1)].text() == "LEARN\n..."

    def test_clear_learning_resets_highlighted_cell(self, qtbot):
        page = SetupPage(qtbot)
        page.midi.set_grid_cc_learning(0, 0)
        page.midi.clear_grid_cc_learning()
        assert page.midi._gm_buttons[(0, 0)].text() == "R1\nC1"

    def test_refresh_grid_cc_map_restores_assignments(self, qtbot):
        page = SetupPage(qtbot)
        page.midi.refresh_grid_cc_map({"0,0": {"channel": 0, "cc": 10}})
        btn = page.midi._gm_buttons[(0, 0)]
        assert btn.text() == "CC10\nCh1"

    def test_refresh_grid_cc_map_skips_malformed_keys(self, qtbot):
        page = SetupPage(qtbot)
        page.midi.refresh_grid_cc_map({"bad-key": {"channel": 0, "cc": 10}})  # must not raise
        assert len(page.midi._gm_buttons) == 64

    def test_clear_all_emits_clear_for_every_cell(self, qtbot):
        page = SetupPage(qtbot)
        page.midi.set_grid_cc_size(2, 2)
        cleared = []
        page.widget.midi_grid_cc_cleared.connect(lambda r, c: cleared.append((r, c)))
        page.midi._on_grid_cc_clear_all()
        assert set(cleared) == {(0, 0), (0, 1), (1, 0), (1, 1)}

    def test_toggle_button_shows_and_hides_gm_panel(self, qtbot):
        page = SetupPage(qtbot)
        assert page.midi._gm_panel.isHidden() is True
        page.midi._on_toggle_grid_cc_map(True)
        assert page.midi._gm_panel.isHidden() is False
