"""Tagging existing files: scan -> group into albums -> identify -> match files to tracks -> save.

Toolkit independent; the Tag Files tab drives it.
"""
from __future__ import annotations

import difflib
import os
import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path

from . import acoustid, coverart, naming, net, tagger
from .metadata import musicbrainz as mb
from .metadata.base import AlbumMeta, CoverCandidate, TrackMeta

AUDIO_EXT = {".flac", ".mp3", ".m4a", ".mp4", ".m4b", ".ogg", ".oga", ".opus", ".wv", ".wav"}


@dataclass
class AudioFile:
    path: str
    tags: dict[str, str]
    length: float
    error: str = ""

    @property
    def name(self) -> str:
        return os.path.basename(self.path)

    def tag(self, key: str) -> str:
        return self.tags.get(key, "")

    @property
    def track_no(self) -> int:
        m = re.match(r"\d+", self.tag("tracknumber"))
        if m:
            return int(m.group())
        m = re.match(r"\s*(\d{1,3})[\s.\-_]", self.name)     # "03 - Title.flac"
        return int(m.group(1)) if m else 0

    @property
    def title_guess(self) -> str:
        if self.tag("title"):
            return self.tag("title")
        stem = os.path.splitext(self.name)[0]
        return re.sub(r"^\s*\d{1,3}[\s.\-_]+", "", stem).strip()


@dataclass
class Candidate:
    album: AlbumMeta
    score: float = 0.0
    assignment: dict[int, int] = field(default_factory=dict)   # file index -> track number

    def label(self) -> str:
        a = self.album
        disc = f" disc {a.disc_number}/{a.disc_total}" if a.disc_total > 1 else ""
        info = " · ".join(x for x in (a.date[:4], a.country, a.media) if x)
        return f"{int(self.score * 100)}%  {a.artist} – {a.title}{disc}  ({info})"


@dataclass
class FileAlbum:
    files: list[AudioFile]
    folder: str
    candidates: list[Candidate] = field(default_factory=list)
    chosen: int = -1
    edited: AlbumMeta | None = None      # what will be written (chosen candidate + user edits)
    assignment: dict[int, int] = field(default_factory=dict)
    status: str = "new"                  # new | working | matched | check | nomatch | saved | error
    message: str = ""
    cover: CoverCandidate | None = None  # new cover to embed (None = keep existing)
    existing_cover: bytes | None = None

    @property
    def guess_artist(self) -> str:
        return _most_common([f.tag("albumartist") or f.tag("artist") for f in self.files])

    @property
    def guess_album(self) -> str:
        return _most_common([f.tag("album") for f in self.files]) or _folder_album(self.folder)

    @property
    def title(self) -> str:
        if self.edited:
            return f"{self.edited.artist} – {self.edited.title}"
        a, b = self.guess_artist, self.guess_album
        return f"{a} – {b}" if a else (b or os.path.basename(self.folder))

    @property
    def best(self) -> Candidate | None:
        return self.candidates[self.chosen] if 0 <= self.chosen < len(self.candidates) else None


def _most_common(vals: list[str]) -> str:
    vals = [v for v in vals if v]
    return max(set(vals), key=vals.count) if vals else ""


def _folder_album(folder: str) -> str:
    name = os.path.basename(folder.rstrip("/"))
    name = re.sub(r"[\(\[]\d{4}[\)\]]", "", name)            # drop "(1991)"
    name = re.sub(r"^(cd|disc)\s*\d+$", "", name, flags=re.I)
    if " - " in name:
        name = name.split(" - ", 1)[1]
    return name.strip()


def _folder_artist(folder: str) -> str:
    name = os.path.basename(folder.rstrip("/"))
    if " - " in name:
        return name.split(" - ", 1)[0].strip()
    return os.path.basename(os.path.dirname(folder.rstrip("/")))


