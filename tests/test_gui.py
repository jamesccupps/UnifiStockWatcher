"""GUI behaviour.

Skipped where Tk cannot open a display. Windows never sees that; Linux CI
needs xvfb, which the workflow provides.
"""

import logging
import threading
import time

import pytest

import unifi_core
from conftest import product

tk = pytest.importorskip("tkinter")

try:
    _probe = tk.Tk()
    _probe.destroy()
except Exception:                                     # pragma: no cover
    pytest.skip("no display available for Tk", allow_module_level=True)

import unifi_watcher_gui as gui  # noqa: E402

logging.getLogger("unifi_watcher").setLevel(logging.CRITICAL)


@pytest.fixture
def app(isolated_files, monkeypatch):
    monkeypatch.setattr(gui, "get_build_id", lambda *a, **k: "bid")
    monkeypatch.setattr(gui, "fetch_all_products", lambda *a, **k: [])
    a = gui.UnifiWatcherApp()
    a.withdraw()
    yield a
    a.watching = False
    a._wake.set()
    try:
        a.destroy()
    except tk.TclError:
        pass


def pump(root, seconds=0.3):
    """Run a real event loop for a bounded time.

    mainloop() rather than repeated update(): worker threads call after(),
    which raises "main thread is not in main loop" unless the loop is running.
    """
    root.after(int(seconds * 1000), root.quit)
    root.mainloop()


def pump_until(root, predicate, timeout=10.0):
    """Run the event loop until predicate() is true, or timeout.

    Preferred over a fixed pump() wherever a background thread has to finish
    first: a CI runner is far slower than a dev box, and a hardcoded wait
    turns "slow" into "failed".
    """
    deadline = time.monotonic() + timeout

    def poll():
        if predicate() or time.monotonic() > deadline:
            root.quit()
        else:
            root.after(20, poll)

    root.after(0, poll)
    root.mainloop()
    return predicate()


# ── watcher state ────────────────────────────────────────────────────────────

def test_applying_settings_while_watching_keeps_the_button_correct(app):
    """Regression: the rebuild reset the button, so Start actually stopped it."""
    app.watched = [{"title": "T", "slug": "s", "favourite": False}]
    app._refresh_list()
    app.watching = True
    app.start_btn.config(text="⏹  Stop Watching")
    app._set_status("Watching…", app.C["green"])

    app._on_settings_apply(dict(app.settings))

    assert app.watching is True
    assert "Stop Watching" in app.start_btn.cget("text")
    assert app.status_lbl.cget("text") == "Watching…"


def test_changing_region_clears_the_catalog_baseline(app):
    """Regression: US stock was diffed against EU and reported as changes."""
    app._prev_status = {"s": (True, "T", "$1.00")}
    app.notified = {"s": True}
    app._delisted = {"gone"}

    new = dict(app.settings)
    new["region"] = "eu"
    app._on_settings_apply(new)

    assert app._prev_status == {}
    assert app.notified == {}
    assert app._delisted == set()


def test_same_region_keeps_the_baseline(app):
    app._prev_status = {"s": (True, "T", "$1.00")}
    app._on_settings_apply(dict(app.settings))
    assert app._prev_status != {}


def test_force_check_wakes_the_countdown(app):
    app.watching = True
    app._wake.clear()
    app._force_check()
    assert app._wake.is_set()


def test_stop_wakes_the_countdown(app):
    app.watched = [{"title": "T", "slug": "s", "favourite": False}]
    app.watching = True
    app._wake.clear()
    app._toggle_watch()
    assert app.watching is False
    assert app._wake.is_set()


def test_wait_returns_immediately_when_woken(app):
    app.watching = True
    threading.Timer(0.05, app._wake.set).start()
    started = time.monotonic()
    app._wait_for_next_cycle(30)
    assert time.monotonic() - started < 2.0


def test_close_stops_watching_and_flushes(app, monkeypatch):
    flushed = []
    monkeypatch.setattr(gui.stock_history, "flush", lambda: flushed.append(1))
    app.watching = True
    app._on_close()
    assert app.watching is False
    assert flushed == [1]


