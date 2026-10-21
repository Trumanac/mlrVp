"""Tempo must stay in sync between clock, engine and Ableton Link peers."""
import time
from types import SimpleNamespace

import pytest

from mlrvp.app import AppController
from mlrvp.transport.clock import MasterClock


class _FakeClock:
    def __init__(self, link_tempo=None):
        self.link_tempo = link_tempo
        self.calls = []

    def set_tempo(self, bpm, immediate=False):
        self.calls.append((bpm, immediate))


def _controller(link_tempo, tempo=120.0, link_enabled=True, changed_ago=10.0):
    applied = []
    ctrl = SimpleNamespace(
        clock=_FakeClock(link_tempo),
        config=SimpleNamespace(link_enabled=link_enabled, tempo=tempo),
        window=None,
        _local_tempo_changed_at=time.monotonic() - changed_ago,
        _LINK_FOLLOW_HOLDOFF=AppController._LINK_FOLLOW_HOLDOFF,
        _apply_tempo=lambda bpm: applied.append(bpm),
    )
    return ctrl, applied


def test_follows_tempo_set_by_link_peer():
    ctrl, applied = _controller(link_tempo=128.0)
    AppController._follow_link_tempo(ctrl)
    assert applied == [128.0]


@pytest.mark.parametrize("kwargs", [
    dict(link_tempo=120.0),                      # already in sync
    dict(link_tempo=None),                       # Link not running
    dict(link_tempo=128.0, link_enabled=False),  # Link turned off
    dict(link_tempo=128.0, changed_ago=0.1),     # local change still round-tripping
])
def test_does_not_follow(kwargs):
    ctrl, applied = _controller(**kwargs)
    AppController._follow_link_tempo(ctrl)
    assert applied == []


def test_preset_recall_moves_clock_to_preset_tempo():
    ctrl = SimpleNamespace(clock=_FakeClock(), config=SimpleNamespace(tempo=96.0),
                           _local_tempo_changed_at=-1e9)
    AppController._sync_clock_tempo(ctrl)
    assert ctrl.clock.calls == [(96.0, True)]
    assert time.monotonic() - ctrl._local_tempo_changed_at < 1.0


def test_clock_link_tempo_is_none_when_link_off():
    assert MasterClock(120.0).link_tempo is None


def test_clock_link_tempo_reads_session_tempo():
    clock = MasterClock(120.0)
    clock._link_enabled = True
    clock._link_bridge = SimpleNamespace(tempo=133.0)
    assert clock.link_tempo == 133.0


def test_link_peer_above_range_is_clamped_and_applied_once():
    ctrl, applied = _controller(link_tempo=400.0)
    AppController._follow_link_tempo(ctrl)
    assert applied == [300.0]
    ctrl.config.tempo = 300.0
    AppController._follow_link_tempo(ctrl)
    assert applied == [300.0]          # not re-applied every frame


@pytest.mark.parametrize("bpm, expected", [
    (500.0, 300.0), (5.0, 20.0), (float("nan"), None), (float("inf"), None),
])
def test_apply_tempo_clamps_and_rejects_non_finite(bpm, expected):
    ctrl = SimpleNamespace(clock=_FakeClock(), engine=None,
                           config=SimpleNamespace(tempo=120.0),
                           _notify_automation=lambda *a: None)
    AppController._apply_tempo(ctrl, bpm)
    assert ctrl.clock.calls == ([] if expected is None else [(expected, False)])


def test_preset_tempo_uses_the_same_range(preset_manager):
    from mlrvp.config import AppConfig
    cfg = preset_manager.apply_preset({"config": {"tempo": 500}}, AppConfig())
    assert cfg.tempo == 300.0
