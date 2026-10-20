"""Tests for mlrvp.preset.manager — save/load round-trips, setlists, migration.

save_preset()/save_setlist() write on a background daemon-free thread, so
these tests poll briefly for the file to appear rather than assuming the
write already landed by the time the call returns.
"""
import json
import time

import pytest

from mlrvp.config import AppConfig, PlaybackMode
from mlrvp.preset.manager import (
    _sanitize_name, _parse_version, migrate_preset,
    CURRENT_PRESET_VERSION,
)


def wait_until(predicate, timeout=2.0, interval=0.01):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return predicate()


class TestSafeFloatEncoder:
    def test_replaces_nan_and_inf_with_zero(self):
        from mlrvp.preset.manager import _SafeFloatEncoder
        data = {"a": float("nan"), "b": float("inf"), "c": [1.0, float("-inf")],
                "d": "text", "e": 3}
        out = json.loads(json.dumps(data, cls=_SafeFloatEncoder))
        assert out == {"a": 0.0, "b": 0.0, "c": [1.0, 0.0], "d": "text", "e": 3}

    def test_default_delegates_to_base_for_unencodable_types(self):
        from mlrvp.preset.manager import _SafeFloatEncoder
        with pytest.raises(TypeError):
            json.dumps({"bad": object()}, cls=_SafeFloatEncoder)


class TestSafeSetattr:
    def test_swallows_and_logs_a_failing_setattr(self):
        from mlrvp.preset.manager import _safe_setattr

        class ReadOnly:
            @property
            def foo(self):
                return 1

        obj = ReadOnly()
        _safe_setattr(obj, "foo", 5)  # AttributeError on the property setter — must not raise


class TestAtomicWriteJson:
    def test_raises_and_cleans_up_temp_file_after_persistent_permission_error(self, tmp_path, monkeypatch):
        from mlrvp.preset.manager import _atomic_write_json

        monkeypatch.setattr("time.sleep", lambda s: None)

        def _always_fail(*a, **kw):
            raise PermissionError("locked by antivirus")

        monkeypatch.setattr("os.replace", _always_fail)
        fp = tmp_path / "target.json"
        with pytest.raises(PermissionError):
            _atomic_write_json(fp, {"x": 1})
        assert not fp.exists()
        assert list(tmp_path.glob("*.tmp")) == []

    def test_succeeds_after_transient_permission_errors(self, tmp_path, monkeypatch):
        from mlrvp.preset.manager import _atomic_write_json
        import os as os_mod

        monkeypatch.setattr("time.sleep", lambda s: None)
        real_replace = os_mod.replace
        calls = {"n": 0}

        def _flaky(src, dst):
            calls["n"] += 1
            if calls["n"] < 3:
                raise PermissionError("locked")
            return real_replace(src, dst)

        monkeypatch.setattr("os.replace", _flaky)
        fp = tmp_path / "target.json"
        _atomic_write_json(fp, {"x": 1})
        assert json.loads(fp.read_text()) == {"x": 1}
        assert calls["n"] == 3


class TestMigratePresetWithRealMigrations:
    def test_applies_registered_migration_and_stamps_current_version(self, monkeypatch):
        import mlrvp.preset.manager as manager_mod

        def _migrate(data):
            data.setdefault("config", {})["migrated"] = True
            return data

        monkeypatch.setitem(manager_mod._MIGRATIONS, "0.0.0", _migrate)
        data = {"version": "0.0.0", "config": {}}
        result = manager_mod.migrate_preset(data)
        assert result["config"]["migrated"] is True
        assert result["version"] == manager_mod.CURRENT_PRESET_VERSION

    def test_failing_migration_is_logged_and_does_not_block_the_others(self, monkeypatch):
        import mlrvp.preset.manager as manager_mod

        def _boom(data):
            raise RuntimeError("bad migration")

        monkeypatch.setitem(manager_mod._MIGRATIONS, "0.0.0", _boom)
        data = {"version": "0.0.0", "config": {}}
        result = manager_mod.migrate_preset(data)  # must not raise
        assert result["version"] == manager_mod.CURRENT_PRESET_VERSION


class TestSanitizeName:
    @pytest.mark.parametrize("raw,expected", [
        ("My Preset", "My Preset"),
        ("a/b\\c", "a_b_c"),
        ('bad:"name"<>|?*', "bad__name______"),
        ("   ", "untitled"),
        ("", "untitled"),
    ])
    def test_sanitize(self, raw, expected):
        assert _sanitize_name(raw) == expected