# ── delisted items ───────────────────────────────────────────────────────────

def test_a_delisted_item_is_checked_once(app, monkeypatch):
    """Regression: ProductNotFound was retried with backoff every cycle."""
    app.watched = [{"title": "Gone", "slug": "gone", "favourite": False}]
    app._refresh_list()
    app.settings["poll_interval"] = 1

    calls = []

    def raiser(*a, **k):
        calls.append(1)
        raise unifi_core.ProductNotFound("gone")

    monkeypatch.setattr(gui, "check_slug", raiser)
    app.watching = True
    threading.Thread(target=app._watch_loop, daemon=True).start()
    assert pump_until(app, lambda: "gone" in app._delisted)
    # give the loop room to wrongly re-check across further cycles
    pump(app, 2.5)
    app.watching = False
    app._wake.set()

    assert calls == [1]
    assert "gone" in app._delisted
    assert app.rows["gone"].badge.cget("text") == "DELISTED"


# ── text widget bounds ───────────────────────────────────────────────────────

def test_activity_log_is_bounded(app):
    for i in range(gui.MAX_LOG_LINES + 250):
        app._log(f"line {i}")
    lines = int(app.log_text.index("end-1c").split(".")[0])
    assert lines == gui.MAX_LOG_LINES


def test_changes_feed_is_bounded_but_counts_everything(app):
    total = gui.MAX_CHANGE_LINES + 100
    for i in range(total):
        app._add_change(f"P{i}", i % 2 == 0)
    lines = int(app.changes_text.index("end-1c").split(".")[0])
    assert lines == gui.MAX_CHANGE_LINES
    assert app._changes_count_lbl.cget("text") == f"({total})"


# ── browse dialog ────────────────────────────────────────────────────────────

@pytest.fixture
def browse(app, monkeypatch):
    catalog = [product("in-stock-a", "Alpha", status="Available", amount=10000),
               product("oos-b", "Bravo", status="SoldOut", amount=20000),
               product("oos-c", "Charlie", status="SoldOut", amount=30000)]
    for p in catalog:
        p["_category"] = "category/all-switching"
    monkeypatch.setattr(gui, "fetch_all_products", lambda *a, **k: catalog)
    picks = []
    d = gui.BrowseDialog(app, [{"slug": "oos-c", "title": "Charlie"}],
                         picks.extend, app.C, app.settings)
    d.withdraw()
    d.all_prods = sorted(catalog, key=lambda p: p["title"])
    d.picks = picks
    pump_until(app, lambda: d._filter_job is None)
    d.all_prods = sorted(catalog, key=lambda p: p["title"])
    yield d
    try:
        d.destroy()
    except tk.TclError:
        pass


def shown(dialog):
    """Rows currently placed by the geometry manager.

    winfo_ismapped() is False for everything inside an unmapped Toplevel, so
    it cannot distinguish shown from hidden rows in a headless test.
    """
    return [r["cb"].cget("text").strip() for r in dialog._row_pool
            if r["frame"].winfo_manager()]


def test_browse_defaults_to_out_of_stock_only(browse):
    browse._filter()
    assert shown(browse) == ["Bravo", "Charlie (watching)"]


def test_browse_show_in_stock_includes_everything(browse):
    browse._stock_var.set(True)
    browse._filter()
    assert shown(browse) == ["Alpha", "Bravo", "Charlie (watching)"]


def test_browse_rows_are_reused_not_recreated(browse):
    """Regression: every filter pass destroyed and rebuilt every row."""
    browse._stock_var.set(True)
    browse._filter()
    first = [id(r["frame"]) for r in browse._row_pool]
    for text in ("a", "al", "alp"):
        browse.q.set(text)
        browse._filter()
    browse.q.set("")
    browse._filter()
    assert [id(r["frame"]) for r in browse._row_pool] == first


