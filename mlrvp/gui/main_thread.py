"""Hand work from background threads to the Qt main thread."""

import threading
from typing import Callable, Optional

from PySide6.QtCore import QCoreApplication, QObject, Qt, Signal, Slot


class _MainThreadInvoker(QObject):
    """Lives on the main thread; runs callables delivered via a queued signal."""

    call = Signal(object)

    def __init__(self):
        super().__init__()
        self.call.connect(self._run, Qt.ConnectionType.QueuedConnection)

    @Slot(object)
    def _run(self, fn):
        fn()


_invoker: Optional[_MainThreadInvoker] = None
_invoker_lock = threading.Lock()


def _get_invoker() -> Optional[_MainThreadInvoker]:
    global _invoker
    if _invoker is None:
        with _invoker_lock:
            if _invoker is None:
                app = QCoreApplication.instance()
                if app is None:
                    return None
                inv = _MainThreadInvoker()
                inv.moveToThread(app.thread())
                _invoker = inv
    return _invoker


def run_on_main_thread(fn: Callable[[], None]) -> None:
    """Schedule *fn* to run on the Qt main thread's event loop.

    Safe to call from any thread, including plain ``threading.Thread``
    workers, python-osc server threads, and the main thread itself (where it
    runs on the next event-loop pass).

    Uses a queued signal to an object owned by the main thread — the
    supported cross-thread mechanism.  Do NOT use ``QTimer.singleShot(0, fn)``
    from a worker thread instead: without a context object the call is
    queued to the *calling* thread, which has no event loop, so *fn*
    silently never runs (verified on PySide6 6.11); and the context-object
    overload aborted the process on PySide6 6.12 / Python 3.11 when the
    calling thread exited straight away.
    """
    invoker = _get_invoker()
    if invoker is not None:
        invoker.call.emit(fn)


def init_main_thread_invoker() -> None:
    """Create the invoker on the main thread (call once after QApplication)."""
    _get_invoker()