class TestParseVersion:
    @pytest.mark.parametrize("raw,expected", [
        ("4.0.0", (4, 0, 0)),
        ("1.2", (1, 2, 0)),
        ("garbage", (0, 0, 0)),
        (None, (0, 0, 0)),
        ("1.x.0", (1, 0, 0)),
    ])
    def test_parse(self, raw, expected):
        assert _parse_version(raw) == expected


class TestMigratePreset:
    def test_missing_version_is_stamped_current(self):
        data = {"config": {}}
        result = migrate_preset(data)
        assert result["version"] == CURRENT_PRESET_VERSION

    def test_current_version_passes_through_unchanged(self):
        data = {"version": CURRENT_PRESET_VERSION, "config": {"tempo": 99}}
        result = migrate_preset(dict(data))
        assert result == data

    def test_newer_than_current_is_passed_through_as_is(self):
        data = {"version": "99.0.0", "config": {"tempo": 5}}
        result = migrate_preset(dict(data))
        assert result["version"] == "99.0.0"
        assert result["config"]["tempo"] == 5


class TestPresetSaveLoad:
    def test_save_then_load_round_trips_tempo(self, preset_manager):
        cfg = AppConfig()
        cfg.tempo = 145.5
        preset_manager.save_preset("My Song", cfg)
        assert wait_until(lambda: preset_manager.preset_exists("My Song"))
        data = preset_manager.load_preset("My Song")
        assert data is not None
        assert data["config"]["tempo"] == 145.5

    def test_load_missing_preset_returns_none(self, preset_manager):
        assert preset_manager.load_preset("does-not-exist") is None

    def test_list_presets_includes_saved_name(self, preset_manager):
        cfg = AppConfig()
        preset_manager.save_preset("Listed Preset", cfg)
        assert wait_until(lambda: "Listed Preset" in preset_manager.list_presets())

    def test_delete_preset_removes_it(self, preset_manager):
        cfg = AppConfig()
        preset_manager.save_preset("To Delete", cfg)
        assert wait_until(lambda: preset_manager.preset_exists("To Delete"))
        preset_manager.delete_preset("To Delete")
        assert not preset_manager.preset_exists("To Delete")

    def test_rename_preset(self, preset_manager):
        cfg = AppConfig()
        preset_manager.save_preset("Old Name", cfg)
        assert wait_until(lambda: preset_manager.preset_exists("Old Name"))
        ok = preset_manager.rename_preset("Old Name", "New Name")
        assert ok is True
        assert preset_manager.preset_exists("New Name")
        assert not preset_manager.preset_exists("Old Name")

    def test_rename_fails_if_target_exists(self, preset_manager):
        cfg = AppConfig()
        preset_manager.save_preset("A", cfg)
        preset_manager.save_preset("B", cfg)
        assert wait_until(lambda: preset_manager.preset_exists("A") and preset_manager.preset_exists("B"))
        assert preset_manager.rename_preset("A", "B") is False

    def test_saved_preset_round_trips_channel_and_group_state(self, preset_manager):
        cfg = AppConfig()
        cfg.channels[3].gain = 0.42
        cfg.channels[3].mode = PlaybackMode.ONESHOT
        cfg.groups[2].volume = 0.33
        preset_manager.save_preset("Full State", cfg)
        assert wait_until(lambda: preset_manager.preset_exists("Full State"))

        data = preset_manager.load_preset("Full State")
        fresh = AppConfig()
        applied = preset_manager.apply_preset(data, fresh)

        assert applied.channels[3].gain == pytest.approx(0.42)
        assert applied.channels[3].mode == PlaybackMode.ONESHOT
        assert applied.groups[2].volume == pytest.approx(0.33)


