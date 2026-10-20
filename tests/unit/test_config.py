"""Tests for mlrvp.config — dataclass defaults, enums, and scale/step tables.

These are pure data-shape checks: they exist to catch accidental breakage
(a renamed field, a table that no longer matches its documented shape)
rather than to test behavior, since config.py itself has none.
"""
import pytest

from mlrvp.config import (
    AppConfig, ChannelState, GroupState, LiveLoopState,
    PlaybackMode, QuantizeMode,
    MAX_ROWS, MAX_COLUMNS, NUM_GROUPS,
    PITCH_SCALE_MAPS, PITCH_SCALE_NAMES,
    SEQ_STEP_DIV_NAMES, SEQ_STEP_DIV_BEATS, SEQ_STEP_DIV_DEFAULT,
    LIVE_SOURCE_NAMES, LIVE_STATE_EMPTY,
    MONOME_BACKENDS, MONOME_BACKEND_AUTO,
)


class TestPlaybackMode:
    def test_values_are_stable(self):
        # These integers are persisted in preset JSON files — renumbering
        # any of them silently corrupts every saved preset on disk.
        assert PlaybackMode.LOOP == 0
        assert PlaybackMode.REVERSE == 1
        assert PlaybackMode.ONESHOT == 2
        assert PlaybackMode.SLICE == 3
        assert PlaybackMode.SEQ == 4
        assert PlaybackMode.PITCH == 5
        assert PlaybackMode.STEP == 6
        assert PlaybackMode.LIVE == 7


class TestQuantizeMode:
    def test_values_are_stable(self):
        assert QuantizeMode.NONE == 0
        assert QuantizeMode.QUARTER == 1
        assert QuantizeMode.EIGHTH == 2
        assert QuantizeMode.SIXTEENTH == 3
        assert QuantizeMode.BAR == 4


class TestChannelState:
    def test_defaults_are_sane(self):
        ch = ChannelState()
        assert ch.mode == PlaybackMode.LOOP
        assert ch.speed == 1.0
        assert ch.gain == 1.0
        assert ch.sel_start == 0.0
        assert ch.sel_end == 1.0
        assert ch.pan == 0.5
        assert ch.mute is False
        assert isinstance(ch.live, LiveLoopState)

    def test_live_state_is_independent_per_instance(self):
        # dataclass field(default_factory=...) — verify no shared mutable
        # default is accidentally aliased across instances.
        a = ChannelState()
        b = ChannelState()
        a.live.bars = 16
        assert b.live.bars == 4
        assert a.live is not b.live


class TestGroupState:
    def test_defaults(self):
        g = GroupState()
        assert g.volume == 1.0
        assert g.mute is False
        assert g.fx_route == 0


class TestAppConfig:
    def test_constructs_with_defaults(self):
        cfg = AppConfig()
        assert cfg.tempo == 120.0
        assert cfg.row_count == 8
        assert len(cfg.channels) == MAX_ROWS
        assert len(cfg.groups) == NUM_GROUPS

    def test_channels_are_independent_instances(self):
        cfg = AppConfig()
        cfg.channels[0].gain = 0.25
        assert cfg.channels[1].gain == 1.0

    def test_groups_are_independent_instances(self):
        cfg = AppConfig()
        cfg.groups[0].mute = True
        assert cfg.groups[1].mute is False

    def test_two_instances_do_not_share_state(self):
        a = AppConfig()
        b = AppConfig()
        a.tempo = 90.0
        a.channels[0].filename = "kick.wav"
        assert b.tempo == 120.0
        assert b.channels[0].filename == ""


class TestPitchScaleMaps:
    @pytest.mark.parametrize("idx", sorted(PITCH_SCALE_MAPS.keys()))
    def test_each_scale_has_one_entry_per_name(self, idx):
        assert len(PITCH_SCALE_MAPS[idx]) == MAX_COLUMNS

    def test_index_matches_name_list_length(self):
        assert len(PITCH_SCALE_NAMES) == len(PITCH_SCALE_MAPS)

    def test_scales_are_sorted_ascending(self):
        # PITCH mode maps grid column -> semitone; a non-monotonic scale
        # would make higher-numbered buttons sound lower than earlier ones.
        for idx, scale in PITCH_SCALE_MAPS.items():
            assert scale == sorted(scale), f"scale {idx} is not ascending"

    def test_chromatic_scale_is_semitone_per_step(self):
        chromatic = PITCH_SCALE_MAPS[4]
        assert chromatic == list(range(MAX_COLUMNS))


class TestSeqStepDivisions:
    def test_names_and_beats_are_parallel(self):
        assert len(SEQ_STEP_DIV_NAMES) == len(SEQ_STEP_DIV_BEATS)

    def test_default_index_in_range(self):
        assert 0 <= SEQ_STEP_DIV_DEFAULT < len(SEQ_STEP_DIV_NAMES)

    def test_beats_are_strictly_decreasing(self):
        # 1 bar -> 1/64: each division should represent fewer beats than
        # the last, or SEQ step timing silently breaks.
        beats = SEQ_STEP_DIV_BEATS
        assert all(beats[i] > beats[i + 1] for i in range(len(beats) - 1))


class TestLiveSourceNames:
    def test_input_is_first(self):
        assert LIVE_SOURCE_NAMES[0] == "INPUT"

    def test_has_one_entry_per_group_plus_input_main_fx(self):
        # INPUT, MAIN, FX-A..D (4), GRP-1..8 (NUM_GROUPS)
        assert len(LIVE_SOURCE_NAMES) == 2 + 4 + NUM_GROUPS


class TestMonomeBackends:
    def test_auto_is_default_and_included(self):
        assert MONOME_BACKEND_AUTO in MONOME_BACKENDS

    def test_no_duplicate_backends(self):
        assert len(MONOME_BACKENDS) == len(set(MONOME_BACKENDS))


class TestLiveLoopStateDefaults:
    def test_defaults(self):
        live = LiveLoopState()
        assert live.loop_state == LIVE_STATE_EMPTY
        assert live.bars == 4
        assert live.normalize is True
