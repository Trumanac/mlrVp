"""Hand work from background threads to the Qt main thread."""

from typing import Callable

from PySide6.QtCore import QCoreApplication, QTimer


def run_on_main_thread(fn: Callable[[], None]) -> None:
    """Schedule *fn* to run on the Qt main thread's event loop.

    Safe to call from any thread, including plain ``threading.Thread``
    workers, python-osc server threads, and the main thread itself.

    Do NOT use ``QTimer.singleShot(0, fn)`` from a worker thread instead:
    without a context object Qt queues the call to the *calling* thread,
    which has no event loop, so *fn* silently never runs (verified on
    PySide6 6.11).  Passing the application object as the context routes
    the call to the main thread.
    """
    app = QCoreApplication.instance()
    if app is None:
        return
    QTimer.singleShot(0, app, fn)
