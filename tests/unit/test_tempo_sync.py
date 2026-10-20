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
