"""Page object for mlrvp.gui.views.setup_view.SetupView.

SetupView is a QTabWidget of four independent panels (Audio/Grid/MIDI/
OSC), each of which forwards its own signals up to a SetupView-level
signal of the same name. The page exposes the tab panels directly
(``page.audio``, ``page.grid``, ...) plus a few high-value interactive
helpers for AudioSetupPanel, the panel most GUI-relevant to first-run
setup (device/sample-rate/buffer-size selection).
"""
from mlrvp.gui.views.setup_view import SetupView

from .base_page import BasePage


class SetupPage(BasePage):
    def __init__(self, qtbot):
        widget = SetupView()
        super().__init__(widget, qtbot)

    # --- tabs --------------------------------------------------------------- #
    @property
    def audio(self):
        return self.widget.audio_panel

    @property
    def grid(self):
        return self.widget.grid_panel

    @property
    def midi(self):
        return self.widget.midi_panel

    @property
    def osc(self):
        return self.widget.osc_panel

    def switch_tab(self, index: int):
        self._tabs().setCurrentIndex(index)

    def _tabs(self):
        from PySide6.QtWidgets import QTabWidget
        return self.widget.findChild(QTabWidget)

    def current_tab_index(self) -> int:
        return self._tabs().currentIndex()

    def current_tab_label(self) -> str:
        tabs = self._tabs()
        return tabs.tabText(tabs.currentIndex())

    # --- audio panel ---------------------------------------------------------- #
    def set_sample_rate(self, value: str):
        self.set_combo_text(self.audio._sr_combo, value)

    def set_buffer_size(self, value: str):
        self.set_combo_text(self.audio._buf_combo, value)

    def set_devices(self, outputs, inputs, host_apis=None):
        self.audio.set_devices(outputs, inputs, host_apis)

    def select_output_by_text(self, text: str):
        self.set_combo_text(self.audio._output_combo, text)

    def select_driver(self, api_name: str):
        self.set_combo_text(self.audio._driver_combo, api_name)

    def click_multichannel_output(self):
        self.click(self.audio._mc_cb)

    def click_link(self):
        self.click(self.audio._link_btn)

    def click_rescan(self):
        self.click(self.audio._rescan_btn)
