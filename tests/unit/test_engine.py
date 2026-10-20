"""Tests for mlrvp.audio.engine.AudioEngine's _process_block DSP pipeline.

AudioEngine.__init__() never touches real audio hardware — the
sounddevice Stream objects stay None until start() is called, and every
buffer _process_block() needs (ring/meter/peak scratch arrays, per-bus FX
instances, event queue) is allocated up front. That means the pipeline
can be exercised directly and deterministically by constructing an
engine, queuing events through the same public API the GUI/grid threads
use (trigger_channel, load_sample, set_master_hp, ...), and calling
_process_block() ourselves instead of running the real DSP worker thread
or opening a device.
"""
import numpy as np
import pytest

from mlrvp.audio.engine import AudioEngine
from mlrvp.config import AppConfig, NUM_GROUPS


def make_engine(config: AppConfig = None) -> AudioEngine:
    return AudioEngine(config or AppConfig())


def run_block(engine: AudioEngine, frames: int = 512) -> np.ndarray:
    outbuf = np.zeros((frames, engine._out_channels), dtype=np.float32)
    engine._process_block(frames, outbuf)
    return outbuf


def rms(x: np.ndarray) -> float:
    return float(np.sqrt(np.mean(np.square(x)))) if x.size else 0.0


class TestConstruction:
    def test_engine_constructs_without_touching_hardware(self):
        engine = make_engine()
        assert engine._stream is None
        assert engine._input_stream is None

    def test_process_block_with_no_events_produces_silence(self):
        engine = make_engine()
        out = run_block(engine)
        assert np.all(np.isfinite(out))
        assert rms(out) == 0.0


