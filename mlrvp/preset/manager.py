"""Preset save/load manager for mlrVp.

Handles full state serialization including channel settings,
mixer state, effect parameters, MIDI mappings, and setlist.
"""

import json
import logging
import re
from pathlib import Path
from typing import Optional

from mlrvp.config import (
    AppConfig, ChannelState, GroupState, DelayState,
    RecorderState, PatternRecorderState, FilterState, FilterBState,
    TapeDelayState, ModState, HallReverbState, PlateReverbState,
    PlaybackMode, QuantizeMode,
    get_presets_dir, get_setlists_dir, NUM_PRESETS, NUM_SETLIST_SLOTS
)

log = logging.getLogger("mlrvp")


def _safe_setattr(obj, key, value):
    """setattr wrapped in try/except so a bad preset value won't crash."""
    try:
        setattr(obj, key, value)
    except (ValueError, TypeError, AttributeError) as exc:
        log.warning("Preset load: skipping bad value %s=%r (%s)", key, value, exc)


def _sanitize_name(name: str) -> str:
    """Convert a display name to a safe directory/file name."""
    s = re.sub(r'[<>:"/\\|?*]', '_', name.strip())
    return s or "untitled"


class PresetManager:
    """Manages a global preset library and ordered setlists.

    Presets are standalone JSON files in data/presets/ (global pool).
    Setlists are ordered lists of preset names in data/setlists/.
    """

    def __init__(self):
        self.setlist_name: str = "Default"
        self.setlist_presets: list[str] = []   # ordered preset names in current setlist
        self.active_index: int = -1            # selected preset in setlist (-1 = none)

        self._ensure_dirs()
        self.load_setlist("Default")

    def _ensure_dirs(self):
        """Create preset library and setlist directories if missing."""
        get_presets_dir().mkdir(parents=True, exist_ok=True)
        get_setlists_dir().mkdir(parents=True, exist_ok=True)
        fp = get_setlists_dir() / "Default.json"
        if not fp.exists():
            with open(fp, 'w') as f:
                json.dump({"name": "Default", "presets": []}, f, indent=2)

    # ------------------------------------------------------------------ #
    #  Preset library (global pool)                                       #
    # ------------------------------------------------------------------ #

    def list_presets(self) -> list[str]:
        """Return names of all presets in the global library."""
        result = []
        for fp in sorted(get_presets_dir().glob("*.json")):
            try:
                with open(fp) as f:
                    data = json.load(f)
                result.append(data.get("name", fp.stem))
            except Exception:
                result.append(fp.stem)
        return result

    def save_preset(self, name: str, config: AppConfig,
                    midi_mappings: Optional[list] = None) -> bool:
        """Save current state to the preset library under the given name."""
        fp = get_presets_dir() / f"{_sanitize_name(name)}.json"
        data = {
            "version": "4.0.0",
            "name": name,
            "config": self._serialize_config(config),
            "midi_mappings": midi_mappings or [],
        }
        try:
            with open(fp, 'w') as f:
                json.dump(data, f, indent=2)
            return True
        except Exception as e:
            log.error("Preset save error: %s", e)
            return False

    def load_preset(self, name: str) -> Optional[dict]:
        """Load a preset from the library by name."""
        fp = get_presets_dir() / f"{_sanitize_name(name)}.json"
        if not fp.exists():
            return None
        try:
            with open(fp) as f:
                return json.load(f)
        except Exception as e:
            log.error("Preset load error: %s", e)
            return None

    def delete_preset(self, name: str):
        """Remove a preset from the library."""
        fp = get_presets_dir() / f"{_sanitize_name(name)}.json"
        if fp.exists():
            fp.unlink()

    def rename_preset(self, old_name: str, new_name: str) -> bool:
        """Rename a preset in the library. Updates internal name field."""
        old_fp = get_presets_dir() / f"{_sanitize_name(old_name)}.json"
        new_fp = get_presets_dir() / f"{_sanitize_name(new_name)}.json"
        if not old_fp.exists() or new_fp.exists():
            return False
        try:
            with open(old_fp) as f:
                data = json.load(f)
            data["name"] = new_name
            with open(new_fp, 'w') as f:
                json.dump(data, f, indent=2)
            old_fp.unlink()
            # Update references in current setlist
            for i, p in enumerate(self.setlist_presets):
                if p == old_name:
                    self.setlist_presets[i] = new_name
            self.save_setlist()
            return True
        except Exception:
            return False

    def preset_exists(self, name: str) -> bool:
        """Check if a preset exists in the library."""
        return (get_presets_dir() / f"{_sanitize_name(name)}.json").exists()

    # ------------------------------------------------------------------ #
    #  Setlist operations                                                 #
    # ------------------------------------------------------------------ #

    def list_setlists(self) -> list[str]:
        """Return names of all setlists on disk."""
        result = []
        for fp in sorted(get_setlists_dir().glob("*.json")):
            try:
                with open(fp) as f:
                    data = json.load(f)
                result.append(data.get("name", fp.stem))
            except Exception:
                result.append(fp.stem)
        return result or ["Default"]

    def load_setlist(self, name: str) -> bool:
        """Load a setlist by name."""
        fp = get_setlists_dir() / f"{_sanitize_name(name)}.json"
        if not fp.exists():
            return False
        try:
            with open(fp) as f:
                data = json.load(f)
            self.setlist_name = data.get("name", name)
            self.setlist_presets = data.get("presets", [])
            self.active_index = -1
            return True
        except Exception as e:
            print(f"Setlist load error: {e}")
            return False

    def save_setlist(self):
        """Persist the current setlist."""
        fp = get_setlists_dir() / f"{_sanitize_name(self.setlist_name)}.json"
        data = {"name": self.setlist_name, "presets": self.setlist_presets}
        try:
            with open(fp, 'w') as f:
                json.dump(data, f, indent=2)
        except Exception as e:
            print(f"Setlist save error: {e}")

    def create_setlist(self, name: str):
        """Create a new empty setlist."""
        fp = get_setlists_dir() / f"{_sanitize_name(name)}.json"
        data = {"name": name, "presets": []}
        with open(fp, 'w') as f:
            json.dump(data, f, indent=2)

    def rename_setlist(self, old_name: str, new_name: str):
        """Rename a setlist."""
        old_fp = get_setlists_dir() / f"{_sanitize_name(old_name)}.json"
        new_fp = get_setlists_dir() / f"{_sanitize_name(new_name)}.json"
        if old_fp.exists() and not new_fp.exists():
            try:
                with open(old_fp) as f:
                    data = json.load(f)
                data["name"] = new_name
                with open(new_fp, 'w') as f:
                    json.dump(data, f, indent=2)
                old_fp.unlink()
                if self.setlist_name == old_name:
                    self.setlist_name = new_name
            except Exception:
                pass

    def delete_setlist(self, name: str):
        """Delete a setlist (does not delete the presets it references)."""
        fp = get_setlists_dir() / f"{_sanitize_name(name)}.json"
        if fp.exists():
            fp.unlink()
        if self.setlist_name == name:
            self._ensure_dirs()
            self.load_setlist("Default")

    # ------------------------------------------------------------------ #
    #  Setlist preset management                                          #
    # ------------------------------------------------------------------ #

    def add_to_setlist(self, preset_name: str, index: int = -1) -> int:
        """Add a preset reference to the current setlist. Returns index."""
        if len(self.setlist_presets) >= NUM_SETLIST_SLOTS:
            return -1
        if index < 0 or index > len(self.setlist_presets):
            self.setlist_presets.append(preset_name)
            idx = len(self.setlist_presets) - 1
        else:
            self.setlist_presets.insert(index, preset_name)
            idx = index
        self.save_setlist()
        return idx

    def remove_from_setlist(self, index: int):
        """Remove a preset reference from the setlist (doesn't delete preset)."""
        if 0 <= index < len(self.setlist_presets):
            self.setlist_presets.pop(index)
            if self.active_index >= len(self.setlist_presets):
                self.active_index = max(-1, len(self.setlist_presets) - 1)
            self.save_setlist()

    def reorder_setlist(self, new_order: list[int]):
        """Reorder setlist by a list of old indices."""
        old = list(self.setlist_presets)
        self.setlist_presets = [old[i] for i in new_order if 0 <= i < len(old)]
        if 0 <= self.active_index < len(new_order):
            try:
                self.active_index = new_order.index(self.active_index)
            except ValueError:
                self.active_index = -1
        self.save_setlist()

    def load_setlist_preset(self, index: int) -> Optional[dict]:
        """Load preset data from setlist by index. Sets active_index."""
        if index < 0 or index >= len(self.setlist_presets):
            return None
        name = self.setlist_presets[index]
        data = self.load_preset(name)
        if data:
            self.active_index = index
        return data

    def apply_preset(self, preset_data: dict, config: AppConfig) -> AppConfig:
        """Apply loaded preset data to the config."""
        cfg_data = preset_data.get("config", {})

        # Tempo
        try:
            config.tempo = max(20.0, min(999.0, float(cfg_data.get("tempo", config.tempo))))
        except (ValueError, TypeError):
            pass
        try:
            config.quantize = QuantizeMode(cfg_data.get("quantize", config.quantize))
        except (ValueError, KeyError):
            pass
        try:
            config.row_count = max(1, min(16, int(cfg_data.get("row_count", config.row_count))))
        except (ValueError, TypeError):
            pass
        try:
            config.master_volume = max(0.0, min(2.0, float(cfg_data.get("master_volume", config.master_volume))))
        except (ValueError, TypeError):
            pass
        try:
            config.fadetime = max(0.0, min(5000.0, float(cfg_data.get("fadetime", config.fadetime))))
        except (ValueError, TypeError):
            pass

        # Channels
        channels_data = cfg_data.get("channels", [])
        for i, ch_data in enumerate(channels_data):
            if i < len(config.channels):
                ch = config.channels[i]
                try:
                    ch.speed = max(0.125, min(8.0, float(ch_data.get("speed", ch.speed))))
                    ch.normal_speed = max(0.125, min(8.0, float(ch_data.get("normal_speed", ch_data.get("speed", ch.speed)))))
                except (ValueError, TypeError):
                    pass
                try:
                    ch.mode = PlaybackMode(ch_data.get("mode", ch.mode))
                except (ValueError, KeyError):
                    pass
                ch.slave = ch_data.get("slave", ch.slave)
                ch.filename = ch_data.get("filename", ch.filename)
                try:
                    ch.gain = max(0.0, min(2.0, float(ch_data.get("gain", ch.gain))))
                    ch.group = max(0, min(3, int(ch_data.get("group", ch.group))))
                except (ValueError, TypeError):
                    pass
                ch.reverse = ch_data.get("reverse", ch.reverse)
                try:
                    ch.steps = max(1, min(64, int(ch_data.get("steps", ch.steps))))
                    ch.transpose = max(-24, min(24, int(ch_data.get("transpose", ch.transpose))))
                    ch.octave = max(-4, min(4, int(ch_data.get("octave", ch.octave))))
                    ch.sel_start = max(0.0, min(1.0, float(ch_data.get("sel_start", ch.sel_start))))
                    ch.sel_end = max(0.0, min(1.0, float(ch_data.get("sel_end", ch.sel_end))))
                except (ValueError, TypeError):
                    pass
                ch.mute = ch_data.get("mute", ch.mute)
                try:
                    ch.attack = max(0.0, min(5000.0, float(ch_data.get("attack", ch.attack))))
                    ch.release = max(0.0, min(5000.0, float(ch_data.get("release", ch.release))))
                    ch.pitch = max(-24.0, min(24.0, float(ch_data.get("pitch", ch.pitch))))
                    ch.velocity = max(0.0, min(1.0, float(ch_data.get("velocity", ch.velocity))))
                    ch.filter_val = max(-1.0, min(1.0, float(ch_data.get("filter_val", ch.filter_val))))
                    ch.pitch_scale = max(0, min(4, int(ch_data.get("pitch_scale", ch.pitch_scale))))
                    ch.pitch_root = max(-24, min(24, int(ch_data.get("pitch_root", ch.pitch_root))))
                except (ValueError, TypeError):
                    pass
                ch.pitch_hold = bool(ch_data.get("pitch_hold", ch.pitch_hold))

        # Groups
        groups_data = cfg_data.get("groups", [])
        for i, g_data in enumerate(groups_data):
            if i < len(config.groups):
                g = config.groups[i]
                try:
                    g.volume = max(0.0, min(2.0, float(g_data.get("volume", g.volume))))
                    g.send = max(0.0, min(1.0, float(g_data.get("send", g.send))))
                    g.mute = g_data.get("mute", g.mute)
                    g.route = g_data.get("route", g.route)
                    g.fx_route = max(0, min(4, int(g_data.get("fx_route", g.fx_route))))
                except (ValueError, TypeError):
                    pass

        # Delay
        delay_data = cfg_data.get("delay", {})
        if delay_data:
            d = config.delay
            for key in ["sync_time", "ms_time", "time_mode", "fb_attack",
                        "fb_release", "fb_range_low", "fb_range_high",
                        "filter_lp", "filter_hp", "filter_freq_lp",
                        "filter_freq_hp", "gain", "enabled", "fx_route"]:
                if key in delay_data:
                    _safe_setattr(d, key, delay_data[key])

        # Tape Delay
        tape_delay_data = cfg_data.get("tape_delay", {})
        if tape_delay_data:
            td = config.tape_delay
            for key in ["sync_time", "ms_time", "time_mode", "feedback",
                        "wow_flutter", "saturation", "tone", "gain",
                        "enabled", "fx_route"]:
                if key in tape_delay_data:
                    _safe_setattr(td, key, tape_delay_data[key])

        # Filter A
        filter_a_data = cfg_data.get("filter_a", cfg_data.get("filter", {}))
        if filter_a_data:
            fa = config.filter_a
            for key in ["lp_enabled", "hp_enabled", "lp_freq", "hp_freq",
                        "lp_q", "hp_q", "enabled", "fx_route"]:
                if key in filter_a_data:
                    _safe_setattr(fa, key, filter_a_data[key])
            # Backward compat: old presets used single "resonance" for both Q
            if "resonance" in filter_a_data and "lp_q" not in filter_a_data:
                fa.lp_q = filter_a_data["resonance"]
                fa.hp_q = filter_a_data["resonance"]

        # Filter B (Moog ladder)
        filter_b_data = cfg_data.get("filter_b", {})
        if filter_b_data:
            fb = config.filter_b
            for key in ["cutoff", "resonance", "drive", "enabled", "fx_route"]:
                if key in filter_b_data:
                    _safe_setattr(fb, key, filter_b_data[key])
            # Backward compat: old presets had LP/HP fields for filter_b
            if "lp_freq" in filter_b_data and "cutoff" not in filter_b_data:
                fb.cutoff = filter_b_data["lp_freq"]

        # Modulation
        mod_data = cfg_data.get("mod", {})
        if mod_data:
            m = config.mod
            for key in ["phaser_enabled", "phaser_rate", "phaser_depth", "phaser_feedback",
                        "chorus_enabled", "chorus_rate", "chorus_depth", "chorus_mix",
                        "flanger_enabled", "flanger_rate", "flanger_depth",
                        "flanger_feedback", "flanger_mix"]:
                if key in mod_data:
                    _safe_setattr(m, key, mod_data[key])

        # Lo-fi media effects + compressor (bus A and bus B)
        _FX_KEYS = (
            ("reel_to_reel", ["enabled", "tape_speed", "saturation", "flutter",
                              "bias", "tone", "mix", "fx_route"]),
            ("cassette_tape", ["enabled", "saturation", "hiss", "punch",
                               "warp", "tone", "mix", "fx_route"]),
            ("vhs", ["enabled", "wobble", "dropout", "worn",
                     "noise", "mix", "fx_route"]),
            ("vinyl", ["enabled", "crackle", "pops", "warmth",
                       "wear", "mix", "fx_route"]),
            ("compressor", ["enabled", "threshold", "ratio", "attack",
                            "release", "makeup_gain", "sidechain_source",
                            "sc_hp_freq", "thrust_enabled", "fx_route"]),
            ("hall_reverb", ["enabled", "room_size", "decay", "diffusion",
                             "mix", "room_type", "fx_route"]),
            ("plate_reverb", ["enabled", "decay", "damping", "pre_delay",
                              "mix", "fx_route"]),
            ("bit_crusher", ["enabled", "bit_depth", "sample_rate_reduction",
                             "mix", "fx_route"]),
            ("tube_overdrive", ["enabled", "drive", "tone", "tube_bias",
                                "mix", "fx_route"]),
            ("glue_compressor", ["enabled", "threshold", "ratio_idx",
                                  "attack_idx", "release_idx", "makeup_gain",
                                  "hpf_idx", "fx_route"]),
            ("ether_drift", ["enabled", "rate", "depth", "feedback",
                             "spread", "route", "sync", "mix", "fx_route"]),
        )
        for fx_name, fx_keys in _FX_KEYS:
            for suffix in ("", "_b", "_m", "_c", "_d"):
                cfg_key = f"{fx_name}{suffix}" if suffix else fx_name
                fx_data = cfg_data.get(cfg_key, {})
                if fx_data:
                    state = getattr(config, cfg_key)
                    for key in fx_keys:
                        if key in fx_data:
                            _safe_setattr(state, key, fx_data[key])

        # Per-bus state for delay / tape_delay / filters / mod
        _EXTRA_KEYS = (
            ("delay", ["sync_time", "ms_time", "time_mode", "fb_attack",
                       "fb_release", "fb_range_low", "fb_range_high",
                       "filter_lp", "filter_hp", "filter_freq_lp",
                       "filter_freq_hp", "gain", "enabled", "fx_route"]),
            ("tape_delay", ["sync_time", "ms_time", "time_mode", "feedback",
                            "wow_flutter", "saturation", "tone", "gain",
                            "head_mode", "spring_mix",
                            "enabled", "fx_route"]),
            ("filter_a", ["lp_enabled", "hp_enabled", "lp_freq", "hp_freq",
                          "lp_q", "hp_q", "enabled", "fx_route"]),
            ("filter_b", ["cutoff", "resonance", "drive", "enabled", "fx_route"]),
            ("mod", ["phaser_enabled", "phaser_rate", "phaser_depth", "phaser_feedback",
                     "chorus_enabled", "chorus_rate", "chorus_depth", "chorus_mix",
                     "flanger_enabled", "flanger_rate", "flanger_depth",
                     "flanger_feedback", "flanger_mix"]),
        )
        for base_name, base_keys in _EXTRA_KEYS:
            for suffix in ("_b", "_m", "_c", "_d"):
                cfg_name = f"{base_name}{suffix}"
                entry = cfg_data.get(cfg_name, {})
                if entry:
                    st = getattr(config, cfg_name)
                    for key in base_keys:
                        if key in entry:
                            _safe_setattr(st, key, entry[key])

        # Patterns
        patterns_data = cfg_data.get("patterns", [])
        for i, p_data in enumerate(patterns_data):
            if i < len(config.patterns):
                config.patterns[i].length = p_data.get("length", config.patterns[i].length)

        # Group colors
        if "group_colors" in cfg_data:
            config.group_colors = cfg_data["group_colors"]

        # OSC
        config.osc_host = cfg_data.get("osc_host", config.osc_host)
        config.osc_send_port = cfg_data.get("osc_send_port", config.osc_send_port)
        config.osc_recv_port = cfg_data.get("osc_recv_port", config.osc_recv_port)

        # Aux bus
        config.aux_volume = max(0.0, min(2.0, float(cfg_data.get("aux_volume", config.aux_volume))))
        config.aux_mute = cfg_data.get("aux_mute", config.aux_mute)
        config.aux_fx_route = max(0, min(4, int(cfg_data.get("aux_fx_route", config.aux_fx_route))))
        # Support both new float key and legacy bool key from older presets
        if "aux_send_level" in cfg_data:
            config.aux_send_level = max(0.0, min(1.0, float(cfg_data["aux_send_level"])))
        elif "aux_send_toggle" in cfg_data:
            config.aux_send_level = 1.0 if cfg_data["aux_send_toggle"] else 0.0
        else:
            config.aux_send_level = config.aux_send_level

        # Grid
        config.grid_columns = max(1, min(16, int(cfg_data.get("grid_columns", config.grid_columns))))

        # Per-effect routing
        # Backward compat: old presets stored delay_route at top level
        if "delay_route" in cfg_data:
            config.delay.fx_route = max(0, min(2, int(cfg_data["delay_route"])))
        config.mod_route = cfg_data.get("mod_route", config.mod_route)

        return config

    def _serialize_config(self, config: AppConfig) -> dict:
        """Serialize config to a JSON-compatible dict."""
        result = {
            "tempo": config.tempo,
            "quantize": int(config.quantize),
            "row_count": config.row_count,
            "master_volume": config.master_volume,
            "fadetime": config.fadetime,
            "channels": [
                {
                    "speed": ch.speed,
                    "normal_speed": ch.normal_speed,
                    "mode": int(ch.mode),
                    "slave": ch.slave,
                    "filename": ch.filename,
                    "gain": ch.gain,
                    "group": ch.group,
                    "reverse": ch.reverse,
                    "steps": ch.steps,
                    "transpose": ch.transpose,
                    "octave": ch.octave,
                    "sel_start": ch.sel_start,
                    "sel_end": ch.sel_end,
                    "mute": ch.mute,
                    "attack": ch.attack,
                    "release": ch.release,
                    "pitch": ch.pitch,
                    "velocity": ch.velocity,
                    "filter_val": ch.filter_val,
                    "pitch_scale": ch.pitch_scale,
                    "pitch_root": ch.pitch_root,
                    "pitch_hold": ch.pitch_hold,
                }
                for ch in config.channels
            ],
            "groups": [
                {"volume": g.volume, "send": g.send, "mute": g.mute,
                 "route": g.route, "fx_route": g.fx_route}
                for g in config.groups
            ],
            "delay": {
                "sync_time": config.delay.sync_time,
                "ms_time": config.delay.ms_time,
                "time_mode": config.delay.time_mode,
                "fb_attack": config.delay.fb_attack,
                "fb_release": config.delay.fb_release,
                "fb_range_low": config.delay.fb_range_low,
                "fb_range_high": config.delay.fb_range_high,
                "filter_lp": config.delay.filter_lp,
                "filter_hp": config.delay.filter_hp,
                "filter_freq_lp": config.delay.filter_freq_lp,
                "filter_freq_hp": config.delay.filter_freq_hp,
                "gain": config.delay.gain,
                "enabled": config.delay.enabled,
                "fx_route": config.delay.fx_route,
            },
            "tape_delay": {
                "sync_time": config.tape_delay.sync_time,
                "ms_time": config.tape_delay.ms_time,
                "time_mode": config.tape_delay.time_mode,
                "feedback": config.tape_delay.feedback,
                "wow_flutter": config.tape_delay.wow_flutter,
                "saturation": config.tape_delay.saturation,
                "tone": config.tape_delay.tone,
                "gain": config.tape_delay.gain,
                "head_mode": config.tape_delay.head_mode,
                "spring_mix": config.tape_delay.spring_mix,
                "enabled": config.tape_delay.enabled,
                "fx_route": config.tape_delay.fx_route,
            },
            "filter_a": {
                "lp_enabled": config.filter_a.lp_enabled,
                "hp_enabled": config.filter_a.hp_enabled,
                "lp_freq": config.filter_a.lp_freq,
                "hp_freq": config.filter_a.hp_freq,
                "lp_q": config.filter_a.lp_q,
                "hp_q": config.filter_a.hp_q,
                "enabled": config.filter_a.enabled,
                "fx_route": config.filter_a.fx_route,
            },
            "filter_b": {
                "cutoff": config.filter_b.cutoff,
                "resonance": config.filter_b.resonance,
                "drive": config.filter_b.drive,
                "enabled": config.filter_b.enabled,
                "fx_route": config.filter_b.fx_route,
            },
            "mod": {
                "phaser_enabled": config.mod.phaser_enabled,
                "phaser_rate": config.mod.phaser_rate,
                "phaser_depth": config.mod.phaser_depth,
                "phaser_feedback": config.mod.phaser_feedback,
                "chorus_enabled": config.mod.chorus_enabled,
                "chorus_rate": config.mod.chorus_rate,
                "chorus_depth": config.mod.chorus_depth,
                "chorus_mix": config.mod.chorus_mix,
                "flanger_enabled": config.mod.flanger_enabled,
                "flanger_rate": config.mod.flanger_rate,
                "flanger_depth": config.mod.flanger_depth,
                "flanger_feedback": config.mod.flanger_feedback,
                "flanger_mix": config.mod.flanger_mix,
            },
            "patterns": [{"length": p.length} for p in config.patterns],
            "group_colors": config.group_colors,
            "aux_volume": config.aux_volume,
            "aux_mute": config.aux_mute,
            "aux_fx_route": config.aux_fx_route,
            "aux_send_level": config.aux_send_level,
            "grid_columns": config.grid_columns,
            "osc_host": config.osc_host,
            "osc_send_port": config.osc_send_port,
            "osc_recv_port": config.osc_recv_port,
            "mod_route": config.mod_route,
            "reel_to_reel": {
                "enabled": config.reel_to_reel.enabled,
                "tape_speed": config.reel_to_reel.tape_speed,
                "saturation": config.reel_to_reel.saturation,
                "flutter": config.reel_to_reel.flutter,
                "bias": config.reel_to_reel.bias,
                "tone": config.reel_to_reel.tone,
                "mix": config.reel_to_reel.mix,
                "fx_route": config.reel_to_reel.fx_route,
            },
            "cassette_tape": {
                "enabled": config.cassette_tape.enabled,
                "saturation": config.cassette_tape.saturation,
                "hiss": config.cassette_tape.hiss,
                "punch": config.cassette_tape.punch,
                "warp": config.cassette_tape.warp,
                "tone": config.cassette_tape.tone,
                "mix": config.cassette_tape.mix,
                "fx_route": config.cassette_tape.fx_route,
            },
            "vhs": {
                "enabled": config.vhs.enabled,
                "wobble": config.vhs.wobble,
                "dropout": config.vhs.dropout,
                "worn": config.vhs.worn,
                "noise": config.vhs.noise,
                "mix": config.vhs.mix,
                "fx_route": config.vhs.fx_route,
            },
            "vinyl": {
                "enabled": config.vinyl.enabled,
                "crackle": config.vinyl.crackle,
                "pops": config.vinyl.pops,
                "warmth": config.vinyl.warmth,
                "wear": config.vinyl.wear,
                "mix": config.vinyl.mix,
                "fx_route": config.vinyl.fx_route,
            },
            "compressor": {
                "enabled": config.compressor.enabled,
                "threshold": config.compressor.threshold,
                "ratio": config.compressor.ratio,
                "attack": config.compressor.attack,
                "release": config.compressor.release,
                "makeup_gain": config.compressor.makeup_gain,
                "sidechain_source": config.compressor.sidechain_source,
                "sc_hp_freq": config.compressor.sc_hp_freq,
                "thrust_enabled": config.compressor.thrust_enabled,
                "fx_route": config.compressor.fx_route,
            },
            "hall_reverb": {
                "enabled": config.hall_reverb.enabled,
                "room_size": config.hall_reverb.room_size,
                "decay": config.hall_reverb.decay,
                "diffusion": config.hall_reverb.diffusion,
                "mix": config.hall_reverb.mix,
                "room_type": config.hall_reverb.room_type,
                "fx_route": config.hall_reverb.fx_route,
            },
            "plate_reverb": {
                "enabled": config.plate_reverb.enabled,
                "decay": config.plate_reverb.decay,
                "damping": config.plate_reverb.damping,
                "pre_delay": config.plate_reverb.pre_delay,
                "mix": config.plate_reverb.mix,
                "fx_route": config.plate_reverb.fx_route,
            },
            "bit_crusher": {
                "enabled": config.bit_crusher.enabled,
                "bit_depth": config.bit_crusher.bit_depth,
                "sample_rate_reduction": config.bit_crusher.sample_rate_reduction,
                "mix": config.bit_crusher.mix,
                "fx_route": config.bit_crusher.fx_route,
            },
            "tube_overdrive": {
                "enabled": config.tube_overdrive.enabled,
                "drive": config.tube_overdrive.drive,
                "tone": config.tube_overdrive.tone,
                "tube_bias": config.tube_overdrive.tube_bias,
                "mix": config.tube_overdrive.mix,
                "fx_route": config.tube_overdrive.fx_route,
            },
            "bit_crusher_b": {
                "enabled": config.bit_crusher_b.enabled,
                "bit_depth": config.bit_crusher_b.bit_depth,
                "sample_rate_reduction": config.bit_crusher_b.sample_rate_reduction,
                "mix": config.bit_crusher_b.mix,
                "fx_route": config.bit_crusher_b.fx_route,
            },
            "tube_overdrive_b": {
                "enabled": config.tube_overdrive_b.enabled,
                "drive": config.tube_overdrive_b.drive,
                "tone": config.tube_overdrive_b.tone,
                "tube_bias": config.tube_overdrive_b.tube_bias,
                "mix": config.tube_overdrive_b.mix,
                "fx_route": config.tube_overdrive_b.fx_route,
            },
            # Per-bus B state
            "delay_b": {
                "sync_time": config.delay_b.sync_time,
                "ms_time": config.delay_b.ms_time,
                "time_mode": config.delay_b.time_mode,
                "fb_attack": config.delay_b.fb_attack,
                "fb_release": config.delay_b.fb_release,
                "fb_range_low": config.delay_b.fb_range_low,
                "fb_range_high": config.delay_b.fb_range_high,
                "filter_lp": config.delay_b.filter_lp,
                "filter_hp": config.delay_b.filter_hp,
                "filter_freq_lp": config.delay_b.filter_freq_lp,
                "filter_freq_hp": config.delay_b.filter_freq_hp,
                "gain": config.delay_b.gain,
                "enabled": config.delay_b.enabled,
                "fx_route": config.delay_b.fx_route,
            },
            "tape_delay_b": {
                "sync_time": config.tape_delay_b.sync_time,
                "ms_time": config.tape_delay_b.ms_time,
                "time_mode": config.tape_delay_b.time_mode,
                "feedback": config.tape_delay_b.feedback,
                "wow_flutter": config.tape_delay_b.wow_flutter,
                "saturation": config.tape_delay_b.saturation,
                "tone": config.tape_delay_b.tone,
                "gain": config.tape_delay_b.gain,
                "head_mode": config.tape_delay_b.head_mode,
                "spring_mix": config.tape_delay_b.spring_mix,
                "enabled": config.tape_delay_b.enabled,
                "fx_route": config.tape_delay_b.fx_route,
            },
            "filter_a_b": {
                "lp_enabled": config.filter_a_b.lp_enabled,
                "hp_enabled": config.filter_a_b.hp_enabled,
                "lp_freq": config.filter_a_b.lp_freq,
                "hp_freq": config.filter_a_b.hp_freq,
                "lp_q": config.filter_a_b.lp_q,
                "hp_q": config.filter_a_b.hp_q,
                "enabled": config.filter_a_b.enabled,
                "fx_route": config.filter_a_b.fx_route,
            },
            "filter_b_b": {
                "cutoff": config.filter_b_b.cutoff,
                "resonance": config.filter_b_b.resonance,
                "drive": config.filter_b_b.drive,
                "enabled": config.filter_b_b.enabled,
                "fx_route": config.filter_b_b.fx_route,
            },
            "mod_b": {
                "phaser_enabled": config.mod_b.phaser_enabled,
                "phaser_rate": config.mod_b.phaser_rate,
                "phaser_depth": config.mod_b.phaser_depth,
                "phaser_feedback": config.mod_b.phaser_feedback,
                "chorus_enabled": config.mod_b.chorus_enabled,
                "chorus_rate": config.mod_b.chorus_rate,
                "chorus_depth": config.mod_b.chorus_depth,
                "chorus_mix": config.mod_b.chorus_mix,
                "flanger_enabled": config.mod_b.flanger_enabled,
                "flanger_rate": config.mod_b.flanger_rate,
                "flanger_depth": config.mod_b.flanger_depth,
                "flanger_feedback": config.mod_b.flanger_feedback,
                "flanger_mix": config.mod_b.flanger_mix,
            },
            "reel_to_reel_b": {
                "enabled": config.reel_to_reel_b.enabled,
                "tape_speed": config.reel_to_reel_b.tape_speed,
                "saturation": config.reel_to_reel_b.saturation,
                "flutter": config.reel_to_reel_b.flutter,
                "bias": config.reel_to_reel_b.bias,
                "tone": config.reel_to_reel_b.tone,
                "mix": config.reel_to_reel_b.mix,
                "fx_route": config.reel_to_reel_b.fx_route,
            },
            "cassette_tape_b": {
                "enabled": config.cassette_tape_b.enabled,
                "saturation": config.cassette_tape_b.saturation,
                "hiss": config.cassette_tape_b.hiss,
                "punch": config.cassette_tape_b.punch,
                "warp": config.cassette_tape_b.warp,
                "tone": config.cassette_tape_b.tone,
                "mix": config.cassette_tape_b.mix,
                "fx_route": config.cassette_tape_b.fx_route,
            },
            "vhs_b": {
                "enabled": config.vhs_b.enabled,
                "wobble": config.vhs_b.wobble,
                "dropout": config.vhs_b.dropout,
                "worn": config.vhs_b.worn,
                "noise": config.vhs_b.noise,
                "mix": config.vhs_b.mix,
                "fx_route": config.vhs_b.fx_route,
            },
            "vinyl_b": {
                "enabled": config.vinyl_b.enabled,
                "crackle": config.vinyl_b.crackle,
                "pops": config.vinyl_b.pops,
                "warmth": config.vinyl_b.warmth,
                "wear": config.vinyl_b.wear,
                "mix": config.vinyl_b.mix,
                "fx_route": config.vinyl_b.fx_route,
            },
            "compressor_b": {
                "enabled": config.compressor_b.enabled,
                "threshold": config.compressor_b.threshold,
                "ratio": config.compressor_b.ratio,
                "attack": config.compressor_b.attack,
                "release": config.compressor_b.release,
                "makeup_gain": config.compressor_b.makeup_gain,
                "sidechain_source": config.compressor_b.sidechain_source,
                "sc_hp_freq": config.compressor_b.sc_hp_freq,
                "thrust_enabled": config.compressor_b.thrust_enabled,
                "fx_route": config.compressor_b.fx_route,
            },
            "hall_reverb_b": {
                "enabled": config.hall_reverb_b.enabled,
                "room_size": config.hall_reverb_b.room_size,
                "decay": config.hall_reverb_b.decay,
                "diffusion": config.hall_reverb_b.diffusion,
                "mix": config.hall_reverb_b.mix,
                "room_type": config.hall_reverb_b.room_type,
                "fx_route": config.hall_reverb_b.fx_route,
            },
            "plate_reverb_b": {
                "enabled": config.plate_reverb_b.enabled,
                "decay": config.plate_reverb_b.decay,
                "damping": config.plate_reverb_b.damping,
                "pre_delay": config.plate_reverb_b.pre_delay,
                "mix": config.plate_reverb_b.mix,
                "fx_route": config.plate_reverb_b.fx_route,
            },
            # Per-bus M state
            "delay_m": {
                "sync_time": config.delay_m.sync_time,
                "ms_time": config.delay_m.ms_time,
                "time_mode": config.delay_m.time_mode,
                "fb_attack": config.delay_m.fb_attack,
                "fb_release": config.delay_m.fb_release,
                "fb_range_low": config.delay_m.fb_range_low,
                "fb_range_high": config.delay_m.fb_range_high,
                "filter_lp": config.delay_m.filter_lp,
                "filter_hp": config.delay_m.filter_hp,
                "filter_freq_lp": config.delay_m.filter_freq_lp,
                "filter_freq_hp": config.delay_m.filter_freq_hp,
                "gain": config.delay_m.gain,
                "enabled": config.delay_m.enabled,
                "fx_route": config.delay_m.fx_route,
            },
            "tape_delay_m": {
                "sync_time": config.tape_delay_m.sync_time,
                "ms_time": config.tape_delay_m.ms_time,
                "time_mode": config.tape_delay_m.time_mode,
                "feedback": config.tape_delay_m.feedback,
                "wow_flutter": config.tape_delay_m.wow_flutter,
                "saturation": config.tape_delay_m.saturation,
                "tone": config.tape_delay_m.tone,
                "gain": config.tape_delay_m.gain,
                "head_mode": config.tape_delay_m.head_mode,
                "spring_mix": config.tape_delay_m.spring_mix,
                "enabled": config.tape_delay_m.enabled,
                "fx_route": config.tape_delay_m.fx_route,
            },
            "filter_a_m": {
                "lp_enabled": config.filter_a_m.lp_enabled,
                "hp_enabled": config.filter_a_m.hp_enabled,
                "lp_freq": config.filter_a_m.lp_freq,
                "hp_freq": config.filter_a_m.hp_freq,
                "lp_q": config.filter_a_m.lp_q,
                "hp_q": config.filter_a_m.hp_q,
                "enabled": config.filter_a_m.enabled,
                "fx_route": config.filter_a_m.fx_route,
            },
            "filter_b_m": {
                "cutoff": config.filter_b_m.cutoff,
                "resonance": config.filter_b_m.resonance,
                "drive": config.filter_b_m.drive,
                "enabled": config.filter_b_m.enabled,
                "fx_route": config.filter_b_m.fx_route,
            },
            "mod_m": {
                "phaser_enabled": config.mod_m.phaser_enabled,
                "phaser_rate": config.mod_m.phaser_rate,
                "phaser_depth": config.mod_m.phaser_depth,
                "phaser_feedback": config.mod_m.phaser_feedback,
                "chorus_enabled": config.mod_m.chorus_enabled,
                "chorus_rate": config.mod_m.chorus_rate,
                "chorus_depth": config.mod_m.chorus_depth,
                "chorus_mix": config.mod_m.chorus_mix,
                "flanger_enabled": config.mod_m.flanger_enabled,
                "flanger_rate": config.mod_m.flanger_rate,
                "flanger_depth": config.mod_m.flanger_depth,
                "flanger_feedback": config.mod_m.flanger_feedback,
                "flanger_mix": config.mod_m.flanger_mix,
            },
            "reel_to_reel_m": {
                "enabled": config.reel_to_reel_m.enabled,
                "tape_speed": config.reel_to_reel_m.tape_speed,
                "saturation": config.reel_to_reel_m.saturation,
                "flutter": config.reel_to_reel_m.flutter,
                "bias": config.reel_to_reel_m.bias,
                "tone": config.reel_to_reel_m.tone,
                "mix": config.reel_to_reel_m.mix,
                "fx_route": config.reel_to_reel_m.fx_route,
            },
            "cassette_tape_m": {
                "enabled": config.cassette_tape_m.enabled,
                "saturation": config.cassette_tape_m.saturation,
                "hiss": config.cassette_tape_m.hiss,
                "punch": config.cassette_tape_m.punch,
                "warp": config.cassette_tape_m.warp,
                "tone": config.cassette_tape_m.tone,
                "mix": config.cassette_tape_m.mix,
                "fx_route": config.cassette_tape_m.fx_route,
            },
            "vhs_m": {
                "enabled": config.vhs_m.enabled,
                "wobble": config.vhs_m.wobble,
                "dropout": config.vhs_m.dropout,
                "worn": config.vhs_m.worn,
                "noise": config.vhs_m.noise,
                "mix": config.vhs_m.mix,
                "fx_route": config.vhs_m.fx_route,
            },
            "vinyl_m": {
                "enabled": config.vinyl_m.enabled,
                "crackle": config.vinyl_m.crackle,
                "pops": config.vinyl_m.pops,
                "warmth": config.vinyl_m.warmth,
                "wear": config.vinyl_m.wear,
                "mix": config.vinyl_m.mix,
                "fx_route": config.vinyl_m.fx_route,
            },
            "compressor_m": {
                "enabled": config.compressor_m.enabled,
                "threshold": config.compressor_m.threshold,
                "ratio": config.compressor_m.ratio,
                "attack": config.compressor_m.attack,
                "release": config.compressor_m.release,
                "makeup_gain": config.compressor_m.makeup_gain,
                "sidechain_source": config.compressor_m.sidechain_source,
                "sc_hp_freq": config.compressor_m.sc_hp_freq,
                "thrust_enabled": config.compressor_m.thrust_enabled,
                "fx_route": config.compressor_m.fx_route,
            },
            "glue_compressor": {
                "enabled": config.glue_compressor.enabled,
                "threshold": config.glue_compressor.threshold,
                "ratio_idx": config.glue_compressor.ratio_idx,
                "attack_idx": config.glue_compressor.attack_idx,
                "release_idx": config.glue_compressor.release_idx,
                "makeup_gain": config.glue_compressor.makeup_gain,
                "hpf_idx": config.glue_compressor.hpf_idx,
                "fx_route": config.glue_compressor.fx_route,
            },
            "glue_compressor_b": {
                "enabled": config.glue_compressor_b.enabled,
                "threshold": config.glue_compressor_b.threshold,
                "ratio_idx": config.glue_compressor_b.ratio_idx,
                "attack_idx": config.glue_compressor_b.attack_idx,
                "release_idx": config.glue_compressor_b.release_idx,
                "makeup_gain": config.glue_compressor_b.makeup_gain,
                "hpf_idx": config.glue_compressor_b.hpf_idx,
                "fx_route": config.glue_compressor_b.fx_route,
            },
            "glue_compressor_m": {
                "enabled": config.glue_compressor_m.enabled,
                "threshold": config.glue_compressor_m.threshold,
                "ratio_idx": config.glue_compressor_m.ratio_idx,
                "attack_idx": config.glue_compressor_m.attack_idx,
                "release_idx": config.glue_compressor_m.release_idx,
                "makeup_gain": config.glue_compressor_m.makeup_gain,
                "hpf_idx": config.glue_compressor_m.hpf_idx,
                "fx_route": config.glue_compressor_m.fx_route,
            },
            "hall_reverb_m": {
                "enabled": config.hall_reverb_m.enabled,
                "room_size": config.hall_reverb_m.room_size,
                "decay": config.hall_reverb_m.decay,
                "diffusion": config.hall_reverb_m.diffusion,
                "mix": config.hall_reverb_m.mix,
                "room_type": config.hall_reverb_m.room_type,
                "fx_route": config.hall_reverb_m.fx_route,
            },
            "plate_reverb_m": {
                "enabled": config.plate_reverb_m.enabled,
                "decay": config.plate_reverb_m.decay,
                "damping": config.plate_reverb_m.damping,
                "pre_delay": config.plate_reverb_m.pre_delay,
                "mix": config.plate_reverb_m.mix,
                "fx_route": config.plate_reverb_m.fx_route,
            },
            "bit_crusher_m": {
                "enabled": config.bit_crusher_m.enabled,
                "bit_depth": config.bit_crusher_m.bit_depth,
                "sample_rate_reduction": config.bit_crusher_m.sample_rate_reduction,
                "mix": config.bit_crusher_m.mix,
                "fx_route": config.bit_crusher_m.fx_route,
            },
            "tube_overdrive_m": {
                "enabled": config.tube_overdrive_m.enabled,
                "drive": config.tube_overdrive_m.drive,
                "tone": config.tube_overdrive_m.tone,
                "tube_bias": config.tube_overdrive_m.tube_bias,
                "mix": config.tube_overdrive_m.mix,
                "fx_route": config.tube_overdrive_m.fx_route,
            },
            "ether_drift": {
                "enabled": config.ether_drift.enabled,
                "rate": config.ether_drift.rate,
                "depth": config.ether_drift.depth,
                "feedback": config.ether_drift.feedback,
                "spread": config.ether_drift.spread,
                "route": config.ether_drift.route,
                "sync": config.ether_drift.sync,
                "mix": config.ether_drift.mix,
                "fx_route": config.ether_drift.fx_route,
            },
            "ether_drift_b": {
                "enabled": config.ether_drift_b.enabled,
                "rate": config.ether_drift_b.rate,
                "depth": config.ether_drift_b.depth,
                "feedback": config.ether_drift_b.feedback,
                "spread": config.ether_drift_b.spread,
                "route": config.ether_drift_b.route,
                "sync": config.ether_drift_b.sync,
                "mix": config.ether_drift_b.mix,
                "fx_route": config.ether_drift_b.fx_route,
            },
            "ether_drift_m": {
                "enabled": config.ether_drift_m.enabled,
                "rate": config.ether_drift_m.rate,
                "depth": config.ether_drift_m.depth,
                "feedback": config.ether_drift_m.feedback,
                "spread": config.ether_drift_m.spread,
                "route": config.ether_drift_m.route,
                "sync": config.ether_drift_m.sync,
                "mix": config.ether_drift_m.mix,
                "fx_route": config.ether_drift_m.fx_route,
            },
        }

        # Per-bus C and D state — auto-serialize all dataclass fields
        _ALL_FX = (
            "delay", "tape_delay", "filter_a", "filter_b", "mod",
            "reel_to_reel", "cassette_tape", "vhs", "vinyl",
            "compressor", "hall_reverb", "plate_reverb",
            "glue_compressor", "bit_crusher", "tube_overdrive",
            "ether_drift",
        )
        for suffix in ("_c", "_d"):
            for fx_base in _ALL_FX:
                cfg_key = f"{fx_base}{suffix}"
                state = getattr(config, cfg_key)
                result[cfg_key] = {
                    k: v for k, v in vars(state).items()
                    if not k.startswith('_')
                }

        return result