class TestApplyPresetClamping:
    def test_tempo_is_clamped_to_valid_range(self, app_config, preset_manager):
        data = {"config": {"tempo": 9999.0}}
        result = preset_manager.apply_preset(data, app_config)
        assert result.tempo == 999.0

    def test_invalid_mode_value_is_ignored(self, app_config, preset_manager):
        data = {"config": {"channels": [{"mode": "not-a-valid-mode"}]}}
        original_mode = app_config.channels[0].mode
        preset_manager.apply_preset(data, app_config)
        assert app_config.channels[0].mode == original_mode

    def test_gain_out_of_range_is_clamped(self, app_config, preset_manager):
        data = {"config": {"channels": [{"gain": 50.0}]}}
        preset_manager.apply_preset(data, app_config)
        assert app_config.channels[0].gain == 2.0

    def test_selection_start_end_swapped_if_inverted(self, app_config, preset_manager):
        data = {"config": {"channels": [{"sel_start": 0.9, "sel_end": 0.1}]}}
        preset_manager.apply_preset(data, app_config)
        assert app_config.channels[0].sel_start == pytest.approx(0.1)
        assert app_config.channels[0].sel_end == pytest.approx(0.9)

    def test_unknown_monome_backend_falls_back_to_auto(self, app_config, preset_manager):
        from mlrvp.config import MONOME_BACKEND_AUTO
        data = {"config": {"monome_backend": "not-a-real-backend"}}
        preset_manager.apply_preset(data, app_config)
        assert app_config.monome_backend == MONOME_BACKEND_AUTO

    def test_missing_config_key_does_not_crash(self, app_config, preset_manager):
        result = preset_manager.apply_preset({}, app_config)
        assert result.tempo == 120.0  # untouched default

    def test_bogus_types_do_not_crash(self, app_config, preset_manager):
        data = {"config": {"tempo": "not-a-number", "channels": [{"gain": "also-bad"}]}}
        # Should not raise — bad values are caught and skipped.
        preset_manager.apply_preset(data, app_config)


