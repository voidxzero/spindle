# Spindle for Windows - changelog

Spindle for Windows has its own version numbers. Linux changes are in [CHANGELOG.md](CHANGELOG.md).

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
- The title bar now matches Spindle's look: it takes the skin's colours on Windows 11, and follows dark
  or light on Windows 10. Dialogs too
- The **System** theme follows Windows' dark or light mode, and switches straight away when you change
  it in Windows Settings
- **Amp Classic** is now the default look. If you were on the System theme, Spindle switches to Amp
  Classic once after this update; a skin you picked yourself is kept (*Options ▸ Skin*)

## 1.15.1
- Spindle now keeps a log (`%LOCALAPPDATA%\Spindle\spindle.log`): if it ever closes unexpectedly, it
  tells you next time and shows where the details are. **Help ▸ Open log folder** opens it
- Fixed a possible crash after choosing a release in the *Choose release* window

## 1.15.0
- **CD ripping**: Spindle finds your drive, reads discs, rips, plays CDs and ejects, with nothing extra
  to install. Bad spots are re-read sector by sector; AccurateRip and Test & copy check every track.
  Drive offsets are looked up by model as usual

## 1.14.2
- No more "cdparanoia not found" message (that's a Linux tool)

## 1.14.1
- Fixed several Spindle windows opening after an update

## 1.14.0
- First Windows version: a normal installer (`SpindleSetup-x.y.z.exe`) and a portable zip on the
  GitHub releases page, with ffmpeg included. Tag Files and Convert work fully. Updates install
  themselves from inside Spindle
- Settings are kept in `%APPDATA%\Spindle`, so they survive updates and reinstalling