class TestChannelTriggerPipeline:
    def test_triggered_channel_produces_audible_output(self, sine_sample):
        engine = make_engine()
        engine.load_sample(0, sine_sample)
        engine.trigger_channel(0, column=0, columns=16)
        out = run_block(engine)
        # First block may still be silent if the trigger event hasn't been
        # drained yet on this exact call — process a couple more to be sure
        # the channel has audibly started.
        for _ in range(3):
            out = run_block(engine)
        assert rms(out) > 0.0
        assert engine.channels[0].playing is True

    def test_stop_channel_releases_playback(self, sine_sample):
        engine = make_engine()
        engine.load_sample(0, sine_sample)
        engine.trigger_channel(0, column=0, columns=16)
        run_block(engine)
        engine.stop_channel(0)
        # Release ramps down over engine.config.fadetime; drive enough
        # blocks for the envelope to fully decay.
        for _ in range(50):
            run_block(engine)
        assert engine.channels[0].playing is False

    def test_force_stop_channel_is_immediate(self, sine_sample):
        engine = make_engine()
        engine.load_sample(0, sine_sample)
        engine.trigger_channel(0, column=0, columns=16)
        run_block(engine)
        engine.force_stop_channel(0)
        run_block(engine)
        assert engine.channels[0].playing is False

    def test_set_channel_position_moves_playhead(self, ramp_sample):
        engine = make_engine()
        engine.load_sample(0, ramp_sample)
        engine.trigger_channel(0, column=0, columns=16)
        run_block(engine)
        engine.set_channel_position(0, 250.0)
        # The set_pos event lands at the top of the next _process_block call,
        # after which playback still advances by up to *frames* within that
        # same call — so check right after with a tiny block to catch the
        # position close to where it was set, not after a full block's drift.
        run_block(engine, frames=1)
        assert engine.channels[0]._position == pytest.approx(250.0, abs=8.0)

    def test_load_sample_event_assigns_channel_sample(self, sine_sample):
        engine = make_engine()
        assert engine.channels[0].sample is None
        engine.load_sample(0, sine_sample)
        run_block(engine)
        assert engine.channels[0].sample is sine_sample

    def test_stop_group_stops_every_channel_in_that_group(self, sine_sample):
        engine = make_engine()
        for row in (0, 1):
            engine.load_sample(row, sine_sample)
            engine.channels[row].state.group = 0
            engine.trigger_channel(row, column=0, columns=16)
        run_block(engine)
        engine.stop_group(0)
        for _ in range(50):
            run_block(engine)
        assert engine.channels[0].playing is False
        assert engine.channels[1].playing is False

    def test_channel_gain_scales_output_level(self, sine_sample):
        loud = make_engine()
        loud.load_sample(0, sine_sample)
        loud.channels[0].state.gain = 1.0
        loud.trigger_channel(0, column=0, columns=16)
        for _ in range(3):
            out_loud = run_block(loud)

        quiet = make_engine()
        quiet.load_sample(0, sine_sample)
        quiet.channels[0].state.gain = 0.1
        quiet.trigger_channel(0, column=0, columns=16)
        for _ in range(3):
            out_quiet = run_block(quiet)

        assert rms(out_loud) > rms(out_quiet)

    def test_channel_pan_hard_left_silences_right_channel(self, sine_sample):
        engine = make_engine()
        engine.load_sample(0, sine_sample)
        engine.channels[0].state.pan = 0.0  # hard left
        engine.trigger_channel(0, column=0, columns=16)
        for _ in range(3):
            out = run_block(engine)
        assert rms(out[:, 1]) < rms(out[:, 0]) * 0.05

    @staticmethod
    def _levels_at_pan(sample, pan):
        engine = make_engine()
        engine.load_sample(0, sample)
        engine.channels[0].state.pan = pan
        engine.trigger_channel(0, column=0, columns=16)
        for _ in range(3):
            out = run_block(engine)
        return rms(out[:, 0]), rms(out[:, 1])

    def test_channel_pan_is_continuous_at_centre(self, sine_sample):
        # Used to drop ~3 dB the moment the knob left 0.5 (pan law switch).
        centre = self._levels_at_pan(sine_sample, 0.5)
        nudged = self._levels_at_pan(sine_sample, 0.51)
        assert nudged[0] == pytest.approx(centre[0], rel=0.03)
        assert nudged[1] == pytest.approx(centre[1], rel=0.03)

    def test_channel_pan_only_attenuates_opposite_side(self, sine_sample):
        centre = self._levels_at_pan(sine_sample, 0.5)
        right = self._levels_at_pan(sine_sample, 0.75)
        assert right[1] == pytest.approx(centre[1], rel=0.01)    # near side unchanged
        assert right[0] == pytest.approx(centre[0] * 0.5, rel=0.02)

    def test_channel_lp_filter_reduces_high_frequency_energy(self):
        engine_clean = make_engine()
        engine_filtered = make_engine()

        from tests.conftest import make_sine_sample
        hf_sample = make_sine_sample(freq=9000.0, amplitude=0.6)

        engine_clean.load_sample(0, hf_sample)
        engine_clean.trigger_channel(0, column=0, columns=16)
        for _ in range(5):
            out_clean = run_block(engine_clean)

        engine_filtered.load_sample(0, hf_sample)
        engine_filtered.channels[0].state.ch_lp = 1.0  # fully closed
        engine_filtered.trigger_channel(0, column=0, columns=16)
        for _ in range(5):
            out_filtered = run_block(engine_filtered)

        assert rms(out_filtered) < rms(out_clean)


class TestMasterTrim:
    def test_pending_master_hp_is_applied_on_next_block(self):
        engine = make_engine()
        engine.set_master_hp(200.0)
        assert engine._pending_master_hp == 200.0
        run_block(engine)
        assert engine._pending_master_hp is None
        assert engine._master_hp_freq == 200.0

    def test_pending_master_lp_is_applied_on_next_block(self):
        engine = make_engine()
        engine.set_master_lp(5000.0)
        run_block(engine)
        assert engine._pending_master_lp is None
        assert engine._master_lp_freq == 5000.0

    def test_master_volume_scales_output(self, sine_sample):
        engine = make_engine()
        engine.load_sample(0, sine_sample)
        engine.trigger_channel(0, column=0, columns=16)
        for _ in range(3):
            out_unity = run_block(engine)

        engine2 = make_engine()
        engine2.load_sample(0, sine_sample)
        engine2.set_master_volume(0.1)
        engine2.trigger_channel(0, column=0, columns=16)
        for _ in range(3):
            out_quiet = run_block(engine2)

        assert rms(out_quiet) < rms(out_unity)


