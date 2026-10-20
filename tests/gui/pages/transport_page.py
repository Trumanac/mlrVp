"""Page object for mlrvp.gui.views.transport_view.TransportView."""
from mlrvp.gui.views.transport_view import TransportView

from .base_page import BasePage


class TransportPage(BasePage):
    def __init__(self, qtbot):
        widget = TransportView()
        super().__init__(widget, qtbot)

    # --- tempo ------------------------------------------------------- #
    def set_bpm(self, value: float):
        self.set_spin_value(self.widget._tempo_spin, value)

    def get_bpm(self) -> float:
        return self.widget._tempo_spin.value()

    def click_tempo_up(self):
        self.click(self.widget._tempo_up_btn)

    def click_tempo_down(self):
        self.click(self.widget._tempo_down_btn)

    def click_sync(self):
        self.click(self.widget._sync_btn)

    # --- group stop / all stop ---------------------------------------- #
    def click_group_stop(self, group_index: int):
        self.click(self.widget._grp_stop_btns[group_index])

    def click_all_stop(self):
        self.click(self.widget._all_stop_btn)

    # --- grid mode ------------------------------------------------------ #
    def click_grid_mode(self):
        self.click(self.widget._grid_mode_btn)

    # --- presets ----------------------------------------------------------- #
    def click_save(self):
        self.click(self.widget._save_btn)

    def click_new(self):
        self.click(self.widget._new_btn)

    def click_preset_prev(self):
        self.click(self.widget._prev_btn)

    def click_preset_next(self):
        self.click(self.widget._next_btn)
