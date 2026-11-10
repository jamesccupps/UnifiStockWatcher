"""The release's code signing: build_windows.py's two halves and the release workflow's rules.

The signing itself only happens on a release runner (it needs the Azure sign-in), so what's
tested here is everything around it: the build signs the exes before it packs them, Inno Setup
is told how to sign the uninstaller and Setup.exe, and the workflow keeps the code-signing
identity away from everything installed from PyPI and tests the signed files before anything is
published. The workflow is read as text: PyYAML isn't a dependency.
"""

import re
from pathlib import Path

import pytest

from scripts import build_windows as bw

ROOT = Path(__file__).resolve().parent.parent
RELEASE = (ROOT / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")


def jobs(text):
    """{job name: job text} for a workflow."""
    body = text[text.index("\njobs:\n") + len("\njobs:\n"):]
    heads = list(re.finditer(r"^  ([A-Za-z0-9_-]+):\s*$", body, re.M))
    return {m.group(1): body[m.start():heads[i + 1].start() if i + 1 < len(heads) else len(body)]
            for i, m in enumerate(heads)}


def code(job):
    """A job's text without its comment lines."""
    return "\n".join(ln for ln in job.splitlines() if not ln.strip().startswith("#"))


# ── build_windows.py ─────────────────────────────────────────────────────────

@pytest.fixture
def recorded(tmp_path, monkeypatch):
    """build_windows with every stage replaced by a note of its running."""
    app = tmp_path / "dist" / "UnifiStockWatcher"
    monkeypatch.setattr(bw, "DIST", tmp_path / "dist")
    monkeypatch.setattr(bw, "APP_DIR", app)
    order = []

    def freeze():
        order.append("pyinstaller")
        app.mkdir(parents=True)
        for exe in bw.EXES:
            (app / exe).write_bytes(b"MZ")

    monkeypatch.setattr(bw, "version_resource", lambda: order.append("version"))
    monkeypatch.setattr(bw, "pyinstaller", freeze)
    monkeypatch.setattr(bw, "authenticode", lambda p: order.append(("codesign", p.name)))
    monkeypatch.setattr(bw, "portable_zip", lambda: order.append("zip"))
    monkeypatch.setattr(bw, "installer", lambda code_sign=False: order.append(("installer", code_sign)))
    monkeypatch.setattr(bw, "sign", lambda no_sign=False: order.append(("manifest", not no_sign)))
    monkeypatch.setattr(bw, "checksums", lambda: order.append("sums"))
    return order, app


def test_the_build_runs_whole_or_in_the_release_jobs_two_halves(recorded, tmp_path):
    order, app = recorded
    bw.main(["--no-sign"])          # what CI's package job runs
    assert order == ["version", "pyinstaller", "zip", ("installer", False), ("manifest", False), "sums"]

    order.clear()
    bw.main(["--program"])          # the release's build job
    assert order == ["version", "pyinstaller"]

    order.clear()
    stale = tmp_path / "dist" / bw.PORTABLE_NAME
    stale.write_bytes(b"from an earlier run")
    bw.main(["--package", "--authenticode", "--no-sign"])      # the release's authenticode job
    assert order == [("codesign", "UnifiStockWatcher.exe"), ("codesign", "UnifiStockWatcher-cli.exe"),
                     "zip", ("installer", True), ("manifest", False), "sums"], \
        "the exes must be signed before the zip and the installer pack them"
    assert not stale.exists(), "--package must not leave an earlier run's download"
    assert all((app / exe).is_file() for exe in bw.EXES), "--package keeps the program folder"


def test_the_build_refuses_what_it_cant_do_before_touching_anything(recorded, tmp_path):
    order, _app = recorded
    for argv in (["--authenticode"], ["--program", "--authenticode"],
                 ["--package", "--authenticode", "--no-installer"], ["--program", "--package"], ["--sign"]):
        with pytest.raises(SystemExit):
            bw.main(argv)
    with pytest.raises(SystemExit, match="needs the program folder"):
        bw.main(["--package"])
    assert order == [] and not (tmp_path / "dist").exists()


def test_a_signed_installer_has_inno_setup_sign_the_uninstaller_and_setup(monkeypatch):
    """Inno Setup makes the uninstaller while it compiles, so only its SignTool can sign it. The
    command reaches ISCC through its command line, which doesn't undo the \\" Python would write
    for a literal quote: hence Inno's $q."""
    calls = []
    monkeypatch.setattr(bw, "find_iscc", lambda: "ISCC.exe")
    monkeypatch.setattr(bw.subprocess, "run", lambda args, **kw: calls.append(args))
    bw.installer(code_sign=True)
    tool = [a for a in calls[0] if a.startswith("/S")]
    assert tool == [f"/Sauthenticode=pwsh -NoProfile -NonInteractive -File $q{bw.AUTHENTICODE}$q $f"]
    assert "/DSign" in calls[0] and '"' not in tool[0]
    iss = (ROOT / "installer" / "UnifiStockWatcher.iss").read_text(encoding="utf-8")
    block = iss[iss.index("#ifdef Sign"):iss.index("#endif", iss.index("#ifdef Sign"))]
    assert "SignTool=authenticode" in block and "SignedUninstaller=yes" in block

    calls.clear()
    bw.installer(code_sign=False)
    assert not [a for a in calls[0] if a.startswith(("/S", "/DSign"))], "an unsigned build signs nothing"

    monkeypatch.setattr(bw, "find_iscc", lambda: None)
    with pytest.raises(SystemExit):
        bw.installer(code_sign=True)


# ── release.yml ──────────────────────────────────────────────────────────────

def test_the_release_has_its_five_jobs():
    assert list(jobs(RELEASE)) == ["build", "authenticode", "verify", "sign", "publish"]


def test_only_the_authenticode_job_can_sign_in_to_azure():
    for name, job in jobs(RELEASE).items():
        if name != "authenticode":
            assert "id-token" not in job and "azure/login" not in job and "AZURE_" not in job, name
    for wf in (ROOT / ".github" / "workflows").glob("*.yml"):
        if wf.name not in ("release.yml", "pages.yml"):
            assert "id-token: write" not in wf.read_text(encoding="utf-8"), wf.name


def test_the_authenticode_job_runs_nothing_from_pypi_and_never_runs_the_program():
    """It can sign anything as James Cupps: a compromised build dependency must never run
    beside it. It gets the folder the build job froze, signs the exes without running them, and
    makes the zip and the installer with the standard library and the pinned Inno Setup."""
    job = code(jobs(RELEASE)["authenticode"])
    assert "environment: release" in job and "needs: build" in job
    assert "id-token: write" in job and "contents: read" in job and "contents: write" not in job
    for forbidden in ("pip ", "pytest", "PyInstaller", "pyflakes", "requirements", "test_portable",
                      "test_installer", "test_update", "--self-test", "--program", "UNIFI", "USW_"):
        assert forbidden not in job, f"the authenticode job runs {forbidden.strip()}"
    assert "python -I scripts/build_windows.py --package --authenticode --no-sign" in job
    assert "./scripts/install_inno.ps1" in job
    assert "Install-Module -Name ArtifactSigning -RequiredVersion 0.1.8" in job
    ps1 = (ROOT / "scripts" / "authenticode.ps1").read_text(encoding="utf-8")
    assert "Import-Module ArtifactSigning -RequiredVersion 0.1.8" in ps1, \
        "the script and the workflow must agree on the module version"
    assert "name: program-build" in job and "name: release-build" in job


def test_the_signed_files_are_tested_before_anything_is_signed_or_published():
    js = jobs(RELEASE)
    verify = code(js["verify"])
    assert "needs: [build, authenticode]" in verify and "name: release-build" in verify
    assert 'PUBLISHER: "CN=James Cupps, O=James Cupps"' in verify
    for step in ("./scripts/test_portable.ps1 -Publisher $env:PUBLISHER",
                 "-Version $v -Publisher $env:PUBLISHER", "./scripts/test_update.ps1"):
        assert step in verify, step
    assert "needs: [build, authenticode, verify]" in js["sign"]
    assert "needs: [build, authenticode, verify, sign]" in js["publish"]
    # The publish job merges every release-* artifact into dist: the unsigned program folder
    # must not be one of them.
    assert "pattern: release-*" in js["publish"] and "name: program-build" in js["build"]


def test_a_signtest_tag_is_never_published():
    js = jobs(RELEASE)
    for name in ("sign", "publish"):
        assert "if: needs.build.outputs.signtest != 'true'" in js[name], name
    for name in ("build", "authenticode", "verify"):
        assert not re.search(r"^    if:", js[name], re.M), f"{name} must run for a signing test too"
    assert "signtest: ${{ steps.tag.outputs.signtest }}" in js["build"]