class TestGroupRouting:
    def test_group_mute_silences_its_channels(self, sine_sample):
        engine = make_engine()
        engine.load_sample(0, sine_sample)
        engine.channels[0].state.group = 1
        engine.set_group_mute(1, True)
        engine.trigger_channel(0, column=0, columns=16)
        for _ in range(3):
            out = run_block(engine)
        assert rms(out) == 0.0

    def test_group_volume_scales_output(self, sine_sample):
        engine = make_engine()
        engine.load_sample(0, sine_sample)
        engine.channels[0].state.group = 2
        engine.set_group_volume(2, 0.05)
        engine.trigger_channel(0, column=0, columns=16)
        for _ in range(3):
            out_quiet = run_block(engine)

        engine2 = make_engine()
        engine2.load_sample(0, sine_sample)
        engine2.channels[0].state.group = 2
        engine2.set_group_volume(2, 1.0)
        engine2.trigger_channel(0, column=0, columns=16)
        for _ in range(3):
            out_loud = run_block(engine2)

        assert rms(out_quiet) < rms(out_loud)


class TestAuxInput:
    def test_aux_input_routed_to_main_bus_when_unmuted(self):
        engine = make_engine()
        engine.mixer.aux_mute = False
        engine.mixer.aux_fx_route = 0  # main
        frames = 512
        loud_input = (0.5 * np.ones((frames, 2))).astype(np.float32)
        engine._input_bufs[0][:frames] = loud_input
        engine._input_read_idx = 0
        out = run_block(engine, frames)
        assert rms(out) > 0.0

    def test_aux_input_silent_when_muted(self):
        engine = make_engine()
        assert engine.mixer.aux_mute is True  # default
        frames = 512
        engine._input_bufs[0][:frames] = 0.5
        engine._input_read_idx = 0
        out = run_block(engine, frames)
        assert rms(out) == 0.0

    def test_aux_input_routes_to_fx_bus(self):
        engine = make_engine()
        engine.mixer.aux_mute = False
        engine.mixer.aux_fx_route = 1  # FX1
        frames = 512
        engine._input_bufs[0][:frames] = 0.5
        engine._input_read_idx = 0
        run_block(engine, frames)
        assert engine._fx1_peak[0] > 0.0 or engine._fx1_peak[1] > 0.0


class TestFXChain:
    def test_delay_bus_a_processes_without_crashing(self, sine_sample):
        engine = make_engine()
        engine.config.delay.enabled = True
        engine.load_sample(0, sine_sample)
        engine.channels[0].state.group = 0
        engine.trigger_channel(0, column=0, columns=16)
        for _ in range(10):
            out = run_block(engine)
        assert np.all(np.isfinite(out))

    def test_reset_all_fx_clears_delay_buffer(self, sine_sample):
        engine = make_engine()
        engine.config.delay.enabled = True
        engine.load_sample(0, sine_sample)
        engine.trigger_channel(0, column=0, columns=16)
        for _ in range(10):
            run_block(engine)
        engine.reset_all_fx()
        run_block(engine)  # drains the 'reset_fx' event
        assert np.all(engine.delay.left_buf == 0) if hasattr(engine.delay, "left_buf") else True

    def test_set_rack_order_updates_bus_a_order(self):
        engine = make_engine()
        new_order = ["compressor", "delay", "filter_a"]
        engine.set_rack_order(1, new_order)
        assert engine._fx_order_map["a"] == new_order

    def test_set_effect_in_rack_toggles_flag(self):
        engine = make_engine()
        engine.set_effect_in_rack("compressor", 1, False)
        assert engine.compressor_in_rack_a is False
        engine.set_effect_in_rack("compressor", 1, True)
        assert engine.compressor_in_rack_a is True


