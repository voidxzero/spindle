"""Check GitHub for a newer Spindle and update an installed copy in place.

The installer records which GitHub repository it was installed from (install.json in the data folder);
Settings ▸ Updates can override it. A check reads the version and changelog from the repository's
default branch; an update downloads that branch, runs its install.sh --no-deps (which keeps settings,
plugins and drive offsets) and Spindle restarts."""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tarfile
import tempfile
from dataclasses import dataclass
from pathlib import Path

from . import __version__, net
from .config import DATA_DIR

INSTALL_INFO = DATA_DIR / "install.json"
_REPO_RE = re.compile(r"^[A-Za-z0-9-]+/[A-Za-z0-9._-]+$")


# --- where to look ---------------------------------------------------------------------------------
def parse_repo(text: str) -> str:
    """'owner/name' from a GitHub URL (https or ssh) or an 'owner/name' string; '' if it isn't one."""
    t = (text or "").strip()
    m = re.match(r"^(?:https?://(?:www\.)?github\.com/|git@github\.com:|ssh://git@github\.com/)?"
                 r"([A-Za-z0-9-]+)/([A-Za-z0-9._-]+?)(?:\.git)?/?$", t)
    if not m:
        return ""
    repo = f"{m.group(1)}/{m.group(2)}"
    return repo if _REPO_RE.match(repo) else ""


def install_info() -> dict:
    try:
        return json.loads(INSTALL_INFO.read_text())
    except (OSError, ValueError):
        return {}


def repo_for(cfg) -> str:
    """The repository to update from: Settings first, then the one the installer recorded."""
    return parse_repo(cfg.get("update", {}).get("repo", "")) or parse_repo(install_info().get("repo", ""))


def installed_lib() -> Path | None:
    """The installed copy's lib folder, or None when running from a source folder (git checkout)."""
    lib = Path(__file__).resolve().parent.parent
    try:
        if lib == (DATA_DIR / "lib").resolve():
            return lib
    except OSError:
        pass
    return None


# --- versions ---------------------------------------------------------------------------------------
def vtuple(v: str) -> tuple:
    return tuple(int(x) for x in re.findall(r"\d+", v)[:4]) or (0,)


def newer(a: str, b: str) -> bool:
    return vtuple(a) > vtuple(b)


def parse_version(init_py: str) -> str:
    m = re.search(r'^__version__\s*=\s*["\']([^"\']+)["\']', init_py, re.M)
    return m.group(1) if m else ""


def changes_since(changelog: str, current: str) -> str:
    """The CHANGELOG.md sections for versions newer than `current` (Markdown)."""
    out, keep = [], False
    for line in changelog.splitlines():
        m = re.match(r"^##\s+(\S+)", line)
        if m:
            ver = m.group(1)
            if re.match(r"\d", ver) and not ver.endswith(".x"):
                keep = newer(ver, current)
            else:
                keep = False
            if not keep and out:
                break
        if keep:
            out.append(line)
    return "\n".join(out).strip()


@dataclass
class UpdateInfo:
    repo: str
    current: str
    latest: str
    notes: str             # Markdown

    @property
    def available(self) -> bool:
        return newer(self.latest, self.current)


def _raw(repo: str, path: str) -> str:
    # HEAD = the repository's default branch, whatever it's called
    return net.get(f"https://raw.githubusercontent.com/{repo}/HEAD/{path}", timeout=15,
                   headers={"Cache-Control": "no-cache"}).decode("utf-8", "replace")


def check(repo: str, current: str = __version__) -> UpdateInfo:
    """Ask GitHub for the latest version. Raises net.HttpError when it can't be reached."""
    if not _REPO_RE.match(repo or ""):
        raise ValueError("no GitHub repository set")
    latest = parse_version(_raw(repo, "spindle/__init__.py"))
    if not latest:
        raise ValueError(f"{repo} doesn't look like Spindle (no version found)")
    notes = ""
    if newer(latest, current):
        try:
            notes = changes_since(_raw(repo, "CHANGELOG.md"), current)
        except net.HttpError:
            pass
    return UpdateInfo(repo, current, latest, notes)


# --- installing -------------------------------------------------------------------------------------
class UpdateError(Exception):
    pass


def _safe_extract(tar: tarfile.TarFile, dest: Path) -> None:
    dest = dest.resolve()
    for m in tar.getmembers():
        target = (dest / m.name).resolve()
        if dest not in target.parents and target != dest:
            raise UpdateError(f"unsafe path in download: {m.name}")
        if m.issym() or m.islnk() or m.isdev():
            raise UpdateError(f"unexpected link or device in download: {m.name}")
    try:
        tar.extractall(dest, filter="data")          # Python 3.12+ (and security backports)
    except TypeError:
        tar.extractall(dest)


def install(repo: str, expect: str = "", log=lambda s: None, prefix: Path | None = None) -> str:
    """Download the repository's default branch and run its installer (no system packages, keeps
    settings). Returns the version installed. Spindle must be restarted afterwards."""
    if not _REPO_RE.match(repo or ""):
        raise UpdateError("no GitHub repository set")
    tmp = Path(tempfile.mkdtemp(prefix="spindle-update-"))
    try:
        log(f"Downloading {repo}…")
        data = net.get(f"https://codeload.github.com/{repo}/tar.gz/HEAD", timeout=60)
        archive = tmp / "spindle.tar.gz"
        archive.write_bytes(data)
        src = tmp / "src"
        src.mkdir()
        with tarfile.open(archive) as tar:
            _safe_extract(tar, src)
        tops = [p for p in src.iterdir() if p.is_dir()]
        if len(tops) != 1 or not (tops[0] / "install.sh").is_file():
            raise UpdateError("the download doesn't contain Spindle's install.sh")
        top = tops[0]
        version = parse_version((top / "spindle" / "__init__.py").read_text())
        if not version:
            raise UpdateError("the download doesn't contain Spindle")
        if expect and version != expect:
            log(f"Note: GitHub now has {version} (expected {expect}).")
        log(f"Installing Spindle {version}…")
        env = dict(os.environ, SPINDLE_REPO=repo)
        if prefix is not None:
            env["XDG_DATA_HOME"] = str(prefix)
        proc = subprocess.run(["bash", str(top / "install.sh"), "--no-deps"], cwd=top, env=env,
                              stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, timeout=600)
        for line in proc.stdout.splitlines():
            line = re.sub(r"\x1b\[[0-9;]*m", "", line).strip()
            if line:
                log(line)
        if proc.returncode != 0:
            raise UpdateError(f"the installer stopped with an error (exit {proc.returncode})")
        return version
    except net.HttpError as e:
        raise UpdateError(f"couldn't download the update: {e}") from e
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
