# Changelog

## 1.8.0
- A chime plays when a rip finishes (a lower warning tone if any track had problems or the rip
  failed). Turn it off, pick your own sound or set the volume in Settings ▸ Drive

## 1.7.1
- Removing the disc (or opening the tray) now clears its details, track list, cover art and
  now-playing info, and cancels a pending auto-rip; the log and Open folder are kept
- A cover search that finishes after the disc was swapped no longer lands on the new disc
- README: key-disc offset detection is no longer listed as unsupported

## 1.7.0
- Detect the drive's read offset with a key disc (any CD in the AccurateRip database), the same
  frame-450 method EAC and CUETools use - works for drives missing from AccurateRip's list
- Image button now sits above Rip in the sidebar

## 1.6.0
- Cover editor: brightness, contrast, saturation and sharpen sliders, plus Auto levels for faded
  scans - shown live on the picture and in the result preview
- Spectrum analyser moved to the far right of the player bar

## 1.5.2
- Cover editor: Shape option - crop to square, stretch to square, fit in square (blurred picture,
  edge colour, black or white fill) or keep shape - with a live preview of the result

## 1.5.1
- Spectrum analyser framed like the other display boxes (LCD bezel in the skins, rounded border in
  the System theme); peak caps readable in light themes

## 1.5.0
- Optional automatic ripping when a disc is inserted (Settings ▸ Drive), with a 5-second countdown you
  can cancel; uses the best-matching release, and waits for you if nothing matches

## 1.4.2
- Seek bar: click anywhere to jump there, hold and drag to scrub, hover to see the time
- Elapsed time on the left of the seek bar, track length on the right

## 1.4.1
- Track checkboxes are drawn by Spindle itself, so they show on every row in every skin
  (Qt's style sheets skipped them on alternating rows)
- Loading overlays always fill the area they cover (the cover art window showed it half-size)

## 1.4.0
- Cover art editor: crop (free or square, drag to move/resize), rotate, trim plain borders, resize
- Tag Files: **Save Cover Only** and **Add Covers to All…** embed artwork without touching any other tag
- Right-click a cover for Find / Edit / Load from file / Use image from the album folder
- Loading spinners in the cover art window (with per-image placeholders), release search and offset lookup
- Unticked checkboxes now show in every skin

## 1.3.2
- Glossy, full-colour Windows XP-style icons in the skins (flat icons stay in the System theme)
- Spectrum analyser now runs from the first frame and stays in sync with deeply buffered audio
- Checkboxes and radio buttons clearly visible in every skin

## 1.3.0
- Instant, gapless CD playback read straight from the drive; smooth seeking
- Click, double-click, Enter or right-click to play a track; ▶ marks the playing track
- Mini Winamp-style spectrum analyser
- Page greys out with a progress card while reading the disc or searching databases

## 1.2.0
- Tag Files: batch **Rename Files…** (from tags, or find & replace with regex) with live preview
- Naming templates accept common alternatives (`%artists%`, `%track%`, …) and never write unknown fields literally
- Fixed-width now-playing label so the seek bar no longer jumps

## 1.1.x
- Follows the desktop theme by default; retro skins optional
- Tag Files tab for fixing existing music (MusicBrainz matching, AcoustID, cover art, preview of changes)
- CD playback, green ✓ / red ✗ status badges, progress bars that only move forward
- App icon, consistent control sizing and spacing, editing locked while ripping

## 1.0.0
- Secure ripping with cdparanoia, AccurateRip v1/v2 verification and test & copy
- MusicBrainz and GnuDB metadata, plugin system (Discogs example)
- Automatic cover art (Cover Art Archive, iTunes, Deezer)
- FLAC, ALAC, WavPack, WAV, MP3, Opus, Vorbis and AAC with full tags, ReplayGain, log, cue and playlist
