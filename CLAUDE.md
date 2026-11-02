# Spindle – notes for Claude

Spindle is an all-in-one, EAC-style secure CD ripper for Linux with a PySide6 GUI. It reads a disc, looks it
up (MusicBrainz, GnuDB, plugins), finds cover art, rips securely with cdparanoia, verifies with
AccurateRip, encodes to several formats at once, tags everything and writes a log. It also plays CDs, and
its **Tag Files** tab fixes tags, artwork and file names for music the user already has.

- Owner: voidxzero. Repo: https://github.com/voidxzero/spindle (public, GPL-3.0).
- The owner uses **Fedora** (KDE/GNOME). Test install steps against Fedora's own repos first.
- The code is written by Claude. The README opens with a "Made with Claude" credit, and the owner
  doesn't want credit for writing the code. Keep that credit at the top of the README.

## Layout

```
spindle/
  __init__.py      __version__ (bump on every release)
  disc.py          TOC via Linux cdrom ioctls, disc IDs (MusicBrainz/FreeDB/AccurateRip), drives, eject
  ripper.py        ParanoiaDrive (cdparanoia), SimulatedDrive, open_stream() for playback (CDROMREADAUDIO)
  accuraterip.py   v1/v2 checksums, dBAR lookup, drive-offset list, key-disc offset detection (frame 450)
  pipeline.py      RipJob: rip → verify → test&copy/re-read → encode (parallel) → tag → extras
  encoders.py      FLAC/ALAC/WavPack/WAV/MP3/Opus/Vorbis/AAC (reference encoders, ffmpeg fallback)
  tagger.py        mutagen tagging in "preserve" mode (only touches fields Spindle manages), covers
  tagfiles.py      Tag Files engine: scan, group into albums, identify, save, batch rename, cover-only
  convert.py       Convert engine: decode (ffmpeg/flac) -> encoders -> copy tags + cover; ConvertJob
  naming.py        %field% templates, [optional] sections, aliases; unknown fields are never written
  metadata/        providers (MusicBrainz, GnuDB) + plugin base classes; plugins/ has a Discogs example
  coverart.py      Cover Art Archive → iTunes → Deezer (configurable order), auto-pick
  extras.py        .log (EAC-style), .cue, .m3u8, ReplayGain
  config.py        ~/.config/spindle/config.json; DEFAULTS + migrations (config_version)
  updater.py       GitHub update check (raw HEAD __init__.py + CHANGELOG) and in-place install
  net.py           urllib helper: user agent, per-host rate limits, retries
  system.py        platform differences: config/data/cache folders, run()/popen() (no console windows on
                   Windows), bundled tools on PATH
  cli.py           spindle-cli
  gui/
    app.py           entry point, app icon
    main_window.py   Rip CD page, menus, disc polling, auto-rip, player bar, update bar wiring
    tagger_page.py   Tag Files tab (+ RenameDialog)
    convert_page.py  Convert tab
    dialogs.py       ReleaseDialog, CoverDialog, SettingsDialog (Drive/Output/Formats/Metadata/Cover art/
                     Plugins/Appearance/Updates)
    widgets.py       StatusDelegate, CheckDelegate, CoverLabel, Spinner, LoadingOverlay, Visualizer,
                     SeekSlider, ProviderList, unify_heights
    player.py        gapless CD playback (QAudioSink)
    cover_editor.py  crop/shape/rotate/trim/adjust/resize editor
    themes.py        "System" theme + skins (Modern Night, Luna Midnight, Olive Noir, Amp Classic)
    icons.py         flat mono icons (System) and glossy XP-style icons (skins)
    sounds.py        finish chime / problem tone (synthesised, cached) or the user's own file
    update_ui.py     update bar, update dialog, restart
install.sh / uninstall.sh   per-user install to ~/.local/share/spindle (venv with --system-site-packages)
windows/                    PyInstaller spec, Inno Setup script, icon maker, launcher for the Windows build
.github/workflows/windows.yml  builds + tests on Windows, makes SpindleSetup-x.y.z.exe + portable zip,
                            and creates the GitHub Release for each new version pushed to main
tests/                      plain-Python tests, no pytest needed
```

## Running and testing

```sh
python3 -m spindle --simulate              # GUI from the source folder, with a virtual drive (no disc needed)
python3 tests/test_engine.py               # IDs, AccurateRip, naming, simulated rips
python3 tests/test_tagging.py              # tags + artwork in every format (needs mutagen, ffmpeg)
python3 tests/test_tagfiles.py             # Tag Files matching, saving, rename
python3 tests/test_imageadjust.py          # cover editor adjustments
python3 tests/test_updater.py              # update check + install (no network)
python3 tests/test_convert.py              # Convert: every format, tags + cover, 24-bit, safety
ruff check --select F,E9 spindle tests     # must pass
bash -n install.sh
```

Run every test file after a change, and try the GUI with `--simulate` for anything visual. To try an
install without touching the real one: `HOME=$(mktemp -d) ./install.sh --no-deps`.

## Release routine (every change the owner asks for)

