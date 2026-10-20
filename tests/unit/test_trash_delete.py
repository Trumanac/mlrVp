"""Library deletes go to the Recycle Bin / Trash, never straight to oblivion."""
import os

import mlrvp.gui.views.tools_view as tv


class _FakeQFile:
    trashed = []
    fail = set()

    @classmethod
    def moveToTrash(cls, path):
        if os.path.basename(path) in cls.fail:
            return False
        cls.trashed.append(os.path.basename(path))
        os.remove(path)
        return True


def test_trash_files_moves_files_and_sidecars(tmp_path, monkeypatch):
    _FakeQFile.trashed, _FakeQFile.fail = [], {"locked.wav"}
    monkeypatch.setattr(tv, "QFile", _FakeQFile)
    for name in ("a.wav", "a.json", "locked.wav", "locked.json"):
        (tmp_path / name).write_text("x")

    failed = tv._trash_files([str(tmp_path / "a.wav"), str(tmp_path / "locked.wav")],
                             with_sidecars=True)

    assert failed == [str(tmp_path / "locked.wav")]
    assert sorted(_FakeQFile.trashed) == ["a.json", "a.wav"]
    assert (tmp_path / "locked.wav").exists() and (tmp_path / "locked.json").exists()


def test_trash_files_ignores_already_missing(tmp_path, monkeypatch):
    _FakeQFile.trashed, _FakeQFile.fail = [], set()
    monkeypatch.setattr(tv, "QFile", _FakeQFile)
    assert tv._trash_files([str(tmp_path / "gone.wav")]) == []
