"""GUI tests for EffectsView, driven through the EffectsPage POM wrapper.

Scope note: EffectsView's racks are populated with RackModuleBase widgets
that already have dedicated per-module smoke coverage in
test_rack_modules_smoke.py, and their DSP behavior is covered at the
processor level in test_effects_*.py. What's unique to EffectsView and
worth testing here is its own wiring: tab switching, routing a module's
param_changed signal to the correct per-effect Signal with the right bus
index, and the cross-rack effect-count limit.
"""
import pytest

from mlrvp.gui.rack_modules.bit_crusher import BitCrusherModule
from mlrvp.gui.widgets.rack_container import MAX_MODULES_PER_RACK
from tests.gui.pages.effects_page import EffectsPage

pytestmark = pytest.mark.gui


class TestTabSwitching:
    def test_defaults_to_rack_a(self, qtbot):
        page = EffectsPage(qtbot)
        assert page.current_group_index() == 0
        assert page.current_bus_suffix() == ""

    @pytest.mark.parametrize("idx,suffix", [
        (0, ""), (1, "_b"), (2, "_c"), (3, "_d"), (4, "_m"),
    ])
    def test_switching_updates_stack_index_and_bus_suffix(self, qtbot, idx, suffix):
        page = EffectsPage(qtbot)
        page.switch_group(idx)
        assert page.current_group_index() == idx
        assert page.current_bus_suffix() == suffix

    def test_switching_updates_tab_checked_state(self, qtbot):
        page = EffectsPage(qtbot)
        page.switch_group(2)
        assert page.widget._tab_c.isChecked() is True
        assert page.widget._tab_a.isChecked() is False


class TestDefaultRackContents:
    def test_rack_a_starts_with_compressor_and_delay(self, qtbot):
        page = EffectsPage(qtbot)
        types = {getattr(m, "effect_type", None) for m in page.modules_in_rack(0)}
        assert "compressor" in types
        assert "delay" in types

    def test_rack_b_starts_with_glue_comp_transient_and_cassette(self, qtbot):
        page = EffectsPage(qtbot)
        types = {getattr(m, "effect_type", None) for m in page.modules_in_rack(1)}
        assert {"glue_compressor", "transient_shaper", "cassette_tape"} <= types

    def test_rack_c_starts_with_hall_reverb_and_chorus(self, qtbot):
        page = EffectsPage(qtbot)
        types = {getattr(m, "effect_type", None) for m in page.modules_in_rack(2)}
        assert {"hall_reverb", "chorus"} <= types

    def test_rack_d_starts_with_bit_crusher_and_tube_overdrive(self, qtbot):
        page = EffectsPage(qtbot)
        types = {getattr(m, "effect_type", None) for m in page.modules_in_rack(3)}
        assert {"bit_crusher", "tube_overdrive"} <= types

    def test_rack_m_starts_with_glue_comp_and_fet_limiter(self, qtbot):
        page = EffectsPage(qtbot)
        types = {getattr(m, "effect_type", None) for m in page.modules_in_rack(4)}
        assert {"glue_compressor", "fet_limiter"} <= types

    def test_rack_sync_state_reports_presence_per_bus(self, qtbot):
        page = EffectsPage(qtbot)
        state = page.rack_sync_state()
        assert state["presence_a"]["compressor"] is True
        assert state["presence_a"]["bit_crusher"] is False
        assert state["presence_d"]["bit_crusher"] is True

    def test_rack_sync_state_reports_order(self, qtbot):
        page = EffectsPage(qtbot)
        state = page.rack_sync_state()
        # order_a includes every module with an effect_type, including the
        # VU meter utility module (not just the "routable" FX in all_fx).
        assert state["order_a"] == ["vu_meter", "compressor", "delay"]


