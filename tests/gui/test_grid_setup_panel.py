"""GUI tests for GridSetupPanel (mlrvp.gui.views.setup_view), driven
through the SetupPage POM wrapper's ``.grid`` property.

Scope note: the per-cell right-click context menus (_on_cr_button_clicked,
_on_grid_cc_ctx — this file's counterpart) open a real modal QMenu via
menu.exec(), which blocks the event loop under a real click — not worth
simulating here. Their actual mutation logic (_assign_cr_slot) is public
enough to call directly and is what's tested instead.
"""
import pytest

from mlrvp.config import CR_SLOT_ALL_STOP, CR_SLOT_EMPTY, CR_SLOT_GROUP_STOP, CR_SLOT_PATTERN
from tests.gui.pages.setup_page import SetupPage

pytestmark = pytest.mark.gui


class TestBasicSettings:
    def test_row_count_change_emits_setting_changed(self, qtbot):
        page = SetupPage(qtbot)
        blocker = page.run_and_capture_signal(
            page.widget.grid_setting_changed,
            lambda: page.grid._rows_spin.setValue(4),
        )
        assert tuple(blocker.args) == ("row_count", 4)

    def test_grid_columns_change_emits_setting_changed(self, qtbot):
        page = SetupPage(qtbot)
        blocker = page.run_and_capture_signal(
            page.widget.grid_setting_changed,
            lambda: page.set_combo_text(page.grid._cols_combo, "16"),
        )
        assert tuple(blocker.args) == ("grid_columns", 16)

    def test_active_groups_change_emits_setting_changed(self, qtbot):
        page = SetupPage(qtbot)
        blocker = page.run_and_capture_signal(
            page.widget.grid_setting_changed,
            lambda: page.set_combo_index(page.grid._active_groups_combo, 0),
        )
        assert blocker.args[0] == "active_groups"

    def test_backend_change_emits_setting_changed(self, qtbot):
        page = SetupPage(qtbot)
        blocker = page.run_and_capture_signal(
            page.widget.grid_setting_changed,
            lambda: page.set_combo_index(page.grid._backend_combo, 1),
        )
        assert blocker.args[0] == "monome_backend"

    def test_virtual_grid_checkbox_emits_setting_changed(self, qtbot):
        page = SetupPage(qtbot)
        blocker = page.run_and_capture_signal(
            page.widget.grid_setting_changed, lambda: page.grid._virtual_chk.setChecked(True)
        )
        assert tuple(blocker.args) == ("show_virtual_grid", True)

    def test_snap_speed_checkbox_emits_setting_changed(self, qtbot):
        page = SetupPage(qtbot)
        blocker = page.run_and_capture_signal(
            page.widget.grid_setting_changed, lambda: page.grid._snap_speed_chk.setChecked(True)
        )
        assert tuple(blocker.args) == ("snap_speed_to_rate", True)

    def test_set_grid_settings_round_trips_without_emitting(self, qtbot):
        page = SetupPage(qtbot)
        fired = []
        page.widget.grid_setting_changed.connect(lambda k, v: fired.append((k, v)))
        page.grid.set_grid_settings(
            row_count=6, grid_columns=16, show_virtual_grid=True,
            snap_speed_to_rate=True, monome_backend="serialosc",
        )
        assert fired == []
        assert page.grid._rows_spin.value() == 6
        assert page.grid._cols_combo.currentText() == "16"
        assert page.grid._virtual_chk.isChecked() is True

    def test_set_grid_settings_unknown_backend_falls_back_to_auto(self, qtbot):
        page = SetupPage(qtbot)
        from mlrvp.config import MONOME_BACKEND_AUTO
        page.grid.set_grid_settings(
            row_count=8, grid_columns=8, show_virtual_grid=False,
            snap_speed_to_rate=False, monome_backend="not-a-real-backend",
        )
        assert page.grid._backend_combo.currentData() == MONOME_BACKEND_AUTO

    def test_set_active_groups_updates_combo_without_emitting(self, qtbot):
        page = SetupPage(qtbot)
        fired = []
        page.widget.grid_setting_changed.connect(lambda k, v: fired.append((k, v)))
        page.grid.set_active_groups(4)
        assert fired == []
        assert page.grid._active_groups_combo.currentData() == 4