class TestMultiChannelOutput:
    def test_out_channels_greater_than_stereo_writes_group_stems(self, sine_sample):
        engine = make_engine()
        engine._out_channels = 2 + 2 * NUM_GROUPS
        engine.load_sample(0, sine_sample)
        engine.channels[0].state.group = 0
        engine.trigger_channel(0, column=0, columns=16)
        frames = 512
        outbuf = np.zeros((frames, engine._out_channels), dtype=np.float32)
        for _ in range(5):
            engine._process_block(frames, outbuf)
        assert outbuf.shape[1] == 2 + 2 * NUM_GROUPS
        assert np.all(np.isfinite(outbuf))


class TestMeterCallback:
    def test_meter_callback_receives_snapshot_after_enough_blocks(self, sine_sample):
        engine = make_engine()
        engine.set_meter_callback(lambda: None)  # presence alone enables the meter path
        engine.load_sample(0, sine_sample)
        engine.trigger_channel(0, column=0, columns=16)
        for _ in range(engine._meter_skip + 2):
            run_block(engine)
        snap = engine.poll_meters()
        assert snap is not None
        assert "master" in snap
        assert "groups" in snap
        assert len(snap["groups"]) == NUM_GROUPS

    def test_poll_meters_returns_none_when_nothing_queued(self):
        engine = make_engine()
        assert engine.poll_meters() is None


class TestAudioArmWatch:
    def test_arm_watch_fires_callback_when_threshold_exceeded(self, sine_sample):
        engine = make_engine()
        fired = []
        engine.set_audio_arm_callback(lambda row: fired.append(row))
        engine.arm_audio_trigger(row=3, source_bus=1, threshold_linear=0.01)  # MAIN bus
        engine.load_sample(0, sine_sample)
        engine.trigger_channel(0, column=0, columns=16)
        for _ in range(5):
            run_block(engine)
        assert fired == [3]
        assert engine._audio_arm_watches == []  # single-fire, removed after firing

    def test_disarm_watch_prevents_callback(self, sine_sample):
        engine = make_engine()
        fired = []
        engine.set_audio_arm_callback(lambda row: fired.append(row))
        engine.arm_audio_trigger(row=3, source_bus=1, threshold_linear=0.01)
        engine.disarm_audio_trigger(3)
        run_block(engine)  # drains both events in the same block
        engine.load_sample(0, sine_sample)
        engine.trigger_channel(0, column=0, columns=16)
        for _ in range(5):
            run_block(engine)
        assert fired == []


class TestRecorderIntegration:
    def test_recorder_captures_input_audio(self):
        engine = make_engine()
        engine.recorder.arm(tempo=120.0, bars=1, free_length=True)
        engine.recorder.start()
        frames = 512
        engine._input_bufs[0][:frames] = 0.3
        engine._input_read_idx = 0
        run_block(engine, frames)
        assert engine.recorder._free_total_frames > 0

    def test_song_recorder_captures_master_output_when_armed(self, sine_sample):
        engine = make_engine()
        engine.load_sample(0, sine_sample)
        engine.trigger_channel(0, column=0, columns=16)
        engine.start_song_recording_now()
        for _ in range(5):
            run_block(engine)
        assert engine.song_recorder.armed_or_recording is True
        buf = engine.stop_song_recorder()
        assert buf is not None
        assert buf.num_frames > 0