def sim(a: str, b: str) -> float:
    norm = lambda s: re.sub(r"[\W_]+", " ", (s or "").lower()).strip()  # noqa: E731
    a, b = norm(a), norm(b)
    if not a or not b:
        return 0.0
    return 1.0 if a == b else difflib.SequenceMatcher(None, a, b).ratio()


# ------------------------------------------------------------------ scanning
def scan(paths: list[str]) -> list[AudioFile]:
    files: list[str] = []
    for p in paths:
        if os.path.isdir(p):
            for root, dirs, names in os.walk(p):
                dirs.sort()
                for n in sorted(names):
                    if os.path.splitext(n)[1].lower() in AUDIO_EXT:
                        files.append(os.path.join(root, n))
        elif os.path.splitext(p)[1].lower() in AUDIO_EXT:
            files.append(p)
    out = []
    for f in files:
        try:
            tags, length = tagger.read(f)
            out.append(AudioFile(f, tags, length))
        except Exception as e:  # noqa: BLE001
            out.append(AudioFile(f, {}, 0.0, error=str(e)))
    return out


def _split(fs: list[AudioFile], key: str) -> list[list[AudioFile]]:
    """Split by a tag only when it clearly separates several multi-file groups."""
    vals: dict[str, list[AudioFile]] = {}
    for f in fs:
        vals.setdefault(f.tag(key).lower(), []).append(f)
    real = {v: g for v, g in vals.items() if v and len(g) >= 2}
    if len(real) < 2:
        return [fs]
    out = list(real.values())
    biggest = max(out, key=len)
    for v, g in vals.items():
        if v not in real:
            biggest.extend(g)          # untagged/odd files join the largest group
    return out


def group(files: list[AudioFile]) -> list[FileAlbum]:
    """One album per folder; split a folder only when its album or disc tags clearly differ."""
    by_folder: dict[str, list[AudioFile]] = {}
    for f in files:
        if not f.error:
            by_folder.setdefault(os.path.dirname(f.path), []).append(f)
    albums = []
    for folder, fs in by_folder.items():
        for g in _split(fs, "album"):
            for g2 in _split(g, "discnumber"):
                g2.sort(key=lambda f: (f.track_no or 999, f.name))
                albums.append(FileAlbum(g2, folder))
    albums.sort(key=lambda a: (a.folder.lower(), a.files[0].tag("discnumber")))
    return albums


# --------------------------------------------------------------- matching
def assign(files: list[AudioFile], album: AlbumMeta) -> tuple[dict[int, int], float]:
    """Greedy best match of files to tracks; returns (file index -> track number, quality 0..1)."""
    if not files or not album.tracks:
        return {}, 0.0
    costs = []
    for i, f in enumerate(files):
        for t in album.tracks:
            c = 0.0
            if f.track_no:
                c += 0.0 if f.track_no == t.number else 0.6
            c += (1 - sim(f.title_guess, t.title)) * 0.8
            if t.length_ms and f.length:
                c += min(1.0, abs(f.length - t.length_ms / 1000) / 20) * 0.9
            costs.append((c, i, t.number))
    costs.sort()
    used_f, used_t, out = set(), set(), {}
    total = 0.0
    for c, i, n in costs:
        if i in used_f or n in used_t:
            continue
        out[i] = n
        used_f.add(i)
        used_t.add(n)
        total += c
    worst = 0.6 + 0.8 + 0.9
    quality = 1 - (total / max(1, len(out))) / worst
    return out, quality


def score(fa: FileAlbum, album: AlbumMeta) -> Candidate:
    assignment, q = assign(fa.files, album)
    by_num = {t.number: t for t in album.tracks}
    durs = [abs(f.length - by_num[n].length_ms / 1000) for i, n in assignment.items()
            for f in [fa.files[i]] if by_num[n].length_ms and f.length]
    dur_score = (1 - min(1.0, (sum(durs) / len(durs)) / 8)) if durs else 0.5
    count = 1 - min(1.0, abs(len(album.tracks) - len(fa.files)) / max(1, len(album.tracks)))
    name = 0.5 * sim(fa.guess_album, album.title) + 0.5 * (sim(fa.guess_artist, album.artist) if fa.guess_artist else 0.5)
    total = 0.35 * dur_score + 0.25 * q + 0.25 * count + 0.15 * name
    if len(album.tracks) < len(fa.files):
        total *= 0.7                           # can't place every file
    return Candidate(album, round(total, 3), assignment)