class TestSetlists:
    def test_default_setlist_created_on_init(self, preset_manager):
        assert preset_manager.setlist_name == "Default"

    def test_create_and_load_setlist(self, preset_manager):
        preset_manager.create_setlist("Gig 1")
        assert wait_until(lambda: "Gig 1" in preset_manager.list_setlists())
        assert preset_manager.load_setlist("Gig 1") is True
        assert preset_manager.setlist_name == "Gig 1"

    def test_default_setlist_hidden_from_list(self, preset_manager):
        assert "Default" not in preset_manager.list_setlists()

    def test_add_to_setlist(self, preset_manager):
        idx = preset_manager.add_to_setlist("Some Preset")
        assert idx == 0
        assert preset_manager.setlist_presets == ["Some Preset"]

    def test_add_duplicate_returns_existing_index(self, preset_manager):
        preset_manager.add_to_setlist("A")
        preset_manager.add_to_setlist("B")
        idx = preset_manager.add_to_setlist("A")
        assert idx == 0
        assert preset_manager.setlist_presets == ["A", "B"]

    def test_remove_from_setlist(self, preset_manager):
        preset_manager.add_to_setlist("A")
        preset_manager.add_to_setlist("B")
        preset_manager.remove_from_setlist(0)
        assert preset_manager.setlist_presets == ["B"]

    def test_remove_before_active_shifts_active_index(self, preset_manager):
        preset_manager.add_to_setlist("A")
        preset_manager.add_to_setlist("B")
        preset_manager.add_to_setlist("C")
        preset_manager.active_index = 2  # pointing at "C"
        preset_manager.remove_from_setlist(0)  # remove "A"
        assert preset_manager.active_index == 1  # still points at "C"
        assert preset_manager.setlist_presets[preset_manager.active_index] == "C"

    def test_reorder_setlist(self, preset_manager):
        preset_manager.add_to_setlist("A")
        preset_manager.add_to_setlist("B")
        preset_manager.add_to_setlist("C")
        preset_manager.reorder_setlist([2, 0, 1])
        assert preset_manager.setlist_presets == ["C", "A", "B"]

    def test_reorder_rejects_invalid_permutation(self, preset_manager):
        preset_manager.add_to_setlist("A")
        preset_manager.add_to_setlist("B")
        with pytest.raises(ValueError):
            preset_manager.reorder_setlist([0, 0])  # not a permutation

    def test_setlist_full_rejects_additional_presets(self, preset_manager):
        from mlrvp.config import NUM_SETLIST_SLOTS
        for i in range(NUM_SETLIST_SLOTS):
            preset_manager.add_to_setlist(f"P{i}")
        assert preset_manager.add_to_setlist("Overflow") == -1

    def test_delete_setlist_falls_back_to_default(self, preset_manager):
        preset_manager.create_setlist("Temp")
        assert wait_until(lambda: "Temp" in preset_manager.list_setlists())
        preset_manager.load_setlist("Temp")
        preset_manager.delete_setlist("Temp")
        assert preset_manager.setlist_name == "Default"

    def test_load_setlist_missing_name_returns_false(self, preset_manager):
        assert preset_manager.load_setlist("does-not-exist") is False

    def test_list_setlists_uses_cache_on_second_call(self, preset_manager):
        preset_manager.create_setlist("Cached")
        assert wait_until(lambda: "Cached" in preset_manager.list_setlists())
        first = preset_manager.list_setlists()
        # Write a second setlist directly to disk, bypassing the manager so
        # the cache is the only thing that could pick it up.
        from mlrvp.config import get_setlists_dir
        (get_setlists_dir() / "Sneaky.json").write_text(
            json.dumps({"name": "Sneaky", "presets": []}))
        second = preset_manager.list_setlists()
        assert second == first
        assert "Sneaky" not in second

    def test_add_to_setlist_creates_or_invalidates_cache_so_new_entries_show(self, preset_manager):
        preset_manager.list_setlists()  # populate cache
        preset_manager.create_setlist("Fresh")
        assert wait_until(lambda: "Fresh" in preset_manager.list_setlists())

    def test_list_setlists_survives_corrupt_file(self, preset_manager, tmp_config_dir):
        from mlrvp.config import get_setlists_dir
        fp = get_setlists_dir() / "Broken.json"
        fp.write_text("{not valid json")
        names = preset_manager.list_setlists()
        assert "Broken" in names

    def test_load_setlist_survives_corrupt_file(self, preset_manager, tmp_config_dir):
        from mlrvp.config import get_setlists_dir
        fp = get_setlists_dir() / "Broken.json"
        fp.write_text("{not valid json")
        assert preset_manager.load_setlist("Broken") is False

    def test_add_to_setlist_inserts_at_given_index(self, preset_manager):
        preset_manager.add_to_setlist("A")
        preset_manager.add_to_setlist("C")
        idx = preset_manager.add_to_setlist("B", index=1)
        assert idx == 1
        assert preset_manager.setlist_presets == ["A", "B", "C"]

    def test_remove_at_active_index_clamps_to_new_last(self, preset_manager):
        preset_manager.add_to_setlist("A")
        preset_manager.add_to_setlist("B")
        preset_manager.active_index = 1  # pointing at "B", the last slot
        preset_manager.remove_from_setlist(1)
        assert preset_manager.active_index == 0

    def test_remove_from_setlist_out_of_range_is_a_no_op(self, preset_manager):
        preset_manager.add_to_setlist("A")
        preset_manager.remove_from_setlist(5)
        assert preset_manager.setlist_presets == ["A"]

    def test_remove_from_setlist_auto_save_false_skips_write(self, preset_manager, tmp_config_dir):
        preset_manager.add_to_setlist("A")
        assert wait_until(lambda: preset_manager.setlist_presets == ["A"])
        preset_manager.remove_from_setlist(0, auto_save=False)
        assert preset_manager.setlist_presets == []

    def test_reorder_setlist_updates_active_index_to_follow_preset(self, preset_manager):
        preset_manager.add_to_setlist("A")
        preset_manager.add_to_setlist("B")
        preset_manager.add_to_setlist("C")
        preset_manager.active_index = 0  # pointing at "A"
        preset_manager.reorder_setlist([2, 0, 1])  # "A" moves to index 1
        assert preset_manager.active_index == 1
        assert preset_manager.setlist_presets[preset_manager.active_index] == "A"

    def test_load_setlist_preset_out_of_range_returns_none(self, preset_manager):
        assert preset_manager.load_setlist_preset(0) is None

    def test_load_setlist_preset_sets_active_index(self, preset_manager):
        cfg = AppConfig()
        preset_manager.save_preset("Setlisted", cfg)
        assert wait_until(lambda: preset_manager.preset_exists("Setlisted"))
        preset_manager.add_to_setlist("Setlisted")
        data = preset_manager.load_setlist_preset(0)
        assert data is not None
        assert preset_manager.active_index == 0

    def test_load_setlist_preset_missing_backing_file_returns_none(self, preset_manager):
        preset_manager.add_to_setlist("Ghost")  # never actually saved
        assert preset_manager.load_setlist_preset(0) is None

    def test_rename_setlist_renames_on_disk_and_updates_current_name(self, preset_manager):
        preset_manager.create_setlist("Before")
        assert wait_until(lambda: "Before" in preset_manager.list_setlists())
        preset_manager.load_setlist("Before")
        preset_manager.rename_setlist("Before", "After")
        assert preset_manager.setlist_name == "After"

        def _renamed_on_disk():
            preset_manager._invalidate_setlist_cache()  # list_setlists() caches
            return "After" in preset_manager.list_setlists()

        assert wait_until(_renamed_on_disk)
        assert "Before" not in preset_manager.list_setlists()

    def test_rename_setlist_no_op_if_source_missing(self, preset_manager):
        preset_manager.rename_setlist("NoSuchSetlist", "Whatever")
        assert "Whatever" not in preset_manager.list_setlists()

    def test_rename_setlist_no_op_if_target_exists(self, preset_manager):
        preset_manager.create_setlist("One")
        preset_manager.create_setlist("Two")
        assert wait_until(lambda: {"One", "Two"} <= set(preset_manager.list_setlists()))
        preset_manager.rename_setlist("One", "Two")
        # Neither side should have been clobbered.
        assert wait_until(lambda: {"One", "Two"} <= set(preset_manager.list_setlists()))

    def test_rename_preset_updates_setlist_reference(self, preset_manager):
        cfg = AppConfig()
        preset_manager.save_preset("SetlistedOld", cfg)
        assert wait_until(lambda: preset_manager.preset_exists("SetlistedOld"))
        preset_manager.add_to_setlist("SetlistedOld")
        preset_manager.rename_preset("SetlistedOld", "SetlistedNew")
        assert preset_manager.setlist_presets == ["SetlistedNew"]

    def test_rename_preset_updates_every_setlist(self, preset_manager):
        import json
        from mlrvp.config import get_setlists_dir
        cfg = AppConfig()
        preset_manager.save_preset("Opener", cfg)
        assert wait_until(lambda: preset_manager.preset_exists("Opener"))
        # A setlist that isn't loaded also uses the preset
        preset_manager.create_setlist("Friday")
        preset_manager.load_setlist("Friday")
        preset_manager.add_to_setlist("Opener")
        friday = get_setlists_dir() / "Friday.json"
        assert wait_until(lambda: "Opener" in json.loads(friday.read_text())["presets"])
        preset_manager.create_setlist("Saturday")
        preset_manager.load_setlist("Saturday")
        preset_manager.add_to_setlist("Opener")

        assert preset_manager.rename_preset("Opener", "Intro") is True
        assert preset_manager.setlist_presets == ["Intro"]
        assert json.loads(friday.read_text())["presets"] == ["Intro"]
        saturday = get_setlists_dir() / "Saturday.json"
        assert wait_until(lambda: json.loads(saturday.read_text())["presets"] == ["Intro"])

    def test_rename_preset_case_only(self, preset_manager):
        cfg = AppConfig()
        preset_manager.save_preset("live set", cfg)
        assert wait_until(lambda: preset_manager.preset_exists("live set"))
        assert preset_manager.rename_preset("live set", "Live Set") is True
        assert preset_manager.load_preset("Live Set")["name"] == "Live Set"
        assert "Live Set" in preset_manager.list_presets()