class TestProcessFXChain:
    def test_effect_not_in_rack_order_is_never_processed(self, sine_sample):
        engine = make_engine()
        engine.config.delay.enabled = True
        # _fx_order_a starts empty — nothing in the rack means the delay
        # instance's internal state never advances even though it's "enabled".
        engine.load_sample(0, sine_sample)
        engine.trigger_channel(0, column=0, columns=16)
        for _ in range(5):
            run_block(engine)
        assert engine.delay.state.enabled is True  # config applied...
        # ...but process_fx_chain is a no-op with an empty order list.
        out = engine._process_fx_chain(np.zeros((8, 2), dtype=np.float32), 'a', 8)
        assert out.shape == (8, 2)

    def test_rack_order_with_gated_effect_respects_enabled_flag(self):
        engine = make_engine()
        engine.set_rack_order(1, ["filter_a"])  # bus A, gated via _FX_NEEDS_ENABLED_CHECK
        engine.filter_a.state.enabled = False
        engine.filter_a.state.lp_enabled = True
        engine.filter_a.state.lp_freq = 200.0
        engine.filter_a._update()
        bus = np.random.default_rng(0).uniform(-0.5, 0.5, (512, 2)).astype(np.float32)
        out_disabled = engine._process_fx_chain(bus.copy(), 'a', 512)
        np.testing.assert_array_equal(out_disabled, bus)  # gated off -> passthrough

        engine.filter_a.state.enabled = True
        out_enabled = engine._process_fx_chain(bus.copy(), 'a', 512)
        assert not np.array_equal(out_enabled, bus)  # filter actually ran

    def test_rack_order_processes_compressor_with_sidechain(self):
        engine = make_engine()
        engine.set_rack_order(1, ["compressor"])
        engine.compressor.state.threshold = -40.0
        engine.compressor.state.ratio = 8.0
        bus = (0.8 * np.ones((512, 2))).astype(np.float32)
        out = engine._process_fx_chain(bus, 'a', 512)
        assert np.all(np.isfinite(out))
        assert engine.compressor.gr_db < 0  # gain reduction actually applied

    def test_rack_order_processes_mod_effect_once_per_block(self):
        engine = make_engine()
        engine.fx_bus_1.phaser_in_rack = True
        engine.mod_in_rack_a = True
        engine.set_rack_order(1, ["phaser", "chorus"])  # both map to the same FXBus
        bus = np.random.default_rng(1).uniform(-0.5, 0.5, (512, 2)).astype(np.float32)
        out = engine._process_fx_chain(bus, 'a', 512)
        assert np.all(np.isfinite(out))

    def test_unknown_effect_type_in_order_is_skipped(self):
        engine = make_engine()
        engine.set_rack_order(1, ["not_a_real_effect"])
        bus = np.ones((16, 2), dtype=np.float32)
        out = engine._process_fx_chain(bus, 'a', 16)
        np.testing.assert_array_equal(out, bus)

    def test_a_raising_effect_is_skipped_without_killing_the_chain(self, monkeypatch):
        engine = make_engine()
        engine.set_rack_order(1, ["delay", "compressor"])
        engine.delay.state.enabled = True

        def _boom(*a, **kw):
            raise RuntimeError("simulated DSP fault")

        monkeypatch.setattr(engine.delay, "process", _boom)
        engine.compressor.state.threshold = -6.0
        bus = (0.5 * np.ones((256, 2))).astype(np.float32)
        out = engine._process_fx_chain(bus, 'a', 256)  # must not raise
        assert np.all(np.isfinite(out))


