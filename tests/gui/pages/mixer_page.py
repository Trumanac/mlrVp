"""Page object for mlrvp.gui.views.mixer_view.MixerView."""
from mlrvp.gui.views.mixer_view import MixerView

from .base_page import BasePage


class MixerPage(BasePage):
    def __init__(self, qtbot):
        widget = MixerView()
        super().__init__(widget, qtbot)

    # --- group strips --------------------------------------------------- #
    def group_channel(self, group: int):
        return self.widget.get_group_channel(group)

    def drag_group_volume(self, group: int, fader_pos: float):
        """Simulate a fader drag by emitting value_changed directly —
        VerticalFader only emits on real mouse drag, not on set_value()."""
        self.group_channel(group)._vol_fader.value_changed.emit(fader_pos)

    def drag_group_send(self, group: int, fader_pos: float):
        self.group_channel(group)._send_fader.value_changed.emit(fader_pos)

    def click_group_mute(self, group: int):
        self.click(self.group_channel(group)._mute_btn)

    def is_group_muted(self, group: int) -> bool:
        return self.group_channel(group)._mute_btn.isChecked()

    def click_group_fx_route(self, group: int):
        self.click(self.group_channel(group)._fx_route_btn)

    def group_fx_route_label(self, group: int) -> str:
        return self.group_channel(group)._fx_route_btn.text()

    def set_group_level(self, group: int, level: float):
        self.widget.set_group_level(group, level)

    def set_active_groups(self, n: int):
        self.widget.set_active_groups(n)

    def is_group_visible(self, group: int) -> bool:
        # isVisible() requires the whole window chain to be shown; isHidden()
        # reflects the widget's own explicit setVisible(False) regardless.
        return not self.group_channel(group).isHidden()

    # --- aux -------------------------------------------------------------- #
    def drag_aux_volume(self, fader_pos: float):
        self.widget._aux_fader.value_changed.emit(fader_pos)

    def drag_aux_send(self, fader_pos: float):
        self.widget._aux_send_fader.value_changed.emit(fader_pos)

    def click_aux_mute(self):
        self.click(self.widget._aux_mute_btn)

    def is_aux_muted(self) -> bool:
        return self.widget._aux_mute_btn.isChecked()

    def click_aux_fx_route(self):
        self.click(self.widget._aux_fx_route_btn)

    def aux_fx_route_label(self) -> str:
        return self.widget._aux_fx_route_btn.text()

    # --- master ------------------------------------------------------------ #
    def drag_master_volume(self, fader_pos: float):
        self.widget._master_fader.value_changed.emit(fader_pos)

    def click_dac(self):
        self.click(self.widget._dac_btn)

    def is_dac_engaged(self) -> bool:
        """True = OFF (checked/dark); the DAC button semantics are inverted."""
        return self.widget._dac_btn.isChecked()

    def set_audio_enabled(self, enabled: bool):
        self.widget.set_audio_enabled(enabled)
