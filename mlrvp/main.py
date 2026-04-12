"""mlrVp entry point."""

import os
import sys

# ── Limit BLAS/OpenMP internal thread pools to 1 ──────────────────────────────
# Must be set before numpy/scipy are imported.  Without this, PyInstaller
# bundles of OpenBLAS default to nthreads=cpu_count, which spawns a burst of
# worker threads every time scipy.signal.lfilter is called (every DSP block).
# Those threads compete via the GIL and cause latency spikes that are
# invisible in a local Python install (where the system Python is already
# tuned) but show up clearly in a frozen exe.
os.environ.setdefault("OMP_NUM_THREADS", "1")
os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")
os.environ.setdefault("MKL_NUM_THREADS", "1")
os.environ.setdefault("BLIS_NUM_THREADS", "1")
os.environ.setdefault("NUMEXPR_NUM_THREADS", "1")

# ── Pin Numba threading layer ─────────────────────────────────────────────────
# Our @njit kernels use nogil=True but NOT parallel=True / prange, so the
# parallel threading layer (TBB/OMP) is never needed.  Pinning to "workqueue"
# prevents Numba from attempting to load tbb12.dll (which isn't bundled) and
# avoids a noisy ImportError fallback chain on frozen builds.
os.environ.setdefault("NUMBA_THREADING_LAYER", "workqueue")

# ── Elevate process priority on Windows ───────────────────────────────────────
# Raises the whole process to HIGH_PRIORITY_CLASS so the DSP worker thread
# (separately elevated to TIME_CRITICAL inside the engine) competes more
# favourably against background OS services in the frozen exe.
if sys.platform == "win32":
    try:
        import ctypes
        HIGH_PRIORITY_CLASS = 0x0080
        ctypes.windll.kernel32.SetPriorityClass(
            ctypes.windll.kernel32.GetCurrentProcess(),
            HIGH_PRIORITY_CLASS,
        )
    except Exception:
        pass

from mlrvp.app import run_app


def main():
    sys.exit(run_app(sys.argv))


if __name__ == "__main__":
    main()
