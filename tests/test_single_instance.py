"""One copy at a time: the app and the command-line watcher share one lock
(winplatform.SingleInstance), which the installer and the updater wait on.

The real mutexes are made under test names, so a copy of the app running on
this PC is never seen or poked."""

import sys
import uuid

import pytest

import unifi_watcher as cli
import unifi_watcher_gui as gui
import winplatform

windows_only = pytest.mark.skipif(sys.platform != "win32", reason="Windows named mutexes")


@pytest.fixture
def names(monkeypatch):
    base = f"JamesCupps.UnifiStockWatcher.Test.{uuid.uuid4().hex}"
    monkeypatch.setattr(winplatform, "MUTEX_NAME", base)
    monkeypatch.setattr(winplatform, "CLI_MUTEX_NAME", base + ".CLI")
    monkeypatch.setattr(winplatform, "TRAY_CLASS", "UnifiStockWatcherTrayTest")
    return base


@windows_only
def test_a_second_copy_waits_and_takes_the_lock_when_the_first_goes(names):
    first = winplatform.SingleInstance()
    assert not first.already_running
    second = winplatform.SingleInstance()
    assert second.already_running and second.handle is None
    # No tray window and not the CLI: a copy that's stuck (or still starting).
    assert second.wait_for_other(timeout=0.3, poll=0.05) is None
    first.release()
    assert second.wait_for_other(timeout=2, poll=0.05) == "gone"
    assert not second.already_running and second.handle
    third = winplatform.SingleInstance()
    assert third.already_running                 # the second copy holds it now
    second.release()
    third.release()


@windows_only
def test_the_command_line_watcher_is_told_apart(names):
    watcher = winplatform.SingleInstance(cli=True)
    app = winplatform.SingleInstance()
    assert app.wait_for_other(timeout=0.3, poll=0.05) == "cli"
    watcher.release()                             # both of its mutexes
    assert app.wait_for_other(timeout=2, poll=0.05) == "gone"
    app.release()


def _taken(cli=False):
    """A SingleInstance that found the lock taken, on any platform."""
    si = winplatform.SingleInstance.__new__(winplatform.SingleInstance)
    si.cli, si.handle, si.marker, si.already_running = cli, None, None, True
    return si


def test_a_copy_that_is_still_starting_is_waited_for(monkeypatch):
    """It holds the lock a few seconds before its tray window is up. The 3.2.1
    app found no window, so it started a second copy beside it."""
    looks = []

    def tray_window():
        looks.append(1)
        return 4242 if len(looks) > 5 else None
    monkeypatch.setattr(winplatform, "_tray_window", tray_window)
    monkeypatch.setattr(winplatform, "_mutex_exists", lambda name: name == winplatform.MUTEX_NAME)
    assert _taken().wait_for_other(timeout=5, poll=0.01) == "app"
    assert len(looks) == 6


def test_waiting_gives_up(monkeypatch):
    monkeypatch.setattr(winplatform, "_tray_window", lambda: None)
    monkeypatch.setattr(winplatform, "_mutex_exists", lambda name: name == winplatform.MUTEX_NAME)
    assert _taken().wait_for_other(timeout=0.05, poll=0.01) is None


# ── The app ──────────────────────────────────────────────────────────────────

class Lock:
    def __init__(self, running=False, other=None, shown=True):
        self.already_running, self.other, self.shown = running, other, shown
        self.waited, self.released, self.kwargs = None, False, None

    def wait_for_other(self, timeout=30, poll=0.1):
        self.waited = timeout
        return self.other

    def signal_existing(self):
        return self.shown

    def release(self):
        self.released = True


@pytest.fixture
def said(monkeypatch):
    said = []
    monkeypatch.setattr(gui, "_say_already_running", said.append)
    return said


def _use(monkeypatch, module, lock):
    def make(**kwargs):
        lock.kwargs = kwargs
        return lock
    monkeypatch.setattr(module.winplatform, "SingleInstance", make)


@pytest.mark.parametrize("other,shown,code,message", [
    ("app", True, 0, []),             # shown its window instead
    ("app", False, 1, ["app"]),       # its window went between looking and asking
    ("cli", True, 1, ["cli"]),
    (None, True, 1, [None]),          # stuck, or no tray window
])
def test_the_app_wont_start_beside_another_copy(monkeypatch, said, other, shown, code, message):
    lock = Lock(running=True, other=other, shown=shown)
    _use(monkeypatch, gui, lock)
    assert gui._single_instance() == (None, code)
    assert said == message
    assert lock.waited == gui.OTHER_COPY_WAIT


@pytest.mark.parametrize("lock", [Lock(), Lock(running=True, other="gone")],
                         ids=["free", "the other copy quit while this one waited"])
def test_the_app_starts_when_it_has_the_lock(monkeypatch, said, lock):
    _use(monkeypatch, gui, lock)
    assert gui._single_instance() == (lock, None)
    assert said == []


# ── The command-line watcher ─────────────────────────────────────────────────

@pytest.fixture
def quiet_cli(monkeypatch):
    ran = []
    monkeypatch.setattr(cli, "setup_logging", lambda **kw: None)
    monkeypatch.setattr(cli, "migrate_legacy_files", lambda: None)
    monkeypatch.setattr(cli.stock_history, "flush", lambda *a, **kw: None)
    monkeypatch.setattr(cli, "main", lambda: ran.append("main"))
    monkeypatch.setattr(cli, "run_setup", lambda: ran.append("setup"))
    monkeypatch.setattr(cli, "test_mode", lambda: ran.append("test"))
    monkeypatch.setattr(cli.time, "sleep", lambda s: None)
    return ran


@pytest.mark.parametrize("other,words", [
    ("app", "quit the app first"),
    ("cli", "Another command-line watcher"),
    (None, "may still be starting"),
])
@pytest.mark.parametrize("argv", [[], ["--setup"]])
def test_the_cli_wont_watch_beside_another_copy(monkeypatch, capsys, quiet_cli, argv, other, words):
    _use(monkeypatch, cli, Lock(running=True, other=other))
    assert cli.run(argv) == 1
    assert words in capsys.readouterr().out
    assert quiet_cli == []                    # no watching, and the watch list left alone


def test_the_cli_holds_the_lock_while_it_watches(monkeypatch, quiet_cli):
    lock = Lock()
    _use(monkeypatch, cli, lock)
    assert cli.run([]) == 0
    assert quiet_cli == ["main"]
    assert lock.kwargs == {"cli": True} and lock.released


def test_the_cli_test_mode_runs_beside_the_app(monkeypatch, quiet_cli):
    def no_lock(**kw):
        raise AssertionError("--test took the lock")
    monkeypatch.setattr(cli.winplatform, "SingleInstance", no_lock)
    assert cli.run(["--test"]) == 0
    assert quiet_cli == ["test"]
