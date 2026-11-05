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
  - Manual Link test **deferred past 1.0** by the owner (2026-10-21) — not a release blocker.
- [x] **B3. Pan law −3 dB jump** just off center.
  - Decision: balance law — centre unchanged (unity both sides), panning only attenuates the opposite side; no jump, no boost. 2 tests. — `a1f0954`
- [x] **B4. Plugin scan timeout ineffective** (executor `shutdown(wait=True)` blocks on hung plugin).
  - Daemon probe thread + join(timeout): scan moves on after the timeout and a permanently hung plugin no longer blocks app exit. 2 tests. — `345bd50`
- [x] **B5. Renaming a preset breaks other setlists.**
  - Rename rewrites the preset name in every setlist file; case-only renames now work on case-insensitive filesystems; failures logged. 2 tests. — `d094552`
- [x] **B6. serialosc elevation:** predictable `%TEMP%` script run elevated (use `-EncodedCommand`); service start mode changed and never restored.
  - Decision: stop for the session only. Startup type never changed; recovery actions suspended during the stop and restored from `sc qfailure`; elevated scripts passed via -EncodedCommand (no temp .ps1); log in a random temp folder. Also fixed the elevated *restart* path, whose nested quoting broke the command line. Both scripts verified with PowerShell's parser. README note for users whose serialosc was left on manual start. 2 tests. — `f89acf9`
- [x] **B7. Settings worker** can drop the last change on quit; non-atomic write; thread not joined.
  - New `startup_settings` module: locked, merged, atomic writes. Worker saves a pending change even when the quit sentinel arrives in the same batch; `stop()` joins it.
- [x] **B8. Folder-delete safety check** uses string `startswith`.
  - Strict resolved-path check (rejects the Slices folder itself, look-alike siblings, ../ escapes); applied to Rename too, which also rejects names containing / or \. 1 test. — `035dc0b`
- [x] **B10. Audio settings never persist / never apply.** Output/input device, driver, sample rate and buffer size aren't saved anywhere; sample rate & buffer size are only read when the engine is created, so the "restart to apply" message can never come true. Persist (devices by name, not index) and apply. _(with B7/F5)_
  - Saved in startup_settings.json (devices by name, resolved per launch with fallback + status notice); restored before the engine is created so sample rate/buffer size apply on next launch (verified end-to-end: 512 → 256 survives relaunch). Device/driver changes now reopen the stream immediately (debounced). Setup combos show the real values (buffer combo used to show 256 while running 512). 9 tests. — `05b65cc`
- [x] **B9. Silent save failures** — surface preset/rack/export errors in the UI.
  - PresetManager.on_write_error hook (presets, setlists, setlist create/rename) + the app's own writers (Save Preset File, Save Default, Save Rack) → status bar + non-modal warning dialog. Rack save now reports "Saved" only after the write; its thread is no longer a daemon. 2 tests. — `57e0bd7`

## C. Quality-of-life

- [x] **C1.** Library "Delete from disk" → move to Recycle Bin/Trash (`QFile.moveToTrash`); don't drop entries whose delete failed.
  - Samples/Slices/Songs delete, slice rows, batch and folder delete all use QFile.moveToTrash; failures left in place + listed; entries only dropped for moved files. 2 tests. — `9471285`
- [x] **C2.** Show a reason when a sample fails to load.
  - read_sample() raises SampleLoadError with a user-facing reason; status bar shows it. 4 tests. — `4ab09ca`
- [x] **C3.** Buffer-size options: keep all effects working at every offered size (or cap the list). _(done with A6, guarded by test)_
- [ ] **C4.** Ableton Link phase/beat sync (beyond tempo) — stretch goal.

## D. Git / CI / release process

- [x] **D1.** Single-source the version (spec, Inno script derive from `__version__`); CI fails if tag ≠ version.
  - spec + build.py read mlrvp/__init__.py; installer.iss always regenerated; CI fails on tag/version mismatch. — `4b5559d`
- [x] **D2.** Build workflow: run tests first; quote `pyinstaller>=6.0`; draft releases with generated notes.
  - build.yml reuses pr_check (full matrix) before building; quoted pyinstaller; draft releases with generated notes; least-privilege permissions. — `4b5559d`
- [x] **D3.** PR check: Windows + Python 3.11–3.13 matrix; add ruff; pre-commit config.
  - Ubuntu 3.11/3.12/3.13 + Windows 3.12 + macOS 3.12; ruff (correctness rules in pyproject) replaces pyflakes; .pre-commit-config.yaml. — `4b5559d`
