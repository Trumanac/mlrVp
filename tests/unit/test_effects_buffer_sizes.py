"""Every rack effect must run at every buffer size offered in Setup.

Effects pre-allocate scratch buffers at construction time; a buffer size
larger than that default used to make some effects raise on every block,
which the engine swallowed — the effect was silently bypassed.
"""
import logging

import numpy as np
import pytest

from mlrvp.audio.engine import AudioEngine
from mlrvp.config import AppConfig, BUFFER_SIZE_CHOICES


def _engine(buffer_size):
    cfg = AppConfig()
    cfg.buffer_size = buffer_size
    return AudioEngine(cfg)


def _bus_a_effects(engine):
    """(name, processor) for each distinct effect on bus A, enabled and wet."""
    seen = set()
    for name, proc in engine._fx_processors['a'].items():
        if name.startswith('stowaway') or id(proc) in seen:
            continue
        seen.add(id(proc))
        st = getattr(proc, 'state', None)
        if st is not None:
            for attr in ('enabled', 'active', 'on'):
                if hasattr(st, attr):
                    setattr(st, attr, True)
            if hasattr(st, 'mix'):
                st.mix = 0.5
        if name in ('phaser', 'chorus', 'flanger'):
            for mod in ('phaser', 'chorus', 'flanger'):
                setattr(proc, f'{mod}_in_rack', True)
        yield name, proc


@pytest.mark.parametrize("buffer_size", BUFFER_SIZE_CHOICES)
def test_all_effects_run_at_buffer_size(buffer_size):
    engine = _engine(buffer_size)
    rng = np.random.default_rng(0)
    failures = []
    for name, proc in _bus_a_effects(engine):
        for _ in range(3):
            x = (rng.standard_normal((buffer_size, 2)) * 0.3).astype(np.float32)
            try:
                y = proc.process_mod_only(x) if name in ('phaser', 'chorus', 'flanger') \
                    else proc.process(x)
            except Exception as e:
                failures.append(f"{name}: {type(e).__name__}: {e}")
                break
            if y.shape != x.shape:
                failures.append(f"{name}: output shape {y.shape} != {x.shape}")
                break
            if not np.all(np.isfinite(y)):
                failures.append(f"{name}: non-finite output")
                break
    assert not failures, "\n".join(failures)


class _Broken:
    def process(self, data):
        raise RuntimeError("broken effect")


def test_failing_effect_is_logged_once_and_reported(caplog):
    engine = _engine(512)
    engine._fx_processors['a']['broken'] = _Broken()
    engine.set_rack_order(1, ['broken'])
    x = np.zeros((512, 2), np.float32)

    with caplog.at_level(logging.WARNING, logger="mlrvp"):
        for _ in range(200):                      # ~2.3 s of audio blocks
            out = engine._process_fx_chain(x, 'a', 512)
    assert out is x                               # bypassed, audio passes through

    records = [r for r in caplog.records if "broken" in r.getMessage()]
    assert len(records) == 1 and records[0].exc_info is not None

    notice = engine.poll_fx_error_notice()
    assert notice is not None and "broken" in notice and "FX1" in notice
    assert engine.poll_fx_error_notice() is None  # cleared once read