class TestQuickSizePresets:
    def test_click_preset_updates_spin_and_combo(self, qtbot):
        page = SetupPage(qtbot)
        page.click(page.grid._grid_preset_btns[3])  # 16x16
        assert page.grid._rows_spin.value() == 16
        assert page.grid._cols_combo.currentText() == "16"

    def test_click_preset_emits_both_settings(self, qtbot):
        page = SetupPage(qtbot)
        seen = []
        page.widget.grid_setting_changed.connect(lambda k, v: seen.append((k, v)))
        page.click(page.grid._grid_preset_btns[1])  # 8x16
        assert ("row_count", 8) in seen
        assert ("grid_columns", 16) in seen

    def test_click_preset_checks_only_that_button(self, qtbot):
        page = SetupPage(qtbot)
        page.click(page.grid._grid_preset_btns[2])  # 16x8
        checked = [b.isChecked() for b in page.grid._grid_preset_btns]
        assert checked == [False, False, True, False]


class TestTiltAndControlRowToggles:
    def test_tilt_checkbox_emits_tilt_enabled_changed(self, qtbot):
        page = SetupPage(qtbot)
        blocker = page.run_and_capture_signal(
            page.widget.tilt_enabled_changed, lambda: page.grid._tilt_chk.setChecked(True)
        )
        assert blocker.args[0] is True

    def test_set_tilt_enabled_programmatically_still_emits(self, qtbot):
        # Documents current behavior: unlike most set_* sync helpers,
        # set_tilt_enabled deliberately re-emits (see its docstring).
        page = SetupPage(qtbot)
        blocker = page.run_and_capture_signal(
            page.widget.tilt_enabled_changed, lambda: page.grid.set_tilt_enabled(True)
        )
        assert blocker.args[0] is True

    def test_control_row_checkbox_toggles_container_visibility(self, qtbot):
        page = SetupPage(qtbot)
        assert page.grid._control_row_container.isHidden() is False
        page.grid._control_row_chk.setChecked(False)
        assert page.grid._control_row_container.isHidden() is True

    def test_control_row_checkbox_emits_control_row_enabled_changed(self, qtbot):
        page = SetupPage(qtbot)
        blocker = page.run_and_capture_signal(
            page.widget.control_row_enabled_changed,
            lambda: page.grid._control_row_chk.setChecked(False),
        )
        assert blocker.args[0] is False


