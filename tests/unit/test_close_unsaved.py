"""Quitting with unsaved changes never loses them by accident."""
from types import SimpleNamespace

import pytest
from PySide6.QtWidgets import QMessageBox

from mlrvp.app import AppController


def _ctrl(save_result):
    closed, saved = [], []
    ctrl = SimpleNamespace(
        _preset_dirty=True,
        presets=SimpleNamespace(setlist_presets=["Gig"], active_index=0),
        window=SimpleNamespace(confirm_close=lambda: closed.append(1)),
        _gui_save_preset=lambda name: saved.append(name) or save_result,
    )
    return ctrl, closed, saved


@pytest.mark.parametrize("save_result, quits", [(True, True), (False, False)])
def test_save_choice_quits_only_if_saved(monkeypatch, save_result, quits):
    monkeypatch.setattr(QMessageBox, "__init__", lambda self, *a, **k: None)
    for name in ("setWindowTitle", "setText", "setStandardButtons", "setDefaultButton"):
        monkeypatch.setattr(QMessageBox, name, lambda self, *a: None)
    monkeypatch.setattr(QMessageBox, "exec", lambda self: QMessageBox.StandardButton.Save)
    ctrl, closed, saved = _ctrl(save_result)
    AppController._on_close_requested(ctrl)
    assert saved == ["Gig"]
    assert bool(closed) is quits     # cancelled name/setlist prompt -> stay open


def test_effect_and_tempo_edits_count_as_unsaved_changes():
    # Every effect / tempo / mixer handler reports through _notify_automation
    ctrl = SimpleNamespace(_preset_dirty=False, clock=None, patterns=[])
    AppController._notify_automation(ctrl, "fx.delay.mix", 0.4)
    assert ctrl._preset_dirty is True


@pytest.mark.parametrize("handler, arg", [
    ("_gui_master_volume", 0.5), ("_gui_quantize_changed", 2),
    ("_gui_master_hp", 0.2), ("_gui_master_lp", 0.2),
])
def test_master_and_quantize_edits_count_as_unsaved_changes(handler, arg):
    from mlrvp.config import AppConfig
    ctrl = SimpleNamespace(config=AppConfig(), engine=None, clock=None, window=None,
                           _preset_dirty=False)
    ctrl._mark_preset_dirty = lambda: setattr(ctrl, "_preset_dirty", True)
    getattr(AppController, handler)(ctrl, arg)
    assert ctrl._preset_dirty is True