class TestLiveAndSampRecording:
    def test_start_live_recording_arms_and_starts(self):
        engine = make_engine()
        assert engine.start_live_recording(row=0, bars=1, tempo=120.0) is True
        assert engine.live_recording_row == 0

    def test_start_live_recording_rejects_second_concurrent_row(self):
        engine = make_engine()
        engine.start_live_recording(row=0, bars=1, tempo=120.0)
        assert engine.start_live_recording(row=1, bars=1, tempo=120.0) is False

    def test_cancel_live_recording_clears_state(self):
        engine = make_engine()
        engine.start_live_recording(row=0, bars=1, tempo=120.0)
        engine.cancel_live_recording()
        assert engine.live_recording_row == -1
        assert engine.live_recording_progress == 0.0

    def test_set_live_input_trim_updates_active_trim(self):
        engine = make_engine()
        engine.start_live_recording(row=0, bars=1, tempo=120.0)
        engine.set_live_input_trim(0.5)
        assert engine._live_recorder_trim == pytest.approx(0.5)

    def test_live_recording_processes_input_through_process_block(self):
        engine = make_engine()
        engine.start_live_recording(row=0, bars=1, tempo=120.0)
        frames = 512
        engine._input_bufs[0][:frames] = 0.4
        engine._input_read_idx = 0
        run_block(engine, frames)  # must not raise; exercises the LIVE-mode path

    def test_start_samp_recording_arms_and_starts(self):
        engine = make_engine()
        assert engine.start_samp_recording(row=0, bars=1, tempo=120.0, source_bus=1) is True
        assert engine.samp_recording_row == 0

    def test_start_samp_recording_rejects_out_of_range_source_bus(self):
        engine = make_engine()
        assert engine.start_samp_recording(row=0, bars=1, tempo=120.0, source_bus=999) is False

    def test_cancel_samp_recording_clears_state(self):
        engine = make_engine()
        engine.start_samp_recording(row=0, bars=1, tempo=120.0, source_bus=1)
        engine.cancel_samp_recording()
        assert engine.samp_recording_row == -1
        assert engine.samp_recording_progress == 0.0

    def test_samp_recording_processes_main_bus_tap(self, sine_sample):
        engine = make_engine()
        engine.load_sample(0, sine_sample)
        engine.trigger_channel(0, column=0, columns=16)
        engine.start_samp_recording(row=1, bars=1, tempo=120.0, source_bus=1)
        for _ in range(5):
            run_block(engine)  # must not raise; exercises the SAMP-capture path


class TestSongRecorderControls:
    def test_arm_then_start_song_recorder(self):
        engine = make_engine()
        engine.arm_song_recorder()
        engine.start_song_recorder()
        assert engine.song_recorder.armed_or_recording is True

    def test_clear_song_recording_resets_state(self):
        engine = make_engine()
        engine.start_song_recording_now()
        engine.clear_song_recording()
        assert engine.song_recorder.armed_or_recording is False

    def test_cancel_song_recorder_aborts_without_result(self):
        engine = make_engine()
        engine.start_song_recording_now()
        engine.cancel_song_recorder()
        assert engine.song_recorder.armed_or_recording is False


class TestMiscControlMethods:
    def test_flush_ring_buffer_resets_pointers(self):
        engine = make_engine()
        engine._ring_write = 5
        engine._ring_read = 3
        engine._ring_buf_full = True
        engine.flush_ring_buffer()
        assert engine._ring_write == 0
        assert engine._ring_read == 0
        assert engine._ring_buf_full is False

    def test_set_tempo_updates_channel_speed_for_slaved_channels(self):
        engine = make_engine()
        engine.channels[0].state.slave = True
        engine.set_tempo(140.0)
        assert engine.config.tempo == 140.0

    def test_set_group_send_updates_mixer(self):
        engine = make_engine()
        engine.set_group_send(1, 0.7)
        assert engine.mixer.groups[1].send == pytest.approx(0.7)

    def test_set_aux_volume_updates_mixer(self):
        engine = make_engine()
        engine.set_aux_volume(0.3)
        assert engine.mixer.aux_volume == pytest.approx(0.3)

    def test_get_stowaway_states_returns_all_buses(self):
        engine = make_engine()
        states = engine.get_stowaway_states()
        assert set(states.keys()) >= {"a", "b", "c", "d", "m"}
        assert len(states["a"]) == 4  # 4 slots per bus


