"""Page object for mlrvp.gui.views.effects_view.EffectsView.

EffectsView is a tabbed set of 5 FX racks (A/B/C/D/Master), each a
RackContainer holding draggable RackModuleBase widgets (see
tests/gui/test_rack_modules_smoke.py for per-module coverage). The
view's own job — and this page's testable surface — is the wiring
around the modules: tab switching, routing a module's generic
param_changed signal to the correct per-effect Signal with the right
bus index, and enforcing the cross-rack effect-count limit.
"""
from mlrvp.gui.views.effects_view import EffectsView

from .base_page import BasePage

# Stack index -> tab button attribute name, matching EffectsView._switch_group.
_TAB_ATTRS = ("_tab_a", "_tab_b", "_tab_c", "_tab_d", "_tab_m")
_RACK_ATTRS = ("_rack_a", "_rack_b", "_rack_c", "_rack_d", "_rack_m")


class EffectsPage(BasePage):
    def __init__(self, qtbot):
        widget = EffectsView()
        super().__init__(widget, qtbot)

    # --- tabs ------------------------------------------------------------- #
    def switch_group(self, idx: int):
        self.click(getattr(self.widget, _TAB_ATTRS[idx]))

    def current_group_index(self) -> int:
        return self.widget._stack.currentIndex()

    def current_bus_suffix(self) -> str:
        return self.widget.current_bus_suffix

    def rack(self, idx: int):
        return getattr(self.widget, _RACK_ATTRS[idx])

    # --- rack contents ------------------------------------------------------ #
    def modules_in_rack(self, idx: int) -> list:
        return self.rack(idx).get_modules()

    def add_module(self, idx: int, module) -> bool:
        return self.rack(idx).add_module(module)

    def remove_module(self, idx: int, module):
        self.rack(idx).remove_module(module)

    def rack_sync_state(self) -> dict:
        return self.widget.get_rack_sync_state()

    # --- MIDI map ----------------------------------------------------------- #
    def click_midi_map(self):
        self.click(self.widget._midi_map_btn)

    def is_midi_map_active(self) -> bool:
        return self.widget._midi_map_btn.isChecked()

    def set_midi_map_active(self, on: bool):
        self.widget.set_midi_map_active(on)

    # --- meters -------------------------------------------------------------- #
    def update_meters(self, levels: dict):
        self.widget.update_meters(levels)
