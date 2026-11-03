# Spindle for Linux - changelog

Spindle for Linux has its own version numbers. Windows changes are in
[CHANGELOG-windows.md](CHANGELOG-windows.md) (versions 1.14.0-1.15.0 below were shared with Windows).

## 1.17.2
- Fixed a crash while loading release details after choosing a release ("heap corruption" on
  Windows): Python's memory clean-up could run on a background thread and free parts of a window
  that had just closed. Clean-up now always happens on the main thread

## 1.17.1
- A track with sectors that couldn't be read is no longer marked *Success* just because two reads
  agree (identical reads of a bad spot prove nothing); it's marked as failed unless AccurateRip
  confirms the audio. A track AccurateRip confirms is now always a success, even if the drive had to
  retry some sectors
- GnuDB lookups no longer send your computer's name
- The cover editor's picture area now uses the skin's colours instead of white

## 1.17.0
- **Set up your drive**: if your drive's read offset isn't set yet, Spindle offers to look it up in
  AccurateRip's drive list for you (or to measure it with a key disc), at start and before a rip.
  Without it AccurateRip can't confirm your rips. *Don't ask again* turns it off for that drive
- The "Searching…" and "Loading…" boxes now use the skin's colours instead of a white box with
  hard-to-read text
- Fixed crashes when an online lookup finished while a window was open: choosing or cancelling in
  *Choose release*, the update window, offset detection, and sometimes right at start-up. Background
  work now hands its results back in a way that can't crash

## 1.16.0
- **Modern Night** is now the default look. If you were on the System theme, Spindle switches to Modern
  Night once after this update; a skin you picked yourself is kept. *Options ▸ Skin ▸ System* still
  follows your desktop (KDE/GNOME, light or dark)

## 1.15.1
- Spindle now keeps a log (`~/.local/share/spindle/spindle.log`): if it ever closes unexpectedly, it
  tells you next time and shows where the details are. **Help ▸ Open log folder** opens it
- Fixed a possible crash after choosing a release in the *Choose release* window

## 1.15.0
- **CD ripping on Windows**: Spindle now finds your drive, reads discs, rips, plays CDs and ejects on
  Windows, with nothing extra to install. Bad spots are re-read sector by sector; AccurateRip and
  Test & copy check every track, as on Linux. Drive offsets are looked up by model as usual. This is
  new, so please report anything odd (the rip log has the details)

## 1.14.2
- Windows: no more "cdparanoia not found" message (that's a Linux tool). Spindle now says that CD
  ripping on Windows is coming in a later version

## 1.14.1
- Fixed several Spindle windows opening after an update: the old window now closes properly when
  Spindle restarts, and pressing *Restart now* more than once only starts one new Spindle

## 1.14.0
- **Spindle for Windows** (first stage): a normal installer (`SpindleSetup-1.14.0.exe`) and a portable
  zip, on the GitHub releases page. Tag Files and Convert work fully; ffmpeg is included, so nothing
  else needs installing. CD ripping on Windows comes in a later version. Updates install themselves
  from inside Spindle, like on Linux
- Settings on Windows are kept in `%APPDATA%\Spindle`, so they survive updates and reinstalling

## 1.13.0
- New **Convert** tab: turn music you already have into another format (FLAC, ALAC, WavPack, WAV, MP3,
  Opus, Ogg Vorbis, AAC). Drop in files or folders, pick a format, and save next to the originals or
  into another folder (subfolders are kept). Tags and cover art are copied, 24-bit audio stays 24-bit
  for lossless formats, and your original files are never changed. Uses the same encoder settings as
  ripping

## 1.12.0
- **Retry failed** button: when tracks fail, rip just those again. Their new files replace the old
  ones, and the log, cue sheet, playlist and album ReplayGain are redone for the whole album. Also
  *Retry track N* when you right-click a failed track
- Fixed rips stopping with "Operation not permitted" when the music folder is on a network share
  (sshfs, SMB, NFS): the temporary WAV files now stay on your own disk (`~/.cache/spindle/work`) and
  only the finished files are written to the music folder. Faster on network shares and USB drives too

## 1.11.1
- The Settings button is always available, also while a disc is being read or looked up (the
  "please wait" shading no longer covers the button column)

## 1.11.0
- Settings and **Open folder** stay available during a rip. Open folder shows the album folder being
  written (before your first rip, your music folder)
- While a rip is running, the settings it uses (drive, read offset, read mode, output and formats) are
  greyed out until it finishes; sound, eject and auto-rip options can still be changed
- When the drive's tray is open, the **Eject** button becomes **Close** and closes it (Ctrl+E too)
- Greyed-out controls now look greyed out in the retro skins as well

## 1.10.0
- Settings ▸ Updates has a **Check for updates now** button. It checks the repository typed in the
  box (no need to save first) and offers **Update…** when there's a new version

## 1.9.0
- Updates: Spindle checks GitHub for a new version when it starts and shows a bar at the top of the
  window; **Update…** shows what's new, downloads it, reinstalls in place (settings, plugins and drive
  offsets are kept) and restarts. Also Help ▸ Check for updates…, and Skip this version
- The installer remembers which GitHub repository it was installed from; change it or turn off the
  startup check in Settings ▸ Updates
- The installer swaps in the new program files only after copying them, so a failed copy can't break
  an existing install

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