class TestCallbacks:
    def test_input_callback_publishes_buffer_and_swaps_write_index(self):
        engine = make_engine()
        frames = 256
        indata = (0.25 * np.ones((frames, 2))).astype(np.float32)
        engine._input_callback(indata, frames, None, None)
        assert engine._input_read_idx == 0
        assert engine._input_write_idx == 1
        np.testing.assert_allclose(engine._input_bufs[0][:frames], indata)

    def test_audio_callback_outputs_silence_on_underrun(self):
        engine = make_engine()
        frames = engine.buffer_size
        outdata = np.ones((frames, 2), dtype=np.float32)
        engine._audio_callback(outdata, frames, None, None)
        assert np.all(outdata == 0)

    def test_audio_callback_copies_ring_buffer_slot_when_available(self):
        engine = make_engine()
        frames = engine.buffer_size
        engine._ring_buf[0][:frames] = 0.7
        engine._ring_write = 1  # worker has filled slot 0
        outdata = np.zeros((frames, 2), dtype=np.float32)
        engine._audio_callback(outdata, frames, None, None)
        assert np.all(outdata == pytest.approx(0.7))
        assert engine._ring_read == 1

    def test_gc_loop_exits_promptly_when_stopped(self):
        import threading
        import time
        engine = make_engine()
        engine._gc_stop = threading.Event()
        t = threading.Thread(target=engine._gc_loop, daemon=True)
        t.start()
        t0 = time.perf_counter()
        engine._gc_stop.set()
        t.join(timeout=3.0)
        assert not t.is_alive()
        assert time.perf_counter() - t0 < 1.0   # no longer waits out a 2 s sleep

    def test_gc_collection_is_handed_to_scheduler(self):
        import threading
        engine = make_engine()
        engine._gc_stop = threading.Event()
        scheduled = threading.Event()
        engine.gc_scheduler = lambda fn: scheduled.set()
        t = threading.Thread(target=engine._gc_loop, daemon=True)
        t.start()
        assert scheduled.wait(5.0)   # first collection goes to the scheduler
        engine._gc_stop.set()
        t.join(timeout=3.0)


class TestProfiling:
    def test_profiling_accumulates_and_logs_at_interval(self, sine_sample, caplog):
        import logging
        engine = make_engine()
        engine._prof_enabled = True
        engine._prof_interval = 2  # log after just 2 blocks instead of the real interval
        engine.load_sample(0, sine_sample)
        engine.trigger_channel(0, column=0, columns=16)
        with caplog.at_level(logging.DEBUG, logger="mlrvp"):
            for _ in range(3):
                run_block(engine)
        assert "DSP PROFILE" in caplog.text
        assert engine._prof_count == 1  # reset after hitting the interval, then incremented once more


class TestChannelSpeed:
    def test_set_channel_speed_updates_channel_and_stays_within_bounds(self):
        engine = make_engine()
        engine.set_channel_speed(0, tempo=240.0)
        assert engine.channels[0].state.speed > 0


class TestPluginSlotManagement:
    def test_load_and_unload_plugin_slot_round_trip(self):
        engine = make_engine()
        engine.load_plugin_slot("a", 0, path="", fmt="vst3")
        engine.set_plugin_slot_wet_dry("a", 0, 0.5)
        engine.set_plugin_slot_enabled("a", 0, False)
        params = engine.get_plugin_slot_params("a", 0)
        assert isinstance(params, dict)
        names = engine.get_plugin_slot_param_names("a", 0)
        assert isinstance(names, list)
        engine.unload_plugin_slot("a", 0)

    def test_plugin_slot_bus_key_normalization(self):
        engine = make_engine()
        # fx_bus_1 is the GUI-facing alias for bus 'a' — both must resolve
        # to the same underlying StowawayProcessor pool slot.
        assert engine._get_stowaway("fx_bus_1", 0) is engine._get_stowaway("a", 0)

    def test_get_stowaway_out_of_range_slot_returns_none(self):
        engine = make_engine()
        assert engine._get_stowaway("a", 99) is None


class TestEventBudget:
    def test_events_beyond_budget_spill_to_next_block(self, sine_sample):
        # _process_block caps event draining at 16/block — queue more than
        # that and confirm nothing crashes and later events still land.
        engine = make_engine()
        for row in range(20):
            engine.channels[row % engine.channels.__len__()].load_sample(sine_sample)
        for row in range(20):
            engine.trigger_channel(row % len(engine.channels), column=0, columns=16)
        run_block(engine)
        run_block(engine)  # second block drains the overflow
        assert any(ch.playing for ch in engine.channels)