def test_browse_narrowing_hides_surplus_rows(browse):
    browse._stock_var.set(True)
    browse._filter()
    browse.q.set("alpha")
    browse._filter()
    assert shown(browse) == ["Alpha"]
    assert sum(1 for r in browse._row_pool
               if not r["frame"].winfo_manager()) == 2


def test_browse_selection_survives_filtering(browse):
    browse._stock_var.set(True)
    browse._filter()
    browse.check_vars["oos-b"].set(True)
    browse.q.set("alpha")
    browse._filter()
    browse.q.set("")
    browse._filter()
    assert browse.check_vars["oos-b"].get() is True


def test_browse_disables_already_watched_rows(browse):
    browse._filter()
    row = next(r for r in browse._row_pool
               if "Charlie" in r["cb"].cget("text"))
    assert row["cb"].cget("state") == "disabled"


def test_browse_confirm_excludes_already_watched(browse):
    browse._stock_var.set(True)
    browse._filter()
    browse.check_vars["oos-b"].set(True)
    browse.check_vars["oos-c"].set(True)      # already watched
    browse._confirm()
    assert [p["slug"] for p in browse.picks] == ["oos-b"]


def test_browse_search_is_debounced(browse, app):
    # Let the dialog's own background fetch land first; its _on_fetched also
    # calls _filter and would otherwise be counted.
    pump_until(app, lambda: browse._filter_job is None and browse.filtered)
    browse._stock_var.set(True)
    browse._filter()

    calls = []
    real = browse._filter
    browse._filter = lambda: (calls.append(1), real())
    for text in ("b", "br", "bra", "brav", "bravo"):
        browse.q.set(text)
    assert calls == []                      # nothing ran while typing
    assert pump_until(app, lambda: len(calls) >= 1)
    pump(app, 0.4)                          # nothing further should arrive
    assert len(calls) == 1                  # one coalesced pass
    assert shown(browse) == ["Bravo"]


def test_browse_reports_fetch_failure(app, monkeypatch):
    """Regression: a deferred lambda closing over `ex` raised NameError."""
    def boom(*a, **k):
        raise RuntimeError("store unreachable")

    monkeypatch.setattr(gui, "get_build_id", boom)
    d = gui.BrowseDialog(app, [], lambda *_: None, app.C, app.settings)
    d.withdraw()
    pump_until(app, lambda: "Error" in d.status_lbl.cget("text"))
    assert "store unreachable" in d.status_lbl.cget("text")
    d.destroy()


def test_wheel_binding_does_not_outlive_the_dialog(app, monkeypatch):
    """Regression: bind_all left a dead canvas receiving wheel events."""
    monkeypatch.setattr(gui, "fetch_all_products", lambda *a, **k: [])
    errors = []
    app.report_callback_exception = lambda *a: errors.append(a)
    d = gui.BrowseDialog(app, [], lambda *_: None, app.C, app.settings)
    d.withdraw()
    pump(app, 0.3)
    d.destroy()
    app.update()
    for _ in range(5):
        app.event_generate("<MouseWheel>", delta=120, x=10, y=10)
    app.update()
    assert errors == []


# ── watched row ──────────────────────────────────────────────────────────────

def test_watched_row_shows_stock_states(app):
    app.watched = [{"title": "T", "slug": "s", "favourite": False}]
    app._refresh_list()
    row = app.rows["s"]

    row.update_status(True, "12:00:00", "$99.00")
    assert row.badge.cget("text") == "IN STOCK"

    row.update_status(False, "12:01:00", "$99.00")
    assert row.badge.cget("text") == "OUT OF STOCK"

    row.mark_delisted()
    assert row.badge.cget("text") == "DELISTED"
    assert row.in_stock is None


def test_favourites_are_listed_first(app):
    app.watched = [{"title": "Plain", "slug": "p", "favourite": False},
                   {"title": "Starred", "slug": "s", "favourite": True}]
    app._refresh_list()
    assert set(app.rows) == {"p", "s"}
    assert "1 ★" in app.title()
