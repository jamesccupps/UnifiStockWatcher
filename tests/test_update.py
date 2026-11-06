"""The signed updater: signature checks, manifest rules, verified downloads,
the portable swap. Offline: releases are folders in tmp_path.

Signing uses the `cryptography` package (a dev dependency, never shipped);
verification is the app's own ed25519.py.
"""

import base64
import hashlib
import json
import os
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

import ed25519
import updater

ROOT = Path(__file__).resolve().parent.parent

crypto = pytest.importorskip("cryptography.hazmat.primitives.asymmetric.ed25519")
from cryptography.hazmat.primitives import serialization  # noqa: E402


# ── Ed25519 ──────────────────────────────────────────────────────────────────

# RFC 8032, section 7.1, tests 1 and 2.
RFC = [
    ("d75a980182b10ab7d54bfed3c964073a0ee172f3daa62325af021a68f707511a", "",
     "e5564300c360ac729086e2cc806e828a84877f1eb8e5d974d873e065224901555f"
     "b8821590a33bacc61e39701cf9b46bd25bf5f0595bbe24655141438e7a100b"),
    ("3d4017c3e843895a92b70aa74d1b7ebc9c982ccf2ec4968cc0cd55f12af4660c", "72",
     "92a009a9f0d4cab8720e820b5f642540a2b27b5416503f8fb3762223ebdb69da08"
     "5ac1e43e15996e458f3613d0f11d8c387b2eaeb4302aeeb00d291612bb0c00"),
]


@pytest.mark.parametrize("pub,msg,sig", RFC)
def test_rfc8032_vectors(pub, msg, sig):
    assert ed25519.verify(bytes.fromhex(pub), bytes.fromhex(msg), bytes.fromhex(sig))


@pytest.mark.parametrize("pub,msg,sig", RFC)
def test_any_flipped_bit_fails(pub, msg, sig):
    sig = bytearray.fromhex(sig)
    for i in (0, 31, 32, 63):
        bad = bytearray(sig)
        bad[i] ^= 0x01
        assert not ed25519.verify(bytes.fromhex(pub), bytes.fromhex(msg), bytes(bad))
    assert not ed25519.verify(bytes.fromhex(pub), b"other", bytes(sig))


def test_non_canonical_s_is_rejected():
    pub, msg, sig = RFC[0]
    sig = bytearray.fromhex(sig)
    s = int.from_bytes(sig[32:], "little") + ed25519._L     # same point, S >= L
    sig[32:] = s.to_bytes(32, "little")
    assert not ed25519.verify(bytes.fromhex(pub), bytes.fromhex(msg), bytes(sig))


@pytest.mark.parametrize("pub,sig", [(b"", b"x" * 64), (b"x" * 31, b"x" * 64),
                                     (b"x" * 32, b"x" * 63), ("str", b""), (None, None)])
def test_malformed_inputs_fail_cleanly(pub, sig):
    assert ed25519.verify(pub, b"m", sig) is False


def test_agrees_with_cryptography():
    for i in range(20):
        k = crypto.Ed25519PrivateKey.generate()
        pub = k.public_key().public_bytes(serialization.Encoding.Raw,
                                          serialization.PublicFormat.Raw)
        m = os.urandom(i * 7)
        assert ed25519.verify(pub, m, k.sign(m))


# ── A release in a folder ────────────────────────────────────────────────────

@pytest.fixture
def key(monkeypatch):
    k = crypto.Ed25519PrivateKey.generate()
    raw = k.public_key().public_bytes(serialization.Encoding.Raw,
                                      serialization.PublicFormat.Raw)
    monkeypatch.setattr(updater, "UPDATE_PUBLIC_KEY", base64.b64encode(raw).decode())
    return k


def sign(key, payload):
    raw = json.dumps(payload).encode()
    return json.dumps({"payload": base64.b64encode(raw).decode(),
                       "signature": base64.b64encode(key.sign(raw)).decode()})


def describe(path):
    data = path.read_bytes()
    return {"file": path.name, "size": len(data), "sha256": hashlib.sha256(data).hexdigest()}


@pytest.fixture
def release(tmp_path, key):
    """A 9.9.9 release folder: Setup.exe, the zip (with the exe and a
    portable marker), and a signed latest.json."""
    rel = tmp_path / "release"
    rel.mkdir()
    setup = rel / updater.INSTALLER_NAME
    setup.write_bytes(b"MZ fake installer " * 100)
    zpath = rel / updater.PORTABLE_NAME
    with zipfile.ZipFile(zpath, "w") as z:
        z.writestr(f"UnifiStockWatcher/{updater.GUI_EXE}", b"new exe")
        z.writestr("UnifiStockWatcher/_internal/lib.dll", b"new lib")
        z.writestr("UnifiStockWatcher/portable.txt", b"portable")
    payload = {"app": updater.APP_KEY, "version": "9.9.9", "published": "now",
               "installer": describe(setup), "portable": describe(zpath)}
    (rel / "latest.json").write_text(sign(key, payload))
    return rel


