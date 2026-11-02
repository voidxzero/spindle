"""Updater: repository parsing, version checks and installing a downloaded copy (no network needed)."""
import io
import os
import sys
import tarfile
import tempfile
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from spindle import net, updater  # noqa: E402

CHANGELOG = """# Changelog

## 1.9.0
- Updater

## 1.8.0
- Finish sound

## 1.7.1
- Clear on eject

## 1.1.x
- Old stuff
"""


def test_parse_repo():
    ok = {"alice/spindle": "alice/spindle",
          "https://github.com/alice/spindle": "alice/spindle",
          "https://github.com/alice/spindle.git": "alice/spindle",
          "https://github.com/alice/spindle/": "alice/spindle",
          "git@github.com:alice/spindle.git": "alice/spindle",
          "ssh://git@github.com/alice/my.repo.git": "alice/my.repo"}
    for text, want in ok.items():
        assert updater.parse_repo(text) == want, text
    for bad in ("", "spindle", "https://gitlab.com/alice/spindle", "alice/spindle/tree/main", "a b/c"):
        assert updater.parse_repo(bad) == "", bad


def test_versions_and_notes():
    assert updater.newer("1.10.0", "1.9.3") and not updater.newer("1.8.0", "1.8.0")
    assert updater.parse_version('"""x"""\n__version__ = "2.0.1"\n') == "2.0.1"
    notes = updater.changes_since(CHANGELOG, "1.7.1")
    assert "## 1.9.0" in notes and "## 1.8.0" in notes and "1.7.1" not in notes, notes
    assert updater.changes_since(CHANGELOG, "1.9.0") == ""


def fake_get(files):
    def get(url, *a, **k):
        for key, data in files.items():
            if url.endswith(key):
                return data
        raise net.HttpError("not found", 404)
    return get


def test_check():
    files = {"/HEAD/spindle/__init__.py": b'__version__ = "1.9.0"\n', "/HEAD/CHANGELOG.md": CHANGELOG.encode()}
    with mock.patch.object(net, "get", fake_get(files)):
        info = updater.check("alice/spindle", current="1.8.0")
        assert info.available and info.latest == "1.9.0" and "Updater" in info.notes and "Finish" not in info.notes
        info = updater.check("alice/spindle", current="1.9.0")
        assert not info.available and info.notes == ""
    try:
        updater.check("not a repo")
    except ValueError:
        pass
    else:
        raise AssertionError("bad repo accepted")


def make_tarball(top="alice-spindle-abc123", version="1.9.0", extra=None):
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        def add(name, text, mode=0o644):
            data = text.encode()
            ti = tarfile.TarInfo(f"{top}/{name}" if top else name)
            ti.size, ti.mode = len(data), mode
            tar.addfile(ti, io.BytesIO(data))
        add("spindle/__init__.py", f'__version__ = "{version}"\n')
        # stand-in installer: records what it was run with
        add("install.sh", 'echo "==> args=$* repo=$SPINDLE_REPO"\n'
                          'mkdir -p "$XDG_DATA_HOME/spindle" && echo "$SPINDLE_REPO" > "$XDG_DATA_HOME/spindle/ran"\n',
            0o755)
        for name, text in (extra or {}).items():
            add(name, text)
    return buf.getvalue()


def test_install():
    if sys.platform == "win32":
        return                                  # Linux updater (runs install.sh); Windows uses the setup .exe
    with tempfile.TemporaryDirectory() as home:
        lines = []
        with mock.patch.object(net, "get", fake_get({"/tar.gz/HEAD": make_tarball()})):
            v = updater.install("alice/spindle", "1.9.0", lines.append, prefix=Path(home))
        assert v == "1.9.0"
        assert (Path(home) / "spindle" / "ran").read_text().strip() == "alice/spindle"
        assert any("args=--no-deps repo=alice/spindle" in x for x in lines), lines

        # a download that tries to write outside its folder is refused
        evil = make_tarball(top="", extra={"../escape.txt": "x"})
        with mock.patch.object(net, "get", fake_get({"/tar.gz/HEAD": evil})):
            try:
                updater.install("alice/spindle", log=lambda s: None, prefix=Path(home))
            except updater.UpdateError:
                pass
            else:
                raise AssertionError("unsafe archive accepted")
        assert not (Path(home).parent / "escape.txt").exists()

        # something that isn't Spindle is refused
        with mock.patch.object(net, "get", fake_get({"/tar.gz/HEAD": b"not a tarball"})):
            try:
                updater.install("alice/spindle", log=lambda s: None, prefix=Path(home))
            except (updater.UpdateError, tarfile.TarError):
                pass
            else:
                raise AssertionError("junk accepted")


def test_installer_records_repo():
    """install.sh's repository detection, run on its own against a git checkout."""
    if sys.platform == "win32":
        return
    root = Path(__file__).resolve().parent.parent
    script = (root / "install.sh").read_text()
    start = script.index('REPO="${SPINDLE_REPO:-}"')
    end = script.index("fi\n", script.index('if [[ -n "$REPO" ]]')) + 3
    block = script[start:end]
    for url, want in (("https://github.com/alice/spindle.git", "alice/spindle"),
                      ("git@github.com:bob/spindle.git", "bob/spindle"),
                      ("https://gitlab.com/carol/spindle.git", "")):
        with tempfile.TemporaryDirectory() as d:
            src, prefix = Path(d) / "src", Path(d) / "prefix"
            (src / "spindle").mkdir(parents=True)
            prefix.mkdir()
            (src / "spindle" / "__init__.py").write_text('__version__ = "1.9.0"\n')
            os.system(f"git init -q {src} && git -C {src} remote add origin {url}")
            test = (f'set -euo pipefail\nSRC="{src}"\nPREFIX="{prefix}"\nsay() {{ :; }}\nwarn() {{ :; }}\n'
                    + block)
            rc = os.system(f"env -u SPINDLE_REPO bash -c {sh_quote(test)}")
            assert rc == 0, (url, rc)
            info = prefix / "install.json"
            if want:
                assert f'"repo": "{want}"' in info.read_text(), info.read_text()
                assert '"version": "1.9.0"' in info.read_text()
            else:
                assert not info.exists()


def test_windows_release_check():
    """Windows checks the latest *release* and its setup .exe; notes skip versions not released yet."""
    rel = {"tag_name": "v1.14.0", "assets": [
        {"name": "Spindle-1.14.0-portable.zip", "browser_download_url": "https://x/portable.zip"},
        {"name": "SpindleSetup-1.14.0.exe", "browser_download_url": "https://x/setup.exe"}]}
    log = "# Changelog\n\n## 1.15.0\n- not released yet\n\n## 1.14.0\n- windows\n\n## 1.13.0\n- convert\n"
    asked = []
    with mock.patch.object(net, "get_json", return_value=rel), \
         mock.patch.object(updater, "_raw", lambda repo, path: asked.append(path) or log):
        info = updater.check_release("alice/spindle", "1.13.0")
    assert asked == ["CHANGELOG-windows.md"], asked             # Windows reads its own changelog
    assert info.latest == "1.14.0" and info.available and info.download == "https://x/setup.exe"
    assert "windows" in info.notes and "not released" not in info.notes and "convert" not in info.notes, info.notes
    assert updater.windows_kind() == ""             # not the packaged Windows build
    assert updater.releases_page("alice/spindle").endswith("/alice/spindle/releases/latest")


def sh_quote(s):
    return "'" + s.replace("'", "'\\''") + "'"


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print("ok", name)
