"""File dialogs start in a useful folder and remember the last one."""
import os

from mlrvp.gui import file_dialogs


def test_audio_filter_lists_every_loadable_format():
    for ext in (".wav", ".aiff", ".aif", ".flac", ".ogg", ".mp3"):
        assert f"*{ext}" in file_dialogs.AUDIO_FILTER


def test_defaults_before_anything_is_remembered(tmp_config_dir):
    from mlrvp.config import get_presets_dir
    assert file_dialogs.last_dir("presets") == str(get_presets_dir())
    assert os.path.isabs(file_dialogs.last_dir("samples"))


def test_remembers_folder_of_chosen_file(tmp_config_dir, tmp_path):
    kit = tmp_path / "Drum Kits"
    kit.mkdir()
    file_dialogs.remember_dir("samples", str(kit / "kick.wav"))
    assert file_dialogs.last_dir("samples") == str(kit)
    # Other kinds are independent
    assert file_dialogs.last_dir("presets") != str(kit)


def test_forgets_folders_that_no_longer_exist(tmp_config_dir, tmp_path):
    gone = tmp_path / "unplugged drive"
    gone.mkdir()
    file_dialogs.remember_dir("library", str(gone))
    gone.rmdir()
    assert file_dialogs.last_dir("library") != str(gone)


def test_open_file_starts_in_last_dir_and_remembers(tmp_config_dir, tmp_path, monkeypatch):
    from PySide6.QtWidgets import QFileDialog
    start = tmp_path / "start"
    picked = tmp_path / "other" / "loop.wav"
    start.mkdir()
    picked.parent.mkdir()
    file_dialogs.remember_dir("samples", str(start))
    seen = {}

    def fake(parent, title, directory, flt):
        seen["dir"] = directory
        return str(picked), flt
    monkeypatch.setattr(QFileDialog, "getOpenFileName", fake)
    assert file_dialogs.open_file(None, "Load", "samples", file_dialogs.AUDIO_FILTER) == str(picked)
    assert seen["dir"] == str(start)
    assert file_dialogs.last_dir("samples") == str(picked.parent)