- [x] **D4.** Pinned/constraints file for release builds.
  - requirements-lock.txt (uv universal lock, Python 3.12) used by build.yml; PR checks test latest. — `da6f6e1`
- [x] **D5.** Close/merge the 4 stale Dependabot PRs; bump actions; delete `copilot/*` branches.
  - Closed Dependabot PRs #4/#5/#7/#8 as superseded by 4b5559d; deleted local copilot/* branches and remote copilot/remove-duplicate-branches (old pre-rewrite history). Only main remains.
  - Actions bumped in the workflows (supersedes the PRs) — PRs still to close; `copilot/*` branches need a decision (two hold unmerged commits).
- [x] **D6.** Add `CHANGELOG.md`; adopt semver from 1.0.0.
  - CHANGELOG.md (Keep a Changelog) with an Unreleased section covering this review's changes.
- [x] **D7.** Remove test rack `data/racks/Updated Names.json`; gitignore user racks; stop tracking runtime-written `startup_settings.json`. _(done with A5; local copies kept as untracked user files)_
- [~] **D8.** Repo settings: enable Discussions (or drop link), private vulnerability reporting, topics, auto-delete merged branches.
  - Done: Discussions enabled, auto-delete merged branches, topics added. **After going public:** enable private vulnerability reporting (SECURITY.md links to it).
- [x] **D9.** Decide on history scrub (personal paths in deleted presets) before going public.
  - Decision: accept. main + all tags are clean; the only personal data (Windows username + one song filename in an old preset) was in the deleted stale branch and remains reachable only through the permanent refs of merged PRs #1/#2.
- [ ] **D10.** After public: protect `main` (PR + passing checks, no force-push).
- [x] **D13.** macOS CI job queued/cancelled at times — GitHub macOS arm64 *capacity* (annotation: "longer queue times"), not billing. When it runs it passes (~1.5 min). Keeping it.
- [x] **D11.** Known distribution gaps (document or address): unsigned Windows exe, ad-hoc-signed un-notarized macOS, arm64-only mac build.
  - Documented in README + release body: SmartScreen prompt, Gatekeeper workaround, Apple-Silicon-only macOS download (universal2 not viable: numba/llvmlite). Real signing/notarization needs paid certificates — post-1.0. — `d36b6fe`
- [ ] **D12.** Tag v1.0.0.

## G. Second full review (pre-tag)

- [x] **G1. License conflict — accepted, license kept.** mlrVp is MIT + Commons Clause, but release builds bundle **pedalboard (GPL-3.0)** and **aalink (GPL-3.0+, wraps Ableton Link)**, and the GPL doesn't permit added restrictions like the Commons Clause.
  - Decision (2026-10-21): keep MIT + Commons Clause (no reselling — the owner wants mlrVp free always; GPLv3 would allow resale) and keep both libraries bundled; accepted as a low practical risk.
  - **If it ever needs revisiting** (e.g. a request from Spotify/pedalboard, the aalink author or Ableton): ship release builds without pedalboard and aalink — the app already greys out Link and plugin hosting when they're absent, and source users can `pip install` them. Relicensing to GPL-3.0 was considered and rejected because it allows reselling.
