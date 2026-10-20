"""MIDI / tilt mappings restored from presets: round-trip, bad entries skipped."""
from mlrvp.app import _clean_tilt_mappings
from mlrvp.midi.handler import MidiHandler, MidiMapping


def test_mappings_round_trip(qapp):
    h = MidiHandler()
    h.add_mapping("mixer.group.0.volume", MidiMapping(channel=2, cc=7, max_val=0.8))
    h.add_mapping("fx.delay.mix", MidiMapping(channel=0, cc=36, toggle=True, msg_type=0x90))
    saved = h.get_mappings_serializable()

    h2 = MidiHandler()
    h2.load_mappings(saved)
    assert h2.get_mappings_serializable() == saved


def test_load_mappings_skips_malformed_entries(qapp):
    h = MidiHandler()
    h.load_mappings([
        "not-a-dict",
        {"param_path": "a", "channel": "x"},       # wrong type
        {"param_path": "b", "cc": 1e999},          # overflow
        {"cc": 5},                                 # no target
        {"param_path": "ok", "channel": 1, "cc": 10},
    ])
    assert list(h.get_mappings()) == ["ok"]


def test_clean_tilt_mappings():
    raw = [
        {"axis": "x", "target": "fx.delay.mix", "min_val": 0.1, "max_val": "0.9"},
        {"axis": "y", "target": "t", "sensitivity": "high"},
        42,
    ]
    assert _clean_tilt_mappings(raw) == [{
        "axis": "x", "target": "fx.delay.mix",
        "min_val": 0.1, "max_val": 0.9, "sensitivity": 5.0,
    }]
    assert _clean_tilt_mappings(None) == []
    assert _clean_tilt_mappings({"axis": "x"}) == []
