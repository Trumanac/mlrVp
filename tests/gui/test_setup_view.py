"""GUI tests for SetupView, driven through the SetupPage POM wrapper."""
import pytest

from tests.gui.pages.setup_page import SetupPage

pytestmark = pytest.mark.gui

_SAMPLE_DEVICES = [
    {"index": 0, "name": "Built-in Output", "host_api": "WASAPI"},
    {"index": 1, "name": "ASIO Interface", "host_api": "ASIO"},
]
_SAMPLE_INPUTS = [
    {"index": 2, "name": "Built-in Mic", "host_api": "WASAPI"},
]


class TestTabs:
    def test_default_tab_is_audio(self, qtbot):
        page = SetupPage(qtbot)
        assert page.current_tab_label() == "Audio"

    @pytest.mark.parametrize("idx,label", [
        (0, "Audio"), (1, "Grid"), (2, "MIDI"), (3, "OSC"),
    ])
    def test_switch_tab_updates_label(self, qtbot, idx, label):
        page = SetupPage(qtbot)
        page.switch_tab(idx)
        assert page.current_tab_index() == idx
        assert page.current_tab_label() == label


class TestAudioPanelBasics:
    def test_sample_rate_change_emits_setting_changed(self, qtbot):
        page = SetupPage(qtbot)
        blocker = page.run_and_capture_signal(
            page.widget.audio_setting_changed, lambda: page.set_sample_rate("48000")
        )
        assert tuple(blocker.args) == ("sample_rate", 48000)

    def test_buffer_size_change_emits_setting_changed(self, qtbot):
        page = SetupPage(qtbot)
        blocker = page.run_and_capture_signal(
            page.widget.audio_setting_changed, lambda: page.set_buffer_size("1024")
        )
        assert tuple(blocker.args) == ("buffer_size", 1024)

    def test_multichannel_checkbox_emits_setting_changed(self, qtbot):
        page = SetupPage(qtbot)
        blocker = page.run_and_capture_signal(
            page.widget.audio_setting_changed, page.click_multichannel_output
        )
        assert tuple(blocker.args) == ("multichannel_output", True)

    def test_link_button_emits_link_toggled(self, qtbot):
        page = SetupPage(qtbot)
        blocker = page.run_and_capture_signal(
            page.widget.link_toggled, page.click_link
        )
        assert blocker.args[0] is True

    def test_rescan_button_exists_and_is_clickable(self, qtbot):
        page = SetupPage(qtbot)
        page.click_rescan()  # RescanPanel has no built-in signal; smoke-check it doesn't crash

    def test_rescan_button_property_returns_the_widget(self, qtbot):
        page = SetupPage(qtbot)
        assert page.audio.rescan_button is page.audio._rescan_btn


