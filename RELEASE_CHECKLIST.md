# v1.0.0 Release Checklist

Tracking list from the full codebase + git-process review (2026-10-20).
Check items off as they land; note the commit hash next to each.
New issues discovered along the way go under **Found during fixes**.

Baseline: `c7319d2` (v0.6.9.24)

## A. Release blockers

- [x] **A1. Worker-thread → GUI handoffs never fire.** `QTimer.singleShot(0, fn)` from a Python thread is silently dropped on PySide6 6.11. Affects OSC control (all), File → Open preset, Slice EXPORT ALL (button stuck), Send-to-Slice, Song Recorder library refresh.
  - Added `mlrvp/gui/main_thread.run_on_main_thread()`; all 14 deferred calls in app.py/tools_view.py now use it. OSC verified end-to-end. Slice export always re-enables its button even on unexpected errors. Song Recorder save status now reports after the export finishes, including failures (part of B9). — `58854b9`
- [x] **A2. Song Recorder export ≠ what you hear.** Punch-in export drops pre-punch audio; tape speed applied twice.
  - Export now writes the full spliced tape (incl. a take in progress) resampled by 1/speed, matching panel playback. Also fixed: takes stopped by the 60-min cap were lost (now auto-finalised with a status message), and `stop()` kept every take in memory twice. 6 regression tests. — `0f23847`
- [x] **A3. Song Recorder waveform peaks recomputed every 50 ms** (130 ms/call at 5 min, 488 ms at 20 min) → UI freeze / audio dropouts.
  - Cached envelope (1 peak / 512 frames), built once per finished tape and extended incrementally while recording; `stop()` seeds the cache. Now ~0.04 ms/poll (20-min tape), ≤0.4 ms/poll while recording. 3 tests incl. reference comparison. — `b076168`
- [x] **A4. No log file in release builds.** Windowed exe has no console; add rotating log file + "Show logs" menu item; update bug template.
  - Rotating `logs/mlrvp.log` (2 MB × 4) in the data folder; console only when present; `sys.excepthook` + `threading.excepthook` log uncaught errors (incl. Qt slots); `faulthandler` → `logs/crash.log`; startup banner with versions; `MLRVP_DEBUG=1` now actually shows DEBUG/profiler output. Help → Open Log Folder / Report a Bug…; version in window title; bug template asks for the log. 2 tests. — `b2b12d8`
- [x] **A5. User data stored inside the app / site-packages.** Move to per-user data dir, migrate existing `data/`, bundle factory racks/defaults read-only.
  - Decision: settings/presets/racks/logs → OS user-data folder (`%LOCALAPPDATA%\mlrVp`, `~/Library/Application Support/mlrVp`, `~/.local/share/mlrVp`); audio → `Music/mlrVp/{Songs,Slices}`; source checkouts keep repo `data/`; `MLRVP_DATA_DIR` override.
  - One-time never-overwrite migration from the in-place legacy folder; **File → Import Data from Previous Version…** for users who extract new versions to a new folder; **File → Open Data Folder**. Factory racks shipped in `mlrvp/resources/factory_racks`, seeded once. README "Where your files are stored" + "Updating" sections. Verified end-to-end launch/migrate/shutdown in simulated end-user mode. 14 tests. — `293e1e1`
- [x] **A6. Effects fail at buffer ≥1024.** Tape Delay (≥1024), Shimmer Reverb (2048). Also rate-limit per-block FX exception logging.
  - Tape Delay grows its scratch buffer; Shimmer processes in sub-blocks ≤ shortest comb. Failing effects now log once per 10 s (with repeat count) and show a status-bar notice; whole-block DSP failures likewise rate-limited into the log (was `print_exc` every block). `BUFFER_SIZE_CHOICES` shared by Setup + a test running every effect at every size. — `e1a9f0b`

## B. Bugs

- [x] **B1. ~270 `setStyleSheet` calls/sec** from the 30 fps loop (group-stop buttons + Link button).
  - Both setters cache their last state (keyed incl. group color); cache cleared after MIDI-map mode and on manual Link clicks so a failed Link enable still reverts the button. 4 tests. — `5ccd7c3`
- [x] **B2. Ableton Link tempo** from peers not pushed to engine / BPM display.
  - GUI timer follows `clock.link_tempo` through the normal tempo path (engine, delays, speed spinners, BPM box), with a 0.5 s hold-off after local changes so they aren't undone. Unit-tested; **needs a manual check with a real Link peer** (multicast blocked in the dev sandbox; also `aalink` isn't installed in the local .venv, so the Link button is disabled there — `pip install -r requirements.txt`). — `93f3af5`