def _candidates_from_release(fa: FileAlbum, rel: dict) -> list[Candidate]:
    return [score(fa, a) for a in mb.release_albums(rel)]


def identify(cfg, fa: FileAlbum, log=lambda s: None, use_fingerprints: bool | None = None) -> None:
    """Fill fa.candidates (best first) and pick one when confident."""
    fa.status, fa.message = "working", "Identifying…"
    seen: set[str] = set()
    cands: list[Candidate] = []

    def add_release(mbid: str):
        if not mbid or mbid in seen:
            return
        seen.add(mbid)
        try:
            cands.extend(_candidates_from_release(fa, mb.fetch_release(mbid)))
        except net.HttpError as e:
            log(f"MusicBrainz: {e}")

    # 1. files already carry a MusicBrainz release ID
    add_release(_most_common([f.tag("musicbrainz_albumid") for f in fa.files]))
    # 2. text search with the tags (or the folder name)
    if not cands or max(c.score for c in cands) < 0.85:
        artist = fa.guess_artist or _folder_artist(fa.folder)
        album = fa.guess_album
        try:
            for rel in mb.search_releases(artist, album, limit=6)[:5]:
                add_release(rel.get("id", ""))
        except net.HttpError as e:
            log(f"MusicBrainz search: {e}")
    # 3. audio fingerprints (AcoustID) when text didn't settle it
    key = cfg["metadata"].get("acoustid_key", "").strip()
    want_fp = use_fingerprints if use_fingerprints is not None else (
        bool(key) and (not cands or max(c.score for c in cands) < 0.75))
    if want_fp and key and acoustid.available():
        fa.message = "Fingerprinting…"
        try:
            for mbid in acoustid.vote_releases(key, [f.path for f in fa.files], log)[:3]:
                add_release(mbid)
        except Exception as e:  # noqa: BLE001
            log(f"AcoustID: {e}")
    cands.sort(key=lambda c: -c.score)
    fa.candidates = cands[:12]
    if not fa.candidates:
        fa.chosen, fa.status, fa.message = -1, "nomatch", "No match found"
        return
    choose(fa, 0)


def choose(fa: FileAlbum, idx: int) -> None:
    fa.chosen = idx
    c = fa.candidates[idx]
    fa.edited = AlbumMeta.from_dict(c.album.to_dict())
    fa.assignment = dict(c.assignment)
    fa.cover = None
    pct = int(c.score * 100)
    if c.score >= 0.8:
        fa.status, fa.message = "matched", f"Matched {pct}%"
    else:
        fa.status, fa.message = "check", f"Please check ({pct}%)"


def find_cover(cfg, fa: FileAlbum, log=lambda s: None) -> CoverCandidate | None:
    if not fa.edited:
        return None
    return coverart.auto_pick(cfg, fa.edited, log)


# ------------------------------------------------------------------- saving
def new_tags_for(fa: FileAlbum, i: int, cfg) -> dict[str, str] | None:
    n = fa.assignment.get(i)
    if n is None or fa.edited is None:
        return None
    return tagger.tag_values(fa.edited, fa.edited.track(n), len(fa.edited.tracks),
                             cfg["metadata"].get("write_mb_ids", True))


