"""Optional audio fingerprint identification via AcoustID (needs fpcalc from chromaprint)."""
from __future__ import annotations

import json
import shutil
import subprocess
from collections import Counter

from . import net

API = "https://api.acoustid.org/v2/lookup"


def available() -> bool:
    return shutil.which("fpcalc") is not None


def fingerprint(path: str) -> tuple[int, str]:
    p = subprocess.run(["fpcalc", "-json", path], capture_output=True, text=True, timeout=120)
    if p.returncode != 0:
        raise RuntimeError(p.stderr.strip() or "fpcalc failed")
    d = json.loads(p.stdout)
    return int(d["duration"]), d["fingerprint"]


def lookup(key: str, duration: int, fp: str) -> list[dict]:
    d = net.get_json(API, {"client": key, "meta": "recordings releases", "duration": duration,
                           "fingerprint": fp, "format": "json"})
    if d.get("status") != "ok":
        raise RuntimeError((d.get("error") or {}).get("message", "lookup failed"))
    return d.get("results", [])


def vote_releases(key: str, paths: list[str], log=lambda s: None) -> list[str]:
    """Fingerprint every file and return MusicBrainz release IDs, most votes first."""
    votes: Counter = Counter()
    for path in paths:
        try:
            dur, fp = fingerprint(path)
            for res in lookup(key, dur, fp):
                weight = float(res.get("score", 0))
                for rec in res.get("recordings", []) or []:
                    for rel in rec.get("releases", []) or []:
                        if rel.get("id"):
                            votes[rel["id"]] += weight
        except Exception as e:  # noqa: BLE001
            log(f"AcoustID: {path.rsplit('/', 1)[-1]}: {e}")
    return [mbid for mbid, _ in votes.most_common(5)]