- [x] **G2.** Saved audio device busy/absent or rejecting the saved sample rate/buffer size crashed startup on *every* launch (settings now persist). Falls back to default output, then starts with audio off + explanation. 4 tests. — `90f033b`
- [x] **G3.** Presets store absolute sample paths → after the data-folder move, every sample under an old `data/slices|songs` broke once the old folder was deleted; a missing sample left its row on "(loading...)" forever. Paths are followed to the new folders; missing ones show "(missing)" + status. 6 tests. — `cc4dd75`
- [x] **G4.** Fuzzing preset loading: `1e999`/`Infinity`/wrong types raised out of `apply_preset` (OverflowError, control-row block); malformed racks (non-object, bad UTF-8) crashed; case-only rack rename failed. All text files read/written as UTF-8 explicitly. Fuzzer: 1000+ presets × 25 bad values, clean. 5 tests. — `a5d4fe1`
- [x] **G5.** Malformed MIDI/tilt mappings in a preset aborted recall part-way (raw `m["param_path"]`); validated + deduplicated into one helper. 3 tests. — `eeced6a`
- [x] **G6.** `stop()` ran twice on every quit; quitting could leave a zombie process if another top-level window was open (Qt 6 `quit()` vetoed by the hide-only grid window). — `7be2d85`, `ed041fb`
- [x] **G7.** Release zips had no license texts (LGPL Qt/soxr/libsndfile, GPL pedalboard/aalink). Generated `THIRD_PARTY_LICENSES.txt` + `LICENSE` + `licenses/` now ship with every build. — `68251e6`
- [x] **G8.** Depend on PySide6-Essentials (only QtCore/Gui/Widgets used) instead of PySide6 + Addons/WebEngine/Pdf. — `ef5a81b`
- [ ] **G9.** Before tagging: `Development Status :: 4 - Beta` → `5 - Production/Stable` in pyproject; bump `__version__`; CHANGELOG Unreleased → 1.0.0. _(with D12)_
- Verified clean: OSC binds 127.0.0.1 and is off by default; preset/rack keys are whitelisted (no arbitrary attribute writes); no eval/pickle; packaged Windows build launches, seeds racks, logs correctly.
- Note: the local `origin` URL uses old casing (`trumanac/mlrVp`); pushes work via redirect — `git remote set-url origin https://github.com/Trumanac/mlrVp.git` to silence the notice.

## H. Third full review (pre-tag)

- [x] **H1.** Mute/solo, group mute, channel gain and master volume switched level instantly → clicks up to ~24× the waveform's normal step on playing loops. Constant-rate gain ramp in the channel kernel (+ numpy fallback), group mute via the volume ramp, master volume ramp. — `4d4a0b2`
- [x] **H2.** PITCH mode built up to 16 full-length pitch-shifted copies per row (GBs of RAM, minutes of CPU for a whole track). Samples > 30 s use speed-based pitch. — `60251e5`
- [x] **H3.** Tempo limits disagreed (presets 999, clock/UI 300, Link unclamped) → clock and loops on different tempos; NaN over OSC became 300 BPM. One TEMPO_MIN/MAX applied everywhere. — `1342e86`
- [x] **H4.** Welcome dialog, How-to and README told users to click a nonexistent "Start Engine"; README missed the new File/Help menu items; About now has project link + licenses. — `83b6c87`
- [x] **H5. Latency.** The DSP worker renders 7 blocks ahead and applies grid triggers when rendering → ~81 ms from press to sound at 512 (comment claimed triggers were unaffected). New Setup → Audio → Latency (Low/Medium/High = 2/4/7 blocks; High = old behaviour). Measured: no underruns at Low with 16 rows + grid-mode/tab stress. — `3aa5852`
  - [x] **Decision (2026-11-05):** default is Medium (~46 ms at 512), chosen by the owner after trying it.
- [x] **H6.** File dialogs opened in the working directory (install folder) and never remembered folders; MP3 missing from Load dialogs. — `b87afc0`
- [x] **H7.** Quit → Save → cancelling the name/setlist prompt quit anyway (changes lost). — `5297976`
- [x] **H8.** Saving a preset didn't save in-memory SAMP/LIVE/studio recordings — reloaded the row's *previous* sample. Now written to Slices/Recordings on save. — `de26bd9`
- [x] **H9.** Only channel/group edits marked the preset unsaved; effects, tempo, quantize, master and recordings were discarded on quit without asking. — `fab3e86`
- Post-1.0 ideas: sample-accurate trigger timing (triggers land on block boundaries, ±1 block jitter); undo for destructive grid/preset actions; per-row "save recording" button.

## I. Pre-public / pre-tag audit (2026-11-05)

Checked clean:
- Tracked files + full git history: no local paths/username, personal email, or secret-like tokens. Every commit on every ref uses a GitHub noreply address (one early commit shows the `SuperBirdy` alias on the same account; the rest are `Trumanac`, Copilot and Dependabot).
- All 24 existing releases (v0.6.9 … v0.6.9.23) become public with the repo — every Windows zip scanned (bundled `data/` files + entry names): no personal strings.
- Actions: default workflow token is read-only, no secrets/variables/webhooks/deploy keys, no `pull_request_target`, no self-hosted runners.
- Links in README/CHANGELOG/SECURITY/About: all repo-owned links 404 only while the repo is private (they resolve once public).

