"""Destructive Slices-folder actions must stay inside the Slices folder."""
from mlrvp.gui.views.tools_view import _is_slices_subfolder


def test_guard(tmp_config_dir):
    from mlrvp.config import get_slices_dir
    root = get_slices_dir()
    (root / "Live").mkdir()
    sibling = root.parent / (root.name + "_backup")
    sibling.mkdir()

    assert _is_slices_subfolder(str(root / "Live"))
    assert not _is_slices_subfolder(str(root))                 # the folder itself
    assert not _is_slices_subfolder(str(sibling))              # look-alike sibling
    assert not _is_slices_subfolder(str(root / ".." / "x"))    # escapes via ..
