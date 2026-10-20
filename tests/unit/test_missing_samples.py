"""Preset samples that moved or vanished are followed or reported, not left 'loading'."""
from types import SimpleNamespace

from mlrvp.app import AppController
from mlrvp.config import AppConfig


class _Label:
    def __init__(self):
        self.text = self.tip = ""

    def setText(self, t):
        self.text = t

    def setToolTip(self, t):
        self.tip = t


def _ctrl(filenames):
    config = AppConfig()
    for ch, name in zip(config.channels, filenames):
        ch.filename = name
    labels = [_Label() for _ in config.channels]
    statuses = []
    window = SimpleNamespace(
        channel_view=SimpleNamespace(
            get_strip=lambda i: SimpleNamespace(_sample_name=labels[i])),
        set_status=statuses.append,
    )
    return SimpleNamespace(config=config, window=window), labels, statuses


def test_existing_sample_is_loaded_as_is(tmp_path):
    wav = tmp_path / "kick.wav"
    wav.write_bytes(b"RIFF")
    ctrl, _, _ = _ctrl([str(wav)])
    missing = []
    assert AppController._resolve_config_sample(ctrl, 0, missing) == str(wav)
    assert missing == []


def test_moved_sample_is_followed_and_path_updated(tmp_path, monkeypatch):
    import mlrvp.datadirs as datadirs
    new = tmp_path / "new.wav"
    new.write_bytes(b"RIFF")
    monkeypatch.setattr(datadirs, "relocate_sample_path", lambda p: str(new))
    ctrl, _, _ = _ctrl([str(tmp_path / "old" / "data" / "slices" / "new.wav")])
    assert AppController._resolve_config_sample(ctrl, 0, []) == str(new)
    assert ctrl.config.channels[0].filename == str(new)


def test_missing_sample_is_labelled_and_reported(tmp_path):
    gone = str(tmp_path / "gone.wav")
    ctrl, labels, statuses = _ctrl([gone, ""])
    missing = []
    assert AppController._resolve_config_sample(ctrl, 0, missing) is None
    assert AppController._resolve_config_sample(ctrl, 1, missing) is None
    assert missing == ["gone.wav"]
    assert labels[0].text == "(missing) gone.wav" and gone in labels[0].tip
    assert labels[1].text == ""          # empty row untouched
    AppController._report_missing_samples(ctrl, missing)
    assert "1 sample(s) not found: gone.wav" in statuses[-1]