class TestMonomeRowSelector:
    def test_selecting_top_emits_zero(self, qtbot):
        page = SetupPage(qtbot)
        page.set_combo_index(page.grid._monome_row_combo, 1)  # move off the default (Top) first
        blocker = page.run_and_capture_signal(
            page.widget.control_row_setting_changed,
            lambda: page.set_combo_index(page.grid._monome_row_combo, 0),
        )
        assert tuple(blocker.args) == ("monome_row", 0)

    def test_selecting_bottom_emits_negative_one(self, qtbot):
        page = SetupPage(qtbot)
        blocker = page.run_and_capture_signal(
            page.widget.control_row_setting_changed,
            lambda: page.set_combo_index(page.grid._monome_row_combo, 1),
        )
        assert tuple(blocker.args) == ("monome_row", -1)

    def test_selecting_custom_reveals_spinbox_and_emits_its_value(self, qtbot):
        page = SetupPage(qtbot)
        assert page.grid._monome_row_spin.isHidden() is True
        blocker = page.run_and_capture_signal(
            page.widget.control_row_setting_changed,
            lambda: page.set_combo_index(page.grid._monome_row_combo, 2),
        )
        assert tuple(blocker.args) == ("monome_row", 0)  # spin defaults to 0
        assert page.grid._monome_row_spin.isHidden() is False

    def test_custom_spinbox_change_emits_new_value(self, qtbot):
        page = SetupPage(qtbot)
        page.set_combo_index(page.grid._monome_row_combo, 2)  # Custom
        blocker = page.run_and_capture_signal(
            page.widget.control_row_setting_changed,
            lambda: page.grid._monome_row_spin.setValue(5),
        )
        assert tuple(blocker.args) == ("monome_row", 5)

    def test_set_control_row_config_top_selects_top_and_hides_spin(self, qtbot):
        page = SetupPage(qtbot)
        page.grid.set_control_row_config(True, 0, [])
        assert page.grid._monome_row_combo.currentData() == 0
        assert page.grid._monome_row_spin.isHidden() is True

    def test_set_control_row_config_bottom_selects_bottom(self, qtbot):
        page = SetupPage(qtbot)
        page.grid.set_control_row_config(True, -1, [])
        assert page.grid._monome_row_combo.currentData() == -1

    def test_set_control_row_config_custom_value_shows_spin(self, qtbot):
        page = SetupPage(qtbot)
        page.grid.set_control_row_config(True, 7, [])
        assert page.grid._monome_row_combo.currentData() == -99
        assert page.grid._monome_row_spin.value() == 7
        assert page.grid._monome_row_spin.isHidden() is False

    def test_set_control_row_config_launchpad_without_lp_option_falls_back_to_top(self, qtbot):
        page = SetupPage(qtbot)
        # Launchpad option (-2) only exists after set_launchpad_connected(True).
        page.grid.set_control_row_config(True, -2, [])
        assert page.grid._monome_row_combo.currentData() == 0

    def test_set_control_row_config_launchpad_with_lp_option_present_selects_it(self, qtbot):
        page = SetupPage(qtbot)
        page.grid.set_launchpad_connected(True)
        page.grid.set_control_row_config(True, -2, [])
        assert page.grid._monome_row_combo.currentData() == -2

    def test_set_launchpad_connected_true_inserts_and_selects_lp_option(self, qtbot):
        page = SetupPage(qtbot)
        page.grid.set_launchpad_connected(True)
        assert page.grid._monome_row_combo.itemText(0) == "Launchpad (CC top row)"
        assert page.grid._monome_row_combo.currentData() == -2

    def test_set_launchpad_connected_false_removes_option_and_resets_to_top(self, qtbot):
        page = SetupPage(qtbot)
        page.grid.set_launchpad_connected(True)
        blocker = page.run_and_capture_signal(
            page.widget.control_row_setting_changed,
            lambda: page.grid.set_launchpad_connected(False),
        )
        assert tuple(blocker.args) == ("monome_row", 0)
        assert page.grid._monome_row_combo.findData(-2) == -1  # option removed