def test_verify_manifest_accepts_a_signed_one(release):
    m = updater.verify_manifest((release / "latest.json").read_text())
    assert m["version"] == "9.9.9" and m["portable"]["file"] == updater.PORTABLE_NAME


def test_verify_manifest_rejects_another_key(release, monkeypatch):
    other = crypto.Ed25519PrivateKey.generate().public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    monkeypatch.setattr(updater, "UPDATE_PUBLIC_KEY", base64.b64encode(other).decode())
    with pytest.raises(updater.UpdateError, match="isn't signed"):
        updater.verify_manifest((release / "latest.json").read_text())


def test_verify_manifest_rejects_a_tampered_payload(release):
    outer = json.loads((release / "latest.json").read_text())
    payload = json.loads(base64.b64decode(outer["payload"]))
    payload["installer"]["sha256"] = "0" * 64
    outer["payload"] = base64.b64encode(json.dumps(payload).encode()).decode()
    with pytest.raises(updater.UpdateError):
        updater.verify_manifest(json.dumps(outer))


@pytest.mark.parametrize("change", [
    {"app": "something-else"},
    {"version": "3.0"}, {"version": "v3.0.0"}, {"version": "3.0.0-beta"},
    {"installer": {"file": "../evil.exe", "size": 1, "sha256": "0" * 64}},
    {"installer": {"file": "https://x/evil.exe", "size": 1, "sha256": "0" * 64}},
    {"installer": {"file": "ok.exe", "size": 0, "sha256": "0" * 64}},
    {"installer": {"file": "ok.exe", "size": 1, "sha256": "zz"}},
    {"installer": "not an object"},
])
def test_a_signed_but_malformed_manifest_is_refused(key, change):
    """Signed is necessary, not sufficient: the manifest names bare files of
    a bounded size, and a plain X.Y.Z version."""
    payload = {"app": updater.APP_KEY, "version": "9.9.9"}
    payload.update(change)
    with pytest.raises(updater.UpdateError):
        updater.verify_manifest(sign(key, payload))


@pytest.mark.parametrize("text", ["", "{}", "not json", '{"payload": "!!", "signature": "x"}'])
def test_garbage_is_an_update_error(key, text):
    with pytest.raises(updater.UpdateError):
        updater.verify_manifest(text)


def test_versions():
    assert updater.parse_version("3.10.2") > updater.parse_version("3.9.9")
    assert updater.parse_version("1.2") is None


@pytest.mark.parametrize("kind,asset", [("installed", "installer"), ("portable", "portable"),
                                        ("folder", "portable"), ("source", None)])
def test_check_offers_the_right_file(release, monkeypatch, kind, asset):
    monkeypatch.setattr(updater, "install_kind", lambda: kind)
    u = updater.check(current="3.0.0", source=str(release))
    assert u.version == "9.9.9" and u.kind == kind
    assert (u.asset["file"] if u.asset else None) == (
        {"installer": updater.INSTALLER_NAME, "portable": updater.PORTABLE_NAME}.get(asset))
    assert u.can_install is (asset is not None)


@pytest.mark.parametrize("current", ["9.9.9", "10.0.0"])
def test_check_finds_nothing_when_current(release, current):
    assert updater.check(current=current, source=str(release)) is None


def test_asset_location_pins_github_downloads_to_the_tag():
    """A release published between reading latest.json and the download
    must not swap in the next version's file."""
    loc = updater.asset_location(updater.DEFAULT_SOURCE, "3.1.0", "UnifiStockWatcher-Setup.exe")
    assert loc.endswith("/releases/download/v3.1.0/UnifiStockWatcher-Setup.exe")
    assert updater.asset_location("https://mirror.example/u/latest.json", "3.1.0", "a.zip") \
        == "https://mirror.example/u/a.zip"


def test_download_verifies_and_keeps_the_file(release, tmp_path, monkeypatch):
    monkeypatch.setattr(updater, "install_kind", lambda: "installed")
    u = updater.check(current="3.0.0", source=str(release))
    seen = []
    path = updater.download(u, dest_dir=tmp_path / "dl", progress=lambda d, t: seen.append(d))
    assert path.read_bytes() == (release / updater.INSTALLER_NAME).read_bytes()
    assert seen and seen[-1] == u.asset["size"]
    assert not list((tmp_path / "dl").glob("*.part"))