Fixed:
- [x] Default Latency → **Medium** (owner's choice after trying it).
- [x] Release builds ship `requirements-lock.txt`, but CI only tested the *latest* packages — the shipped set was never tested. Added locked test jobs (Linux/Windows/macOS). — `aa05eda`
- [x] Dependabot opened one PR per pinned package (certifi, msgpack, narwhals…) against the lock file, untested. Now one grouped monthly PR for Python and one for Actions; the locked CI jobs test it. — `aa05eda`
- [x] Created the `hardware` and `dependencies` labels (issue templates and Dependabot referenced labels that didn't exist).

Do right after flipping to public (I can run all of these):
- [ ] Private vulnerability reporting (SECURITY.md links to it — 404 until enabled), secret scanning + push protection, Dependabot alerts / security updates.
- [ ] Actions → "Require approval for all outside collaborators" before workflows run on fork PRs.
- [ ] Protect `main`: block force-push and deletion (D10). Decide whether to require PRs/checks — owner currently pushes straight to `main`.
- [ ] Dependabot PRs #9–#13 are superseded by the new grouped config — don't merge #9 (`softprops/action-gh-release` v2→v3) before the 1.0 tag: the release job can't be tested without a tag.

Owner decisions / optional:
- Collaborators `macwhisperer2` and `zboone93-sudo` both have **write** access — they keep it after going public; confirm intended or downgrade.
- No `CODE_OF_CONDUCT.md` (community profile 85 %). Optional; needs a contact method for reports.
- GitHub shows the license as "Other/NOASSERTION" (Commons Clause isn't a recognised SPDX license) — cosmetic; the README states it.
- Projects tab enabled but unused — can be turned off.

## Found during fixes

- [x] **F1.** CI test job failed on first run: `test_rejects_oversized_file` crashed pytest on Python 3.12 (Path.stat patch missing `follow_symlinks`); checkbox click missed hit area on Linux. — `bf142b0`
- [x] **F3.** LIVE export/Send-to-Slice and Send-to-Slice wrote to a CWD-relative `data/slices` (wrong place / failure when launched from a shortcut). _(fixed with A5)_
- [x] **F4.** `package.py` source zip included `data/defaults/` (personal sample paths, plugin cache, favorites) and user racks. _(fixed with A5)_
- [x] **F5.** `_save_startup_settings()` overwrites the whole file and erases the `welcome_shown` flag → Welcome dialog reappears after any settings change. _(fix with B7)_
  - Welcome dialog and app share the merged writer — flag no longer erased.
- [ ] **F6.** ~~Startup blocks probing serialosc~~ — profiled: the probe is instant; the ~5–7 s is building MainWindow (16 channel strips; re-parenting styled sub-trees costs ~0.4 ms per addWidget). **Post-1.0 optimisation** (needs strip construction + signal wiring refactor). Measured and kept: tooltip eventFilter/styleHint hooks cost ~0.4% of the GUI thread.
- [x] **F7.** Add `.gitattributes` for consistent line endings across platforms.
  - — `4b5559d`
- [x] **F8.** Preset recall set the engine tempo but never the clock: after loading a preset with a different BPM, beats/quantize/SEQ/patterns kept the old tempo while loops used the new one. _(fixed with B2)_
- [x] **F9.** Inno Setup publisher (`monome community`, mlrV2 URL) and macOS bundle id (`org.monome-community.mlrvp`) should be the project's own.
  - Decision: keep "monome community" publisher and org.monome-community.mlrvp bundle id.
- [x] **F10.** CI abort (SIGABRT) on Ubuntu/py3.11: Qt widgets collected by the GC on a worker thread. run_on_main_thread now uses a queued signal; engine's periodic GC runs on the GUI thread; tests collect on the main thread. — `99ffcce`
- [x] **F11.** Engine disables automatic GC and only ever collects generation 0, so cyclic garbage that survives one collection is never freed (slow memory growth in long sessions). Needs a safe schedule for gen-1/2 collections.
  - gc.freeze() after startup (full collect 60 ms → 2 ms); schedule gen-0/2 s, gen-1/30 s, full/5 min on the GUI thread. — `13e6d42`
- [x] **F12.** PySide6 6.12.0 regression: `Signal.emit()` loses a reference to True/False → abort on Python < 3.12 (reproduced in WSL Ubuntu, Python 3.11). PySide6 capped `<6.12` for Python < 3.12; guard test. Worth reporting upstream to Qt. — `1a4723f`
- [x] **F2.** Song Recorder "saved" status shown before the export ran / even on failure. Fixed for Song Recorder in A1; audit other save paths under B9.
  - Song Recorder fixed in A1; remaining save paths covered by B9.
