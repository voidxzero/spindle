"""The rip job: extract -> verify -> encode -> tag -> extras. Toolkit-independent."""
from __future__ import annotations

import concurrent.futures as cf
import os
import shutil
import tempfile
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

from . import accuraterip, coverart, encoders, extras, naming, system, tagger
from .disc import BYTES_PER_FRAME, TOC
from .metadata.base import AlbumMeta, CoverCandidate
from .ripper import Cancelled, Drive, RipResult, wav_pcm


class Reporter:
    """Override in a front-end. All methods may be called from worker threads."""
    def log(self, msg: str) -> None: ...
    def track_status(self, n: int, text: str) -> None: ...
    def track_progress(self, n: int, frac: float) -> None: ...
    def overall(self, frac: float, text: str = "") -> None: ...
    def track_done(self, n: int, result: "TrackResult") -> None: ...


@dataclass
class TrackResult:
    number: int
    wav: str = ""
    copy_crc: int = 0
    test_crc: int | None = None
    peak: float = 0.0
    corrections: int = 0
    errors: int = 0
    suspicious: list[int] = field(default_factory=list)
    ar: accuraterip.TrackVerdict | None = None
    ar_v1: int = 0
    ar_v2: int = 0
    files: list[str] = field(default_factory=list)
    by_format: dict[str, str] = field(default_factory=dict)
    rg_gain: str = ""
    verdict: str = ""
    good: bool = False

    @property
    def crc_match(self) -> bool:
        return self.test_crc is None or self.test_crc == self.copy_crc


