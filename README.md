# Spindle

An all-in-one, EAC-style secure CD ripper for Linux. Put a disc in, and Spindle reads it,
looks it up, finds the cover, rips it securely, verifies it against AccurateRip, encodes it to as
many formats as you like, tags everything and writes a log — in one window. It also plays CDs,
and its **Tag Files** tab fixes tags, artwork and file names for music you already have.

## Install

```sh
tar xzf spindle-1.0.0.tar.gz && cd spindle
./install.sh
```

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

Every drive model reads audio a few samples early or late. Open **Settings ▸ Drive** and press
**Look up in AccurateRip** — Spindle finds your model in the AccurateRip offset list and remembers it.
Without the right offset, AccurateRip will report "Not accurate" even for perfect rips.

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

By default Spindle follows your desktop theme (Breeze on KDE, Adwaita on GNOME, light or dark).
Prefer something retro? Options ▸ Skin has **Modern Night** (Winamp Modern, darker),
**Luna Midnight** (Windows XP after dark), **Olive Noir** and **Amp Classic** (Winamp 2).

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

Settings: `~/.config/spindle/config.json`. Program: `~/.local/share/spindle/`. `./uninstall.sh`
removes the program and keeps your settings and plugins.

## Running the tests

```sh
python3 tests/test_engine.py      # disc IDs, AccurateRip, naming, simulated rips (no disc needed)
python3 tests/test_tagging.py     # tags + artwork round-trip in every format (needs mutagen, ffmpeg)
python3 tests/test_tagfiles.py    # Tag Files matching, saving and batch rename
```

## Requirements

Python 3.9+, PySide6, mutagen, numpy (installed by `install.sh`); `cdparanoia` (or libcdio's
`cd-paranoia`); `flac`, `lame`, `opus-tools`, `vorbis-tools`, `wavpack` and/or `ffmpeg` for encoding and
ReplayGain. Pillow is optional (shrinks the embedded copy of large cover images).

## Not (yet) supported

Hidden track one audio (HTOA) and pre-gap detection, CD-Text reading, and AccurateRip offset
detection from a key disc (the offset comes from the AccurateRip drive list instead).