@pytest.mark.parametrize("damage", ["append", "flip"])
def test_a_download_that_doesnt_match_is_deleted(release, tmp_path, monkeypatch, damage):
    monkeypatch.setattr(updater, "install_kind", lambda: "installed")
    u = updater.check(current="3.0.0", source=str(release))
    f = release / updater.INSTALLER_NAME
    data = bytearray(f.read_bytes())
    if damage == "append":
        data += b"extra"
    else:
        data[5] ^= 0xFF
    f.write_bytes(bytes(data))
    with pytest.raises(updater.UpdateError):
        updater.download(u, dest_dir=tmp_path / "dl")
    assert list((tmp_path / "dl").iterdir()) == []


def test_staging_rejects_a_path_outside_the_folder(tmp_path):
    z = tmp_path / "evil.zip"
    with zipfile.ZipFile(z, "w") as zf:
        zf.writestr("../../escaped.txt", b"x")
    with pytest.raises(updater.UpdateError):
        updater._safe_extract(z, tmp_path / "out")
    assert not (tmp_path / "escaped.txt").exists()


def test_stage_finds_the_exe_in_a_top_folder(release, tmp_path, monkeypatch):
    monkeypatch.setattr(updater, "install_kind", lambda: "portable")
    u = updater.check(current="3.0.0", source=str(release))
    path = updater.download(u, dest_dir=tmp_path / "dl")
    staged = updater.stage(u, path)
    assert (staged / updater.GUI_EXE).read_bytes() == b"new exe"


# ── The portable swap ────────────────────────────────────────────────────────

def _app_folder(tmp_path, portable=True):
    app = tmp_path / "app"
    (app / "_internal").mkdir(parents=True)
    (app / updater.GUI_EXE).write_bytes(b"old exe")
    (app / "_internal" / "lib.dll").write_bytes(b"old lib")
    (app / "watched_items.json").write_text("[1]")
    if portable:
        (app / "portable.txt").write_text("portable")
    staged = tmp_path / "staged"
    (staged / "_internal").mkdir(parents=True)
    (staged / updater.GUI_EXE).write_bytes(b"new exe")
    (staged / "_internal" / "lib.dll").write_bytes(b"new lib")
    (staged / "portable.txt").write_text("portable")
    return app, staged


def test_swap_replaces_program_files_and_keeps_data(tmp_path):
    app, staged = _app_folder(tmp_path)
    updater.swap_in(app, staged)
    assert (app / updater.GUI_EXE).read_bytes() == b"new exe"
    assert (app / "_internal" / "lib.dll").read_bytes() == b"new lib"
    assert (app / "watched_items.json").read_text() == "[1]"
    assert (app / (updater.GUI_EXE + ".old")).exists()      # removed by the new app


def test_swap_never_makes_a_folder_copy_portable(tmp_path):
    """The zip carries portable.txt; copied into a copy that keeps its data in
    %APPDATA%, it would silently move the user to an empty data folder."""
    app, staged = _app_folder(tmp_path, portable=False)
    updater.swap_in(app, staged)
    assert not (app / "portable.txt").exists()


def test_a_failed_swap_puts_everything_back(tmp_path, monkeypatch):
    app, staged = _app_folder(tmp_path)
    real = updater.shutil.copy2

    def fail_on_exe(src, dst, *a, **k):
        if str(dst).endswith(updater.GUI_EXE):
            raise OSError("in use")
        return real(src, dst, *a, **k)
    monkeypatch.setattr(updater.shutil, "copy2", fail_on_exe)
    monkeypatch.setattr(updater, "_retry", lambda fn, tries=40, delay=0.25: fn())
    with pytest.raises(OSError):
        updater.swap_in(app, staged)
    assert (app / updater.GUI_EXE).read_bytes() == b"old exe"
    assert (app / "_internal" / "lib.dll").read_bytes() == b"old lib"
    assert not list(app.glob("*.old"))


def _fail_half_way_through_internal(monkeypatch):
    """copytree that copies one file of _internal and then fails (disk full,
    antivirus holding a new file, a USB stick pulled)."""
    def half(src, dst, *a, **k):
        os.makedirs(dst)
        (updater.Path(dst) / "lib.dll").write_bytes(b"half")
        raise OSError("disk full")
    monkeypatch.setattr(updater.shutil, "copytree", half)
    monkeypatch.setattr(updater, "_retry", lambda fn, tries=40, delay=0.25: fn())


