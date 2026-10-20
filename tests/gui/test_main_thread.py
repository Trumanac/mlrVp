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


def test_call_from_main_thread_runs(qtbot):
    ran = []
    run_on_main_thread(lambda: ran.append(True))
    qtbot.waitUntil(lambda: bool(ran), timeout=2000)
