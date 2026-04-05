"""Main audio engine for mlrVp.

Manages the sounddevice stream, channel playback, mixing, and effects processing.
Runs the real-time audio callback.
"""

import numpy as np
import sounddevice as sd
import threading
from collections import deque
from typing import Optional, Callable

from mlrvp.config import (
    AppConfig, SAMPLE_RATE, BUFFER_SIZE, MAX_ROWS, NUM_GROUPS,
    PlaybackMode, ChannelState
)
from mlrvp.audio.channel import Channel
from mlrvp.audio.mixer import Mixer
from mlrvp.audio.effects import (
    StereoDelay, TapeDelay, ChannelFilter, FXBus,
    BiquadFilter, GlobalFilter, MoogLadderFilter,
    ReelToReelProcessor, CassetteTapeProcessor, VHSProcessor, VinylProcessor,
    CompressorProcessor, GlueCompressorProcessor,
    HallReverbProcessor, PlateReverbProcessor,
    BitCrusherProcessor, TubeOverdriveProcessor,
    EtherDriftProcessor,
)
from mlrvp.audio.recorder import Recorder
from mlrvp.audio.sample import SampleBuffer


class AudioEngine:
    """Core real-time audio engine.

    Manages channels, mixer, effects, and the audio device stream.
    Thread-safe trigger/control methods for use from GUI and grid threads.
    """

    def __init__(self, config: AppConfig):
        self.config = config
        self.sample_rate = config.sample_rate
        self.buffer_size = config.buffer_size

        # Channels
        self.channels = [Channel(i, self.sample_rate) for i in range(MAX_ROWS)]
        self.channel_filters = [ChannelFilter(self.sample_rate) for _ in range(MAX_ROWS)]

        # Mixer
        self.mixer = Mixer()

        # Effects — per-bus DSP instances so each rack processes independently.
        # Bus A (FX1) instances:
        self.delay = StereoDelay(self.sample_rate)
        self.tape_delay = TapeDelay(self.sample_rate)
        self.filter_a = GlobalFilter(self.sample_rate)
        self.filter_b = MoogLadderFilter(self.sample_rate)
        # Bus B (FX2) instances:
        self.delay_b = StereoDelay(self.sample_rate)
        self.tape_delay_b = TapeDelay(self.sample_rate)
        self.filter_a_b = GlobalFilter(self.sample_rate)
        self.filter_b_b = MoogLadderFilter(self.sample_rate)
        # Mod effects already have per-bus instances inside FXBus:
        self.fx_bus_1 = FXBus(self.sample_rate)
        self.fx_bus_2 = FXBus(self.sample_rate)
        # Lo-fi media effects — per-bus instances:
        self.reel_to_reel = ReelToReelProcessor(self.sample_rate, self.buffer_size)
        self.reel_to_reel_b = ReelToReelProcessor(self.sample_rate, self.buffer_size)
        self.cassette_tape = CassetteTapeProcessor(self.sample_rate, self.buffer_size)
        self.cassette_tape_b = CassetteTapeProcessor(self.sample_rate, self.buffer_size)
        self.vhs = VHSProcessor(self.sample_rate, self.buffer_size)
        self.vhs_b = VHSProcessor(self.sample_rate, self.buffer_size)
        self.vinyl = VinylProcessor(self.sample_rate, self.buffer_size)
        self.vinyl_b = VinylProcessor(self.sample_rate, self.buffer_size)

        # Compressor — per-bus instances
        self.compressor = CompressorProcessor(self.sample_rate)
        self.compressor_b = CompressorProcessor(self.sample_rate)

        # Reverb — per-bus instances
        self.hall_reverb = HallReverbProcessor(self.sample_rate)
        self.hall_reverb_b = HallReverbProcessor(self.sample_rate)
        self.plate_reverb = PlateReverbProcessor(self.sample_rate)
        self.plate_reverb_b = PlateReverbProcessor(self.sample_rate)

        # Bit crusher — per-bus instances
        self.bit_crusher = BitCrusherProcessor(self.sample_rate)
        self.bit_crusher_b = BitCrusherProcessor(self.sample_rate)

        # Tube overdrive — per-bus instances
        self.tube_overdrive = TubeOverdriveProcessor(self.sample_rate)
        self.tube_overdrive_b = TubeOverdriveProcessor(self.sample_rate)

        # Master bus (FX Group M) — processed post-master-HP/LP trim
        self.filter_a_m = GlobalFilter(self.sample_rate)
        self.filter_b_m = MoogLadderFilter(self.sample_rate)
        self.fx_bus_m = FXBus(self.sample_rate)
        self.delay_m = StereoDelay(self.sample_rate)
        self.tape_delay_m = TapeDelay(self.sample_rate)
        self.reel_to_reel_m = ReelToReelProcessor(self.sample_rate, self.buffer_size)
        self.cassette_tape_m = CassetteTapeProcessor(self.sample_rate, self.buffer_size)
        self.vhs_m = VHSProcessor(self.sample_rate, self.buffer_size)
        self.vinyl_m = VinylProcessor(self.sample_rate, self.buffer_size)
        self.compressor_m = CompressorProcessor(self.sample_rate)
        self.hall_reverb_m = HallReverbProcessor(self.sample_rate)
        self.plate_reverb_m = PlateReverbProcessor(self.sample_rate)
        self.bit_crusher_m = BitCrusherProcessor(self.sample_rate)
        self.tube_overdrive_m = TubeOverdriveProcessor(self.sample_rate)
        # Glue compressor — all three buses
        self.glue_compressor = GlueCompressorProcessor(self.sample_rate)
        self.glue_compressor_b = GlueCompressorProcessor(self.sample_rate)
        self.glue_compressor_m = GlueCompressorProcessor(self.sample_rate)

        # Ether Drift — per-bus instances
        self.ether_drift = EtherDriftProcessor(self.sample_rate, self.buffer_size)
        self.ether_drift_b = EtherDriftProcessor(self.sample_rate, self.buffer_size)
        self.ether_drift_m = EtherDriftProcessor(self.sample_rate, self.buffer_size)

        # Recorder
        self.recorder = Recorder(self.sample_rate)

        # Per-bus in-rack flags — True when the module is present in that rack.
        # Rack A → FX1 (Creative Bus): compressor → multiband filter → moog filter → delay
        # Rack B → FX2 (Character Bus): glue compressor → reel → tape delay → vinyl
        self.delay_in_rack_a = True
        self.delay_in_rack_b = False
        self.tape_delay_in_rack_a = False
        self.tape_delay_in_rack_b = True
        self.filter_a_in_rack_a = True
        self.filter_a_in_rack_b = False
        self.filter_b_in_rack_a = True
        self.filter_b_in_rack_b = False
        self.mod_in_rack_a = False
        self.mod_in_rack_b = False
        self.reel_to_reel_in_rack_a = False
        self.reel_to_reel_in_rack_b = True
        self.cassette_tape_in_rack_a = False
        self.cassette_tape_in_rack_b = False
        self.bit_crusher_in_rack_a = False
        self.bit_crusher_in_rack_b = False
        self.tube_overdrive_in_rack_a = False
        self.tube_overdrive_in_rack_b = False
        self.vhs_in_rack_a = False
        self.vhs_in_rack_b = False
        self.vinyl_in_rack_a = False
        self.vinyl_in_rack_b = True
        self.compressor_in_rack_a = True
        self.compressor_in_rack_b = False
        self.hall_reverb_in_rack_a = False
        self.hall_reverb_in_rack_b = False
        self.plate_reverb_in_rack_a = False
        self.plate_reverb_in_rack_b = False
        self.glue_compressor_in_rack_a = False
        self.glue_compressor_in_rack_b = True

        # Master bus in-rack flags — Master chain: glue compressor → multiband EQ
        self.filter_a_in_rack_m = True
        self.filter_b_in_rack_m = False
        self.mod_in_rack_m = False
        self.delay_in_rack_m = False
        self.tape_delay_in_rack_m = False
        self.reel_to_reel_in_rack_m = False
        self.cassette_tape_in_rack_m = False
        self.vhs_in_rack_m = False
        self.vinyl_in_rack_m = False
        self.compressor_in_rack_m = False
        self.hall_reverb_in_rack_m = False
        self.plate_reverb_in_rack_m = False
        self.bit_crusher_in_rack_m = False
        self.tube_overdrive_in_rack_m = False
        self.glue_compressor_in_rack_m = True

        # Ether Drift in-rack flags (off by default — add via + menu)
        self.ether_drift_in_rack_a = False
        self.ether_drift_in_rack_b = False
        self.ether_drift_in_rack_m = False

        # Master filter (gentle HP/LP trim)
        self._master_hp = BiquadFilter(self.sample_rate)
        self._master_lp = BiquadFilter(self.sample_rate)
        self._master_hp_freq: float = 20.0   # default: no cut
        self._master_lp_freq: float = 20000.0  # default: no cut
        self._master_hp.set_highpass(20.0, 0.707)
        self._master_lp.set_lowpass(20000.0, 0.707)

        # Stream
        self._stream: Optional[sd.OutputStream] = None
        self._input_stream: Optional[sd.InputStream] = None
        self._lock = threading.Lock()
        self._running = False

        # Thread-safe event queue — all channel trigger/stop mutations are
        # deferred to the audio thread to avoid races with process().
        # deque.append / popleft are atomic in CPython.
        self._event_queue: deque = deque()

        # Callback for UI updates (called from audio thread, should be fast)
        self._meter_callback: Optional[Callable] = None

        # Pre-allocated meter data to avoid allocations in audio callback
        self._meter_data = {
            "master": [0.0, 0.0],
            "groups": [0.0] * NUM_GROUPS,
            "fx1": [0.0, 0.0],
            "fx2": [0.0, 0.0],
            "waveform_l": [],
            "waveform_r": [],
        }

        # Input buffer for recording
        self._input_buffer = np.zeros((self.buffer_size, 2), dtype=np.float32)
        self._input_ready = False

        # Pre-allocated zero buffer for delay routing bypass
        self._zero_buf = np.zeros((self.buffer_size, 2), dtype=np.float32)

        # Peak meters
        self.output_peak = np.zeros(2, dtype=np.float32)
        self._fx1_peak = np.zeros(2, dtype=np.float32)
        self._fx2_peak = np.zeros(2, dtype=np.float32)

        # Pre-allocated temp buffer for peak metering (avoids allocation in callback)
        self._peak_tmp = np.zeros(self.buffer_size, dtype=np.float32)

        # Meter callback decimation — send every Nth callback (~30 fps)
        self._meter_skip = max(1, int(self.sample_rate / self.buffer_size / 30))
        self._meter_counter = 0

        # Pre-allocated channel output list to avoid per-callback allocation
        self._channel_outputs: list[tuple[int, np.ndarray]] = []

        # Pre-allocated meter dict — reused every meter callback to avoid GC
        self._meter_dict = {
            "master": [0.0, 0.0],
            "groups": [0.0] * NUM_GROUPS,
            "fx1": [0.0, 0.0],
            "fx2": [0.0, 0.0],
            "waveform_l": [],
            "waveform_r": [],
            "waveform_fx1_l": [],
            "waveform_fx1_r": [],
            "waveform_fx2_l": [],
            "waveform_fx2_r": [],
            "compressor_gr_a": 0.0,
            "compressor_gr_b": 0.0,
            "compressor_gr_m": 0.0,
            "glue_compressor_gr_a": 0.0,
            "glue_compressor_gr_b": 0.0,
            "glue_compressor_gr_m": 0.0,
        }

    def set_meter_callback(self, callback: Callable):
        self._meter_callback = callback

    def start(self):
        """Start the audio engine."""
        if self._running:
            return

        try:
            output_device = self.config.output_device if self.config.output_device >= 0 else None
            self._stream = sd.OutputStream(
                samplerate=self.sample_rate,
                blocksize=self.buffer_size,
                channels=2,
                dtype='float32',
                callback=self._audio_callback,
                device=output_device,
                latency='low',
            )
            self._stream.start()
            self._running = True
        except Exception as e:
            print(f"Audio engine start error: {e}")
            raise

        # Start input stream for recording if input device available
        try:
            input_device = self.config.input_device if self.config.input_device >= 0 else None
            self._input_stream = sd.InputStream(
                samplerate=self.sample_rate,
                blocksize=self.buffer_size,
                channels=2,
                dtype='float32',
                callback=self._input_callback,
                device=input_device,
                latency='low',
            )
            self._input_stream.start()
        except Exception:
            # Input not critical - recording just won't work
            self._input_stream = None

    def stop(self):
        """Stop the audio engine."""
        self._running = False
        if self._stream is not None:
            self._stream.stop()
            self._stream.close()
            self._stream = None
        if self._input_stream is not None:
            self._input_stream.stop()
            self._input_stream.close()
            self._input_stream = None

    def _input_callback(self, indata, frames, time_info, status):
        """Capture input audio for recording."""
        self._input_buffer[:frames] = indata
        self._input_ready = True

    def _audio_callback(self, outdata, frames, time_info, status):
        """Main audio callback - runs in real-time audio thread."""
        try:
            # ── Process pending trigger/stop events (thread-safe) ──
            eq = self._event_queue
            while eq:
                try:
                    event = eq.popleft()
                except IndexError:
                    break
                cmd = event[0]
                if cmd == 'trigger':
                    self.channels[event[1]].trigger(event[2], event[3])
                elif cmd == 'stop':
                    self.channels[event[1]].stop()
                elif cmd == 'force_stop':
                    self.channels[event[1]].force_stop()
                elif cmd == 'set_pos':
                    self.channels[event[1]]._position = event[2]

            # Capture input audio (used for both recording and AUX channel)
            if self._input_ready:
                input_audio = self._input_buffer[:frames].copy()
                self._input_ready = False
            else:
                input_audio = None

            # Process recording input
            if input_audio is not None and self.recorder.recording:
                thru = self.recorder.process(input_audio)
            else:
                thru = None

            # Process all active channels
            channel_outputs = self._channel_outputs
            channel_outputs.clear()
            active_count = self.config.row_count

            for i in range(active_count):
                ch = self.channels[i]
                if ch.playing or ch.state.playing:
                    audio = ch.process(frames)
                    # Apply per-channel filter
                    if abs(ch.state.filter_val) > 0.01:
                        self.channel_filters[i].set_filter(ch.state.filter_val)
                        audio = self.channel_filters[i].process(audio)
                    channel_outputs.append((ch.state.group, audio))

            # Mix through groups (now returns FX bus outputs too)
            main_bus, aux_send, fx1_bus, fx2_bus = self.mixer.process_groups(channel_outputs, frames)

            # Add recorder thru to main bus if active
            if thru is not None:
                main_bus += thru

            # --- AUX input channel (external audio source) ---
            if not self.mixer.aux_mute and input_audio is not None:
                aux_scaled = input_audio * self.mixer.aux_volume
                # Route AUX to FX bus or main
                aux_route = self.mixer.aux_fx_route
                if aux_route == 1:
                    fx1_bus += aux_scaled
                elif aux_route == 2:
                    fx2_bus += aux_scaled
                else:
                    main_bus += aux_scaled

            # --- FX Processing Chain (per-bus: rack A → FX1, rack B → FX2) ---
            # Filter A (shapeable LP/HP)
            if self.filter_a_in_rack_a and self.filter_a.state.enabled:
                fx1_bus = self.filter_a.process(fx1_bus)
            if self.filter_a_in_rack_b and self.filter_a_b.state.enabled:
                fx2_bus = self.filter_a_b.process(fx2_bus)

            # Filter B (Moog ladder)
            if self.filter_b_in_rack_a and self.filter_b.state.enabled:
                fx1_bus = self.filter_b.process(fx1_bus)
            if self.filter_b_in_rack_b and self.filter_b_b.state.enabled:
                fx2_bus = self.filter_b_b.process(fx2_bus)

            # Mod effects (phaser, chorus, flanger) — per-bus FXBus handles own enabled
            if self.mod_in_rack_a:
                fx1_bus = self.fx_bus_1.process_mod_only(fx1_bus)
            if self.mod_in_rack_b:
                fx2_bus = self.fx_bus_2.process_mod_only(fx2_bus)

            # Digital delay
            if self.delay_in_rack_a and self.delay.state.enabled:
                fx1_bus = self.delay.process(fx1_bus)
            if self.delay_in_rack_b and self.delay_b.state.enabled:
                fx2_bus = self.delay_b.process(fx2_bus)

            # Tape delay
            if self.tape_delay_in_rack_a and self.tape_delay.state.enabled:
                fx1_bus = self.tape_delay.process(fx1_bus)
            if self.tape_delay_in_rack_b and self.tape_delay_b.state.enabled:
                fx2_bus = self.tape_delay_b.process(fx2_bus)

            # Lo-fi media effects (each checks own state.enabled in process())
            if self.reel_to_reel_in_rack_a:
                fx1_bus = self.reel_to_reel.process(fx1_bus)
            if self.reel_to_reel_in_rack_b:
                fx2_bus = self.reel_to_reel_b.process(fx2_bus)

            if self.cassette_tape_in_rack_a:
                fx1_bus = self.cassette_tape.process(fx1_bus)
            if self.cassette_tape_in_rack_b:
                fx2_bus = self.cassette_tape_b.process(fx2_bus)

            if self.vhs_in_rack_a:
                fx1_bus = self.vhs.process(fx1_bus)
            if self.vhs_in_rack_b:
                fx2_bus = self.vhs_b.process(fx2_bus)

            if self.vinyl_in_rack_a:
                fx1_bus = self.vinyl.process(fx1_bus)
            if self.vinyl_in_rack_b:
                fx2_bus = self.vinyl_b.process(fx2_bus)

            # Hall reverb
            if self.hall_reverb_in_rack_a:
                fx1_bus = self.hall_reverb.process(fx1_bus)
            if self.hall_reverb_in_rack_b:
                fx2_bus = self.hall_reverb_b.process(fx2_bus)

            # Plate reverb
            if self.plate_reverb_in_rack_a:
                fx1_bus = self.plate_reverb.process(fx1_bus)
            if self.plate_reverb_in_rack_b:
                fx2_bus = self.plate_reverb_b.process(fx2_bus)

            # Compressor (with sidechain routing, checks own state.enabled)
            if self.compressor_in_rack_a:
                sc_a = self._get_compressor_sidechain(self.compressor, fx1_bus, frames)
                fx1_bus = self.compressor.process(fx1_bus, sc_a)
            if self.compressor_in_rack_b:
                sc_b = self._get_compressor_sidechain(self.compressor_b, fx2_bus, frames)
                fx2_bus = self.compressor_b.process(fx2_bus, sc_b)

            # Glue compressor (no sidechain routing; self-keyed bus glue)
            if self.glue_compressor_in_rack_a:
                fx1_bus = self.glue_compressor.process(fx1_bus)
            if self.glue_compressor_in_rack_b:
                fx2_bus = self.glue_compressor_b.process(fx2_bus)

            # Bit crusher (checks own state.enabled via process())
            if self.bit_crusher_in_rack_a:
                fx1_bus = self.bit_crusher.process(fx1_bus)
            if self.bit_crusher_in_rack_b:
                fx2_bus = self.bit_crusher_b.process(fx2_bus)

            # Tube overdrive (checks own state.enabled via process())
            if self.tube_overdrive_in_rack_a:
                fx1_bus = self.tube_overdrive.process(fx1_bus)
            if self.tube_overdrive_in_rack_b:
                fx2_bus = self.tube_overdrive_b.process(fx2_bus)

            # Ether Drift (checks own state.enabled via process())
            if self.ether_drift_in_rack_a:
                fx1_bus = self.ether_drift.process(fx1_bus)
            if self.ether_drift_in_rack_b:
                fx2_bus = self.ether_drift_b.process(fx2_bus)

            # Track FX bus peaks before summing (for per-rack metering)
            tmp = self._peak_tmp[:frames]
            np.abs(fx1_bus[:, 0], out=tmp)
            self._fx1_peak[0] = max(self._fx1_peak[0] * 0.95, float(tmp.max()))
            np.abs(fx1_bus[:, 1], out=tmp)
            self._fx1_peak[1] = max(self._fx1_peak[1] * 0.95, float(tmp.max()))
            np.abs(fx2_bus[:, 0], out=tmp)
            self._fx2_peak[0] = max(self._fx2_peak[0] * 0.95, float(tmp.max()))
            np.abs(fx2_bus[:, 1], out=tmp)
            self._fx2_peak[1] = max(self._fx2_peak[1] * 0.95, float(tmp.max()))

            # Capture per-bus waveform for oscilloscopes (before summing)
            # Only compute waveform data when we'll actually send it
            _send_meters = False
            if self._meter_callback is not None:
                self._meter_counter += 1
                if self._meter_counter >= self._meter_skip:
                    self._meter_counter = 0
                    _send_meters = True
                    _bus_step = max(1, frames // 64)
                    _fx1_wf_l = fx1_bus[::_bus_step, 0].tolist()
                    _fx1_wf_r = fx1_bus[::_bus_step, 1].tolist()
                    _fx2_wf_l = fx2_bus[::_bus_step, 0].tolist()
                    _fx2_wf_r = fx2_bus[::_bus_step, 1].tolist()

            main_bus += fx1_bus
            main_bus += fx2_bus

            # Master output
            aux_return = self._zero_buf[:frames]
            output = self.mixer.apply_master(main_bus, aux_return)

            # Master filter trim (HP then LP)
            if self._master_hp_freq > 21.0:
                output = self._master_hp.process(output)
            if self._master_lp_freq < 19900.0:
                output = self._master_lp.process(output)

            # --- FX Group M (Master rack) — post-master-trim chain ---
            if self.filter_a_in_rack_m and self.filter_a_m.state.enabled:
                output = self.filter_a_m.process(output)
            if self.filter_b_in_rack_m and self.filter_b_m.state.enabled:
                output = self.filter_b_m.process(output)
            if self.mod_in_rack_m:
                output = self.fx_bus_m.process_mod_only(output)
            if self.delay_in_rack_m and self.delay_m.state.enabled:
                output = self.delay_m.process(output)
            if self.tape_delay_in_rack_m and self.tape_delay_m.state.enabled:
                output = self.tape_delay_m.process(output)
            if self.reel_to_reel_in_rack_m:
                output = self.reel_to_reel_m.process(output)
            if self.cassette_tape_in_rack_m:
                output = self.cassette_tape_m.process(output)
            if self.vhs_in_rack_m:
                output = self.vhs_m.process(output)
            if self.vinyl_in_rack_m:
                output = self.vinyl_m.process(output)
            if self.hall_reverb_in_rack_m:
                output = self.hall_reverb_m.process(output)
            if self.plate_reverb_in_rack_m:
                output = self.plate_reverb_m.process(output)
            if self.compressor_in_rack_m:
                output = self.compressor_m.process(output)
            if self.glue_compressor_in_rack_m:
                output = self.glue_compressor_m.process(output)
            if self.bit_crusher_in_rack_m:
                output = self.bit_crusher_m.process(output)
            if self.tube_overdrive_in_rack_m:
                output = self.tube_overdrive_m.process(output)
            if self.ether_drift_in_rack_m:
                output = self.ether_drift_m.process(output)

            # Peak metering
            tmp = self._peak_tmp[:len(output)]
            np.abs(output[:, 0], out=tmp)
            self.output_peak[0] = max(self.output_peak[0] * 0.95, float(tmp.max()))
            np.abs(output[:, 1], out=tmp)
            self.output_peak[1] = max(self.output_peak[1] * 0.95, float(tmp.max()))

            outdata[:] = output

            # Meter callback for UI — decimated to ~30 fps to reduce GC pressure
            if _send_meters:
                step = max(1, len(output) // 64)
                md = self._meter_dict
                md["master"][0] = float(self.output_peak[0])
                md["master"][1] = float(self.output_peak[1])
                gp = self.mixer.group_peaks
                md_groups = md["groups"]
                for gi in range(NUM_GROUPS):
                    md_groups[gi] = gp[gi]
                md["fx1"][0] = float(self._fx1_peak[0])
                md["fx1"][1] = float(self._fx1_peak[1])
                md["fx2"][0] = float(self._fx2_peak[0])
                md["fx2"][1] = float(self._fx2_peak[1])
                md["waveform_l"] = output[::step, 0].tolist()
                md["waveform_r"] = output[::step, 1].tolist()
                md["waveform_fx1_l"] = _fx1_wf_l
                md["waveform_fx1_r"] = _fx1_wf_r
                md["waveform_fx2_l"] = _fx2_wf_l
                md["waveform_fx2_r"] = _fx2_wf_r
                md["compressor_gr_a"] = self.compressor.gr_db
                md["compressor_gr_b"] = self.compressor_b.gr_db
                md["compressor_gr_m"] = self.compressor_m.gr_db
                md["glue_compressor_gr_a"] = self.glue_compressor.gr_db
                md["glue_compressor_gr_b"] = self.glue_compressor_b.gr_db
                md["glue_compressor_gr_m"] = self.glue_compressor_m.gr_db
                self._meter_callback(md)

        except Exception as exc:
            import traceback
            traceback.print_exc()
            outdata[:] = 0

    def _get_compressor_sidechain(self, comp: CompressorProcessor,
                                   bus_audio: np.ndarray, frames: int):
        """Return the appropriate sidechain signal for a compressor instance."""
        src = comp.state.sidechain_source
        if src == 0:
            return None  # self-keyed
        # Group 1-4 → mixer._group_audio[0-3]
        grp_idx = src - 1
        if 0 <= grp_idx < len(self.mixer._group_audio):
            return self.mixer._group_audio[grp_idx][:frames]
        return None

    # --- Thread-safe control methods ---

    def set_effect_in_rack(self, effect_type: str, bus: int, in_rack: bool):
        """Update whether an effect module is present in a specific rack.

        Args:
            effect_type: e.g. "delay", "tape_delay", "filter_a", etc.
            bus: 1 for Rack A / FX1, 2 for Rack B / FX2.
            in_rack: True if the module is present.
        """
        if effect_type == "delay":
            if bus == 1:
                self.delay_in_rack_a = in_rack
            elif bus == 2:
                self.delay_in_rack_b = in_rack
        elif effect_type == "tape_delay":
            if bus == 1:
                self.tape_delay_in_rack_a = in_rack
            elif bus == 2:
                self.tape_delay_in_rack_b = in_rack
        elif effect_type == "filter_a":
            if bus == 1:
                self.filter_a_in_rack_a = in_rack
            elif bus == 2:
                self.filter_a_in_rack_b = in_rack
        elif effect_type == "filter_b":
            if bus == 1:
                self.filter_b_in_rack_a = in_rack
            elif bus == 2:
                self.filter_b_in_rack_b = in_rack
        elif effect_type in ("phaser", "chorus", "flanger"):
            fx_bus = self.fx_bus_1 if bus == 1 else self.fx_bus_2
            attr = f"{effect_type}_in_rack"
            setattr(fx_bus, attr, in_rack)
            # Also update per-bus mod flag
            if bus == 1:
                self.mod_in_rack_a = (
                    self.fx_bus_1.phaser_in_rack
                    or self.fx_bus_1.chorus_in_rack
                    or self.fx_bus_1.flanger_in_rack
                )
            else:
                self.mod_in_rack_b = (
                    self.fx_bus_2.phaser_in_rack
                    or self.fx_bus_2.chorus_in_rack
                    or self.fx_bus_2.flanger_in_rack
                )
        elif effect_type == "reel_to_reel":
            if bus == 1:
                self.reel_to_reel_in_rack_a = in_rack
            elif bus == 2:
                self.reel_to_reel_in_rack_b = in_rack
        elif effect_type == "cassette_tape":
            if bus == 1:
                self.cassette_tape_in_rack_a = in_rack
            elif bus == 2:
                self.cassette_tape_in_rack_b = in_rack
        elif effect_type == "vhs":
            if bus == 1:
                self.vhs_in_rack_a = in_rack
            elif bus == 2:
                self.vhs_in_rack_b = in_rack
        elif effect_type == "vinyl":
            if bus == 1:
                self.vinyl_in_rack_a = in_rack
            elif bus == 2:
                self.vinyl_in_rack_b = in_rack
        elif effect_type == "compressor":
            if bus == 1:
                self.compressor_in_rack_a = in_rack
            elif bus == 2:
                self.compressor_in_rack_b = in_rack
        elif effect_type == "hall_reverb":
            if bus == 1:
                self.hall_reverb_in_rack_a = in_rack
            elif bus == 2:
                self.hall_reverb_in_rack_b = in_rack
        elif effect_type == "plate_reverb":
            if bus == 1:
                self.plate_reverb_in_rack_a = in_rack
            elif bus == 2:
                self.plate_reverb_in_rack_b = in_rack
        elif effect_type == "bit_crusher":
            if bus == 1:
                self.bit_crusher_in_rack_a = in_rack
            elif bus == 2:
                self.bit_crusher_in_rack_b = in_rack
        elif effect_type == "tube_overdrive":
            if bus == 1:
                self.tube_overdrive_in_rack_a = in_rack
            elif bus == 2:
                self.tube_overdrive_in_rack_b = in_rack
            else:
                self.tube_overdrive_in_rack_m = in_rack
        elif effect_type == "glue_compressor":
            if bus == 1:
                self.glue_compressor_in_rack_a = in_rack
            elif bus == 2:
                self.glue_compressor_in_rack_b = in_rack
            else:
                self.glue_compressor_in_rack_m = in_rack
        elif effect_type == "ether_drift":
            if bus == 1:
                self.ether_drift_in_rack_a = in_rack
            elif bus == 2:
                self.ether_drift_in_rack_b = in_rack
            else:
                self.ether_drift_in_rack_m = in_rack
        # --- Master bus (bus == 3) routing for remaining effect types ---
        if bus == 3:
            if effect_type == "filter_a":
                self.filter_a_in_rack_m = in_rack
            elif effect_type == "filter_b":
                self.filter_b_in_rack_m = in_rack
            elif effect_type in ("phaser", "chorus", "flanger"):
                setattr(self.fx_bus_m, f"{effect_type}_in_rack", in_rack)
                self.mod_in_rack_m = (
                    self.fx_bus_m.phaser_in_rack
                    or self.fx_bus_m.chorus_in_rack
                    or self.fx_bus_m.flanger_in_rack
                )
            elif effect_type == "delay":
                self.delay_in_rack_m = in_rack
            elif effect_type == "tape_delay":
                self.tape_delay_in_rack_m = in_rack
            elif effect_type == "reel_to_reel":
                self.reel_to_reel_in_rack_m = in_rack
            elif effect_type == "cassette_tape":
                self.cassette_tape_in_rack_m = in_rack
            elif effect_type == "vhs":
                self.vhs_in_rack_m = in_rack
            elif effect_type == "vinyl":
                self.vinyl_in_rack_m = in_rack
            elif effect_type == "compressor":
                self.compressor_in_rack_m = in_rack
            elif effect_type == "hall_reverb":
                self.hall_reverb_in_rack_m = in_rack
            elif effect_type == "plate_reverb":
                self.plate_reverb_in_rack_m = in_rack
            elif effect_type == "bit_crusher":
                self.bit_crusher_in_rack_m = in_rack
            elif effect_type == "glue_compressor":
                self.glue_compressor_in_rack_m = in_rack

    def set_master_hp(self, freq: float):
        """Set master highpass trim frequency (20-500 Hz)."""
        self._master_hp_freq = freq
        self._master_hp.set_highpass(freq, 0.707)

    def set_master_lp(self, freq: float):
        """Set master lowpass trim frequency (5000-20000 Hz)."""
        self._master_lp_freq = freq
        self._master_lp.set_lowpass(freq, 0.707)

    def trigger_channel(self, row: int, column: int, columns: int = 16):
        """Queue a channel trigger — processed on the audio thread."""
        if 0 <= row < MAX_ROWS:
            self._event_queue.append(('trigger', row, column, columns))

    def stop_channel(self, row: int):
        """Queue a channel stop (with release) — processed on the audio thread."""
        if 0 <= row < MAX_ROWS:
            self._event_queue.append(('stop', row))

    def force_stop_channel(self, row: int):
        """Queue an immediate channel stop — processed on the audio thread."""
        if 0 <= row < MAX_ROWS:
            self._event_queue.append(('force_stop', row))

    def stop_group(self, group: int):
        """Queue stops for all channels in a group."""
        for i, ch in enumerate(self.channels):
            if ch.state.group == group:
                self._event_queue.append(('stop', i))

    def set_channel_position(self, row: int, position: float):
        """Queue a playback position change — processed on the audio thread."""
        if 0 <= row < MAX_ROWS:
            self._event_queue.append(('set_pos', row, position))

    def load_sample(self, row: int, sample: SampleBuffer):
        """Load a sample into a channel."""
        if 0 <= row < MAX_ROWS:
            self.channels[row].load_sample(sample)

    def set_channel_speed(self, row: int, tempo: float):
        """Set channel playback speed based on tempo and sample length."""
        if 0 <= row < MAX_ROWS:
            ch = self.channels[row]
            if ch.sample is not None and ch.sample.duration_sec > 0:
                # Calculate speed to fit sample into the step grid
                beats = ch.state.steps
                bar_duration = beats * (60.0 / tempo)
                sample_duration = ch.sample.duration_sec * (ch.state.sel_end - ch.state.sel_start)
                if sample_duration > 0:
                    ch.state.speed = sample_duration / bar_duration

    def set_tempo(self, tempo: float):
        """Update tempo for all tempo-synced components."""
        old_tempo = self.config.tempo
        self.config.tempo = tempo
        self.delay.set_tempo(tempo)
        self.delay_b.set_tempo(tempo)
        self.delay_m.set_tempo(tempo)
        self.tape_delay.set_tempo(tempo)
        self.tape_delay_b.set_tempo(tempo)
        self.tape_delay_m.set_tempo(tempo)
        # Scale existing channel speeds proportionally instead of
        # recalculating from scratch, so manual speed overrides are preserved.
        if old_tempo > 0:
            ratio = tempo / old_tempo
            for i in range(self.config.row_count):
                ch = self.channels[i]
                if ch.sample is not None:
                    ch.state.speed *= ratio

    def set_group_volume(self, group: int, volume: float):
        if 0 <= group < NUM_GROUPS:
            self.mixer.groups[group].volume = volume

    def set_group_send(self, group: int, send: float):
        if 0 <= group < NUM_GROUPS:
            self.mixer.groups[group].send = send

    def set_group_mute(self, group: int, mute: bool):
        if 0 <= group < NUM_GROUPS:
            self.mixer.groups[group].mute = mute

    def set_master_volume(self, volume: float):
        self.mixer.master_volume = volume

    def set_aux_volume(self, volume: float):
        self.mixer.aux_volume = volume

    @staticmethod
    def get_devices() -> dict:
        """Get available audio devices."""
        devices = sd.query_devices()
        result = {"input": [], "output": []}
        for i, d in enumerate(devices):
            info = {"index": i, "name": d["name"],
                    "channels": d["max_output_channels"] or d["max_input_channels"],
                    "sample_rate": d["default_samplerate"]}
            if d["max_output_channels"] > 0:
                result["output"].append(info)
            if d["max_input_channels"] > 0:
                result["input"].append(info)
        return result
