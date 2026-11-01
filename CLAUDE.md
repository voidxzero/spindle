# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

Spindle is an EAC-style secure CD ripper for Linux (PySide6 GUI + `spindle-cli`). It rips with cdparanoia,
verifies against AccurateRip, looks up MusicBrainz/GnuDB/plugins, fetches cover art, encodes to several
formats in parallel, tags with mutagen, and writes `.log`/`.cue`/`.m3u8`. A second "Tag Files" tab retags
existing music. Python ≥ 3.9; runtime deps are only PySide6, mutagen, numpy (Pillow optional).

## Commands

```sh
python3 -m spindle --simulate          # run the GUI from the checkout with a virtual drive (or SPINDLE_SIMULATE=1)
python3 -m spindle.cli --list          # CLI: show matches only; -f flac -f opus, --image, --release N
./install.sh [--no-deps]               # install to ~/.local/share/spindle (venv + launchers + menu entry)
ruff check .                           # lint (ruff is used; there is no config, so defaults apply)
```

Tests are plain scripts, not pytest (pytest isn't installed). Run each file from the project root:

```sh
python3 tests/test_engine.py       # disc IDs, AccurateRip, naming, full simulated rips — no disc/network/Qt
python3 tests/test_tagging.py      # tag + artwork round-trip in every format (needs mutagen, ffmpeg)
python3 tests/test_tagfiles.py     # Tag Files matching, saving, batch rename
python3 tests/test_imageadjust.py  # cover editor adjustments
python3 tests/test_updater.py      # update check/install, network mocked
```

Run a single test: `python3 -c "import sys; sys.path.insert(0,'.'); import tests.test_engine as t; t.test_naming()"`.

Gotchas: each file's `__main__` block lists its tests by hand or loops over `globals()`, so a new test only runs if it's
registered there. In `test_engine.py`, `test_keydisc_offset` is defined *after* the `__main__` loop and so
never runs when the file is executed. Network-touching code gets patched with `unittest.mock`, and
`ripper.SimulatedDrive` stands in for hardware.

## Architecture

**The engine is toolkit-independent; the GUI is a thin layer on top of it.** Everything under `spindle/` except
`spindle/gui/` imports no Qt, so the CLI and tests drive the same code.

- `disc.py` reads the TOC straight from the kernel via ioctl and computes MusicBrainz/FreeDB/AccurateRip IDs locally.
  Enhanced CDs: the audio lead-out is the data-track start minus 11400 frames, and everything downstream uses
  `toc.audio_leadout` / `audio_tracks`.
- `ripper.py` holds the `Drive` abstraction: `ParanoiaDrive` (shells out to `cdparanoia`/`cd-paranoia`) and
  `SimulatedDrive` (synthetic tones; can be flaky or offset for tests). `open_stream()` returns a `PCMStream`
  that the CD player (`gui/player.py`) and key-disc offset detection use.
- `pipeline.py` has `RipJob.run()`, the whole rip: per track, secure read → AccurateRip check (`accuraterip.py`) →
  re-read (test & copy) if unconfirmed → submit encodes to a thread pool while the next track rips →
  tag (`tagger.py`) → extras (`extras.py`: ReplayGain via ffmpeg ebur128, cue, m3u, EAC-style log).
  It also handles single-file image + CUE mode. Progress goes out through a `Reporter`, whose methods are called
  from worker threads. The GUI subclass is `gui/workers.QtReporter`, which turns those calls into Qt signals.
- `encoders.py` keeps a `FORMATS` registry. For each format it prefers the reference encoder binary and falls back to ffmpeg
  (`backend()`, `command()`).
- `metadata/`: `base.py` defines `AlbumMeta`/`TrackMeta` (the common data model) and the `MetadataProvider` /
  `CoverProvider` plugin interfaces. `metadata/__init__.py` is the registry. It loads built-ins plus any subclass
  found in `.py` files in `~/.local/share/spindle/plugins/`, `~/.config/spindle/plugins/`, or the repo's
  `plugins/`, and orders and disables them per config. `plugins/discogs.py` is the reference plugin.
  Cover lookup lives in `coverart.py`.
- `net.py` is the only HTTP client (urllib, no requests). It enforces per-host rate limits (MusicBrainz ~1 req/s)
  and sets the user agent. Route all network calls through it.
- `config.py`: `Config` is a dict persisted at `~/.config/spindle/config.json`, deep-merged over `DEFAULTS`.
  New settings need a default there. Data and plugins live in `~/.local/share/spindle/` (both respect XDG vars).
- `naming.py` handles `%field%` templates with `[...]` optional sections (shared by rip output and Tag Files rename).
- `tagfiles.py` is the Tag Files engine: scan → group into albums → identify (existing MB IDs, tags, folder
  name, AcoustID via `acoustid.py`) → score candidates → save. It only replaces fields Spindle manages.
- `gui/`: `app.py` is the entry point, `main_window.py` the rip tab, `tagger_page.py` the Tag Files tab.
  Blocking work goes through `workers.run_async()` (a QThreadPool with done/failed signals). `themes.py` holds the skins.

## Versioning, releases and self-update

The version lives in **three places that must match**: `spindle/__init__.py` (`__version__`), `pyproject.toml`,
and a new top `## x.y.z` section in `CHANGELOG.md`. Release commits are titled `Spindle x.y.z`.

`updater.py` depends on this repo's layout. It fetches `spindle/__init__.py` and `CHANGELOG.md` from the
GitHub default branch (`raw.githubusercontent.com/<repo>/HEAD/...`), shows the changelog sections newer than
the installed version, and installs by downloading the branch tarball and running its `install.sh --no-deps`.
Don't move those files or change the `__version__ = "..."` line format, and keep `install.sh` non-interactive
and settings-preserving. It writes `install.json` (repo + version) and swaps in the new program files only
after copying them.
