"""Audio CD playback: one continuous PCM stream from the drive -> Qt Multimedia audio sink.

The stream runs from where you press play to the end of the disc, so track changes are gapless;
the current track is worked out from the playback position. Seeking just reopens the stream at a
new sector, which is instant with direct drive reads.
"""
from __future__ import annotations

import queue
import threading

from PySide6.QtCore import QObject, QTimer, Signal

try:
    from PySide6.QtMultimedia import QAudioFormat, QAudioSink, QMediaDevices
    HAVE_AUDIO = True
except ImportError:          # distro split packages without QtMultimedia
    HAVE_AUDIO = False

from ..disc import BYTES_PER_FRAME, TOC
from ..ripper import Drive, PCMStream

BYTES_PER_SEC = 44100 * 4
PREBUFFER = BYTES_PER_SEC // 4          # start the sink once 1/4 s is ready: quick but no stutter


class _Reader(threading.Thread):
    """Pulls PCM from the drive into a bounded queue (~4 s ahead); the drive pauses when it's full."""

    def __init__(self, stream: PCMStream):
        super().__init__(daemon=True)
        self.stream = stream
        self.q: queue.Queue[bytes] = queue.Queue(maxsize=12)
        self.stop = threading.Event()
        self.eof = False
        self.error = ""

    def run(self):
        try:
            while not self.stop.is_set():
                data = self.stream.read(BYTES_PER_FRAME * 25)
                if not data:
                    break
                while not self.stop.is_set():
                    try:
                        self.q.put(data, timeout=0.2)
                        break
                    except queue.Full:
                        continue
        except Exception as e:  # noqa: BLE001
            self.error = str(e)
        finally:
            self.eof = True

    def close(self):
        self.stop.set()
        threading.Thread(target=self.stream.close, daemon=True).start()   # never block the GUI


