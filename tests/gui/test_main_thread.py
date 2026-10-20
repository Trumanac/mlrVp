"""run_on_main_thread must deliver calls from worker threads to the GUI thread."""
import threading

from mlrvp.gui.main_thread import run_on_main_thread


def test_call_from_worker_thread_runs_on_main_thread(qtbot):
    main_ident = threading.get_ident()
    ran_on = []

    def worker():
        run_on_main_thread(lambda: ran_on.append(threading.get_ident()))

    t = threading.Thread(target=worker)
    t.start()
    t.join()

    qtbot.waitUntil(lambda: bool(ran_on), timeout=2000)
    assert ran_on == [main_ident]


def test_many_short_lived_threads(qtbot):
    # Each worker exits immediately after scheduling — the pattern that
    # aborted the process with QTimer.singleShot(0, context, fn).
    main_ident = threading.get_ident()
    ran_on = []
    threads = [
        threading.Thread(target=lambda: run_on_main_thread(
            lambda: ran_on.append(threading.get_ident())))
        for _ in range(50)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    qtbot.waitUntil(lambda: len(ran_on) == 50, timeout=5000)
    assert set(ran_on) == {main_ident}


def test_call_from_main_thread_runs(qtbot):
    ran = []
    run_on_main_thread(lambda: ran.append(True))
    qtbot.waitUntil(lambda: bool(ran), timeout=2000)