def target_path(cfg, fa: FileAlbum, i: int, organize: bool) -> str:
    """Where file i will live after saving with renaming enabled."""
    f = fa.files[i]
    n = fa.assignment.get(i)
    if n is None or fa.edited is None:
        return f.path
    out = cfg["output"]
    tmpl = out["va_template"] if fa.edited.various else out["template"]
    rel = naming.render(tmpl, naming.values(fa.edited, fa.edited.track(n), len(fa.edited.tracks)),
                        out.get("safe_filenames", True))
    ext = os.path.splitext(f.path)[1].lower()
    if organize:
        return os.path.normpath(os.path.join(os.path.expanduser(out["directory"]), rel + ext))
    return os.path.join(os.path.dirname(f.path), os.path.basename(rel) + ext)


def save(cfg, fa: FileAlbum, rename: bool = False, organize: bool = False, log=lambda s: None) -> int:
    """Write tags (keeping unrelated ones), embed a new cover if chosen, optionally rename/move."""
    if fa.edited is None:
        raise ValueError("Nothing to save - pick a match or enter details first.")
    cover = None
    if fa.cover is not None:
        coverart.download(fa.cover)
        cover = coverart.for_embedding(fa.cover.data, int(cfg["coverart"].get("embed_max_px", 0)))
        if not cfg["coverart"].get("embed", True):
            cover = None
    written = 0
    folders = set()
    for i, f in enumerate(fa.files):
        tags = new_tags_for(fa, i, cfg)
        if tags is None:
            continue
        tagger.write(f.path, tags, cover, id3v23=cfg["encoders"]["mp3"].get("id3v23", False), preserve=True)
        if rename:
            dst = target_path(cfg, fa, i, organize)
            if os.path.abspath(dst) != os.path.abspath(f.path):
                os.makedirs(os.path.dirname(dst), exist_ok=True)
                base, ext = os.path.splitext(dst)
                k = 2
                while os.path.exists(dst):
                    dst = f"{base} ({k}){ext}"
                    k += 1
                shutil.move(f.path, dst)
                f.path = dst
        folders.add(os.path.dirname(f.path))
        f.tags, f.length = tagger.read(f.path)
        written += 1
    if fa.cover is not None and fa.cover.data and cfg["coverart"].get("save_file", True):
        name = cfg["coverart"].get("filename") or "cover.jpg"
        if coverart.mime_of(fa.cover.data) == "image/png":
            name = os.path.splitext(name)[0] + ".png"
        for d in folders:
            Path(d, name).write_bytes(fa.cover.data)
    if rename and organize and folders:
        old = fa.folder
        fa.folder = sorted(folders)[0]
        try:
            if os.path.isdir(old) and old != fa.folder and not any(
                    os.path.splitext(n)[1].lower() in AUDIO_EXT for n in os.listdir(old)):
                log(f"Note: {old} has no audio left; remove it yourself if it's empty.")
        except OSError:
            pass
    fa.existing_cover = cover or fa.existing_cover
    fa.cover = None
    fa.status, fa.message = "saved", f"Saved {written} file(s)"
    return written


def track_for(fa: FileAlbum, i: int) -> TrackMeta | None:
    n = fa.assignment.get(i)
    return fa.edited.track(n) if (n is not None and fa.edited) else None


# --------------------------------------------------------------- batch rename
@dataclass
class RenamePlan:
    file: AudioFile
    new_path: str
    problem: str = ""

    @property
    def changed(self) -> bool:
        return os.path.abspath(self.new_path) != os.path.abspath(self.file.path)


