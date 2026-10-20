"""Data-folder resolution, legacy migration, and factory-rack seeding."""
import pytest

import mlrvp.config as config
import mlrvp.datadirs as datadirs


@pytest.fixture
def end_user_mode(tmp_path, monkeypatch):
    """Simulate a frozen / pip-installed app with isolated OS folders."""
    monkeypatch.delenv(config.DATA_DIR_ENV, raising=False)
    monkeypatch.setattr(config, "_is_dev_checkout", lambda: False)
    roots = {
        "GenericDataLocation": tmp_path / "AppData",
        "MusicLocation": tmp_path / "Music",
    }
    monkeypatch.setattr(config, "_standard_location", lambda kind, fallback: roots[kind])
    legacy = tmp_path / "app" / "data"
    monkeypatch.setattr(config, "legacy_data_dir", lambda: legacy)
    return tmp_path, legacy


def test_end_user_paths(end_user_mode):
    tmp, _ = end_user_mode
    assert config.get_config_dir() == tmp / "AppData" / "mlrVp"
    assert config.get_presets_dir() == tmp / "AppData" / "mlrVp" / "presets"
    assert config.get_logs_dir() == tmp / "AppData" / "mlrVp" / "logs"
    assert config.get_slices_dir() == tmp / "Music" / "mlrVp" / "Slices"
    assert config.get_songs_dir() == tmp / "Music" / "mlrVp" / "Songs"


def test_env_override_keeps_everything_local(tmp_path, monkeypatch):
    monkeypatch.setenv(config.DATA_DIR_ENV, str(tmp_path / "custom"))
    monkeypatch.setattr(config, "_is_dev_checkout", lambda: False)
    assert config.get_config_dir() == tmp_path / "custom"
    assert config.get_slices_dir() == tmp_path / "custom" / "slices"
    assert config.get_songs_dir() == tmp_path / "custom" / "songs"


def test_source_checkout_uses_repo_data_dir():
    # The test suite itself runs from a checkout.
    assert config._is_dev_checkout()


def test_migration_copies_legacy_data_without_overwriting(end_user_mode):
    tmp, legacy = end_user_mode
    (legacy / "presets").mkdir(parents=True)
    (legacy / "presets" / "Live Set.json").write_text('{"old": 1}')
    (legacy / "presets" / "Kept.json").write_text('{"old": 1}')
    (legacy / "racks").mkdir()
    (legacy / "racks" / "Mine.json").write_text("{}")
    (legacy / "slices" / "Live").mkdir(parents=True)
    (legacy / "slices" / "Live" / "loop.wav").write_bytes(b"RIFF")
    (legacy / "songs").mkdir()
    (legacy / "songs" / "take.wav").write_bytes(b"RIFF")
    (legacy / "ui_scale.txt").write_text("1.25")
    (legacy / "presets" / "half.json.tmp").write_text("x")

    data = config.get_config_dir()
    (data / "presets").mkdir()
    (data / "presets" / "Kept.json").write_text('{"new": 1}')

    assert datadirs.migrate_legacy_data() == 5   # Kept.json + .tmp skipped
    assert (data / "presets" / "Live Set.json").read_text() == '{"old": 1}'
    assert (data / "presets" / "Kept.json").read_text() == '{"new": 1}'   # not overwritten
    assert not (data / "presets" / "half.json.tmp").exists()
    assert (data / "racks" / "Mine.json").exists()
    assert (data / "ui_scale.txt").read_text() == "1.25"
    assert (tmp / "Music" / "mlrVp" / "Slices" / "Live" / "loop.wav").exists()
    assert (tmp / "Music" / "mlrVp" / "Songs" / "take.wav").exists()

    # Runs once only
    (legacy / "presets" / "Later.json").write_text("{}")
    assert datadirs.migrate_legacy_data() == 0
    assert not (data / "presets" / "Later.json").exists()


def test_migration_skipped_in_local_mode(tmp_path, monkeypatch):
    monkeypatch.setenv(config.DATA_DIR_ENV, str(tmp_path / "custom"))
    assert datadirs.migrate_legacy_data() == 0


def test_factory_racks_seeded_once(end_user_mode):
    shipped = sorted(p.name for p in datadirs.factory_racks_dir().glob("*.json"))
    assert shipped, "factory racks missing from mlrvp/resources/factory_racks"

    assert datadirs.seed_factory_racks() == len(shipped)
    racks = config.get_racks_dir()
    assert sorted(p.name for p in racks.glob("*.json")) == shipped

    # A rack the user deleted stays deleted
    (racks / shipped[0]).unlink()
    assert datadirs.seed_factory_racks() == 0
    assert not (racks / shipped[0]).exists()


@pytest.mark.parametrize("layout", [
    "data",                                      # source checkout / old zip
    "_internal/data",                            # extracted Windows/Linux build
    "mlrVp.app/Contents/Frameworks/data",        # folder holding the macOS app
    "",                                          # user picked the data folder itself
])
def test_import_finds_old_install_data(end_user_mode, layout):
    tmp, _ = end_user_mode
    old = tmp / "old_install"
    data = old / layout if layout else old
    (data / "presets").mkdir(parents=True)
    (data / "presets" / "Gig.json").write_text("{}")
    (data / "setlists").mkdir()
    (data / "setlists" / "Tour.json").write_text("{}")

    found, copied = datadirs.import_data_from(old)
    assert found == data
    assert copied == 2
    assert (config.get_presets_dir() / "Gig.json").exists()
    assert (config.get_setlists_dir() / "Tour.json").exists()


def test_import_from_unrelated_folder_finds_nothing(end_user_mode, tmp_path):
    (tmp_path / "random").mkdir()
    assert datadirs.import_data_from(tmp_path / "random") == (None, 0)
