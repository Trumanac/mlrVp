"""Guard against PySide6 builds whose Signal.emit() mis-refcounts its result.

PySide6 6.12.0 returns True/False from emit() without a reference; on
Python < 3.12 (bools not immortal) the app aborts after enough emits.
requirements.txt caps PySide6 for those Pythons — this fails loudly if a
broken build slips through.
"""
import sys

import pytest
from PySide6.QtCore import QObject, Signal

pytestmark = pytest.mark.skipif(
    sys.version_info >= (3, 12), reason="True/False are immortal on 3.12+")


class _Emitter(QObject):
    sig = Signal(int)


def test_signal_emit_does_not_leak_bool_references(qapp):
    o = _Emitter()
    before = sys.getrefcount(True) + sys.getrefcount(False)
    for _ in range(200):
        o.sig.emit(1)
    after = sys.getrefcount(True) + sys.getrefcount(False)
    assert after - before > -10, (
        "this PySide6 build loses a bool reference per Signal.emit() — "
        "mlrVp would abort; see the PySide6 cap in requirements.txt")