class TestAudioPanelDeviceSelection:
    def test_set_devices_populates_driver_combo(self, qtbot):
        page = SetupPage(qtbot)
        page.set_devices(_SAMPLE_DEVICES, _SAMPLE_INPUTS)
        names = [page.audio._driver_combo.itemText(i) for i in range(page.audio._driver_combo.count())]
        assert names == ["All Drivers", "WASAPI", "ASIO"]

    def test_set_devices_populates_output_combo(self, qtbot):
        page = SetupPage(qtbot)
        page.set_devices(_SAMPLE_DEVICES, _SAMPLE_INPUTS)
        names = [page.audio._output_combo.itemText(i) for i in range(page.audio._output_combo.count())]
        assert names == ["Default Output", "Built-in Output", "ASIO Interface"]

    def test_selecting_output_device_emits_its_sounddevice_index(self, qtbot):
        page = SetupPage(qtbot)
        page.set_devices(_SAMPLE_DEVICES, _SAMPLE_INPUTS)
        blocker = page.run_and_capture_signal(
            page.widget.audio_setting_changed,
            lambda: page.select_output_by_text("ASIO Interface"),
        )
        assert tuple(blocker.args) == ("output_device", 1)

    def test_selecting_input_device_emits_its_sounddevice_index(self, qtbot):
        page = SetupPage(qtbot)
        page.set_devices(_SAMPLE_DEVICES, _SAMPLE_INPUTS)
        blocker = page.run_and_capture_signal(
            page.widget.audio_setting_changed,
            lambda: page.set_combo_text(page.audio._input_combo, "Built-in Mic"),
        )
        assert tuple(blocker.args) == ("input_device", 2)

    def test_selecting_default_input_emits_negative_one(self, qtbot):
        page = SetupPage(qtbot)
        page.set_devices(_SAMPLE_DEVICES, _SAMPLE_INPUTS)
        page.set_combo_text(page.audio._input_combo, "Built-in Mic")
        blocker = page.run_and_capture_signal(
            page.widget.audio_setting_changed,
            lambda: page.set_combo_text(page.audio._input_combo, "Default Input"),
        )
        assert tuple(blocker.args) == ("input_device", -1)

    def test_selecting_default_output_emits_negative_one(self, qtbot):
        page = SetupPage(qtbot)
        page.set_devices(_SAMPLE_DEVICES, _SAMPLE_INPUTS)
        page.select_output_by_text("ASIO Interface")
        blocker = page.run_and_capture_signal(
            page.widget.audio_setting_changed,
            lambda: page.select_output_by_text("Default Output"),
        )
        assert tuple(blocker.args) == ("output_device", -1)

    def test_selecting_driver_filters_device_list(self, qtbot):
        page = SetupPage(qtbot)
        page.set_devices(_SAMPLE_DEVICES, _SAMPLE_INPUTS)
        page.select_driver("ASIO")
        names = [page.audio._output_combo.itemText(i) for i in range(page.audio._output_combo.count())]
        assert names == ["Default Output", "ASIO Interface"]

    def test_select_output_device_programmatically_does_not_emit(self, qtbot):
        page = SetupPage(qtbot)
        page.set_devices(_SAMPLE_DEVICES, _SAMPLE_INPUTS)
        fired = []
        page.widget.audio_setting_changed.connect(lambda k, v: fired.append((k, v)))
        page.audio.select_output_device(1)
        assert fired == []
        assert page.audio._output_combo.currentText() == "ASIO Interface"

    def test_set_multichannel_output_state_does_not_emit(self, qtbot):
        page = SetupPage(qtbot)
        fired = []
        page.widget.audio_setting_changed.connect(lambda k, v: fired.append((k, v)))
        page.audio.set_multichannel_output_state(True)
        assert fired == []
        assert page.audio._mc_cb.isChecked() is True

    def test_select_input_device_programmatically_does_not_emit(self, qtbot):
        page = SetupPage(qtbot)
        page.set_devices(_SAMPLE_DEVICES, _SAMPLE_INPUTS)
        fired = []
        page.widget.audio_setting_changed.connect(lambda k, v: fired.append((k, v)))
        page.audio.select_input_device(2)
        assert fired == []
        assert page.audio._input_combo.currentText() == "Built-in Mic"

    def test_select_input_device_not_found_resets_to_default(self, qtbot):
        page = SetupPage(qtbot)
        page.set_devices(_SAMPLE_DEVICES, _SAMPLE_INPUTS)
        page.audio.select_input_device(2)
        page.audio.select_input_device(999)  # no such device index
        assert page.audio._input_combo.currentIndex() == 0

    def test_select_output_device_not_found_resets_to_default(self, qtbot):
        page = SetupPage(qtbot)
        page.set_devices(_SAMPLE_DEVICES, _SAMPLE_INPUTS)
        page.select_output_by_text("ASIO Interface")
        page.audio.select_output_device(999)
        assert page.audio._output_combo.currentIndex() == 0

    def test_set_driver_programmatically_filters_devices(self, qtbot):
        page = SetupPage(qtbot)
        page.set_devices(_SAMPLE_DEVICES, _SAMPLE_INPUTS)
        page.audio.set_driver("ASIO")
        names = [page.audio._output_combo.itemText(i) for i in range(page.audio._output_combo.count())]
        assert names == ["Default Output", "ASIO Interface"]

    def test_set_driver_unknown_name_falls_back_to_all(self, qtbot):
        page = SetupPage(qtbot)
        page.set_devices(_SAMPLE_DEVICES, _SAMPLE_INPUTS)
        page.audio.set_driver("Nonexistent Driver")
        assert page.audio._driver_combo.currentIndex() == 0