def plan_renames(files: list[AudioFile], mode: str, pattern: str = "", find: str = "", replace: str = "",
                 regex: bool = False, case: bool = False, safe: bool = True) -> list[RenamePlan]:
    """Work out new file names (same folder, same extension) without touching anything.

    mode "pattern": render ``pattern`` from each file's tags (e.g. "%tracknumber% - %title%").
    mode "replace": find/replace in the current name (plain text or a regular expression).
    """
    plans: list[RenamePlan] = []
    rx = None
    if mode == "replace" and find:
        try:
            rx = re.compile(find if regex else re.escape(find), 0 if case else re.IGNORECASE)
        except re.error as e:
            return [RenamePlan(f, f.path, f"Invalid expression: {e}") for f in files]
    for f in files:
        folder, name = os.path.split(f.path)
        stem, ext = os.path.splitext(name)
        problem = ""
        if mode == "pattern":
            new_stem = naming.render(pattern.replace("/", " "), naming.values_from_tags(f.tags), safe)
            if new_stem in ("", "_"):
                new_stem, problem = stem, "Tags are empty for this pattern"
        elif rx is not None:
            try:
                new_stem = rx.sub(replace, stem)
            except (re.error, IndexError) as e:
                new_stem, problem = stem, f"Replacement error: {e}"
            new_stem = naming.clean(new_stem, safe)
        else:
            new_stem = stem
        if not new_stem.strip():
            new_stem, problem = stem, "Name would be empty"
        plans.append(RenamePlan(f, os.path.join(folder, new_stem + ext), problem))
    # clashes: two files wanting one name, or an existing file that isn't being renamed itself
    moving = {os.path.abspath(p.file.path) for p in plans if p.changed}
    seen: dict[str, RenamePlan] = {}
    for p in plans:
        if p.problem or not p.changed:
            continue
        key = os.path.abspath(p.new_path)
        if key in seen:
            p.problem = seen[key].problem = "Two files would get this name"
        elif os.path.exists(key) and key not in moving and key.lower() != os.path.abspath(p.file.path).lower():
            p.problem = "A file with this name already exists"
        seen[key] = p
    return plans


def apply_renames(plans: list[RenamePlan]) -> int:
    """Rename in two steps (via temporary names) so swaps like A->B, B->A are safe."""
    todo = [p for p in plans if p.changed and not p.problem]
    temps = []
    for i, p in enumerate(todo):
        tmp = os.path.join(os.path.dirname(p.file.path), f".spindle-rename-{os.getpid()}-{i}")
        os.rename(p.file.path, tmp)
        temps.append((tmp, p))
    done = 0
    for tmp, p in temps:
        os.rename(tmp, p.new_path)
        p.file.path = p.new_path
        done += 1
    return done


# --------------------------------------------------------------- cover only
def embed_cover_only(cfg, fa: FileAlbum, data: bytes, log=lambda s: None) -> int:
    """Embed ``data`` as the front cover of every file in the album without changing any other
    tag, and (if enabled) save it next to the files as cover.jpg. Returns files updated."""
    embed = coverart.for_embedding(data, int(cfg["coverart"].get("embed_max_px", 0)))
    done = 0
    for f in fa.files:
        try:
            tagger.set_cover(f.path, embed)
            done += 1
        except Exception as e:  # noqa: BLE001
            log(f"Cover: {f.name}: {e}")
    if cfg["coverart"].get("save_file", True):
        name = cfg["coverart"].get("filename") or "cover.jpg"
        if coverart.mime_of(data) == "image/png":
            name = os.path.splitext(name)[0] + ".png"
        for d in {os.path.dirname(f.path) for f in fa.files}:
            Path(d, name).write_bytes(data)
    fa.existing_cover = embed
    fa.cover = None
    return done


def folder_image(fa: FileAlbum) -> bytes | None:
    """An image already sitting in the album folder (cover.jpg, folder.png, front.jpg, …)."""
    names = ("cover", "folder", "front", "album", "albumart")
    try:
        files = os.listdir(fa.folder)
    except OSError:
        return None
    pics = [n for n in files if os.path.splitext(n)[1].lower() in (".jpg", ".jpeg", ".png")]
    pics.sort(key=lambda n: (os.path.splitext(n)[0].lower() not in names, n.lower()))
    for n in pics:
        try:
            data = Path(fa.folder, n).read_bytes()
        except OSError:
            continue
        if coverart.image_size(data)[0]:
            return data
    return None
