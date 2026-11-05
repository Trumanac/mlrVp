# Contributing to mlrVp

mlrVp is developed and maintained by one person, so **pull requests are not accepted**.
Bug reports, hardware reports and ideas are very welcome, though — they're the best way
to help.

## How to help

- **Something broke** — open an issue with the Bug Report template. Attach the log:
  **Help → Open Log Folder** in the app, then `mlrvp.log` (and `crash.log` if it exists).
- **Works / doesn't work with your gear** — open an issue with the Hardware Compatibility
  template (grid controller, audio interface, driver).
- **Feature ideas** — open an issue with the Feature Request template.
- **Questions and general chat** — use
  [Discussions](https://github.com/Trumanac/mlrVp/discussions).

Because there's one maintainer, replies and fixes happen when time allows, and not every
suggestion will be adopted.

You're free to clone or fork the project and change it for your own use under the terms
of the [LICENSE](LICENSE) (note the Commons Clause: no selling).

## Running from source

```bash
# 1. Clone the repo
git clone https://github.com/Trumanac/mlrVp.git
cd mlrVp

# 2. Create a virtual environment
python -m venv .venv

# 3. Activate it
#    Windows (PowerShell):
.venv\Scripts\Activate.ps1
#    macOS / Linux:
source .venv/bin/activate

# 4. Install all dependencies
pip install -r requirements.txt

# 5. Run the app
python -m mlrvp
```

A source checkout keeps its presets, settings and logs in the repo's `data/` folder (see
"Where your files are stored" in the README).

## Maintainer notes

### Dependencies

- `requirements.txt` (and `pyproject.toml`) list the supported version ranges; CI tests the
  latest versions that satisfy them.
- `requirements-lock.txt` pins the exact versions used for release builds, and CI tests it
  too. After changing `requirements.txt`, regenerate it with
  `uv pip compile requirements.txt --universal --python-version 3.12 -o requirements-lock.txt`.

### Coding conventions

- **Python 3.10+** — use type hints where they aid clarity
- **PySide6** for all GUI code — no tkinter or other toolkits
- Follow the existing module structure: audio logic in `mlrvp/audio/`, GUI in `mlrvp/gui/`
- Keep the audio callback path (`engine.py`) free of any Python-level blocking — no I/O, no Qt calls, no locks that can be contended from the main thread
- New rack effect modules go in `mlrvp/gui/rack_modules/` and must register themselves in `mlrvp/gui/rack_modules/__init__.py`

### Releasing

Bump `__version__` in `mlrvp/__init__.py` (the only place), move the changelog's
Unreleased entries under the new version, then push a `vX.Y.Z` tag. CI runs the full test
matrix, builds Windows/macOS/Linux and creates a **draft** release to review and publish.

### Sensitive data

Never commit real file paths from your machine, personal presets that contain absolute
paths, or anything under `data/defaults/` other than `.gitkeep` (the `.gitignore` blocks
most of it — check `git status` anyway).