class TestLinkStateDisplay:
    def test_unavailable_link_disables_button_with_install_hint(self, qtbot):
        page = SetupPage(qtbot)
        page.widget.set_link_state(False, 0, available=False)
        assert page.audio._link_btn.isEnabled() is False
        assert "pip install aalink" in page.audio._link_btn.toolTip()

    def test_enabled_with_peers_shows_peer_count(self, qtbot):
        page = SetupPage(qtbot)
        page.widget.set_link_state(True, 3)
        assert page.audio._link_btn.text() == "3"

    def test_enabled_with_no_peers_shows_dot(self, qtbot):
        page = SetupPage(qtbot)
        page.widget.set_link_state(True, 0)
        assert page.audio._link_btn.text() == "·"

    def test_disabled_shows_dash(self, qtbot):
        page = SetupPage(qtbot)
        page.widget.set_link_state(False, 0)
        assert page.audio._link_btn.text() == "—"


class TestSignalForwarding:
    """SetupView forwards each sub-panel's signals up as its own signal of
    the same name — verify the wiring survives (this is the actual
    behavior SetupView itself contributes, vs. the panels' own logic)."""

    def test_grid_scan_requested_forwards_to_view_level_signal(self, qtbot):
        page = SetupPage(qtbot)
        page.run_and_capture_signal(
            page.widget.grid_scan_requested, page.grid.scan_requested.emit
        )

    def test_grid_setting_changed_forwards_with_payload(self, qtbot):
        page = SetupPage(qtbot)
        blocker = page.run_and_capture_signal(
            page.widget.grid_setting_changed,
            lambda: page.grid.setting_changed.emit("rows", 8),
        )
        assert tuple(blocker.args) == ("rows", 8)

    def test_midi_learn_toggled_forwards(self, qtbot):
        page = SetupPage(qtbot)
        blocker = page.run_and_capture_signal(
            page.widget.midi_learn_toggled,
            lambda: page.midi.midi_learn_toggled.emit(True),
        )
        assert blocker.args[0] is True

    def test_midi_mapping_removed_forwards_with_path(self, qtbot):
        page = SetupPage(qtbot)
        blocker = page.run_and_capture_signal(
            page.widget.midi_mapping_removed,
            lambda: page.midi.mapping_removed.emit("channels.0.gain"),
        )
        assert blocker.args[0] == "channels.0.gain"

    def test_osc_setting_changed_forwards_with_payload(self, qtbot):
        page = SetupPage(qtbot)
        blocker = page.run_and_capture_signal(
            page.widget.osc_setting_changed,
            lambda: page.osc.setting_changed.emit("send_port", 9000),
        )
        assert tuple(blocker.args) == ("send_port", 9000)

    def test_control_row_enabled_changed_forwards(self, qtbot):
        page = SetupPage(qtbot)
        blocker = page.run_and_capture_signal(
            page.widget.control_row_enabled_changed,
            lambda: page.grid.control_row_enabled_changed.emit(True),
        )
        assert blocker.args[0] is True