def test_a_folder_half_copied_when_the_swap_fails_is_put_back(tmp_path, monkeypatch):
    """Regression: a half-copied _internal wasn't counted as placed, so it
    stayed, and the old one couldn't be renamed back over it."""
    app, staged = _app_folder(tmp_path)
    _fail_half_way_through_internal(monkeypatch)
    with pytest.raises(OSError):
        updater.swap_in(app, staged)
    assert (app / "_internal" / "lib.dll").read_bytes() == b"old lib"
    assert (app / updater.GUI_EXE).read_bytes() == b"old exe"
    assert not list(app.glob("*.old"))


def test_a_rollback_that_cant_finish_says_so_and_keeps_the_old_copy(tmp_path, monkeypatch):
    app, staged = _app_folder(tmp_path)
    _fail_half_way_through_internal(monkeypatch)
    real_rename = updater.Path.rename

    def no_rename_back(self, target):
        if self.name == "_internal.old":
            raise PermissionError("in use")
        return real_rename(self, target)
    monkeypatch.setattr(updater.Path, "rename", no_rename_back)
    with pytest.raises(updater.SwapFailed, match="_internal.old"):
        updater.swap_in(app, staged)
    assert (app / "_internal.old" / "lib.dll").read_bytes() == b"old lib"


def test_cleanup_removes_only_what_the_swap_renamed(tmp_path, monkeypatch):
    """Regression: it removed every *.old beside the program, a user's own
    files included, and it ran after a failed update too."""
    app, staged = _app_folder(tmp_path)
    (app / "notes.old").write_text("mine")
    updater.swap_in(app, staged)
    assert (app / "_internal.old").exists() and (app / (updater.GUI_EXE + ".old")).exists()
    monkeypatch.setattr(updater, "program_dir", lambda: app)
    monkeypatch.setattr(updater.tempfile, "gettempdir", lambda: str(tmp_path / "temp"))
    updater.cleanup_after_update()
    assert sorted(p.name for p in app.glob("*.old")) == ["notes.old"]
    assert not (app / updater.SWAPPED_LIST).exists()


def test_finish_update_says_when_the_old_version_isnt_whole(tmp_path, monkeypatch):
    app, staged = _app_folder(tmp_path)
    spawned = []
    monkeypatch.setattr(updater, "program_dir", lambda: staged)
    monkeypatch.setattr(updater, "_wait_for_exit", lambda pid, timeout=60: True)
    monkeypatch.setattr(updater, "_spawn", lambda args, cwd: spawned.append(args))
    monkeypatch.setattr(updater, "swap_in", lambda a, s: (_ for _ in ()).throw(
        updater.SwapFailed("couldn't put back _internal")))
    assert updater.finish_update(str(app), "123") == 1
    assert "couldn't all be put back" in (app / "update.log").read_text(encoding="utf-8")


@pytest.mark.parametrize("text", ["3.2.0\n", "3.2.0 ", " 3.2.0"])
def test_a_version_with_anything_after_it_is_refused(text):
    assert updater.parse_version(text) is None


def test_finish_update_logs_and_restarts(tmp_path, monkeypatch):
    app, staged = _app_folder(tmp_path)
    spawned = []
    monkeypatch.setattr(updater, "program_dir", lambda: staged)
    monkeypatch.setattr(updater, "_wait_for_exit", lambda pid, timeout=60: True)
    monkeypatch.setattr(updater, "_spawn", lambda args, cwd: spawned.append(args))
    assert updater.finish_update(str(app), "123") == 0
    assert spawned[-1][-1] == "--updated"
    assert "update: done" in (app / "update.log").read_text(encoding="utf-8")


def test_finish_update_restarts_the_old_version_on_failure(tmp_path, monkeypatch):
    app, staged = _app_folder(tmp_path)
    spawned = []
    monkeypatch.setattr(updater, "program_dir", lambda: staged)
    monkeypatch.setattr(updater, "_wait_for_exit", lambda pid, timeout=60: True)
    monkeypatch.setattr(updater, "_spawn", lambda args, cwd: spawned.append(args))
    monkeypatch.setattr(updater, "swap_in", lambda a, s: (_ for _ in ()).throw(OSError("locked")))
    assert updater.finish_update(str(app), "123") == 1
    assert spawned[-1][-1] == "--update-failed"


