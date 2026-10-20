"""Smoke test across all 41 FX rack faceplate widgets.

These are skeuomorphic widgets (paint-heavy, each with a unique vintage
look — see the UI review notes on why their color diversity is
intentional and left alone) wired up to an FX processor's state object by
app.py at runtime. Deep per-widget interaction testing isn't worth the
cost here the way it was for ChannelStrip/TransportView — those are
touched on every single interaction, these are opened occasionally in
the rack. What IS worth guarding: every module actually constructs,
paints, and tears down without crashing, since that's exactly the kind
of regression a widget-internals refactor could introduce silently.
"""
import pytest

from mlrvp.gui.rack_modules.auto_pan import AutoPanModule
from mlrvp.gui.rack_modules.auto_wah import AutoWahModule
from mlrvp.gui.rack_modules.bit_crusher import BitCrusherModule
from mlrvp.gui.rack_modules.cassette_tape import MixtapeModule
from mlrvp.gui.rack_modules.chorus import ChorusModule
from mlrvp.gui.rack_modules.compressor import CompressorModule
from mlrvp.gui.rack_modules.digital_delay import DigitalDelayModule
from mlrvp.gui.rack_modules.dual_vu_meter import DualVUMeterModule
from mlrvp.gui.rack_modules.ether_drift import EtherDriftModule
from mlrvp.gui.rack_modules.exciter import ExciterModule
from mlrvp.gui.rack_modules.fet_limiter import FETLimiterModule
from mlrvp.gui.rack_modules.flanger import FlangerModule
from mlrvp.gui.rack_modules.freq_shifter import FreqShifterModule
from mlrvp.gui.rack_modules.fuzz import FuzzModule
from mlrvp.gui.rack_modules.gated_reverb import GatedReverbModule
from mlrvp.gui.rack_modules.glue_compressor import GlueCompressorModule
from mlrvp.gui.rack_modules.granular_freeze import GranularFreezeModule
from mlrvp.gui.rack_modules.hall_reverb import HallReverbModule
from mlrvp.gui.rack_modules.harmonizer import HarmonizerModule
from mlrvp.gui.rack_modules.lofi_radio import LoFiRadioModule
from mlrvp.gui.rack_modules.moog_filter import MoogFilterModule
from mlrvp.gui.rack_modules.multiband_filter import MultibandFilterModule
from mlrvp.gui.rack_modules.noise_gate import NoiseGateModule
from mlrvp.gui.rack_modules.oscilloscope import OscilloscopeModule
from mlrvp.gui.rack_modules.phaser import PhaserModule
from mlrvp.gui.rack_modules.ping_pong_delay import PingPongDelayModule
from mlrvp.gui.rack_modules.plate_spring_reverb import PlateSpringReverbModule
from mlrvp.gui.rack_modules.reel_to_reel import ReelToReelModule
from mlrvp.gui.rack_modules.ring_mod import RingModModule
from mlrvp.gui.rack_modules.rotary import RotaryModule
from mlrvp.gui.rack_modules.shimmer_reverb import ShimmerReverbModule
from mlrvp.gui.rack_modules.simple_eq import SimpleEQModule
from mlrvp.gui.rack_modules.stereo_widener import StereoWidenerModule
from mlrvp.gui.rack_modules.stowaway import ForeignExchangeModule
from mlrvp.gui.rack_modules.stutter import StutterModule
from mlrvp.gui.rack_modules.tape_delay import TapeDelayModule
from mlrvp.gui.rack_modules.transient_shaper import TransientShaperModule
from mlrvp.gui.rack_modules.tremolo import TremoloModule
from mlrvp.gui.rack_modules.tube_overdrive import TubeOverdriveModule
from mlrvp.gui.rack_modules.vhs import VHSModule
from mlrvp.gui.rack_modules.vinyl import VinylModule

pytestmark = pytest.mark.gui

ALL_RACK_MODULES = [
    ("auto_pan", AutoPanModule),
    ("auto_wah", AutoWahModule),
    ("bit_crusher", BitCrusherModule),
    ("cassette_tape", MixtapeModule),
    ("chorus", ChorusModule),
    ("compressor", CompressorModule),
    ("digital_delay", DigitalDelayModule),
    ("dual_vu_meter", DualVUMeterModule),
    ("ether_drift", EtherDriftModule),
    ("exciter", ExciterModule),
    ("fet_limiter", FETLimiterModule),
    ("flanger", FlangerModule),
    ("freq_shifter", FreqShifterModule),
    ("fuzz", FuzzModule),
    ("gated_reverb", GatedReverbModule),
    ("glue_compressor", GlueCompressorModule),
    ("granular_freeze", GranularFreezeModule),
    ("hall_reverb", HallReverbModule),
    ("harmonizer", HarmonizerModule),
    ("lofi_radio", LoFiRadioModule),
    ("moog_filter", MoogFilterModule),
    ("multiband_filter", MultibandFilterModule),
    ("noise_gate", NoiseGateModule),
    ("oscilloscope", OscilloscopeModule),
    ("phaser", PhaserModule),
    ("ping_pong_delay", PingPongDelayModule),
    ("plate_spring_reverb", PlateSpringReverbModule),
    ("reel_to_reel", ReelToReelModule),
    ("ring_mod", RingModModule),
    ("rotary", RotaryModule),
    ("shimmer_reverb", ShimmerReverbModule),
    ("simple_eq", SimpleEQModule),
    ("stereo_widener", StereoWidenerModule),
    ("stowaway", ForeignExchangeModule),
    ("stutter", StutterModule),
    ("tape_delay", TapeDelayModule),
    ("transient_shaper", TransientShaperModule),
    ("tremolo", TremoloModule),
    ("tube_overdrive", TubeOverdriveModule),
    ("vhs", VHSModule),
    ("vinyl", VinylModule),
]

IDS = [label for label, _ in ALL_RACK_MODULES]


@pytest.mark.parametrize("label,cls", ALL_RACK_MODULES, ids=IDS)
def test_module_constructs_and_paints_without_crashing(qtbot, label, cls):
    widget = cls()
    qtbot.addWidget(widget)
    widget.show()
    qtbot.waitExposed(widget)
    widget.repaint()
    widget.hide()


@pytest.mark.parametrize("label,cls", ALL_RACK_MODULES, ids=IDS)
def test_module_has_a_working_power_toggle(qtbot, label, cls):
    """Every rack module inherits the power/bypass toggle from
    RackModuleBase — the README documents this as a universal feature of
    the FX rack ("power toggle on its right rack ear")."""
    widget = cls()
    qtbot.addWidget(widget)
    assert widget.enabled is True  # RackModuleBase starts powered on
    widget.set_power(False)
    assert widget.enabled is False
    widget.set_power(True)
    assert widget.enabled is True