class CDPlayer(QObject):
    position = Signal(float, float)       # seconds into the current track, track length
    track_changed = Signal(int)
    state_changed = Signal(str)           # playing | paused | stopped | buffering
    error = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.drive: Drive | None = None
        self.toc: TOC | None = None
        self.track = 0
        self.state = "stopped"
        self.volume = 0.8
        self._sink = None
        self._io = None
        self._reader: _Reader | None = None
        self._pending = b""
        self._buffered = b""             # held back until PREBUFFER is reached
        self._base = 0                   # absolute sector where the current stream starts
        self._started = False
        self._ring = bytearray()         # recent audio handed to the sink (for the visualiser)
        self._written = 0                # bytes handed to the sink since this stream started
        self._timer = QTimer(self)
        self._timer.setInterval(20)
        self._timer.timeout.connect(self._pump)

    @property
    def available(self) -> bool:
        return HAVE_AUDIO

    def set_disc(self, drive: Drive | None, toc: TOC | None):
        self.stop()
        self.drive, self.toc = drive, toc
        self.track = 0

    def length(self, n: int | None = None) -> float:
        if not self.toc:
            return 0.0
        try:
            return self.toc.track(n or self.track).seconds
        except KeyError:
            return 0.0

    # ------------------------------------------------------------ helpers
    def _audio(self):
        return self.toc.audio_tracks if self.toc else []

    def _track_at(self, lba: int) -> int:
        for t in self._audio():
            if t.start <= lba <= t.end:
                return t.number
        return self.track

    def _abs_sector(self) -> float:
        if not self._sink or not self._started:
            return float(self._base)
        return self._base + self._sink.processedUSecs() / 1e6 * 75

    def elapsed(self) -> float:
        if not self.toc or not self.track:
            return 0.0
        return max(0.0, (self._abs_sector() - self.toc.track(self.track).start) / 75)

    # ---------------------------------------------------------- transport
    def play(self, track: int, start: float = 0.0):
        if not HAVE_AUDIO:
            self.error.emit("Playback needs Qt Multimedia (Fedora: sudo dnf install qt6-qtmultimedia)")
            return
        if not self.drive or not self.toc or not self._audio():
            return
        t = self.toc.track(track)
        start_lba = t.start + int(max(0.0, min(start, t.seconds - 0.5)) * 75)
        self._open(start_lba)
        if self.track != track:
            self.track = track
        self.track_changed.emit(track)

    def _open(self, start_lba: int):
        self._teardown()
        end_lba = self._audio()[-1].end
        try:
            stream = self.drive.open_stream(start_lba, end_lba)
        except Exception as e:  # noqa: BLE001
            self.error.emit(str(e))
            self._set_state("stopped")
            return
        self._base = start_lba
        self._reader = _Reader(stream)
        self._reader.start()
        fmt = QAudioFormat()
        fmt.setSampleRate(44100)
        fmt.setChannelCount(2)
        fmt.setSampleFormat(QAudioFormat.Int16)
        self._sink = QAudioSink(QMediaDevices.defaultAudioOutput(), fmt, self)
        self._sink.setBufferSize(BYTES_PER_SEC // 2)       # 0.5 s device buffer rides out hiccups
        self._sink.setVolume(self.volume)
        self._io = None
        self._started = False
        self._pending = self._buffered = b""
        self._timer.start()
        self._set_state("buffering")

    def toggle(self, track_if_stopped: int = 1):
        if self.state in ("playing", "buffering"):
            self.pause()
        elif self.state == "paused":
            self.resume()
        else:
            self.play(self.track or track_if_stopped)

    def pause(self):
        if self._sink and self.state in ("playing", "buffering"):
            if self._started:
                self._sink.suspend()
            self._set_state("paused")

    def resume(self):
        if self._sink and self.state == "paused":
            if self._started:
                self._sink.resume()
            self._set_state("playing" if self._started else "buffering")

    def stop(self):
        self._teardown()
        if self.state != "stopped":
            self._set_state("stopped")
        self.position.emit(0.0, self.length())

    def seek(self, seconds: float):
        if not self.track or self.state == "stopped":
            return
        was_paused = self.state == "paused"
        self.play(self.track, seconds)
        if was_paused:
            self.pause()

    def next(self):
        nums = [t.number for t in self._audio()]
        if self.track in nums and nums.index(self.track) + 1 < len(nums):
            self.play(nums[nums.index(self.track) + 1])
        else:
            self.stop()

    def previous(self):
        nums = [t.number for t in self._audio()]
        if self.elapsed() > 3 or self.track not in nums or nums.index(self.track) == 0:
            self.play(self.track or (nums[0] if nums else 1))
        else:
            self.play(nums[nums.index(self.track) - 1])

    def set_volume(self, v: float):
        self.volume = max(0.0, min(1.0, v))
        if self._sink:
            self._sink.setVolume(self.volume)

    # ------------------------------------------------------------ internals
    def _set_state(self, s: str):
        if s != self.state:
            self.state = s
            self.state_changed.emit(s)

    def _take(self) -> bytes:
        if self._pending:
            return self._pending
        try:
            self._pending = self._reader.q.get_nowait()
        except queue.Empty:
            self._pending = b""
        return self._pending

    def _pump(self):
        if not self._sink or not self._reader:
            return
        if self._reader.error and not self._started and self._reader.eof:
            self.error.emit(f"Can't read the disc: {self._reader.error}")
            self.stop()
            return
        if not self._started:
            # gather a small prebuffer first so playback starts cleanly, then start the sink
            while len(self._buffered) < PREBUFFER and self._take():
                self._buffered += self._pending
                self._pending = b""
            if len(self._buffered) >= PREBUFFER or (self._reader.eof and self._buffered):
                self._io = self._sink.start()
                self._started = True
                self._pending = self._buffered
                self._buffered = b""
                if self.state == "paused":
                    self._sink.suspend()
                else:
                    self._set_state("playing")
            else:
                return
        if self.state != "paused":
            free = self._sink.bytesFree()
            while free > 0 and self._take():
                n = self._io.write(self._pending[:free])
                if n <= 0:
                    break
                self._ring += self._pending[:n]
                self._written += n
                # keep 10 s: some audio servers (PipeWire/Pulse) buffer several seconds ahead
                if len(self._ring) > BYTES_PER_SEC * 10:
                    del self._ring[: len(self._ring) - BYTES_PER_SEC * 10]
                self._pending = self._pending[n:]
                free -= n
        # which track are we in? (gapless: the stream simply runs on into the next one)
        now = self._track_at(int(self._abs_sector()))
        if now != self.track:
            self.track = now
            self.track_changed.emit(now)
        self.position.emit(min(self.elapsed(), self.length()), self.length())
        # end of disc: reader done, everything handed over, and the device buffer has drained
        if (self._reader.eof and self._reader.q.empty() and not self._pending and self.state == "playing"
                and getattr(self._sink.state(), "name", "") == "IdleState"):
            self.stop()

    def now_playing_pcm(self, nbytes: int) -> bytes:
        """The audio that is coming out of the speakers right now (for the visualiser)."""
        if not self._sink or not self._started or self.state != "playing" or not self._ring:
            return b""
        played = int(self._sink.processedUSecs() / 1e6 * BYTES_PER_SEC) // 4 * 4
        ring_start = self._written - len(self._ring)          # absolute byte offset of ring[0]
        # the device reports nothing played yet in the first moments: show the start of the stream
        end = max(played, min(self._written, nbytes)) - ring_start
        end = max(min(end, len(self._ring)), min(nbytes, len(self._ring)))
        start = max(0, end - nbytes)
        return bytes(self._ring[start:end])

    def _teardown(self):
        self._timer.stop()
        self._ring = bytearray()
        self._written = 0
        if self._sink:
            self._sink.stop()
            self._sink.deleteLater()
            self._sink = None
            self._io = None
        if self._reader:
            self._reader.close()
            self._reader = None
        self._pending = self._buffered = b""
        self._started = False
