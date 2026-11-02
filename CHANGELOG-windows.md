# Spindle for Windows - changelog

Spindle for Windows has its own version numbers. Linux changes are in [CHANGELOG.md](CHANGELOG.md).

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
