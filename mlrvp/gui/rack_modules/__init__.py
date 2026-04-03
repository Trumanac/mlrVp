"""Rack effect modules for the FX Rack UI."""

from mlrvp.gui.rack_modules.digital_delay import DigitalDelayModule
from mlrvp.gui.rack_modules.tape_delay import TapeDelayModule
from mlrvp.gui.rack_modules.multiband_filter import MultibandFilterModule
from mlrvp.gui.rack_modules.moog_filter import MoogFilterModule
from mlrvp.gui.rack_modules.phaser import PhaserModule
from mlrvp.gui.rack_modules.chorus import ChorusModule
from mlrvp.gui.rack_modules.flanger import FlangerModule
from mlrvp.gui.rack_modules.bit_crusher import BitCrusherModule
from mlrvp.gui.rack_modules.tube_overdrive import TubeOverdriveModule
from mlrvp.gui.rack_modules.plate_spring_reverb import PlateSpringReverbModule
from mlrvp.gui.rack_modules.hall_reverb import HallReverbModule
from mlrvp.gui.rack_modules.reel_to_reel import ReelToReelModule
from mlrvp.gui.rack_modules.cassette_tape import MixtapeModule
from mlrvp.gui.rack_modules.vhs import VHSModule
from mlrvp.gui.rack_modules.vinyl import VinylModule
from mlrvp.gui.rack_modules.compressor import CompressorModule
from mlrvp.gui.rack_modules.glue_compressor import GlueCompressorModule
from mlrvp.gui.rack_modules.dual_vu_meter import DualVUMeterModule
from mlrvp.gui.rack_modules.oscilloscope import OscilloscopeModule
from mlrvp.gui.rack_modules.ether_drift import EtherDriftModule

from mlrvp.gui.widgets.rack_container import register_rack_module

# Register all modules in the Add menu
_MODULES = {
    "Déjà Vu Delay (Digital Delay)": DigitalDelayModule,
    "Rewind (Tape Delay)": TapeDelayModule,
    "Byte Me (Bit Crusher)": BitCrusherModule,
    "Hot Snake (Tube Overdrive)": TubeOverdriveModule,
    "Band Practice (Multiband Filter)": MultibandFilterModule,
    "The Ladder (Moog Filter)": MoogFilterModule,
    "Snake Charmer (Phaser)": PhaserModule,
    "Parrot Talk (Chorus)": ChorusModule,
    "Jet Stream (Flanger)": FlangerModule,
    "Spring Fling (Plate/Spring Reverb)": PlateSpringReverbModule,
    "Echo Chamber (Hall Reverb)": HallReverbModule,
    "Reel Deal (Reel-to-Reel)": ReelToReelModule,
    "Mixtape (Cassette Tape)": MixtapeModule,
    "Tracking Shot (VHS)": VHSModule,
    "Wax Poetic (Vinyl)": VinylModule,
    "Thrust Issues (Sidechain Compressor)": CompressorModule,
    "Group Hug (Glue Compressor)": GlueCompressorModule,
    "Twin Peaks (Dual VU Meter)": DualVUMeterModule,
    "CRT:// Scope (Oscilloscope)": OscilloscopeModule,
    "Ether Drift (Bipolar Phaser)": EtherDriftModule,
}

for _name, _cls in _MODULES.items():
    register_rack_module(_name, _cls)

__all__ = [
    "DigitalDelayModule", "TapeDelayModule", "BitCrusherModule",
    "TubeOverdriveModule", "MultibandFilterModule", "MoogFilterModule",
    "PhaserModule", "ChorusModule", "FlangerModule",
    "PlateSpringReverbModule", "HallReverbModule",
    "ReelToReelModule", "MixtapeModule", "VHSModule", "VinylModule",
    "CompressorModule", "GlueCompressorModule", "DualVUMeterModule", "OscilloscopeModule",
    "EtherDriftModule",
]
