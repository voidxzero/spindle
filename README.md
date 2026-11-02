# Spindle

> **Made with Claude.** The code in this project was written by [Claude](https://claude.ai), Anthropic's
> AI assistant, through many rounds of my requests, testing and feedback on real discs. I directed the
> design and tested it; I don't claim credit for writing the code.

An all-in-one, EAC-style secure CD ripper for Linux. Put a disc in, and Spindle reads it,
looks it up, finds the cover, rips it securely, verifies it against AccurateRip, encodes it to as
many formats as you like, tags everything and writes a log — in one window. It also plays CDs,
and its **Tag Files** tab fixes tags, artwork and file names for music you already have.

## Install

### Windows

Download **SpindleSetup-x.y.z.exe** from the [releases page](https://github.com/voidxzero/spindle/releases/latest)
and run it (no administrator rights needed), or take the **portable zip** and unzip it anywhere. Everything
Spindle needs, including ffmpeg, is included. Everything works on Windows: ripping, playing CDs, Tag Files and
Convert. Windows reads the disc directly (no cdparanoia), re-reading bad spots sector by sector, and
AccurateRip plus Test & copy check every track. Settings are kept in `%APPDATA%\Spindle`.

Windows may warn that the installer is from an unknown publisher (it isn't code-signed): choose
*More info* ▸ *Run anyway*.

### Linux

```sh
git clone https://github.com/voidxzero/spindle.git
cd spindle
./install.sh
```

Spindle checks GitHub for a new version when it starts. When there is one, a bar appears at the top
of the window: **Update…** shows what's new, installs it and restarts Spindle. Your settings, offsets
and plugins are kept. You can also use **Help ▸ Check for updates…** or the **Check for updates now**
button in **Settings ▸ Updates**, where you can also turn the startup check off. If you run Spindle from
the cloned folder instead of installing it, update with `git pull && ./install.sh`.

The installer uses your package manager (apt, dnf, pacman or zypper) to install `cdparanoia` and the
encoders, creates a private Python environment with PySide6 and mutagen, and adds **Spindle** to your
application menu. Use `./install.sh --no-deps` to skip the package-manager step.

If the installer says you can't read the drive, add yourself to the group it names
(`sudo usermod -aG cdrom $USER`), then log out and in.

### Fedora notes

- Everything comes from Fedora's own repos (`ffmpeg-free`, `lame`, `fdkaac`, `python3-pyside6`, …);
  RPM Fusion isn't needed. If you already have RPM Fusion's `ffmpeg`, the installer keeps it.
- GNOME may open audio CDs in another app when you insert them. In **Settings ▸ Apps ▸ Default Apps**
  (or *Removable Media* on older versions), set **Audio CD** to *Spindle* or *Do nothing*, so nothing
  else grabs the drive while Spindle reads it.

No disc handy? `spindle --simulate` adds a virtual drive so you can try the whole flow.

## First run: set your drive's read offset

Every drive model reads audio a few samples early or late. Open **Settings ▸ Drive** and either:

- press **Look up in AccurateRip** — Spindle finds your model in the AccurateRip offset list, or
- insert any well-known CD and press **Detect with a key disc…** — Spindle reads a few sectors of each
  track and finds the offset that makes them match other people's rips in the AccurateRip database.
  This works even for drives that aren't in the list; for certainty, confirm with a second CD.

Spindle remembers the offset for each drive model.
Without the right offset, AccurateRip will report "Not accurate" even for perfect rips.

## Automatic ripping

Turn on **Settings ▸ Drive ▸ …then rip it automatically**. When you insert a disc, Spindle reads it,
looks it up, finds the cover and starts ripping after a 5-second countdown (press **Cancel auto-rip**
to stop it). With several matching releases it uses the best one; if nothing matches it waits for
you rather than ripping an unknown album. Add **Eject when the rip is finished** to feed discs in one
after another. A disc that's already in the drive when Spindle starts is never ripped automatically.

During a rip you can still open **Settings** (the drive, output and format settings it's using are
greyed out until it finishes) and **Open folder** to watch the album being written. When the tray is
open, the **Eject** button becomes **Close**.

If any tracks fail, **Retry failed** (next to *Open folder*) rips just those again; right-click a failed
track for *Retry track N*. The new files replace the bad ones and the log, cue sheet and playlist are
rewritten for the whole album.

## Finish sound

When a rip finishes, Spindle plays a short rising chime, or a lower two-note tone if any track had
problems. **Settings ▸ Drive** turns it off, sets the volume, or picks your own sound file for good
rips (WAV, FLAC, OGG, MP3…); **Test** plays it (hold Shift for the problem tone).

## What it does

| Step | How |
|---|---|
| Read the disc | TOC straight from the kernel; Enhanced CDs (with data tracks) handled correctly |
| Disc IDs | MusicBrainz, FreeDB/GnuDB and AccurateRip IDs computed locally |
| Metadata | MusicBrainz (incl. composers, ISRCs, labels, catalog #, genres), GnuDB, and plugins. Search by artist/album when a disc isn't found. |
| Cover art | Cover Art Archive → iTunes → Deezer (order is configurable), picks the first good-sized match automatically; or choose from a gallery / load a file |
| Secure ripping | cdparanoia with your offset; each track is checked against AccurateRip right after it's read. If AccurateRip can't confirm it, Spindle reads it again (test & copy) and re-reads more on CRC mismatch |
| Encoding | FLAC, ALAC, WavPack, WAV, MP3 (LAME), Opus, Ogg Vorbis, AAC — several at once, encoding in parallel while the next track rips. Uses the reference encoders when installed, ffmpeg otherwise |
| Tagging | Full tags + embedded front cover in every format, Picard-compatible MusicBrainz IDs, ReplayGain 2.0 track/album gain |
| Extras | EAC-style `.log`, `.cue`, `.m3u8`, `cover.jpg`; single-file image + CUE mode (cue sheet also embedded in FLAC) |

## Playing CDs

The bar under the album details plays the disc: play/pause, previous/next, a seek bar and volume.
Double-click any track, click its number, or select it and press Enter to play it; right-click for
*Play* or *Rip only this track*. The playing track shows ▶. Audio is read straight from the drive,
so playback starts and seeks almost instantly, and it's gapless across tracks. Playback stops
automatically when you rip. A mini Winamp-style spectrum analyser dances along next to the track title.

## Tag Files (fixing music you already have)

1. Open the **Tag Files** tab and drop in a folder (or use *Add Folder…*). Spindle groups the files
   into albums and looks each one up on MusicBrainz — using MusicBrainz IDs already in the files,
   then the album/artist tags or folder name, scoring every candidate by track count, titles and
   track lengths.
2. Each album gets a status: **green** = matched, **amber** = please check, **red** = no match.
3. Click an album. Changes are shown in **bold colour**, the old title is in the *Currently* column,
   and track lengths that don't fit the release are flagged red. Edit anything inline; pick another
   release from **Match**, or **Search…**. For untagged files, **Identify by Audio** uses AcoustID
   fingerprints (add a free API key in Settings ▸ Metadata).
4. **Save Album** writes the tags and cover. Spindle only replaces the fields it manages, so
   ReplayGain, lyrics, ratings and other apps' tags are kept. Tick *Rename files* (and optionally
   *move them into my music folder*) to apply your naming template too — you're shown a preview first.

### Converting files

The **Convert** tab turns music you already have into another format, such as FLAC to MP3 or Opus for
your phone. Drop in files or folders, choose **Convert to**, and save **next to the originals** or **in
a folder** (subfolders are kept, so `Album/01.flac` becomes `folder/Album/01.mp3`). Tags and cover art
are copied across, 24-bit audio stays 24-bit for lossless formats, and files that already exist are
skipped unless you tick *Replace*. Your originals are never changed. **Encoder options…** uses the same
settings as ripping. Reading MP3/AAC/Opus/Vorbis files needs ffmpeg.

### Cover art only

To just add artwork without changing any tags: pick a cover for an album (click the cover box, or
right-click it to load a file or use an image already in the folder) and press **Save Cover Only**.
**Add Covers to All…** in the toolbar does every album in the list at once, optionally skipping albums
that already have artwork. Right-click any cover and choose **Edit / crop…** to crop, stretch or fit it to a square, rotate it,
adjust brightness, contrast, saturation and sharpness, fix a faded scan with Auto levels, or resize it.

### Renaming files in bulk

**Rename Files…** (Tag Files toolbar) renames files on disk for the selected album or everything in
the list, with a live old → new preview:

- **Name from tags** – e.g. `%tracknumber% - %title%` rebuilds every name from the file's tags.
- **Find and replace** – plain text or a regular expression (`^(\d+) - (.+)$` → `\2 (\1)`).

Clashes are flagged and skipped, and swaps (A→B, B→A) are handled safely.

### File naming

Templates use `%field%` placeholders and `/` for folders. Anything in `[brackets]` disappears if a
field inside it is empty, so `[%disc%-]%tracknumber%` gives `1-03` on multi-disc sets and `03` otherwise.

Default: `%albumartist%/%album% [(%year%)]/[%disc%-]%tracknumber% - %title%`

Fields: `albumartist artist album title tracknumber totaltracks year date genre disc totaldiscs label
catalog composer country discsubtitle`. Common alternatives work too (`%artists%`, `%track%`,
`%discnumber%`, …); anything Spindle doesn't recognise is left out and flagged in the Settings preview.

When you pick more than one format, lossy copies go in their own tree (e.g. `MP3/Artist/Album/…`) so
they don't mix with the lossless files.

## Look

Spindle starts in a retro skin: **Modern Night** (Winamp Modern, darker) on Linux and **Amp Classic**
(Winamp 2) on Windows. Options ▸ Skin also has **Luna Midnight** (Windows XP after dark) and **Olive
Noir**, and **System**, which follows your desktop: Breeze or Adwaita on Linux, light or dark (it
switches when Windows does) on Windows. On Windows the title bar takes the skin's colours (Windows 11)
or its dark/light mode (Windows 10).

## Plugins

Drop a `.py` file into `~/.local/share/spindle/plugins/`. Any class that subclasses
`spindle.metadata.base.MetadataProvider` or `CoverProvider` is picked up on start:

```python
from spindle.metadata.base import MetadataProvider, AlbumMeta, TrackMeta

class MySource(MetadataProvider):
    name = "My Source"
    settings = {"api_key": ("API key", "")}      # shown in Settings ▸ Plugins

    def lookup_disc(self, toc):                   # exact disc lookup (toc.musicbrainz_id(), toc.freedb_id(), …)
        return []
    def search(self, artist, album, toc):         # free-text fallback
        return []
```

`plugins/discogs.py` is a complete example (Discogs search + cover art; needs a free personal token).
Enable, disable and reorder sources in Settings ▸ Metadata / Cover art.

## Command line

`spindle-cli` rips with your saved settings: `spindle-cli -f flac -f opus`, `spindle-cli --list`
(show matches only), `spindle-cli --image`, `spindle-cli --release 2`.

## Settings and files

Settings: `~/.config/spindle/config.json`. Program: `~/.local/share/spindle/`. Temporary rip files:
`~/.cache/spindle/work/` (so a music folder on a network share only receives finished files). `./uninstall.sh`
removes the program and keeps your settings and plugins.

## Versions

Linux and Windows have their own version numbers and changelogs: [CHANGELOG.md](CHANGELOG.md) for
Linux and [CHANGELOG-windows.md](CHANGELOG-windows.md) for Windows.

## Running the tests

```sh
python3 tests/test_engine.py      # disc IDs, AccurateRip, naming, simulated rips (no disc needed)
python3 tests/test_tagging.py     # tags + artwork round-trip in every format (needs mutagen, ffmpeg)
python3 tests/test_tagfiles.py    # Tag Files matching, saving and batch rename
python3 tests/test_imageadjust.py # cover editor colour adjustments
python3 tests/test_updater.py     # update checks and installing (no network needed)
python3 tests/test_convert.py     # Convert tab: every format, tags + cover, 24-bit, safety
python3 tests/test_windrive.py    # Windows drive: TOC, offsets, re-reads, cache flush, full rip (any OS)
python3 tests/test_crashlog.py    # the log catches hard crashes and notices an unclean exit
python3 tests/test_winstyle.py    # Windows look: title bar colours, System theme dark/light
python3 tests/test_workers.py     # background tasks: every result exactly once, even with nested dialogs
```

## Requirements

Python 3.9+, PySide6, mutagen, numpy (installed by `install.sh`); `cdparanoia` (or libcdio's
`cd-paranoia`); `flac`, `lame`, `opus-tools`, `vorbis-tools`, `wavpack` and/or `ffmpeg` for encoding and
ReplayGain. Pillow is optional (shrinks the embedded copy of large cover images).

## License

Spindle is free software, released under the [GNU General Public License v3.0](LICENSE).

## Not (yet) supported

Hidden track one audio (HTOA) and pre-gap detection, and CD-Text reading.