class RipJob:
    def __init__(self, cfg, drive: Drive, toc: TOC, album: AlbumMeta, tracks: list[int],
                 cover: CoverCandidate | None, reporter: Reporter | None = None,
                 retry_of: "RipJob | None" = None):
        self.cfg, self.drive, self.toc, self.album = cfg, drive, toc, album
        self.track_order = [n for n in tracks if toc.track(n).audio]
        # Retrying tracks of an earlier rip: their new files replace the old ones, and the log, cue
        # sheet, playlist and album ReplayGain are rebuilt for the whole album, not just these tracks.
        self.retry_of = retry_of
        if retry_of is not None:
            cfg["output"]["overwrite"] = True
        self.cover = cover
        self.rep = reporter or Reporter()
        self.cancel = threading.Event()
        self.results: dict[int, TrackResult] = {}
        self.ar_note = ""
        self.album_gain = ""
        self.output_dir = ""
        self.log_path = ""
        self.errors: list[str] = []
        d = cfg["drive"]
        self.offset = int(d.get("read_offset", 0))
        self.backend_name = getattr(drive, "binary", None) or drive.__class__.__name__
        self.formats = [f for f in cfg["output"]["formats"] if f in encoders.FORMATS]
        self.format_descriptions = [encoders.describe(f, cfg["encoders"].get(f, {})) for f in self.formats]
        self._ar: list[accuraterip.ARResponse] | None = None
        self._ar_future = None
        self._ar_failed = False
        self._overall_max = 0.0
        self._embed: bytes | None = None
        self._pool: cf.ThreadPoolExecutor | None = None
        self._enc_futures: list[tuple[int | None, str, cf.Future]] = []
        self._image_files: list[str] = []
        self._image_by_format: dict[str, str] = {}
        self._image_wav = ""

    # ------------------------------------------------------------------ helpers
    def _work_root(self, out_root: str) -> str:
        """Where the temporary WAVs go: local disk (~/.cache/spindle/work), so ripping and re-reading
        never depend on a network share or USB drive staying responsive. Only the finished files are
        written to the output folder. Falls back to the output folder if local disk is short of space."""
        sectors = sum(self.toc.track(n).sectors for n in self.track_order)
        passes = 2 + int(self.cfg["drive"].get("retry_on_mismatch", 2))     # copies that can coexist
        need = sectors * BYTES_PER_FRAME * passes + 200 * 1024 * 1024
        root = system.cache_dir() / "work"
        try:
            root.mkdir(parents=True, exist_ok=True)
            for old in root.glob(".spindle-*"):             # left behind by a crash or power cut
                if time.time() - old.stat().st_mtime > 24 * 3600:
                    shutil.rmtree(old, ignore_errors=True)
            if shutil.disk_usage(root).free > need:
                return str(root)
            self.rep.log("Not enough local disk space for the temporary files - using the output folder.")
        except OSError as e:
            self.rep.log(f"Couldn't use {root} for temporary files ({e}) - using the output folder.")
        return out_root

    def _ar_db(self) -> list[accuraterip.ARResponse]:
        if self._ar is None:
            try:
                self._ar = self._ar_future.result(timeout=30) if self._ar_future else []
            except Exception as e:  # noqa: BLE001
                self.rep.log(f"AccurateRip lookup failed: {e}")
                self._ar = []
                self._ar_failed = True
        return self._ar

    def _path_for(self, fmt: str, n: int | None) -> str:
        out = self.cfg["output"]
        total = len(self.toc.audio_tracks)
        if n is None:
            tmpl = out["image_template"]
            vals = naming.values(self.album, None, total)
        else:
            tmpl = out["va_template"] if self.album.various else out["template"]
            vals = naming.values(self.album, self.album.track(n), total)
        rel = naming.render(tmpl, vals, out.get("safe_filenames", True))
        if len(self.formats) > 1 and not encoders.FORMATS[fmt].lossless:
            # keep lossy copies in their own folder tree when ripping to several formats
            rel = f"{encoders.FORMATS[fmt].label.split()[0]}/{rel}"
        # templates use "/" for folders; normpath turns them into "\\" on Windows
        return os.path.normpath(os.path.join(os.path.expanduser(out["directory"]),
                                             rel + "." + encoders.FORMATS[fmt].ext))

    def _tags(self, n: int | None) -> dict[str, str]:
        track = self.album.track(n) if n is not None else None
        return tagger.tag_values(self.album, track, len(self.toc.audio_tracks),
                                 self.cfg["metadata"].get("write_mb_ids", True))

    def _encode_and_tag(self, fmt: str, wav: str, dst: str, n: int | None, extra_tags: dict | None = None):
        if os.path.exists(dst) and not self.cfg["output"].get("overwrite"):
            base, ext = os.path.splitext(dst)
            i = 2
            while os.path.exists(f"{base} ({i}){ext}"):
                i += 1
            dst = f"{base} ({i}){ext}"
        encoders.encode(fmt, self.cfg["encoders"].get(fmt, {}), wav, dst)
        tags = self._tags(n)
        tags.update(extra_tags or {})
        tagger.write(dst, tags, self._embed if self.cfg["coverart"].get("embed", True) else None,
                     id3v23=self.cfg["encoders"]["mp3"].get("id3v23", False))
        return dst

    def _overall(self, frac: float, text: str = "") -> None:
        """Overall progress that only ever moves forward."""
        if frac >= self._overall_max or text:
            self._overall_max = max(self._overall_max, frac)
            self.rep.overall(self._overall_max, text)

    def _single_pass(self, first: int, last: int, out: str, label: str, n_for_ui: int,
                     done_sectors: int, total_sectors: int, span_sectors: int,
                     pass_no: int = 0) -> RipResult:
        d = self.cfg["drive"]
        self.rep.track_status(n_for_ui, label)
        # with test & copy each track normally takes two passes; give each its share of the bar
        passes = 2 if d.get("test_and_copy", True) else 1
        share = min(pass_no, passes - 1)
        last_f = [0.0]

        def prog(f):
            if f < last_f[0]:
                return
            last_f[0] = f
            self.rep.track_progress(n_for_ui, f)
            self._overall((done_sectors + span_sectors * (share + f) / passes) / max(1, total_sectors))
        return self.drive.rip(first, last, out, self.offset, d["paranoia"], int(d["max_retries"]),
                              prog, self.cancel)

    def _secure_rip(self, first: int, last: int, work: str, tag: str, n_ui: int,
                    done: int, total: int, ar_check) -> TrackResult:
        """Copy, verify against AccurateRip, and fall back to test & copy when unverifiable."""
        d = self.cfg["drive"]
        span = sum(self.toc.track(i).sectors for i in range(first, last + 1) if self.toc.track(i).audio)
        res = TrackResult(n_ui)
        copy_path = os.path.join(work, f"{tag}.copy1.wav")
        r = self._single_pass(first, last, copy_path, "Copying", n_ui, done, total, span, 0)
        res.copy_crc, res.peak, res.corrections, res.errors = r.crc, r.peak, r.corrections, r.errors
        res.suspicious = list(r.suspicious)
        res.wav = copy_path
        if ar_check and ar_check(res):
            # AccurateRip proves the audio is right, even if the drive had to retry some sectors
            res.verdict = ("Copy OK (verified by AccurateRip)" if res.errors == 0 else
                           "Copy OK (read errors were corrected; verified by AccurateRip)")
            res.good = True
            return res
        if not d.get("test_and_copy", True):
            res.verdict = "Copy finished" if res.errors == 0 else "Copy finished - suspicious positions"
            res.good = res.errors == 0
            return res
        crcs = {r.crc: copy_path}
        passes = 1 + int(d.get("retry_on_mismatch", 2))
        for i in range(2, passes + 2):
            if self.cancel.is_set():
                raise Cancelled()
            p = os.path.join(work, f"{tag}.copy{i}.wav")
            r2 = self._single_pass(first, last, p, "Testing" if i == 2 else f"Re-reading ({i})",
                                   n_ui, done, total, span, i - 1)
            res.corrections += r2.corrections
            res.errors = min(res.errors, r2.errors) if r2.crc in crcs else max(res.errors, r2.errors)
            if r2.crc in crcs:
                res.test_crc = res.copy_crc = r2.crc
                res.wav = crcs[r2.crc]
                os.remove(p)
                break
            crcs[r2.crc] = p
            res.test_crc = r2.crc
        else:
            res.verdict = "CRC mismatch - copy may contain errors"
            res.good = False
            return res
        for c, path in crcs.items():
            if path != res.wav and os.path.exists(path):
                os.remove(path)
        if ar_check:
            ar_check(res)   # recompute AR with the agreed copy
        if res.errors == 0:
            res.verdict, res.good = "Copy OK", True
        elif res.ar and res.ar.status == "accurate":
            res.verdict, res.good = "Copy OK (read errors were corrected; verified by AccurateRip)", True
        else:
            # Both reads agree, but some sectors never read cleanly: identical reads of a bad spot
            # (e.g. the same silence filled in twice) prove nothing, so this isn't a success.
            res.verdict = (f"Reads agree, but {res.errors} sector(s) couldn't be read cleanly "
                           "- may contain errors")
            res.good = False
        return res

    # --------------------------------------------------------------------- run
    def run(self) -> bool:
        cfg = self.cfg
        if not self.formats:
            raise ValueError("No output format selected.")
        for f in self.formats:
            if encoders.backend(f) is None:
                raise ValueError(f"No encoder installed for {encoders.FORMATS[f].label}.")
        out_root = os.path.expanduser(cfg["output"]["directory"])
        os.makedirs(out_root, exist_ok=True)
        work = tempfile.mkdtemp(prefix=".spindle-", dir=self._work_root(out_root))
        side = cf.ThreadPoolExecutor(max_workers=2)
        self._pool = cf.ThreadPoolExecutor(max_workers=max(1, int(cfg["output"].get("parallel_encoders", 2))))
        try:
            self.rep.log(f"Ripping {len(self.track_order)} track(s) from {self.drive.name}, "
                         f"read offset {self.offset:+d}")
            self._ar_future = side.submit(accuraterip.lookup, self.toc)
            if self.cover is None and cfg["coverart"].get("auto_fetch", True):
                self.cover = side.submit(coverart.auto_pick, cfg, self.album, self.rep.log).result()
            if self.cover:
                coverart.download(self.cover)
                self._embed = coverart.for_embedding(self.cover.data, int(cfg["coverart"].get("embed_max_px", 0)))
            if cfg["output"].get("mode") == "image":
                self._run_image(work)
            else:
                self._run_tracks(work)
            cf.wait([f for _, _, f in self._enc_futures])
            self._collect_encodes()
            if self.cancel.is_set():
                raise Cancelled()
            self._finish()
            return all(r.good for r in self.results.values())
        except Cancelled:
            self.rep.log("Cancelled.")
            return False
        finally:
            self._pool.shutdown(wait=True, cancel_futures=True)
            side.shutdown(wait=False, cancel_futures=True)
            shutil.rmtree(work, ignore_errors=True)

    def _ar_checker(self, track_index_of):
        responses_ready = {"v": None}

        def check(res: TrackResult) -> bool:
            if responses_ready["v"] is None:
                responses_ready["v"] = self._ar_db()
            responses = responses_ready["v"]
            n = res.number
            idx = track_index_of(n)
            audio = self.toc.audio_tracks
            pcm = wav_pcm(res.wav)
            res.ar_v1, res.ar_v2 = accuraterip.checksums(pcm, idx == 0, idx == len(audio) - 1)
            res.ar = (accuraterip.TrackVerdict("unavailable") if self._ar_failed
                      else accuraterip.verify(idx, res.ar_v1, res.ar_v2, responses))
            return res.ar.status == "accurate"
        return check

    def _run_tracks(self, work: str):
        audio = self.toc.audio_tracks
        index = {t.number: i for i, t in enumerate(audio)}
        total = sum(self.toc.track(n).sectors for n in self.track_order)
        done = 0
        checker = self._ar_checker(lambda n: index[n])
        for n in self.track_order:
            if self.cancel.is_set():
                raise Cancelled()
            res = self._secure_rip(n, n, work, f"t{n:02d}", n, done, total, checker)
            self.results[n] = res
            done += self.toc.track(n).sectors
            status = res.ar.text() if res.ar and res.ar.status == "accurate" else res.verdict
            self.rep.track_status(n, "Encoding…")
            self.rep.log(f"Track {n:2d}: {res.verdict}  CRC {res.copy_crc:08X}"
                         + (f"  AR: {res.ar.text()}" if res.ar else ""))
            for fmt in self.formats:
                dst = self._path_for(fmt, n)
                fut = self._pool.submit(self._encode_and_tag, fmt, res.wav, dst, n)
                fut.add_done_callback(self._encoded_cb(n, status))
                self._enc_futures.append((n, fmt, fut))
            self.rep.track_done(n, res)

    def _encoded_cb(self, n, status):
        """UI feedback only; results are collected after all encoders finish."""
        def cb(fut: cf.Future):
            if fut.cancelled():
                return
            if fut.exception():
                self.rep.log(f"Track {n}: encoding/tagging failed: {fut.exception()}")
                self.rep.track_status(n, "Encode failed")
            elif all(f.done() for m, _, f in self._enc_futures if m == n):
                self.rep.track_status(n, status)
        return cb

    def _collect_encodes(self):
        for n, fmt, fut in self._enc_futures:
            if fut.cancelled():
                continue
            err = fut.exception()
            if err:
                self.errors.append(f"Track {n}: {err}" if n is not None else str(err))
                if n is not None and n in self.results:
                    self.results[n].good = False
                    self.results[n].verdict += " - encoding failed"
                continue
            if n is None:
                self._image_files.append(fut.result())
                self._image_by_format[fmt] = fut.result()
            else:
                self.results[n].files.append(fut.result())
                self.results[n].by_format[fmt] = fut.result()

    def _run_image(self, work: str):
        audio = self.toc.audio_tracks
        first, last = audio[0].number, audio[-1].number
        self.track_order = [t.number for t in audio]
        total = sum(t.sectors for t in audio)

        def check_all(res: TrackResult) -> bool:
            responses = self._ar_db()
            pcm = wav_pcm(res.wav)
            all_ok = True
            for i, t in enumerate(audio):
                a = (t.start - audio[0].start) * BYTES_PER_FRAME
                b = a + t.sectors * BYTES_PER_FRAME
                v1, v2 = accuraterip.checksums(pcm[a:b], i == 0, i == len(audio) - 1)
                tr = self.results.setdefault(t.number, TrackResult(t.number))
                tr.ar_v1, tr.ar_v2 = v1, v2
                tr.ar = (accuraterip.TrackVerdict("unavailable") if self._ar_failed
                         else accuraterip.verify(i, v1, v2, responses))
                tr.peak = res.peak
                all_ok &= tr.ar.status == "accurate"
            return all_ok

        res = self._secure_rip(first, last, work, "image", first, 0, total, check_all)
        for t in audio:
            tr = self.results.setdefault(t.number, TrackResult(t.number))
            tr.copy_crc, tr.test_crc, tr.verdict, tr.good = res.copy_crc, res.test_crc, res.verdict, res.good
            self.rep.track_status(t.number, tr.ar.text() if tr.ar and tr.ar.status == "accurate" else res.verdict)
            self.rep.track_done(t.number, tr)
        self.rep.log(f"Image: {res.verdict}  CRC {res.copy_crc:08X}")
        self._image_wav = res.wav
        for fmt in self.formats:
            dst = self._path_for(fmt, None)
            fut = self._pool.submit(self._encode_and_tag, fmt, res.wav, dst, None,
                                    {"CUESHEET": extras.cue_sheet(self.toc, self.album, None, os.path.basename(dst))}
                                    if fmt == "flac" else None)
            self._enc_futures.append((None, fmt, fut))

    # ------------------------------------------------------------------ finish
    def _merge_retry(self):
        """Fold the earlier rip's other tracks back in, and drop the failed tracks' old files when
        the new ones landed somewhere else (e.g. the title was corrected before retrying)."""
        prev = self.retry_of
        retried = set(self.track_order)
        for n in retried:
            old, new = prev.results.get(n), self.results.get(n)
            if old and new and new.files:
                for f in old.files:
                    if f not in new.files and os.path.exists(f):
                        os.remove(f)
        for n in prev.track_order:
            if n not in retried and n in prev.results:
                self.results[n] = prev.results[n]
        index = {t.number: i for i, t in enumerate(self.toc.audio_tracks)}
        self.track_order = sorted(set(prev.track_order) | retried, key=lambda n: index.get(n, n))
        self.retry_of = None                    # merged; don't keep a chain of old jobs alive

    def _loudness_source(self, n: int) -> str:
        """Audio to measure for ReplayGain: the rip's WAV, or - for tracks kept from an earlier rip,
        whose WAVs are gone - the encoded file (lossless if there is one)."""
        r = self.results[n]
        if r.wav and os.path.exists(r.wav):
            return r.wav
        for fmt in sorted(r.by_format, key=lambda f: not encoders.FORMATS[f].lossless):
            if os.path.exists(r.by_format[fmt]):
                return r.by_format[fmt]
        return r.wav

    def _finish(self):
        cfg = self.cfg
        ex = cfg["extras"]
        image = cfg["output"].get("mode") == "image"
        if self.retry_of is not None and not image:
            self._merge_retry()
        responses = self._ar_db()
        if self._ar_failed:
            self.ar_note = "AccurateRip: database could not be reached"
        elif responses:
            confs = [r.entries for r in responses]
            self.ar_note = (f"AccurateRip: disc found ({len(responses)} pressing(s), "
                            f"{sum(e[0][0] for e in confs if e)} submissions for track 1)")
        else:
            self.ar_note = "AccurateRip: disc not present in the database"
        # ReplayGain
        if ex.get("replaygain"):
            self._overall(1.0, "Calculating ReplayGain…")
            if image:
                wavs, peaks = [self._image_wav], [self.results[self.track_order[0]].peak]
            else:
                wavs = [self._loudness_source(n) for n in self.track_order]
                peaks = [self.results[n].peak for n in self.track_order]
            rg = extras.replaygain(wavs, peaks)
            if rg is None:
                self.rep.log("ReplayGain skipped (ffmpeg not found)")
            else:
                per_track, self.album_gain = rg
                if image:
                    for f in self._image_files:
                        tagger.add(f, {k: v for k, v in per_track[0].items() if "ALBUM" in k})
                else:
                    for n, tags in zip(self.track_order, per_track):
                        self.results[n].rg_gain = tags["REPLAYGAIN_TRACK_GAIN"]
                        for f in self.results[n].files:
                            try:
                                tagger.add(f, tags)
                            except Exception as e:  # noqa: BLE001
                                self.rep.log(f"ReplayGain tag failed for {os.path.basename(f)}: {e}")
                self.rep.log(f"ReplayGain: album {self.album_gain}")
        # where did things land? (one folder per format tree)
        files_by_fmt: dict[str, list[str]] = {}
        for fmt in self.formats:
            if image:
                if fmt in self._image_by_format:
                    files_by_fmt[fmt] = [self._image_by_format[fmt]]
            else:
                files = [self.results[n].by_format[fmt] for n in self.track_order
                         if fmt in self.results[n].by_format]
                if files:
                    files_by_fmt[fmt] = files
        folders = []
        for files in files_by_fmt.values():
            folder = os.path.dirname(files[0])
            if folder not in folders:
                folders.append(folder)
            stem = naming.clean(f"{self.album.artist} - {self.album.title}", True) or "album"
            if ex.get("write_cue"):
                if image:
                    cue = extras.cue_sheet(self.toc, self.album, None, files[0])
                else:
                    cue = extras.cue_sheet(self.toc, self.album, {n: f for n, f in zip(self.track_order, files)}, None)
                Path(folder, stem + ".cue").write_text(cue, encoding="utf-8")
            if ex.get("write_m3u") and not image:
                Path(folder, stem + ".m3u8").write_text(extras.m3u(files, self.album), encoding="utf-8")
        self.output_dir = folders[0] if folders else ""
        for folder in folders:
            if self.cover and cfg["coverart"].get("save_file", True):
                name = cfg["coverart"].get("filename") or "cover.jpg"
                if coverart.mime_of(self.cover.data) == "image/png":
                    name = os.path.splitext(name)[0] + ".png"
                Path(folder, name).write_bytes(self.cover.data)
            if ex.get("write_log"):
                stem = naming.clean(f"{self.album.artist} - {self.album.title}", True) or "rip"
                path = os.path.join(folder, stem + ".log")
                Path(path).write_text(extras.build_log(self), encoding="utf-8")
                self.log_path = self.log_path or path
        good = sum(1 for r in self.results.values() if r.good)
        self._overall(1.0, f"Done: {good}/{len(self.results)} tracks OK")
        self.rep.log(f"Finished. Output: {self.output_dir}")