class TestOscPanel:
    def test_enable_checkbox_disables_fields_when_unchecked_by_default(self, qtbot):
        page = SetupPage(qtbot)
        assert page.osc._host_edit.isEnabled() is False

    def test_enable_checkbox_toggle_enables_fields_and_emits(self, qtbot):
        page = SetupPage(qtbot)
        blocker = page.run_and_capture_signal(
            page.widget.osc_setting_changed, lambda: page.osc._enable_cb.setChecked(True)
        )
        assert tuple(blocker.args) == ("osc_enabled", True)
        assert page.osc._host_edit.isEnabled() is True
        assert page.osc._meter_output_cb.isEnabled() is True

    def test_set_enabled_state_syncs_without_emitting(self, qtbot):
        page = SetupPage(qtbot)
        fired = []
        page.widget.osc_setting_changed.connect(lambda k, v: fired.append((k, v)))
        page.osc.set_enabled_state(True)
        assert fired == []
        assert page.osc._enable_cb.isChecked() is True
        assert page.osc._send_port.isEnabled() is True

    def test_set_meter_output_state_syncs_without_emitting(self, qtbot):
        page = SetupPage(qtbot)
        fired = []
        page.widget.osc_setting_changed.connect(lambda k, v: fired.append((k, v)))
        page.osc.set_meter_output_state(False)
        assert fired == []
        assert page.osc._meter_output_cb.isChecked() is False

    def test_meter_output_toggle_emits_setting_changed(self, qtbot):
        page = SetupPage(qtbot)
        blocker = page.run_and_capture_signal(
            page.widget.osc_setting_changed, lambda: page.osc._meter_output_cb.setChecked(False)
        )
        assert tuple(blocker.args) == ("osc_meter_output", False)


class TestExternalStateSync:
    def test_set_midi_learn_active_does_not_raise(self, qtbot):
        page = SetupPage(qtbot)
        page.widget.set_midi_learn_active(True)
        assert page.midi._learn_btn.isChecked() is True

    def test_update_tilt_live_does_not_raise(self, qtbot):
        page = SetupPage(qtbot)
        page.widget.update_tilt_live(0.1, -0.2)

    def test_set_link_state_updates_audio_panel_without_raising(self, qtbot):
        page = SetupPage(qtbot)
        page.widget.set_link_state(True, 2)
        assert page.audio._link_btn.isChecked() is True


class TestLinkStateCaching:
    def test_repeated_state_does_not_restyle(self, qtbot, monkeypatch):
        page = SetupPage(qtbot)
        btn = page.audio._link_btn
        calls = []
        orig = btn.setStyleSheet
        monkeypatch.setattr(btn, "setStyleSheet", lambda ss: (calls.append(ss), orig(ss)))
        for _ in range(30):
            page.widget.set_link_state(True, 2)
        assert len(calls) == 1

    def test_failed_enable_reverts_button(self, qtbot):
        page = SetupPage(qtbot)
        page.widget.set_link_state(False, 0)
        page.click_link()                        # user turns Link on...
        assert page.audio._link_btn.isChecked()
        page.widget.set_link_state(False, 0)     # ...controller reports failure
        assert not page.audio._link_btn.isChecked()
        assert page.audio._link_btn.text() == "—"


class TestAudioFormatDisplay:
    def test_combos_default_to_engine_defaults(self, qtbot):
        from mlrvp.config import BUFFER_SIZE, SAMPLE_RATE
        page = SetupPage(qtbot)
        assert page.audio._buf_combo.currentText() == str(BUFFER_SIZE)
        assert page.audio._sr_combo.currentText() == str(SAMPLE_RATE)

    def test_set_audio_format_shows_values_without_emitting(self, qtbot):
        page = SetupPage(qtbot)
        with qtbot.assertNotEmitted(page.widget.audio_setting_changed):
            page.audio.set_audio_format(48000, 256)
        assert page.audio._sr_combo.currentText() == "48000"
        assert page.audio._buf_combo.currentText() == "256"

    def test_latency_change_emits_and_shows_added_ms(self, qtbot):
        page = SetupPage(qtbot)
        page.audio.set_audio_format(44100, 512)
        blocker = page.run_and_capture_signal(
            page.widget.audio_setting_changed,
            lambda: page.audio._latency_combo.setCurrentText("Low"))
        assert tuple(blocker.args) == ("latency", "Low")
        assert page.audio._latency_info.text() == "+23 ms"     # 2 blocks of 512
        page.audio.set_audio_format(44100, 128)
        assert page.audio._latency_info.text() == "+6 ms"

    def test_set_latency_shows_value_without_emitting(self, qtbot):
        page = SetupPage(qtbot)
        with qtbot.assertNotEmitted(page.widget.audio_setting_changed):
            page.audio.set_latency("Medium")
        assert page.audio._latency_combo.currentText() == "Medium"
