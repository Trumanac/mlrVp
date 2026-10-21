# Changelog

All notable changes to mlrVp are documented here.
The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and from 1.0.0 the
project uses [Semantic Versioning](https://semver.org/).

## [Unreleased]

### Changed
- **Your files now live outside the app folder**, so updating never touches them.
  Presets, setlists, racks, settings and logs go in the per-user data folder
  (`%LOCALAPPDATA%\mlrVp`, `~/Library/Application Support/mlrVp`, `~/.local/share/mlrVp`);
  recordings and exported slices go in `Music/mlrVp`. See
  [Where your files are stored](README.md#where-your-files-are-stored).
- Channel pan is now a stereo balance: centre is unchanged, and panning only turns down
  the opposite side (it used to drop ~3 dB the moment the knob left centre).
- Audio device and driver changes take effect immediately instead of after a restart.
- 40h direct mode stops the serialosc service for the current session only; it no longer
  switches serialosc to manual start, so other monome apps keep working after a reboot.
- New releases are published as drafts after the full test suite passes on Windows,
  macOS and Linux.
- Depends on `PySide6-Essentials` instead of the full `PySide6` package, which also
  installed the large Qt Addons (WebEngine, Multimedia, …) that mlrVp never uses.

### Added
- **File → Import Data from Previous Version…** to bring presets, setlists, racks and
  recordings across from an older mlrVp folder (nothing is overwritten).
- **File → Open Data Folder**, **Help → Open Log Folder** and **Help → Report a Bug…**.
- A log file (`logs/mlrvp.log`, plus `crash.log` for native crashes) — release builds
  previously had no way to see errors.
- The version number is shown in the window title.
- Release downloads include mlrVp's license and the licenses of all bundled third-party
  software (`THIRD_PARTY_LICENSES.txt`, `licenses/`).
- A status-bar notice when an effect fails and is bypassed.
- A warning when a background save (preset, setlist, rack, default) fails — previously
  only visible in the log.
- **Delete from disk** in the Samples, Slices and Songs libraries moves files to the
  Recycle Bin / Trash instead of deleting them permanently.
- When a sample can't be loaded, the status bar says why (not found, too large, damaged…).

### Fixed
- OSC remote control did nothing; File → Open Preset never applied the preset; Slice
  **EXPORT ALL** left its button disabled forever; Send-to-Slice and the Songs library
  refresh never ran (background-thread callbacks were silently dropped).
- Song Recorder: exporting after a punch-in dropped everything before the punch point;
  tape speed was applied twice on export; a take that hit the 60-minute limit was lost;
  finished takes were held in memory twice.
- The Song Recorder waveform re-scanned the whole recording 20 times a second, freezing
  the UI on long recordings.
- Audio device, driver, sample rate and buffer size were never saved, and sample rate /
  buffer size never took effect; the Setup page now also shows the real values.
- Tape Delay and Shimmer Reverb stopped working at buffer sizes of 1024/2048.
- Loading a preset with a different BPM left beats, quantize, SEQ and pattern recorders
  at the old tempo.
- A preset or Link peer above 300 BPM (or a NaN tempo over OSC) left the clock and the
  loops on different tempos; one 20–300 BPM range now applies everywhere.
- Ableton Link tempo changes from other peers weren't applied to loops, delays or the BPM
  display.
- The welcome dialog reappeared after changing grid or control-row settings.
- Renaming a preset broke every setlist other than the one currently loaded; case-only
  renames failed on Windows/macOS.
- A hung plugin could stall the plugin scan indefinitely.
- LIVE loop export and Send-to-Slice could write into the wrong folder depending on how
  mlrVp was launched.
- Folder delete/rename in the Slices library could act on the Slices folder itself or
  outside it.
- Reduced GUI-thread stalls from restyling buttons on every UI frame.
- A preset whose sample file is missing showed "(loading...)" on that row forever; the
  row now says "(missing)" and the status bar lists the files. Presets from older
  versions find samples that were moved to the new Slices / Songs folders.
- mlrVp no longer fails to start when the saved audio device is unavailable or rejects
  the saved sample rate / buffer size — it falls back to the default output, or starts
  with audio off and explains why.
- A damaged or hand-edited preset or rack (out-of-range numbers, wrong value types,
  malformed MIDI/tilt mappings, non-UTF-8 text) could abort loading part-way; bad values
  are now skipped. Renaming a
  rack to change only its capitalisation failed on Windows/macOS.
- Muting, soloing or unmuting a playing channel, muting a group, and jumps in channel
  gain or master volume clicked (the level switched instantly); they now fade over ~6 ms.
- PITCH mode on a long sample (e.g. a whole track) built up to 16 full-length
  pitch-shifted copies — gigabytes of RAM and minutes of CPU. Samples over 30 s now use
  speed-based pitch, with a status message.
- Memory slowly grew during long sessions (some objects were never garbage-collected).

### Security
- Elevated serialosc commands are passed inline instead of via a script file in the
  temp folder that another process could have replaced before the UAC prompt.

## [0.6.9.24] and earlier

See the [GitHub releases](https://github.com/Trumanac/mlrVp/releases) page.

[Unreleased]: https://github.com/Trumanac/mlrVp/compare/v0.6.9.23...HEAD
[0.6.9.24]: https://github.com/Trumanac/mlrVp/releases
