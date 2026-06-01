"""MIDI mapping overlay — highlights mappable controls and handles auto-learn.

When MIDI map mode is active, mappable buttons/faders are highlighted.
Click a control → it starts MIDI learn → move/push MIDI controller → mapped.
"""

from PySide6.QtWidgets import QWidget, QPushButton, QDoubleSpinBox, QComboBox
from PySide6.QtCore import Signal, QObject
from mlrvp.gui.styles import ACCENT_BLUE, ACCENT_GOLD


def _has_midi_highlight(widget: QWidget) -> bool:
    """Check if widget is a custom-painted type with _midi_highlight support."""
    return hasattr(widget, '_midi_highlight')


def _highlight_suffix(widget: QWidget, level: int) -> str:
    """Return a stylesheet suffix that visibly highlights the widget.

    Qt ignores ``!important`` and bare CSS is beaten by any explicit selector
    rule already present in the stylesheet.  We therefore wrap the highlight in
    a same-type selector so it wins by "last rule in document" ordering.
    """
    if level == 0:
        return ""

    if level == 1:
        border = f"2px solid {ACCENT_BLUE}"
        bg     = "rgba(121, 177, 255, 40)"
        bg_c   = "rgba(121, 177, 255, 60)"
    else:  # level == 2 — learning / gold
        border = f"2px solid {ACCENT_GOLD}"
        bg     = "rgba(245, 217, 122, 50)"
        bg_c   = "rgba(245, 217, 122, 70)"

    if isinstance(widget, QPushButton):
        return (
            f" QPushButton {{ border: {border}; background: {bg}; }}"
            f" QPushButton:hover {{ border: {border}; background: {bg_c}; }}"
            f" QPushButton:checked {{ border: {border}; background: {bg_c}; }}"
            f" QPushButton:pressed {{ border: {border}; background: {bg_c}; }}"
        )
    elif isinstance(widget, QDoubleSpinBox):
        return f" QDoubleSpinBox {{ border: {border}; background: {bg}; }}"
    elif isinstance(widget, QComboBox):
        return f" QComboBox {{ border: {border}; background: {bg}; }}"
    else:
        # Bare CSS fallback for any other plain widget
        return f" border: {border}; background: {bg};"


def _set_highlight(ctrl, level: int):
    """Set highlight level (0=off, 1=highlight, 2=learning) on a control."""
    w = ctrl.widget
    if _has_midi_highlight(w):
        w._midi_highlight = level
        w.update()
    else:
        w.setStyleSheet(ctrl._original_style + _highlight_suffix(w, level))


def _widget_alive(widget: QWidget) -> bool:
    """Return True if the underlying C++ QWidget is still alive."""
    try:
        widget.objectName()
        return True
    except RuntimeError:
        return False


class MappableControl:
    """Metadata about a mappable GUI control."""

    def __init__(self, widget: QWidget, param_path: str, label: str):
        self.widget = widget
        self.param_path = param_path
        self.label = label
        self._original_style: str = ""


class MidiMapManager(QObject):
    """Manages MIDI map mode — discovers mappable controls, highlights, and learns."""

    mapping_started = Signal(str)  # param_path being learned
    mapping_completed = Signal(str, int, int)  # param_path, channel, cc
    mode_changed = Signal(bool)  # True = active, False = inactive

    def __init__(self, parent=None):
        super().__init__(parent)
        self._active = False
        self._controls: list[MappableControl] = []
        self._learning_control: MappableControl | None = None

    @property
    def active(self) -> bool:
        return self._active

    def register_control(self, widget: QWidget, param_path: str, label: str = ""):
        """Register a widget as mappable."""
        try:
            widget.objectName()  # probe C++ object liveness
        except RuntimeError:
            return
        # Prune dead widget refs while iterating
        self._controls = [c for c in self._controls if _widget_alive(c.widget)]
        for c in self._controls:
            if c.widget is widget and c.param_path == param_path:
                return
        ctrl = MappableControl(widget, param_path, label or param_path)
        self._controls.append(ctrl)
        if self._active:
            ctrl._original_style = widget.styleSheet()
            _set_highlight(ctrl, 1)
            widget.installEventFilter(self)

    def activate(self):
        """Enter MIDI map mode — highlight all mappable controls."""
        self._active = True
        for ctrl in list(self._controls):
            try:
                ctrl._original_style = ctrl.widget.styleSheet()
                _set_highlight(ctrl, 1)
                ctrl.widget.installEventFilter(self)
            except RuntimeError:
                self._controls.remove(ctrl)
        self.mode_changed.emit(True)

    def deactivate(self):
        """Exit MIDI map mode — restore all controls."""
        self._active = False
        self._learning_control = None
        for ctrl in list(self._controls):
            try:
                _set_highlight(ctrl, 0)
                ctrl.widget.removeEventFilter(self)
            except RuntimeError:
                self._controls.remove(ctrl)
        self.mode_changed.emit(False)

    def on_learn_complete(self, param_path: str, channel: int, cc: int):
        """Called when MIDI learn completes for a parameter."""
        if self._learning_control and self._learning_control.param_path == param_path:
            ctrl = self._learning_control
            _set_highlight(ctrl, 1)
            self._learning_control = None
            self.mapping_completed.emit(param_path, channel, cc)

    def eventFilter(self, obj, event):
        """Intercept LEFT clicks on mappable controls during map mode."""
        from PySide6.QtCore import QEvent
        from PySide6.QtCore import Qt as _Qt
        if not self._active:
            return False
        if event.type() == QEvent.Type.MouseButtonPress:
            if event.button() != _Qt.MouseButton.LeftButton:
                return False  # pass right-clicks through to widget handlers
            for ctrl in self._controls:
                try:
                    match = ctrl.widget is obj
                except RuntimeError:
                    continue
                if match:
                    self._start_learn(ctrl)
                    return True  # consume the click
        return False

    def _start_learn(self, ctrl: MappableControl):
        """Start MIDI learn for a specific control."""
        if self._learning_control:
            _set_highlight(self._learning_control, 1)
        self._learning_control = ctrl
        _set_highlight(ctrl, 2)
        self.mapping_started.emit(ctrl.param_path)

    def get_widget(self, param_path: str):
        """Return the widget registered for param_path, or None."""
        for ctrl in self._controls:
            if ctrl.param_path == param_path:
                try:
                    ctrl.widget.objectName()  # liveness check
                    return ctrl.widget
                except RuntimeError:
                    pass
        return None