1. Make the change, then run the tests and the lint check above.
2. Bump the version in **both** `spindle/__init__.py` and `pyproject.toml`: patch for fixes and small
   tweaks, minor for features.
3. Add a `## x.y.z` entry at the top of `CHANGELOG.md`: short bullets, written for users. The updater
   shows this text as "What's new", and commit messages use it.
4. Update `README.md` when behaviour or settings change. Keep the "Not (yet) supported" list honest.
5. Commit, one commit per version:
   ```
   Spindle x.y.z: short summary

   - changelog bullets

   Co-Authored-By: Claude <noreply@anthropic.com>
   ```
   Only commit user-facing versions. Never make standalone commits for behind-the-scenes changes
   (CLAUDE.md, project links, helper scripts, README typo fixes and the like). Leave them uncommitted
   and they go into the next version's commit.
6. **Don't push** unless the owner says to. They review first. The updater offers whatever version is on
   GitHub's default branch, so pushing a higher `__version__` ships an update to every install.

## Design rules the owner has asked for (keep them)

- **System theme is the default** and follows KDE/GNOME. It uses flat, monotone icons and minimal QSS.
  The retro skins are optional and use glossy XP/Winamp-style icons. When adding a button, provide both
  icon styles (`icons.py`).
- Consistent sizing and spacing: line up control heights with `unify_heights`, no dead space, and fill
  the window sensibly. The owner notices spacing problems, so check layouts at small and large sizes.
- Checkboxes are drawn by `CheckDelegate`, because QSS hid them on alternating rows in the skins. Don't go
  back to native item checkboxes in tables.
- Anything that waits on the network or the drive shows activity: `LoadingOverlay` (greys out the page),
  `Spinner`, or `CoverLabel.set_loading`.
- Progress bars only move forward (monotonic).
- A finished track's Status shows a green ✓ **Success** (red ✗ for failures). AccurateRip detail goes in
  its own column.
- Tag editing is locked while ripping. Removing the disc clears that disc's details, tracks and cover.
- The seek bar has elapsed time on its left and total on its right, and click-to-seek. Its width
  doesn't jump. The spectrum visualizer sits framed at the far right of the player bar.
- Tag Files should be *more intuitive than Picard*: changes shown in bold colour, old values visible,
  previews before renaming or moving anything.
- Writing tags: preserve mode. Only replace the fields Spindle manages, and never write unknown template
  fields literally.

## Windows

- Spindle runs on Windows too (built by GitHub Actions, see `.github/workflows/windows.yml`). Stage 1:
  Tag Files + Convert. **Stage 2 (to do): CD ripping/playback on Windows**: a drive layer using Windows
  IOCTLs (`disc.py`/`ripper.py` currently report no drives when `fcntl` is missing). The owner has a
  Windows PC with a CD drive to test on.
- Never call `subprocess` directly: use `system.run()` / `system.popen()` (they stop console windows
  flashing on Windows). Folders come from `system.config_dir()/data_dir()/cache_dir()`, never XDG or
  `~/.cache` directly.
- Pushing a new `__version__` to main makes the workflow build and publish a Windows release
  (`vX.Y.Z` with the setup .exe and portable zip) about 15 minutes later. The Windows updater reads the
  latest *release*, Linux reads `__init__.py` on main. If the Windows build fails, Windows users simply
  aren't offered that version. Check the Actions tab after pushing.
- `Spindle.exe --selftest report.json` (hidden option) checks a packaged build end to end; CI runs it.
- Can't test Windows locally: a Linux PyInstaller build of `windows/spindle.spec` in a clean venv
  (PyPI packages only, not Fedora's numpy) plus `--selftest` catches most packaging mistakes.

## Gotchas

- The disc is polled with `drive_status()`. Async callbacks must check that `self.toc` is still the
  same disc (and `_lookup_serial` for lookups) before touching the UI.
- Never read the drive for playback while ripping. Head seeking ruins secure ripping. Playback stops
  when a rip starts.
- `install.sh` copies to `lib/spindle.new` and then swaps, so a failed copy never leaves a broken install.
  It records the GitHub repo in `~/.local/share/spindle/install.json` for the updater.
- After an in-place update the app must restart. Lazy imports would otherwise mix old and new code.
- Fedora: `ffmpeg-free` (keep RPM Fusion's `ffmpeg` if it's installed), `qt6-qtmultimedia`,
  `qt6-qtsvg`, `chromaprint-tools`. The venv uses `--system-site-packages` so Fedora's PySide6 is reused.
- MusicBrainz needs ≤1 request/s and a real User-Agent (`net.py` handles both).
- Temporary WAVs go in `~/.cache/spindle/work` (`RipJob._work_root`), never the output folder. The
  owner's music folder is on an sshfs share, where re-reading big files can fail with EPERM. The
  output folder is only a fallback when local disk is short of space.
- Retrying (`RipJob(retry_of=...)`) overwrites the failed tracks' files and merges results, so the
  log/cue/m3u/ReplayGain cover the whole album. Keep that when touching `_finish`.

## Talking to the owner

They're comfortable running commands but are new to git. Give exact, copy-pasteable commands for
committing, pushing and installing, and explain git terms briefly when they come up.