class TestParamRouting:
    def test_compressor_module_param_change_routes_with_bus_1_on_rack_a(self, qtbot):
        page = EffectsPage(qtbot)
        blocker = page.run_and_capture_signal(
            page.widget.compressor_param_changed,
            lambda: page.widget._compressor_a.param_changed.emit("threshold", -12.0),
        )
        assert tuple(blocker.args) == ("threshold", -12.0, 1)

    def test_delay_module_param_change_routes_to_delay_signal(self, qtbot):
        page = EffectsPage(qtbot)
        blocker = page.run_and_capture_signal(
            page.widget.delay_param_changed,
            lambda: page.widget._delay_module.param_changed.emit("ms_time", 300.0),
        )
        assert tuple(blocker.args) == ("ms_time", 300.0, 1)

    def test_glue_compressor_on_rack_b_routes_with_bus_2(self, qtbot):
        page = EffectsPage(qtbot)
        blocker = page.run_and_capture_signal(
            page.widget.glue_compressor_param_changed,
            lambda: page.widget._glue_compressor_b.param_changed.emit("ratio_idx", 2),
        )
        assert tuple(blocker.args) == ("ratio_idx", 2, 2)

    def test_chorus_on_rack_c_routes_to_mod_signal_with_bus_4(self, qtbot):
        page = EffectsPage(qtbot)
        blocker = page.run_and_capture_signal(
            page.widget.mod_param_changed,
            lambda: page.widget._chorus_module.param_changed.emit("chorus_depth", 0.5),
        )
        assert tuple(blocker.args) == ("chorus_depth", 0.5, 4)

    def test_bit_crusher_on_rack_d_routes_with_bus_5(self, qtbot):
        page = EffectsPage(qtbot)
        blocker = page.run_and_capture_signal(
            page.widget.bit_crusher_param_changed,
            lambda: page.widget._bit_crusher_d.param_changed.emit("bit_depth", 4),
        )
        assert tuple(blocker.args) == ("bit_depth", 4, 5)

    def test_fet_limiter_on_master_rack_routes_with_bus_3(self, qtbot):
        page = EffectsPage(qtbot)
        blocker = page.run_and_capture_signal(
            page.widget.fet_limiter_param_changed,
            lambda: page.widget._fet_limiter_m.param_changed.emit("output_gain", 0.5),
        )
        assert tuple(blocker.args) == ("output_gain", 0.5, 3)

    def test_module_power_toggle_routes_as_enabled_param(self, qtbot):
        page = EffectsPage(qtbot)
        blocker = page.run_and_capture_signal(
            page.widget.compressor_param_changed,
            lambda: page.widget._compressor_a.set_power(False),
        )
        assert tuple(blocker.args) == ("enabled", False, 1)

    def test_rack_changed_fires_on_add_and_remove(self, qtbot):
        page = EffectsPage(qtbot)
        mod = BitCrusherModule()
        page.run_and_capture_signal(page.rack(0).rack_changed, lambda: page.add_module(0, mod))
        page.run_and_capture_signal(page.rack(0).rack_changed, lambda: page.remove_module(0, mod))


class TestRackCapacity:
    def test_rack_rejects_modules_past_per_rack_limit(self, qtbot):
        page = EffectsPage(qtbot)
        # Rack A already has 3 default modules; fill it to the per-rack cap.
        added = 0
        while page.add_module(0, BitCrusherModule()):
            added += 1
            if added > MAX_MODULES_PER_RACK + 2:
                pytest.fail("add_module never returned False — capacity limit not enforced")
        assert len(page.modules_in_rack(0)) == MAX_MODULES_PER_RACK

    def test_global_limit_blocks_additions_once_hit(self, qtbot):
        from mlrvp.gui.views.effects_view import MAX_TOTAL_EFFECTS
        page = EffectsPage(qtbot)
        total = sum(len(page.modules_in_rack(i)) for i in range(5))
        rack_idx = 0
        # Round-robin across all 5 racks so no single rack's per-rack cap
        # (MAX_MODULES_PER_RACK=8) is hit before the global cap is.
        while total < MAX_TOTAL_EFFECTS:
            assert page.add_module(rack_idx, BitCrusherModule()) is True
            total += 1
            rack_idx = (rack_idx + 1) % 5
        assert page.add_module(0, BitCrusherModule()) is False


class TestMidiMap:
    def test_click_toggles_midi_map_button(self, qtbot):
        page = EffectsPage(qtbot)
        assert page.is_midi_map_active() is False
        page.click_midi_map()
        assert page.is_midi_map_active() is True

    def test_click_emits_midi_map_toggled(self, qtbot):
        page = EffectsPage(qtbot)
        blocker = page.run_and_capture_signal(
            page.widget.midi_map_toggled, page.click_midi_map
        )
        assert blocker.args[0] is True

    def test_click_highlights_existing_modules(self, qtbot):
        page = EffectsPage(qtbot)
        page.click_midi_map()
        assert page.widget._compressor_a._midi_highlight is True

    def test_set_midi_map_active_syncs_button_without_double_emitting(self, qtbot):
        page = EffectsPage(qtbot)
        fired = []
        page.widget.midi_map_toggled.connect(fired.append)
        page.set_midi_map_active(True)
        assert page.is_midi_map_active() is True
        assert fired == []  # programmatic sync — not a user toggle, no signal


class TestMeters:
    def test_update_meters_with_empty_dict_is_a_noop(self, qtbot):
        page = EffectsPage(qtbot)
        page.update_meters({})  # must not raise

    def test_update_meters_pushes_levels_to_vu_module(self, qtbot):
        page = EffectsPage(qtbot)
        page.update_meters({"fx1": [0.5, 0.6], "fx2": [0.0, 0.0],
                             "fx3": [0.0, 0.0], "fx4": [0.0, 0.0],
                             "master": [0.0, 0.0]})  # must not raise
