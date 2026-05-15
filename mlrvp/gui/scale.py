"""UI scale management.

A single global scale factor multiplies every base pixel value used in the
UI.  Widget classes register their size code in an ``_apply_sizes`` method
which is invoked at construction time and again whenever the scale changes
(triggered by :data:`scale_manager`'s ``changed`` signal).

The scale primitive is :func:`sp` ("scaled pixels").  Replace any literal
pixel size with ``sp(N)`` to make it scale-aware.
"""
from __future__ import annotations

from PySide6.QtCore import QObject, Signal


_factor: float = 1.0


class _ScaleManager(QObject):
    """Singleton that emits when the scale factor changes."""

    changed = Signal(float)


scale_manager = _ScaleManager()


def get_scale() -> float:
    """Return the current scale factor (1.0 == 100%)."""
    return _factor


def sp(n: float) -> int:
    """Return ``n`` scaled by the current factor, rounded to a positive int.

    Always returns at least 1 so widgets don't collapse at very small scales.
    """
    return max(1, int(round(n * _factor)))


def init_scale() -> None:
    """Load the persisted scale from disk.  Call once at app startup."""
    global _factor
    from mlrvp.config import get_ui_scale
    _factor = max(0.5, min(3.0, get_ui_scale()))


def apply_scale(factor: float) -> None:
    """Update the live scale factor, persist it, and notify listeners.

    Listeners (widgets) re-run their ``_apply_sizes`` to pick up the new
    values.  No app restart is required.
    """
    global _factor
    factor = max(0.5, min(3.0, float(factor)))
    if abs(factor - _factor) < 1e-4:
        return
    _factor = factor
    from mlrvp.config import set_ui_scale
    try:
        set_ui_scale(factor)
    except Exception:
        # Persistence failures should not block the live update
        pass
    scale_manager.changed.emit(factor)


def set_scale_silent(factor: float) -> float:
    """Update ``_factor`` and persist it WITHOUT emitting the changed signal.

    Returns the clamped factor that was stored.  Use this when the caller
    wants to control exactly when the signal fires (e.g. after disabling
    widget updates so no intermediate repaints occur).
    """
    global _factor
    factor = max(0.5, min(3.0, float(factor)))
    _factor = factor
    from mlrvp.config import set_ui_scale
    try:
        set_ui_scale(factor)
    except Exception:
        pass
    return factor


# --------------------------------------------------------------------------- #
# Live-binding helpers
# --------------------------------------------------------------------------- #
# These let any widget tie one of its size constraints to the live scale
# factor without writing a full ``_apply_sizes`` method.  Each helper applies
# the size immediately, then re-applies it whenever the global scale
# changes.  The connection is parented to the widget itself so it is cleaned
# up automatically when the widget is destroyed.

def _connect(widget, fn):
    """Run ``fn`` once now, then again on every scale change.

    The connection is anchored to the widget's lifetime (``destroyed``
    signal) so we don't accumulate dangling connections.
    """
    fn()
    if widget is None:
        return

    def _slot(_f, fn=fn):
        fn()

    scale_manager.changed.connect(_slot)

    def _cleanup(*_a, _slot=_slot):
        try:
            scale_manager.changed.disconnect(_slot)
        except (TypeError, RuntimeError):
            pass

    try:
        widget.destroyed.connect(_cleanup)
    except (AttributeError, RuntimeError):
        pass


def bind_fixed_size(widget, w: float, h: float):
    """Bind ``widget.setFixedSize(sp(w), sp(h))`` to the live scale."""
    def apply():
        try:
            widget.setFixedSize(sp(w), sp(h))
        except RuntimeError:
            pass  # widget already deleted
    _connect(widget, apply)


def bind_fixed_width(widget, w: float):
    def apply():
        try:
            widget.setFixedWidth(sp(w))
        except RuntimeError:
            pass
    _connect(widget, apply)


def bind_fixed_height(widget, h: float):
    def apply():
        try:
            widget.setFixedHeight(sp(h))
        except RuntimeError:
            pass
    _connect(widget, apply)


def bind_min_width(widget, w: float):
    def apply():
        try:
            widget.setMinimumWidth(sp(w))
        except RuntimeError:
            pass
    _connect(widget, apply)


def bind_min_height(widget, h: float):
    def apply():
        try:
            widget.setMinimumHeight(sp(h))
        except RuntimeError:
            pass
    _connect(widget, apply)


def bind_max_width(widget, w: float):
    def apply():
        try:
            widget.setMaximumWidth(sp(w))
        except RuntimeError:
            pass
    _connect(widget, apply)


def bind_max_height(widget, h: float):
    def apply():
        try:
            widget.setMaximumHeight(sp(h))
        except RuntimeError:
            pass
    _connect(widget, apply)


def bind_min_size(widget, w: float, h: float):
    def apply():
        try:
            widget.setMinimumSize(sp(w), sp(h))
        except RuntimeError:
            pass
    _connect(widget, apply)