- [x] **B3. Pan law −3 dB jump** just off center.
  - Decision: balance law — centre unchanged (unity both sides), panning only attenuates the opposite side; no jump, no boost. 2 tests.
- [ ] **B4. Plugin scan timeout ineffective** (executor `shutdown(wait=True)` blocks on hung plugin).
- [ ] **B5. Renaming a preset breaks other setlists.**
- [ ] **B6. serialosc elevation:** predictable `%TEMP%` script run elevated (use `-EncodedCommand`); service start mode changed and never restored.
- [x] **B7. Settings worker** can drop the last change on quit; non-atomic write; thread not joined.
  - New `startup_settings` module: locked, merged, atomic writes. Worker saves a pending change even when the quit sentinel arrives in the same batch; `stop()` joins it.
- [ ] **B8. Folder-delete safety check** uses string `startswith`.
- [x] **B10. Audio settings never persist / never apply.** Output/input device, driver, sample rate and buffer size aren't saved anywhere; sample rate & buffer size are only read when the engine is created, so the "restart to apply" message can never come true. Persist (devices by name, not index) and apply. _(with B7/F5)_
  - Saved in startup_settings.json (devices by name, resolved per launch with fallback + status notice); restored before the engine is created so sample rate/buffer size apply on next launch (verified end-to-end: 512 → 256 survives relaunch). Device/driver changes now reopen the stream immediately (debounced). Setup combos show the real values (buffer combo used to show 256 while running 512). 9 tests. — `05b65cc`
- [ ] **B9. Silent save failures** — surface preset/rack/export errors in the UI.

## C. Quality-of-life

- [ ] **C1.** Library "Delete from disk" → move to Recycle Bin/Trash (`QFile.moveToTrash`); don't drop entries whose delete failed.
- [ ] **C2.** Show a reason when a sample fails to load.
- [x] **C3.** Buffer-size options: keep all effects working at every offered size (or cap the list). _(done with A6, guarded by test)_
- [ ] **C4.** Ableton Link phase/beat sync (beyond tempo) — stretch goal.

## D. Git / CI / release process

- [ ] **D1.** Single-source the version (spec, Inno script derive from `__version__`); CI fails if tag ≠ version.
- [ ] **D2.** Build workflow: run tests first; quote `pyinstaller>=6.0`; draft releases with generated notes.
- [ ] **D3.** PR check: Windows + Python 3.11–3.13 matrix; add ruff; pre-commit config.
- [ ] **D4.** Pinned/constraints file for release builds.
- [ ] **D5.** Close/merge the 4 stale Dependabot PRs; bump actions; delete `copilot/*` branches.
- [ ] **D6.** Add `CHANGELOG.md`; adopt semver from 1.0.0.
- [x] **D7.** Remove test rack `data/racks/Updated Names.json`; gitignore user racks; stop tracking runtime-written `startup_settings.json`. _(done with A5; local copies kept as untracked user files)_
- [ ] **D8.** Repo settings: enable Discussions (or drop link), private vulnerability reporting, topics, auto-delete merged branches.
- [ ] **D9.** Decide on history scrub (personal paths in deleted presets) before going public.
- [ ] **D10.** After public: protect `main` (PR + passing checks, no force-push).
- [ ] **D11.** Known distribution gaps (document or address): unsigned Windows exe, ad-hoc-signed un-notarized macOS, arm64-only mac build.
- [ ] **D12.** Tag v1.0.0.

## Found during fixes

- [x] **F1.** CI test job failed on first run: `test_rejects_oversized_file` crashed pytest on Python 3.12 (Path.stat patch missing `follow_symlinks`); checkbox click missed hit area on Linux. — `bf142b0`
- [x] **F3.** LIVE export/Send-to-Slice and Send-to-Slice wrote to a CWD-relative `data/slices` (wrong place / failure when launched from a shortcut). _(fixed with A5)_
- [x] **F4.** `package.py` source zip included `data/defaults/` (personal sample paths, plugin cache, favorites) and user racks. _(fixed with A5)_
- [x] **F5.** `_save_startup_settings()` overwrites the whole file and erases the `welcome_shown` flag → Welcome dialog reappears after any settings change. _(fix with B7)_
  - Welcome dialog and app share the merged writer — flag no longer erased.
- [ ] **F6.** Startup blocks the GUI ~7 s probing serialosc when the daemon isn't running.
- [ ] **F7.** Add `.gitattributes` for consistent line endings across platforms.
- [x] **F8.** Preset recall set the engine tempo but never the clock: after loading a preset with a different BPM, beats/quantize/SEQ/patterns kept the old tempo while loops used the new one. _(fixed with B2)_
- [ ] **F2.** Song Recorder "saved" status shown before the export ran / even on failure. Fixed for Song Recorder in A1; audit other save paths under B9.
