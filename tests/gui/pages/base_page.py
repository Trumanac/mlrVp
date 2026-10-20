"""Page-Object base class for mlrVp Qt widget tests.

Each concrete page wraps one GUI widget class and exposes semantic,
task-oriented methods (``set_bpm``, ``click_mute``, ``is_muted``) instead
of letting tests reach into private widget attributes directly. When a
widget's internals change, only its page object needs updating — every
test written against the page keeps working.

Usage pattern::

    class ChannelStripPage(BasePage):
        def __init__(self, qtbot, row=0):
            widget = ChannelStrip(row)
            super().__init__(widget, qtbot)

        def click_mute(self):
            self.click(self.widget._mute_btn)

        def is_muted(self) -> bool:
            return self.widget._mute_btn.isChecked()

    def test_mute_toggles(qtbot):
        page = ChannelStripPage(qtbot)
        page.click_mute()
        assert page.is_muted()
"""
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest


class BasePage:
    """Common widget-interaction helpers shared by all page objects."""

    def __init__(self, widget, qtbot):
        self.widget = widget
        self.qtbot = qtbot
        qtbot.addWidget(widget)

    def click(self, button, wait_ms: int = 0):
        """Click a QAbstractButton (or anything with a .click() method)."""
        self.qtbot.mouseClick(button, Qt.MouseButton.LeftButton)
        if wait_ms:
            self.qtbot.wait(wait_ms)

    def set_spin_value(self, spinbox, value):
        """Set a QSpinBox/QDoubleSpinBox value, firing valueChanged like a user edit would."""
        spinbox.setValue(value)

    def set_combo_index(self, combo, index: int):
        combo.setCurrentIndex(index)

    def set_combo_text(self, combo, text: str):
        idx = combo.findText(text)
        assert idx >= 0, f"{text!r} not found in combo box items: {[combo.itemText(i) for i in range(combo.count())]}"
        combo.setCurrentIndex(idx)

    def key_click(self, widget, key):
        QTest.keyClick(widget, key)

    def run_and_capture_signal(self, signal, action, timeout: int = 1000):
        """Run *action* and assert *signal* fires within *timeout* ms.

        Returns the blocker so callers can inspect blocker.args.
        Example: ``page.run_and_capture_signal(strip.param_changed, page.click_mute)``
        """
        with self.qtbot.waitSignal(signal, timeout=timeout) as blocker:
            action()
        return blocker
