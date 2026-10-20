"""Tests for mlrvp.preset.rack_manager — FX rack profile save/load/rename/delete."""
from mlrvp.preset import rack_manager as rm


class TestSaveLoadRack:
    def test_round_trips_module_list(self, tmp_config_dir):
        modules = [
            {"effect_type": "compressor", "state": {"threshold": -12.0, "ratio": 4.0}},
            {"effect_type": "delay", "state": {"gain": 0.5}},
        ]
        rm.save_rack("My Rack", modules)
        loaded = rm.load_rack("My Rack")
        assert loaded == modules

    def test_load_missing_rack_returns_empty_list(self, tmp_config_dir):
        assert rm.load_rack("nonexistent") == []

    def test_list_racks_includes_saved(self, tmp_config_dir):
        rm.save_rack("Rack A", [])
        rm.save_rack("Rack B", [])
        names = rm.list_racks()
        assert "Rack A" in names
        assert "Rack B" in names

    def test_list_racks_empty_when_dir_missing_entries(self, tmp_config_dir):
        assert rm.list_racks() == []

    def test_load_rejects_malformed_entries(self, tmp_config_dir):
        import json
        # Write a rack file with a mix of valid and invalid module entries directly.
        from mlrvp.config import get_racks_dir
        target = get_racks_dir() / "Messy.json"
        target.write_text(json.dumps({
            "version": "1.0.0",
            "name": "Messy",
            "modules": [
                {"effect_type": "compressor", "state": {"x": 1}},
                {"effect_type": "", "state": {}},          # invalid: empty type
                "not-a-dict",                                # invalid: wrong shape
                {"state": {}},                                # invalid: missing effect_type
                {"effect_type": "delay"},                    # valid: missing state -> {}
            ],
        }))
        loaded = rm.load_rack("Messy")
        assert loaded == [
            {"effect_type": "compressor", "state": {"x": 1}},
            {"effect_type": "delay", "state": {}},
        ]

    def test_load_rejects_non_list_modules(self, tmp_config_dir):
        import json
        from mlrvp.config import get_racks_dir
        target = get_racks_dir() / "Broken.json"
        target.write_text(json.dumps({"version": "1.0.0", "name": "Broken", "modules": "nope"}))
        assert rm.load_rack("Broken") == []

    def test_load_rejects_non_object_and_bad_utf8_files(self, tmp_config_dir):
        from mlrvp.config import get_racks_dir
        (get_racks_dir() / "List.json").write_text("[1, 2]")
        (get_racks_dir() / "Bytes.json").write_bytes(b'{"name": "\xff\xfe"}')
        assert rm.load_rack("List") == []
        assert rm.load_rack("Bytes") == []

    def test_load_reads_utf8_names(self, tmp_config_dir):
        from mlrvp.config import get_racks_dir
        (get_racks_dir() / "Cafe.json").write_text(
            '{"name": "Café — FX", "modules": [{"effect_type": "fuzz", "state": {}}]}',
            encoding="utf-8")
        assert rm.load_rack("Cafe") == [{"effect_type": "fuzz", "state": {}}]


class TestDeleteRack:
    def test_delete_existing_rack(self, tmp_config_dir):
        rm.save_rack("ToDelete", [])
        assert rm.delete_rack("ToDelete") is True
        assert rm.load_rack("ToDelete") == []

    def test_delete_missing_rack_returns_false(self, tmp_config_dir):
        assert rm.delete_rack("never-existed") is False


class TestRenameRack:
    def test_rename_updates_file_and_internal_name(self, tmp_config_dir):
        rm.save_rack("Old", [{"effect_type": "fuzz", "state": {}}])
        assert rm.rename_rack("Old", "New") is True
        assert rm.load_rack("New") == [{"effect_type": "fuzz", "state": {}}]
        assert rm.load_rack("Old") == []

    def test_rename_refuses_to_overwrite_existing(self, tmp_config_dir):
        rm.save_rack("A", [])
        rm.save_rack("B", [])
        assert rm.rename_rack("A", "B") is False

    def test_rename_missing_source_returns_false(self, tmp_config_dir):
        assert rm.rename_rack("nope", "also-nope") is False

    def test_rename_to_same_name_is_a_noop_failure(self, tmp_config_dir):
        rm.save_rack("Same", [])
        assert rm.rename_rack("Same", "Same") is False

    def test_case_only_rename(self, tmp_config_dir):
        from mlrvp.config import get_racks_dir
        rm.save_rack("drums", [{"effect_type": "fuzz", "state": {}}])
        assert rm.rename_rack("drums", "Drums") is True
        assert [f.name for f in get_racks_dir().glob("*.json")] == ["Drums.json"]
        assert rm.load_rack("Drums") == [{"effect_type": "fuzz", "state": {}}]
