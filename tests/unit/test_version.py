"""The version has one source (mlrvp/__init__.py); everything else must agree."""
import importlib.util
import re
from pathlib import Path

import mlrvp

ROOT = Path(__file__).resolve().parents[2]


def test_version_is_plain_semver():
    assert re.fullmatch(r"\d+\.\d+\.\d+", mlrvp.__version__), mlrvp.__version__


def test_init_line_matches_what_ci_and_build_parse():
    # build.yml's tag check greps this exact form
    text = (ROOT / "mlrvp" / "__init__.py").read_text(encoding="utf-8")
    m = re.search(r'__version__ = "([^"]+)"', text)
    assert m and m.group(1) == mlrvp.__version__


def test_build_script_reads_the_same_version():
    spec = importlib.util.spec_from_file_location("mlrvp_build_script", ROOT / "build.py")
    build = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(build)
    assert build.read_version() == mlrvp.__version__


def test_pyspec_reads_the_same_version():
    spec_text = (ROOT / "mlrvp.spec").read_text(encoding="utf-8")
    assert "mlrvp/__init__.py" in spec_text and "__version__" in spec_text
    assert "VERSION = re.search(" in spec_text      # not a hardcoded copy
    assert not re.search(r"VERSION\s*=\s*['\"]\d", spec_text)


def test_changelog_has_an_entry_for_this_version():
    changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    assert re.search(rf"^## \[{re.escape(mlrvp.__version__)}\]", changelog, re.M)
    assert f"[{mlrvp.__version__}]: https://github.com/Trumanac/mlrVp/" in changelog


def test_window_title_shows_the_version(qtbot):
    from mlrvp.gui.main_window import MainWindow
    window = MainWindow()
    qtbot.addWidget(window)
    assert mlrvp.__version__ in window.windowTitle()
    window._allow_close = True
    window.close()