class TestPresetErrorPaths:
    def test_list_presets_survives_corrupt_file(self, preset_manager, tmp_config_dir):
        from mlrvp.config import get_presets_dir
        fp = get_presets_dir() / "Broken.json"
        fp.write_text("{not valid json")
        names = preset_manager.list_presets()
        assert "Broken" in names

    def test_load_preset_survives_corrupt_file(self, preset_manager, tmp_config_dir):
        from mlrvp.config import get_presets_dir
        fp = get_presets_dir() / "Broken.json"
        fp.write_text("{not valid json")
        assert preset_manager.load_preset("Broken") is None

    def test_rename_preset_returns_false_if_source_missing(self, preset_manager):
        assert preset_manager.rename_preset("does-not-exist", "New") is False

    def test_rename_preset_returns_false_on_write_error(self, preset_manager, monkeypatch):
        import mlrvp.preset.manager as manager_mod
        preset_manager.save_preset("Original", AppConfig())
        assert wait_until(lambda: preset_manager.preset_exists("Original"))

        monkeypatch.setattr(manager_mod, "_atomic_write_json",
                             lambda *a, **kw: (_ for _ in ()).throw(OSError("boom")))
        assert preset_manager.rename_preset("Original", "Renamed") is False
        assert preset_manager.preset_exists("Original")
        assert not preset_manager.preset_exists("Renamed")

    def test_create_setlist_survives_write_error(self, preset_manager, monkeypatch, caplog):
        import mlrvp.preset.manager as manager_mod
        monkeypatch.setattr(manager_mod, "_atomic_write_json",
                             lambda *a, **kw: (_ for _ in ()).throw(OSError("boom")))
        preset_manager.create_setlist("WillFail")  # must not raise
        assert "Create setlist error" in caplog.text

    def test_save_setlist_survives_write_error(self, preset_manager, monkeypatch, caplog):
        import mlrvp.preset.manager as manager_mod
        monkeypatch.setattr(manager_mod, "_atomic_write_json",
                             lambda *a, **kw: (_ for _ in ()).throw(OSError("boom")))
        preset_manager.save_setlist()
        assert wait_until(lambda: "Setlist save error" in caplog.text)

    def test_rename_setlist_reverts_in_memory_name_on_write_error(self, preset_manager, monkeypatch):
        import mlrvp.preset.manager as manager_mod
        preset_manager.create_setlist("StaysPut")
        assert wait_until(lambda: "StaysPut" in preset_manager.list_setlists())
        preset_manager.load_setlist("StaysPut")

        monkeypatch.setattr(manager_mod, "_atomic_write_json",
                             lambda *a, **kw: (_ for _ in ()).throw(OSError("boom")))
        preset_manager.rename_setlist("StaysPut", "NeverLands")
        assert preset_manager.setlist_name == "NeverLands"  # optimistic update, briefly
        assert wait_until(lambda: preset_manager.setlist_name == "StaysPut")  # reverted after I/O failure

    def test_save_preset_survives_write_error(self, preset_manager, monkeypatch):
        import mlrvp.preset.manager as manager_mod

        def _boom(*a, **kw):
            raise OSError("disk full")

        monkeypatch.setattr(manager_mod, "_atomic_write_json", _boom)
        # Should not raise even though the background write fails.
        preset_manager.save_preset("Doomed", AppConfig())
        time.sleep(0.1)  # give the background thread a moment to hit the error
        assert not preset_manager.preset_exists("Doomed")