def test_apply_refuses_an_unwritable_folder(release, tmp_path, monkeypatch):
    monkeypatch.setattr(updater, "install_kind", lambda: "portable")
    u = updater.check(current="3.0.0", source=str(release))
    path = updater.download(u, dest_dir=tmp_path / "dl")
    monkeypatch.setattr(updater, "folder_is_writable", lambda folder=None: False)
    with pytest.raises(updater.UpdateError, match="can't be written"):
        updater.apply(u, path)


def test_apply_runs_the_installer_silently(release, tmp_path, monkeypatch):
    monkeypatch.setattr(updater, "install_kind", lambda: "installed")
    u = updater.check(current="3.0.0", source=str(release))
    path = updater.download(u, dest_dir=tmp_path / "dl")
    spawned = []
    monkeypatch.setattr(updater, "_spawn", lambda args, cwd: spawned.append(args))
    updater.apply(u, path)
    assert spawned[0][0] == path and "/VERYSILENT" in spawned[0] and "/relaunch=1" in spawned[0]


def test_the_shipped_public_key_is_a_real_key():
    raw = base64.b64decode(updater.UPDATE_PUBLIC_KEY)
    assert len(raw) == 32 and ed25519._decompress(raw) is not None


def test_a_release_without_latest_json_means_up_to_date():
    """GitHub's latest release can be one from before 3.0 (no latest.json):
    a 404 for the manifest is "nothing to install", not an error to show."""
    class R:
        status_code = 404

    class S:
        def get(self, url, **kw):
            return R()
    assert updater.check(current="3.0.0", source=updater.DEFAULT_SOURCE, session=S()) is None


# ── Signing a release (scripts/), run the way the workflows run them ────────

def _script(name, *args, **env):
    return subprocess.run([sys.executable, str(ROOT / "scripts" / name), *map(str, args)],
                          capture_output=True, text=True, timeout=60, env=dict(os.environ, **env))


def _pem(k):
    return k.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                           serialization.NoEncryption()).decode()


@pytest.fixture
def unsigned(tmp_path):
    """What the release's build job hands the signing job."""
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / updater.INSTALLER_NAME).write_bytes(b"MZ installer " * 50)
    (dist / updater.PORTABLE_NAME).write_bytes(b"PK zip " * 50)
    return dist


def test_sign_release_takes_the_key_text_from_the_environment(unsigned, key):
    """The signing job passes the secret's text, never a file; a secret pasted
    with blank lines around it still works. (USW_SIGNING_KEY points nowhere,
    so a fallback to the development PC's own key file can't hide a bug.)"""
    r = _script("sign_release.py", unsigned, "v9.9.9", USW_SIGNING_KEY_PEM=f"\n{_pem(key)}  \n",
                USW_SIGNING_KEY=str(unsigned / "no-such-key.pem"))
    assert r.returncode == 0, r.stderr
    m = updater.verify_manifest((unsigned / "latest.json").read_text(encoding="utf-8"))
    assert m["version"] == "9.9.9"
    for kind, name in (("installer", updater.INSTALLER_NAME), ("portable", updater.PORTABLE_NAME)):
        assert m[kind]["sha256"] == hashlib.sha256((unsigned / name).read_bytes()).hexdigest()


def test_ci_rehearses_signing_with_a_throwaway_key(unsigned, key):
    """ci.yml's signing job: the helper's throwaway key signs, and
    verify_release checks against its public half, which nothing else accepts."""
    out = _script("update_test_helper.py", "key")
    assert out.returncode == 0, out.stderr
    public, pem = out.stdout.split("\n", 1)
    r = _script("sign_release.py", unsigned, "9.9.9", USW_SIGNING_KEY_PEM=pem,
                USW_SIGNING_KEY=str(unsigned / "no-such-key.pem"))
    assert r.returncode == 0, r.stderr
    ok = _script("verify_release.py", unsigned, "v9.9.9", "--public-key", public)
    assert ok.returncode == 0, ok.stderr
    assert "throwaway" in ok.stdout
    other = base64.b64encode(key.public_key().public_bytes(serialization.Encoding.Raw,
                                                           serialization.PublicFormat.Raw)).decode()
    assert _script("verify_release.py", unsigned, "9.9.9").returncode != 0      # the app's key
    assert _script("verify_release.py", unsigned, "9.9.9", "--public-key", other).returncode != 0
    assert _script("verify_release.py", unsigned, "9.9.8", "--public-key", public).returncode != 0
    (unsigned / updater.PORTABLE_NAME).write_bytes(b"changed after signing")
    bad = _script("verify_release.py", unsigned, "--public-key", public)
    assert bad.returncode != 0 and "don't match" in bad.stderr