class TestControlRowAssignments:
    def test_assign_slot_updates_button_label_and_emits(self, qtbot):
        page = SetupPage(qtbot)
        blocker = page.run_and_capture_signal(
            page.widget.control_row_setting_changed,
            lambda: page.grid._assign_cr_slot(0, {"type": CR_SLOT_ALL_STOP, "value": 0}),
        )
        assert blocker.args[0] == "assignments"
        assert page.grid._cr_assignments[0] == {"type": CR_SLOT_ALL_STOP, "value": 0}
        assert page.grid._cr_buttons[0].text() == "ALL\nSTP"

    @pytest.mark.parametrize("slot_type,label", [
        (CR_SLOT_GROUP_STOP, "GS1"),
        (CR_SLOT_PATTERN, "P1"),
        (CR_SLOT_ALL_STOP, "ALL\nSTP"),
    ])
    def test_assign_slot_label_matches_type(self, qtbot, slot_type, label):
        page = SetupPage(qtbot)
        page.grid._assign_cr_slot(0, {"type": slot_type, "value": 0})
        assert page.grid._cr_buttons[0].text() == label

    def test_assign_grid_mode_slot_label(self, qtbot):
        from mlrvp.config import CR_SLOT_GRID_MODE
        page = SetupPage(qtbot)
        page.grid._assign_cr_slot(0, {"type": CR_SLOT_GRID_MODE, "value": 0})
        assert page.grid._cr_buttons[0].text() == "MODE"

    def test_assign_preset_prev_next_slot_labels(self, qtbot):
        from mlrvp.config import CR_SLOT_PRESET_NEXT, CR_SLOT_PRESET_PREV
        page = SetupPage(qtbot)
        page.grid._assign_cr_slot(0, {"type": CR_SLOT_PRESET_PREV, "value": 0})
        page.grid._assign_cr_slot(1, {"type": CR_SLOT_PRESET_NEXT, "value": 0})
        assert page.grid._cr_buttons[0].text() == "◄ PRE"
        assert page.grid._cr_buttons[1].text() == "PRE ►"

    def test_assign_slot_switches_preset_combo_to_custom(self, qtbot):
        page = SetupPage(qtbot)
        page.grid._assign_cr_slot(0, {"type": CR_SLOT_EMPTY, "value": 0})
        from mlrvp.config import CR_PRESETS
        assert page.grid._cr_preset_combo.currentIndex() == len(CR_PRESETS)  # "Custom"

    def test_selecting_a_preset_replaces_all_assignments(self, qtbot):
        page = SetupPage(qtbot)
        from mlrvp.config import CR_PRESETS
        blocker = page.run_and_capture_signal(
            page.widget.control_row_setting_changed,
            lambda: page.set_combo_index(page.grid._cr_preset_combo, 1),
        )
        assert blocker.args[0] == "assignments"
        assert page.grid._cr_assignments == CR_PRESETS[1][1]

    def test_set_control_row_config_pads_short_assignment_list(self, qtbot):
        page = SetupPage(qtbot)
        page.grid.set_control_row_config(True, 0, [{"type": CR_SLOT_GROUP_STOP, "value": 0}])
        assert len(page.grid._cr_assignments) == 8
        assert page.grid._cr_assignments[1]["type"] == CR_SLOT_EMPTY

    def test_set_control_row_config_truncates_long_assignment_list(self, qtbot):
        page = SetupPage(qtbot)
        long_list = [{"type": CR_SLOT_PATTERN, "value": i} for i in range(12)]
        page.grid.set_control_row_config(True, 0, long_list)
        assert len(page.grid._cr_assignments) == 8


class TestGridDeviceList:
    def test_set_grid_devices_populates_combo(self, qtbot):
        page = SetupPage(qtbot)
        page.grid.set_grid_devices(["USB MIDI 1", "USB MIDI 2"])
        names = [page.grid._grid_combo.itemText(i) for i in range(page.grid._grid_combo.count())]
        assert names == ["Virtual Grid (built-in)", "USB MIDI 1", "USB MIDI 2"]