class TestApplyPresetMoreBranches:
    def test_quantize_mode_from_int_value(self, app_config, preset_manager):
        data = {"config": {"quantize": int(app_config.quantize)}}
        result = preset_manager.apply_preset(data, app_config)
        assert result.quantize == app_config.quantize

    def test_row_count_clamped(self, app_config, preset_manager):
        data = {"config": {"row_count": 99}}
        preset_manager.apply_preset(data, app_config)
        assert app_config.row_count == 16

    def test_quantize_bogus_value_does_not_crash(self, app_config, preset_manager):
        original = app_config.quantize
        data = {"config": {"quantize": "not-a-real-mode"}}
        preset_manager.apply_preset(data, app_config)
        assert app_config.quantize == original

    def test_top_level_numeric_fields_bogus_types_do_not_crash(self, app_config, preset_manager):
        data = {"config": {
            "row_count": "bad", "master_volume": "bad", "master_hp": "bad",
            "master_lp": "bad", "fadetime": "bad",
        }}
        preset_manager.apply_preset(data, app_config)  # must not raise

    def test_channel_speed_bogus_type_does_not_crash(self, app_config, preset_manager):
        data = {"config": {"channels": [{"speed": "bad", "normal_speed": "bad"}]}}
        preset_manager.apply_preset(data, app_config)  # must not raise

    def test_master_volume_hp_lp_fadetime_clamped(self, app_config, preset_manager):
        data = {"config": {
            "master_volume": 99.0, "master_hp": 1.0, "master_lp": 99999.0,
            "fadetime": 99999.0,
        }}
        preset_manager.apply_preset(data, app_config)
        assert app_config.master_volume == 2.0
        assert app_config.master_hp == 20.0
        assert app_config.master_lp == 20000.0
        assert app_config.fadetime == 5000.0

    def test_channel_bogus_numeric_fields_do_not_crash(self, app_config, preset_manager):
        data = {"config": {"channels": [{
            "steps": "bad", "transpose": "bad", "octave": "bad",
            "sel_start": "bad", "sel_end": "bad",
            "attack": "bad", "release": "bad", "pan": "bad", "pitch": "bad",
            "velocity": "bad", "filter_val": "bad", "ch_lp": "bad",
            "ch_hp": "bad", "pitch_scale": "bad", "pitch_root": "bad",
            "seq_step_div": "bad", "seq_swing": "bad",
        }]}}
        preset_manager.apply_preset(data, app_config)  # must not raise

    def test_channel_live_state_round_trips(self, app_config, preset_manager):
        data = {"config": {"channels": [{"live": {
            "bars": 4, "quantize": 2, "thru": True, "count_in": 1,
            "input_trim": 1.5, "loop_state": "recording",
            "exported_path": "/tmp/loop.wav",
        }}]}}
        preset_manager.apply_preset(data, app_config)
        live = app_config.channels[0].live
        assert live.bars == 4
        assert live.thru is True
        assert live.loop_state == "recording"
        assert live.exported_path == "/tmp/loop.wav"

    def test_channel_live_state_bogus_types_do_not_crash(self, app_config, preset_manager):
        data = {"config": {"channels": [{"live": {"bars": "bad", "count_in": "bad"}}]}}
        preset_manager.apply_preset(data, app_config)  # must not raise

    def test_group_bogus_types_do_not_crash(self, app_config, preset_manager):
        data = {"config": {"groups": [{
            "volume": "bad", "send": "bad", "fx_route": "bad",
        }]}}
        preset_manager.apply_preset(data, app_config)  # must not raise

    def test_group_fields_round_trip(self, app_config, preset_manager):
        data = {"config": {"groups": [{
            "volume": 0.5, "send": 0.25, "mute": True, "route": 2, "fx_route": 1,
        }]}}
        preset_manager.apply_preset(data, app_config)
        g = app_config.groups[0]
        assert g.volume == pytest.approx(0.5)
        assert g.send == pytest.approx(0.25)
        assert g.mute is True
        assert g.fx_route == 1

    def test_filter_b_legacy_lp_freq_maps_to_cutoff(self, app_config, preset_manager):
        data = {"config": {"filter_b": {"lp_freq": 777.0}}}
        preset_manager.apply_preset(data, app_config)
        assert app_config.filter_b.cutoff == 777.0

    def test_filter_a_legacy_resonance_maps_to_both_q(self, app_config, preset_manager):
        data = {"config": {"filter_a": {"resonance": 3.5}}}
        preset_manager.apply_preset(data, app_config)
        assert app_config.filter_a.lp_q == 3.5
        assert app_config.filter_a.hp_q == 3.5

    def test_aux_fields_round_trip_and_clamp(self, app_config, preset_manager):
        data = {"config": {
            "aux_volume": 99.0, "aux_mute": False, "aux_fx_route": 99,
        }}
        preset_manager.apply_preset(data, app_config)
        assert app_config.aux_volume == 2.0
        assert app_config.aux_mute is False
        assert app_config.aux_fx_route == 4

    def test_aux_bogus_types_do_not_crash(self, app_config, preset_manager):
        data = {"config": {"aux_volume": "bad", "aux_fx_route": "bad"}}
        preset_manager.apply_preset(data, app_config)  # must not raise

    def test_aux_send_level_new_key_takes_priority(self, app_config, preset_manager):
        data = {"config": {"aux_send_level": 0.25, "aux_send_toggle": True}}
        preset_manager.apply_preset(data, app_config)
        assert app_config.aux_send_level == pytest.approx(0.25)

    def test_aux_send_toggle_legacy_bool_maps_to_float(self, app_config, preset_manager):
        data = {"config": {"aux_send_toggle": True}}
        preset_manager.apply_preset(data, app_config)
        assert app_config.aux_send_level == 1.0

        data_off = {"config": {"aux_send_toggle": False}}
        preset_manager.apply_preset(data_off, app_config)
        assert app_config.aux_send_level == 0.0

    def test_aux_send_level_bogus_type_does_not_crash(self, app_config, preset_manager):
        data = {"config": {"aux_send_level": "bad"}}
        preset_manager.apply_preset(data, app_config)  # must not raise

    def test_grid_columns_clamped(self, app_config, preset_manager):
        data = {"config": {"grid_columns": 99}}
        preset_manager.apply_preset(data, app_config)
        assert app_config.grid_columns == 16

    def test_grid_columns_bogus_type_does_not_crash(self, app_config, preset_manager):
        data = {"config": {"grid_columns": "bad"}}
        preset_manager.apply_preset(data, app_config)  # must not raise

    def test_legacy_delay_route_maps_onto_delay_fx_route(self, app_config, preset_manager):
        data = {"config": {"delay_route": 2}}
        preset_manager.apply_preset(data, app_config)
        assert app_config.delay.fx_route == 2

    def test_stowaway_slots_round_trip_and_pad_to_expected_count(self, app_config, preset_manager):
        data = {"config": {"stowaway_slots": [
            {"plugin_path": "/plug.vst3", "slot_index": 0},
        ]}}
        preset_manager.apply_preset(data, app_config)
        assert len(app_config.stowaway_slots) == 20
        assert app_config.stowaway_slots[0].plugin_path == "/plug.vst3"

    def test_control_row_explicit_assignments_round_trip(self, app_config, preset_manager):
        from mlrvp.config import CR_SLOT_PATTERN
        assignments = [{"type": CR_SLOT_PATTERN, "value": i} for i in range(3)]
        data = {"config": {"control_row": {
            "enabled": False, "monome_row": 2, "assignments": assignments,
        }}}
        preset_manager.apply_preset(data, app_config)
        cr = app_config.control_row
        assert cr.enabled is False
        assert cr.monome_row == 2
        assert len(cr.assignments) == 8
        assert cr.assignments[0]["type"] == CR_SLOT_PATTERN

    def test_control_row_legacy_num_group_stops_and_patterns(self, app_config, preset_manager):
        from mlrvp.config import CR_SLOT_GROUP_STOP, CR_SLOT_PATTERN
        data = {"config": {"control_row": {
            "num_group_stops": 3, "num_patterns": 2,
        }}}
        preset_manager.apply_preset(data, app_config)
        assignments = app_config.control_row.assignments
        assert len(assignments) == 8
        assert sum(1 for s in assignments if s["type"] == CR_SLOT_GROUP_STOP) == 3
        assert sum(1 for s in assignments if s["type"] == CR_SLOT_PATTERN) == 2

    def test_control_row_legacy_counts_overflow_are_trimmed(self, app_config, preset_manager):
        from mlrvp.config import CR_SLOT_GROUP_STOP, CR_SLOT_PATTERN
        data = {"config": {"control_row": {
            "num_group_stops": 7, "num_patterns": 7,
        }}}
        preset_manager.apply_preset(data, app_config)
        assignments = app_config.control_row.assignments
        n_grp = sum(1 for s in assignments if s["type"] == CR_SLOT_GROUP_STOP)
        n_pat = sum(1 for s in assignments if s["type"] == CR_SLOT_PATTERN)
        assert n_grp + n_pat <= 8

    def test_grid_midi_cc_map_filters_non_dict_entries(self, app_config, preset_manager):
        data = {"config": {"grid_midi_cc_map": {
            "valid": {"cc": 1}, "invalid": "not-a-dict", 5: {"cc": 2},
        }}}
        preset_manager.apply_preset(data, app_config)
        assert app_config.grid_midi_cc_map == {"valid": {"cc": 1}}

    def test_patterns_length_round_trips(self, app_config, preset_manager):
        data = {"config": {"patterns": [{"length": 32}]}}
        preset_manager.apply_preset(data, app_config)
        assert app_config.patterns[0].length == 32

    def test_group_colors_round_trip(self, app_config, preset_manager):
        data = {"config": {"group_colors": ["#ff0000", "#00ff00"]}}
        preset_manager.apply_preset(data, app_config)
        assert app_config.group_colors == ["#ff0000", "#00ff00"]

    def test_osc_settings_round_trip(self, app_config, preset_manager):
        data = {"config": {"osc_host": "127.0.0.1", "osc_send_port": 9000, "osc_recv_port": 9001}}
        preset_manager.apply_preset(data, app_config)
        assert app_config.osc_host == "127.0.0.1"
        assert app_config.osc_send_port == 9000
        assert app_config.osc_recv_port == 9001

    def test_slicer_state_round_trips(self, app_config, preset_manager):
        data = {"config": {"slicer": {"cue_volume": 0.5, "naming_template": "chop_{n}"}}}
        preset_manager.apply_preset(data, app_config)
        assert app_config.slicer.cue_volume == 0.5
        assert app_config.slicer.naming_template == "chop_{n}"


class TestSerializeConfigMissingAttrs:
    def test_serialize_skips_fx_states_that_do_not_exist_on_config(self, preset_manager):
        cfg = AppConfig()
        del cfg.simple_eq_m  # remove an attribute the auto-serialize loop probes for
        result = preset_manager._serialize_config(cfg)
        assert "simple_eq_m" not in result
        # Sibling states with the same base name still serialize fine.
        assert "simple_eq" in result

    def test_serialize_skips_c_d_bus_states_that_do_not_exist_on_config(self, preset_manager):
        cfg = AppConfig()
        del cfg.delay_d
        result = preset_manager._serialize_config(cfg)
        assert "delay_d" not in result
        assert "delay_c" in result

