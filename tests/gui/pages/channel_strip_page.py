"""Page object for mlrvp.gui.views.channel_view.ChannelStrip."""
from mlrvp.config import PlaybackMode
from mlrvp.gui.views.channel_view import ChannelStrip

from .base_page import BasePage


class ChannelStripPage(BasePage):
    # Mirrors ChannelStrip._mode_map — kept here so tests can select a mode
    # by name without reaching into the widget's private attribute.
    MODE_LABELS = {
        PlaybackMode.LOOP: "LOOP",
        PlaybackMode.LIVE: "LIVE",
        PlaybackMode.PITCH: "PTCH",
        PlaybackMode.REVERSE: "RVRS",
        PlaybackMode.ONESHOT: "SHOT",
        PlaybackMode.SLICE: "SLC",
        PlaybackMode.SEQ: "SEQ",
        PlaybackMode.STEP: "STEP",
    }

    def __init__(self, qtbot, row: int = 0):
        widget = ChannelStrip(row)
        super().__init__(widget, qtbot)

    # --- state sync -------------------------------------------------- #
    def apply_state(self, state):
        """Push a ChannelState into the widget, as app.py does after load/preset recall."""
        self.widget.set_state(state)

    # --- mute / solo / stop ------------------------------------------- #
    def click_mute(self):
        self.click(self.widget._mute_btn)

    def is_muted(self) -> bool:
        return self.widget._mute_btn.isChecked()

    def click_solo(self):
        self.click(self.widget._solo_btn)

    def is_solo(self) -> bool:
        return self.widget._solo_btn.isChecked()

    def click_stop(self):
        self.click(self.widget._stop_btn)

    # --- mode ----------------------------------------------------------- #
    def set_mode(self, mode: PlaybackMode):
        self.set_combo_text(self.widget._mode_combo, self.MODE_LABELS[mode])

    def get_mode_label(self) -> str:
        return self.widget._mode_combo.currentText()

    # --- group assignment ------------------------------------------------ #
    def click_group(self, index: int):
        self.click(self.widget._group_btns[index])

    def get_selected_group(self) -> int:
        for i, btn in enumerate(self.widget._group_btns):
            if btn.isChecked():
                return i
        return -1

    # --- gain fader ------------------------------------------------------ #
    def set_gain_fader_position(self, pos: float):
        self.widget._gain_fader.set_value(pos)

    def get_gain_fader_position(self) -> float:
        return self.widget._gain_fader.value

    # --- load button ------------------------------------------------------ #
    def click_load(self):
        """Click LOAD — note this opens a real QFileDialog in the app;
        tests should not call this without mocking QFileDialog first."""
        self.click(self.widget._load_btn)

    def emit_sample_load(self, filepath: str):
        """Simulate a completed sample drop/selection without going through
        QFileDialog — mirrors what channel_view does after a real drop."""
        self.widget.sample_load_requested.emit(self.widget._row, filepath)