class TestConnectFlow:
    def test_scan_button_emits_scan_requested(self, qtbot):
        page = SetupPage(qtbot)
        page.run_and_capture_signal(page.widget.grid_scan_requested, page.grid.scan_button.click)

    def test_connect_with_virtual_grid_selected_is_a_noop(self, qtbot):
        page = SetupPage(qtbot)
        fired = []
        page.widget.midi_grid_connect_requested.connect(lambda a, b: fired.append((a, b)))
        page.click(page.grid._connect_btn)  # index 0 = "Virtual Grid"
        assert fired == []

    def test_connect_with_scanned_device_emits_ports(self, qtbot):
        page = SetupPage(qtbot)
        page.grid.set_scanned_midi_devices([
            {"name": "Launchpad X", "model": "LPX", "in_port": 2, "out_port": 3},
        ])
        page.set_combo_index(page.grid._grid_combo, 1)
        blocker = page.run_and_capture_signal(
            page.widget.midi_grid_connect_requested, lambda: page.click(page.grid._connect_btn)
        )
        assert tuple(blocker.args) == (2, 3)

    def test_connect_skips_non_midi_scanned_entries(self, qtbot):
        page = SetupPage(qtbot)
        page.grid.set_scanned_midi_devices([
            {"name": "monome 128", "model": "40h", "in_port": -1, "out_port": -1},
        ])
        page.set_combo_index(page.grid._grid_combo, 1)
        fired = []
        page.widget.midi_grid_connect_requested.connect(lambda a, b: fired.append((a, b)))
        page.click(page.grid._connect_btn)
        assert fired == []

    def test_connected_state_selects_matching_device_in_combo(self, qtbot):
        page = SetupPage(qtbot)
        page.grid.set_grid_devices(["USB MIDI 1", "Launchpad X"])
        page.grid.set_connection_status(True, "Launchpad X")
        assert page.grid._grid_combo.currentText() == "Launchpad X"

    def test_connected_state_flips_button_to_disconnect(self, qtbot):
        page = SetupPage(qtbot)
        page.grid.set_connection_status(True, "Launchpad X")
        assert page.grid._connect_btn.text() == "Disconnect"
        blocker = page.run_and_capture_signal(
            page.widget.midi_grid_disconnect_requested, lambda: page.click(page.grid._connect_btn)
        )
        assert blocker.args == []

    def test_disconnected_state_resets_button_and_label(self, qtbot):
        page = SetupPage(qtbot)
        page.grid.set_connection_status(True, "Launchpad X")
        page.grid.set_connection_status(False)
        assert page.grid._connect_btn.text() == "Connect"
        assert page.grid._status_label.text() == "No hardware grid connected"


class TestDefaultPresets:
    def test_save_default_prompts_and_emits_request(self, qtbot, monkeypatch, tmp_config_dir):
        from PySide6.QtWidgets import QInputDialog
        page = SetupPage(qtbot)
        monkeypatch.setattr(QInputDialog, "getText",
                             staticmethod(lambda *a, **kw: ("MyDefault", True)))
        blocker = page.run_and_capture_signal(
            page.widget.save_default_requested, lambda: page.grid._on_save_default()
        )
        assert blocker.args[0] == "MyDefault"

    def test_save_default_selects_newly_saved_name_once_it_exists_on_disk(
        self, qtbot, monkeypatch, tmp_config_dir
    ):
        from mlrvp.config import get_defaults_dir
        from PySide6.QtWidgets import QInputDialog
        page = SetupPage(qtbot)
        monkeypatch.setattr(QInputDialog, "getText",
                             staticmethod(lambda *a, **kw: ("MyDefault", True)))
        # The real save handler (app.py) writes the file in response to the
        # signal — reproduce that here so _refresh_defaults_list() finds it.
        page.widget.save_default_requested.connect(
            lambda name: (get_defaults_dir() / f"{name}.json").write_text("{}")
        )
        page.grid._on_save_default()
        assert page.grid._default_combo.currentText() == "MyDefault"

    def test_save_default_cancelled_dialog_does_not_emit(self, qtbot, monkeypatch, tmp_config_dir):
        from PySide6.QtWidgets import QInputDialog
        page = SetupPage(qtbot)
        monkeypatch.setattr(QInputDialog, "getText",
                             staticmethod(lambda *a, **kw: ("Ignored", False)))
        fired = []
        page.widget.save_default_requested.connect(fired.append)
        page.grid._on_save_default()
        assert fired == []

    def test_get_selected_default_blank_is_empty_string(self, qtbot):
        page = SetupPage(qtbot)
        assert page.grid.get_selected_default() == ""
