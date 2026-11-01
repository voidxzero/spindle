"""Tiny HTTP helper: user agent, per-host rate limiting, retries, no third-party deps."""
from __future__ import annotations

import json
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

from . import USER_AGENT

_RATE = {"musicbrainz.org": 1.05, "coverartarchive.org": 0.2, "gnudb.gnudb.org": 0.5,
         "api.discogs.com": 1.1, "itunes.apple.com": 0.4, "api.deezer.com": 0.2}
_last: dict[str, float] = {}
_lock = threading.Lock()
contact = ""  # optional contact string appended to the user agent (set from config)


class HttpError(Exception):
    def __init__(self, msg: str, status: int = 0):
        super().__init__(msg)
        self.status = status


def _throttle(host: str) -> None:
    delay = _RATE.get(host, 0)
    if not delay:
        return
    with _lock:
        now = time.monotonic()
        wait = _last.get(host, 0) + delay - now
        _last[host] = max(now, _last.get(host, 0) + delay)
    if wait > 0:
        time.sleep(wait)


def get(url: str, params: dict | None = None, headers: dict | None = None,
        timeout: float = 20, retries: int = 2) -> bytes:
    if params:
        url += ("&" if "?" in url else "?") + urllib.parse.urlencode(params)
    host = urllib.parse.urlparse(url).hostname or ""
    ua = USER_AGENT if not contact else USER_AGENT.replace(")", f"; {contact} )")
    hdrs = {"User-Agent": ua, "Accept": "*/*"}
    hdrs.update(headers or {})
    last_err: Exception | None = None
    for attempt in range(retries + 1):
        _throttle(host)
        req = urllib.request.Request(url, headers=hdrs)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.read()
        except urllib.error.HTTPError as e:
            if e.code in (429, 503) and attempt < retries:
                time.sleep(1.5 * (attempt + 1))
                last_err = e
                continue
            raise HttpError(f"{host}: HTTP {e.code}", e.code) from e
        except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
            last_err = e
            if attempt < retries:
                time.sleep(1.0 * (attempt + 1))
                continue
    raise HttpError(f"{host}: {getattr(last_err, 'reason', last_err)}")


def get_json(url: str, params: dict | None = None, headers: dict | None = None, **kw):
    h = {"Accept": "application/json"}
    h.update(headers or {})
    return json.loads(get(url, params, h, **kw).decode("utf-8"))
