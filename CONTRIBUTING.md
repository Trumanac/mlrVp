# Contributing to mlrVp

Thank you for your interest in contributing! Here's everything you need to know.

## Ways to Contribute

- **Bug reports** — open an issue using the Bug Report template
- **Hardware compatibility reports** — open an issue using the Hardware Compatibility template
- **Feature requests** — open an issue using the Feature Request template
- **Code contributions** — fork, branch, and open a pull request

## Development Setup

```bash
# 1. Fork and clone the repo
git clone https://github.com/<your-username>/mlrVp.git
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

# 5. Run the app to verify your setup
python -m mlrvp
```

## Dependencies

- `requirements.txt` (and `pyproject.toml`) list the supported version ranges; PR checks
  install the latest versions that satisfy them.
- `requirements-lock.txt` pins the exact versions used for release builds. After changing
  `requirements.txt`, regenerate it with
  `uv pip compile requirements.txt --universal --python-version 3.12 -o requirements-lock.txt`.

## Coding Conventions

- **Python 3.10+** — use type hints where they aid clarity
- **PySide6** for all GUI code — no tkinter or other toolkits
- Follow the existing module structure: audio logic in `mlrvp/audio/`, GUI in `mlrvp/gui/`
- Keep the audio callback path (`engine.py`) free of any Python-level blocking — no I/O, no Qt calls, no locks that can be contended from the main thread
- New rack effect modules go in `mlrvp/gui/rack_modules/` and must register themselves in `mlrvp/gui/rack_modules/__init__.py`

## Submitting a Pull Request

1. **Open an issue first** for significant changes — discuss the approach before writing code
2. Branch off `main`: `git checkout -b feature/my-feature`
3. Keep commits focused and atomic
4. Fill out the pull request template completely
5. Make sure the app launches and audio works before submitting

## Sensitive Data Rules

**Never commit:**
- Real file paths from your machine (sample paths, plugin paths)
- Personal presets that contain absolute paths
- Any file under `data/defaults/sample_favorites.json`, `data/defaults/sample_paths.json`, or `data/defaults/plugin_cache.json` — these are all gitignored for a reason

The `.gitignore` is set up to block most of these automatically, but double-check `git status` before committing.

## License

By contributing, you agree that your contributions will be licensed under the same **MIT with Commons Clause** license as the rest of the project. See [LICENSE](LICENSE) for details.
