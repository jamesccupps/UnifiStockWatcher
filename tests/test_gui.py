"""GUI behaviour.

Skipped where Tk cannot open a display. Windows never sees that; Linux CI
needs xvfb, which the workflow provides. The app runs without its tray icon
and without update checks here.
"""

import logging
import threading
import time

import pytest

import unifi_core
import webhooks
from conftest import FakeResponse, product

tk = pytest.importorskip("tkinter")

import unifi_watcher_gui as gui  # noqa: E402
import updater  # noqa: E402
from tkinter import ttk  # noqa: E402

logging.getLogger("unifi_watcher").setLevel(logging.CRITICAL)


def _quiesce(a):
    """Stop the watcher and join its workers."""
    a.watching = False
    a._wake.set()
    for t in threading.enumerate():
        if t is not threading.current_thread() and t.name.startswith("unifi-"):
            t.join(timeout=5)


@pytest.fixture(scope="module")
def _session_app():
    """The one and only Tk interpreter this module creates.

    Every Tcl interpreter beyond the first is a liability. Creating *and
    destroying* a root per test progressively breaks Tcl's library-path state,
    so a later Tk() dies with `invalid command name "tcl_findLibrary"` or
    `Can't find a usable init.tcl` - intermittently, in whichever test runs
    next. So: one root, created once, reused by every test.
    """
    try:
        a = gui.UnifiWatcherApp(use_tray=False, check_updates=False)
    except Exception as e:                            # pragma: no cover
        pytest.skip(f"Tk unavailable: {e}")
        return
    a.withdraw()
    yield a
    _quiesce(a)
    try:
        a.destroy()
    except tk.TclError:
        pass


@pytest.fixture
def app(_session_app, isolated_files, monkeypatch):
    """Hand each test a clean app without building a new Tcl interpreter."""
    monkeypatch.setattr(gui, "get_build_id", lambda *a, **k: "bid")
    monkeypatch.setattr(gui, "fetch_all_products", lambda *a, **k: [])
    monkeypatch.setattr(gui, "refresh_category_coverage", lambda *a, **k: [])
    # A watched item missing from the (empty) fake catalog falls back to
    # check_slug, whose retries sleep 1 s and 2 s against the blocked network.
    monkeypatch.setattr(gui, "check_slug", lambda *a, **k: (False, None))
    monkeypatch.setattr(gui, "play_sound", lambda: None)
    monkeypatch.setattr(gui.webbrowser, "open", lambda *a, **k: None)
    monkeypatch.setattr(gui, "notify_windows", lambda *a, **k: None)

    a = _session_app
    _quiesce(a)
    a._wake.clear()
    a.watched = []
    a.status = {}
    a.notified = {}
    a._prev_status = {}
    a._delisted = set()
    a._closed = False
    a.watcher_thread = None
    a.available_update = None
    a.update_busy = False
    a._status_text = ("Idle", "text3")
    a.settings = gui.load_settings()
    a._log_lines.clear()
    a._changes.clear()
    a._change_count = 0
    a._tab = "watch"
    # The rebuild is production code (theme changes use it), so the reset
    # path is itself covered.
    a._rebuild_ui()

    yield a

    _quiesce(a)
    a._closed = False


def pump(root, seconds=0.3):
    """Run a real event loop for a bounded time."""
    root.after(int(seconds * 1000), root.quit)
    root.mainloop()


def pump_until(root, predicate, timeout=10.0):
    """Run the event loop until predicate() is true, or timeout.

    Preferred over a fixed pump() wherever a background thread has to finish
    first: a CI runner is far slower than a dev box.
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


def items(*slugs, fav=()):
    return [{"title": s.title(), "slug": s, "favourite": s in fav} for s in slugs]


# ── watcher state ────────────────────────────────────────────────────────────

def test_rebuilding_while_watching_keeps_the_button_correct(app):
    """Regression: the rebuild reset the button, so Start actually stopped it."""
    app.watched = items("s")
    app.watching = True
    app._set_status("Watching", "green")

    app.set_setting("theme", "light" if app.settings["theme"] != "light" else "dark")

    assert app.watching is True
    assert app.start_btn.cget("text") == "Stop watching"
    assert app.status_lbl.cget("text") == "Watching"


def test_a_rebuild_keeps_row_status_and_logs(app):
    app.watched = items("a", "b")
    app._refresh_list()
    app._update_row("a", True, "12:00", "$1.00")
    app._log("hello there")
    app._add_change("A", True, "$1.00")
    app.set_setting("font_size", 12)
    assert app.rows["a"].pill.cget("text") == "IN STOCK"
    assert app.rows["b"].pill.cget("text") == "NOT CHECKED"
    assert any("hello there" in m for _, m, _ in app._log_lines)
    assert "A" in app.changes_text.get("1.0", "end")


def test_removing_an_item_keeps_the_others_status(app):
    """Regression (2.x): any add or remove reset every row to unknown."""
    app.watched = items("a", "b")
    app._refresh_list()
    app._update_row("a", False, "12:00", "$1.00", "back ~tomorrow")
    app._remove_item({"slug": "b", "title": "B"})
    assert app.rows["a"].pill.cget("text") == "SOLD OUT"
    assert "back ~tomorrow" in app.rows["a"].sub.cget("text")


def test_changing_region_clears_the_catalog_baseline(app):
    """Regression: US stock was diffed against EU and reported as changes."""
    app._prev_status = {"s": (True, "T", "$1.00", None)}
    app.notified = {"s": True}
    app._delisted = {"gone"}
    app.status = {"s": (True, "1", None, None)}

    app.set_setting("region", "eu")

    assert app._prev_status == {}
    assert app.notified == {}
    assert app._delisted == set()
    assert app.status == {}
    assert unifi_core.load_settings()["region"] == "eu"


def test_same_region_keeps_the_baseline(app):
    app._prev_status = {"s": (True, "T", "$1.00", None)}
    app._on_settings_apply(dict(app.settings))
    assert app._prev_status != {}


def test_settings_are_saved_as_they_change(app):
    app.set_setting("poll_interval", 300)
    assert unifi_core.load_settings()["poll_interval"] == 300
    assert "5 minutes" in app.store_lbl.cget("text")


def test_force_check_wakes_the_countdown(app):
    app.watching = True
    app._wake.clear()
    app._force_check()
    assert app._wake.is_set()


def test_check_now_when_idle_runs_one_check(app, monkeypatch):
    calls = []
    monkeypatch.setattr(gui, "fetch_all_products", lambda *a, **k: calls.append(1) or [])
    app.watched = items("s")
    app._force_check()
    assert pump_until(app, lambda: calls and not app.watcher_thread.is_alive())
    assert calls == [1] and app.watching is False


def test_stop_wakes_the_countdown(app):
    app.watched = items("s")
    app.watching = True
    app._wake.clear()
    app._toggle_watch()
    assert app.watching is False
    assert app._wake.is_set()


def test_wait_returns_immediately_when_woken(app):
    app.watching = True
    app._wake.clear()
    threading.Timer(0.05, app._wake.set).start()
    started = time.monotonic()
    app._wait_for_next_cycle(30)
    elapsed = time.monotonic() - started
    assert elapsed < 5.0, f"countdown ignored the wake event ({elapsed:.1f}s)"


def test_wake_raised_during_a_cycle_is_not_swallowed(app):
    """Regression: Check Now pressed *during* the fetch was discarded."""
    app.watching = True
    app._wake.set()                      # as if F5 landed mid-cycle
    started = time.monotonic()
    app._wait_for_next_cycle(30)
    elapsed = time.monotonic() - started
    assert elapsed < 5.0, f"pending wake was discarded ({elapsed:.1f}s)"


def test_each_cycle_starts_from_a_clear_wake(app, monkeypatch):
    """The clear belongs at the start of the work, not the start of the wait."""
    seen = []
    monkeypatch.setattr(gui, "fetch_all_products",
                        lambda *a, **k: seen.append(app._wake.is_set()) or [])
    app.settings["poll_interval"] = 1
    app.watching = True
    app._wake.set()                      # stale wake left over from before
    threading.Thread(target=app._watch_loop, daemon=True,
                     name="unifi-watch-test").start()
    pump_until(app, lambda: len(seen) >= 2)
    _quiesce(app)
    assert seen[:2] == [False, False], seen


def test_workers_never_call_tk(app, monkeypatch):
    """Worker results reach the window through the queue only: a Tk call from
    another thread waits for the main loop and, after close, raises."""
    called_from = []
    real_after = app.after

    def spy(*a, **k):
        called_from.append(threading.current_thread().name)
        return real_after(*a, **k)
    monkeypatch.setattr(app, "after", spy)
    app.watched = items("s")
    monkeypatch.setattr(gui, "fetch_all_products",
                        lambda *a, **k: [product("s", status="Available")])
    t = threading.Thread(target=app._cycle, name="unifi-watch-test")
    t.start()
    t.join(5)
    assert all(n == "MainThread" for n in called_from), called_from
    pump_until(app, lambda: "s" in app.status)
    assert app.status["s"][0] is True


def test_closing_without_a_tray_quits_and_flushes(app, monkeypatch):
    flushed, destroyed = [], []
    monkeypatch.setattr(gui.stock_history, "flush", lambda: flushed.append(1))
    # The app is shared across this module, so record the teardown rather than
    # actually destroying the interpreter out from under the other tests.
    monkeypatch.setattr(type(app), "destroy", lambda self: destroyed.append(1))
    app.tray = None
    app.watching = True
    app._on_close()
    assert app.watching is False
    assert app._wake.is_set()
    assert flushed == [1]
    assert destroyed == [1]


def test_closing_with_a_tray_keeps_watching(app, monkeypatch):
    class FakeTray:
        def __init__(self):
            self.notes = []

        def notify(self, *a, **k):
            self.notes.append(a)

        def set_state(self, *a):
            pass

    monkeypatch.setattr(app, "withdraw", lambda: None)
    app.tray = FakeTray()
    app._tray_hint_shown = False
    app.watching = True
    try:
        app._on_close()
        assert app.watching is True
        assert len(app.tray.notes) == 1          # the one-time hint
        app._on_close()
        assert len(app.tray.notes) == 1
    finally:
        app.tray = None
        app.watching = False


# ── alerts ───────────────────────────────────────────────────────────────────

def test_an_item_coming_back_alerts_once(app, monkeypatch):
    notes = []
    monkeypatch.setattr(app, "_notify", lambda *a: notes.append(a))
    app.watched = items("s")
    catalog = [product("s", status="Available")]
    monkeypatch.setattr(gui, "fetch_all_products", lambda *a, **k: catalog)
    app._cycle()
    app._cycle()
    pump(app, 0.2)
    assert len(notes) == 1
    assert "s" in notes[0][2]                     # the product's URL


def test_a_quick_check_and_a_cycle_together_alert_once(app, monkeypatch):
    """Regression: both saw notified=False on their own thread and posted."""
    notes = []
    monkeypatch.setattr(app, "_notify", lambda *a: notes.append(a))
    item = items("s")[0]
    app._maybe_alert(item, True)
    app._maybe_alert(item, True)
    pump(app, 0.2)
    assert len(notes) == 1


def test_an_alert_is_counted(app, monkeypatch):
    before = gui.stock_history.get_stats()["alerts"]
    monkeypatch.setattr(app, "_notify", lambda *a: None)
    app._on_in_stock(items("s")[0])
    assert gui.stock_history.get_stats()["alerts"] == before + 1


def test_alert_settings_are_honoured(app, monkeypatch):
    notes, opened = [], []
    monkeypatch.setattr(app, "_notify", lambda *a: notes.append(a))
    monkeypatch.setattr(gui.webbrowser, "open", lambda url: opened.append(url))
    app.settings.update(notifications=False, auto_open_url=False)
    app._on_in_stock(items("s")[0])
    assert notes == [] and opened == []
    app.settings.update(notifications=True, auto_open_url=True)
    app._on_in_stock(items("s")[0])
    assert len(notes) == 1 and opened == [unifi_core.product_url("s", app.settings["region"])]


# ── alerts on your phone ─────────────────────────────────────────────────────

NTFY = {"phone_alerts": True, "phone_service": "ntfy", "ntfy_url": "https://ntfy.sh/usw-test"}


def test_a_phone_alert_goes_out_with_the_price(app, monkeypatch):
    sent = []
    monkeypatch.setattr(app, "_notify", lambda *a: None)
    monkeypatch.setattr(app.phone, "submit", lambda s, a, n, cb: sent.append((s, a, n)))
    app.settings.update(NTFY)
    app.status["s"] = (True, "now", "$99.00", None)
    app._on_in_stock(items("s", fav=("s",))[0])
    assert len(sent) == 1
    settings, alert, attempts = sent[0]
    assert settings["ntfy_url"] == NTFY["ntfy_url"] and attempts == webhooks.ATTEMPTS
    assert alert.slug == "s" and alert.price == "$99.00" and alert.favourite
    app.settings["phone_alerts"] = False
    app._on_in_stock(items("s")[0])
    assert len(sent) == 1


def test_the_test_alert_reaches_the_phone_when_on(app, monkeypatch):
    sent = []
    monkeypatch.setattr(app, "_notify", lambda *a: None)
    monkeypatch.setattr(app.phone, "submit", lambda s, a, n, cb: sent.append((a, n)))
    app._test_notif()
    assert sent == []
    app.settings.update(NTFY)
    app._test_notif()
    assert len(sent) == 1 and sent[0][0].test and sent[0][1] == 1


def test_a_phone_result_is_logged(app, monkeypatch):
    monkeypatch.setattr(webhooks, "http_post",
                        lambda *a: FakeResponse(404, {"message": "Unknown Webhook"}))
    app.settings.update(phone_alerts=True, phone_service="discord",
                        discord_url="https://discord.com/api/webhooks/1/SECRET")
    app._send_phone(webhooks.in_stock_alert("Thing", "s"), attempts=1)
    expect = "Couldn't send the alert for Thing to Discord: HTTP 404 (Unknown Webhook)"
    assert pump_until(app, lambda: any(m == expect for _, m, _ in app._log_lines))
    assert not any("SECRET" in m for _, m, _ in app._log_lines)


def _entries(widget):
    found = []
    for child in widget.winfo_children():
        if isinstance(child, ttk.Entry):
            found.append(child)
        found += _entries(child)
    return found


def test_phone_fields_are_saved_and_follow_the_service(app):
    app._show_tab("settings")
    st = app._settings_widget
    url, token = _entries(st.phone_fields)
    url.insert(0, " https://ntfy.sh/my-topic \n")
    for save in st._phone_savers:
        save()
    assert unifi_core.load_settings()["ntfy_url"] == "https://ntfy.sh/my-topic"
    st.service_box.set("Discord")
    st._service_changed(None)
    assert app.settings["phone_service"] == "discord"
    assert len(_entries(st.phone_fields)) == 1
    st.service_box.set("ntfy")
    st._service_changed(None)
    assert _entries(st.phone_fields)[0].get() == "https://ntfy.sh/my-topic"


def test_typing_saves_after_a_pause(app):
    app._show_tab("settings")
    st = app._settings_widget
    _entries(st.phone_fields)[0].insert(0, "https://ntfy.sh/typed")
    assert pump_until(app, lambda: app.settings["ntfy_url"] == "https://ntfy.sh/typed", 3)


def test_the_phone_test_button_reports_what_happened(app, monkeypatch):
    app._show_tab("settings")
    st = app._settings_widget
    st._phone_test()                                   # nothing entered yet
    assert "topic's address" in st.phone_lbl.cget("text")
    _entries(st.phone_fields)[0].insert(0, NTFY["ntfy_url"])   # saved by the button
    monkeypatch.setattr(webhooks, "http_post", lambda *a: FakeResponse(200))
    st._phone_test()
    assert pump_until(app, lambda: st.phone_lbl.cget("text").startswith("Sent to ntfy"))
    monkeypatch.setattr(webhooks, "http_post", lambda *a: FakeResponse(
        401, {"error": "unauthorized"}))
    st._phone_test()
    assert pump_until(app, lambda: "HTTP 401 (unauthorized)" in st.phone_lbl.cget("text"))


# ── delisted items ───────────────────────────────────────────────────────────

def test_a_delisted_item_is_checked_once(app, monkeypatch):
    """Regression: ProductNotFound was retried with backoff every cycle."""
    app.watched = items("gone")
    app._refresh_list()
    app.settings["poll_interval"] = 1

    calls = []

    def raiser(*a, **k):
        calls.append(1)
        raise unifi_core.ProductNotFound("gone")

    monkeypatch.setattr(gui, "check_slug", raiser)
    app.watching = True
    threading.Thread(target=app._watch_loop, daemon=True,
                     name="unifi-watch-test").start()
    assert pump_until(app, lambda: "gone" in app._delisted)
    pump(app, 2.5)                       # room to wrongly re-check
    _quiesce(app)

    assert calls == [1]
    assert app.rows["gone"].pill.cget("text") == "DELISTED"


# ── store-wide changes ───────────────────────────────────────────────────────

def test_store_wide_changes_reach_the_feed_and_history(app, monkeypatch):
    state = {"x": "SoldOut"}
    monkeypatch.setattr(gui, "fetch_all_products",
                        lambda *a, **k: [product("x", "Xeno", status=state["x"])])
    app._cycle()                         # baseline
    state["x"] = "Available"
    app._cycle()
    pump(app, 0.2)
    assert app._change_count == 1
    assert "Xeno" in app.changes_text.get("1.0", "end")
    ev = gui.stock_history.get_events(limit=1)[0]
    assert ev["slug"] == "x" and ev["in_stock"] is True and ev["watched"] is False


# ── mouse wheel ──────────────────────────────────────────────────────────────

def test_wheel_binding_survives_leaving_the_container(app):
    """Regression: the binding was installed on <Enter> of the container and
    removed on <Leave>; moving onto a child row fires <Leave>."""
    app.watched = items("s")
    app._refresh_list()
    assert app.bind("<MouseWheel>"), "no wheel binding on the toplevel"
    app.list_canvas.event_generate("<Enter>")
    app.update()
    app.list_frame.event_generate("<Leave>")
    app.update()
    assert app.bind("<MouseWheel>")
    assert gui._wheel_target(app.rows["s"].title_lbl) is app.list_canvas


def test_wheel_target_resolves_through_nested_widgets(app):
    app.watched = items("s")
    app._refresh_list()
    assert gui._wheel_target(app.rows["s"].title_lbl) is app.list_canvas
    assert gui._wheel_target(app.list_canvas) is app.list_canvas
    assert gui._wheel_target(app.log_text) is None      # not a scroll area


def test_wheel_scrolls_when_the_pointer_is_over_a_row(app):
    """Regression: scrolling did nothing at all (see bind_wheel)."""
    app.watched = items(*[f"s{i}" for i in range(30)])
    app._refresh_list()
    app.deiconify()
    app.geometry("700x800+40+40")
    try:
        cv = app.list_canvas
        under = px = py = None
        for _ in range(100):
            app.update()
            if cv.winfo_width() > 1 and cv.winfo_height() > 1:
                px = cv.winfo_rootx() + cv.winfo_width() // 2
                py = cv.winfo_rooty() + cv.winfo_height() // 3
                under = app.winfo_containing(px, py)
                if under is not None and under is not cv:
                    break
            time.sleep(0.02)
        if under is None or under is cv:
            pytest.skip("window never mapped; no usable on-screen geometry")
        assert gui._wheel_target(under) is cv
        before = cv.yview()[0]
        under.event_generate("<MouseWheel>", delta=-120, rootx=px, rooty=py, x=5, y=5)
        app.update()
        assert cv.yview()[0] > before, "wheel over a row did not scroll"
    finally:
        app.withdraw()


# ── text widget bounds ───────────────────────────────────────────────────────

def test_activity_log_is_bounded(app):
    app._set_bottom("activity")
    for i in range(gui.MAX_LOG_LINES + 250):
        app._log(f"line {i}")
    lines = int(app.log_text.index("end-1c").split(".")[0])
    assert lines == gui.MAX_LOG_LINES
    assert len(app._log_lines) == gui.MAX_LOG_LINES


def test_changes_feed_is_bounded_but_counts_everything(app):
    app._set_bottom("changes")
    total = gui.MAX_CHANGE_LINES + 100
    for i in range(total):
        app._add_change(f"P{i}", i % 2 == 0)
    lines = int(app.changes_text.index("end-1c").split(".")[0])
    assert lines == gui.MAX_CHANGE_LINES
    assert app._changes_count_lbl.cget("text") == f"{total} this session"


def test_the_feed_and_log_switch_without_losing_lines(app):
    app._log("a log line")
    app._add_change("Changed", True)
    app._set_bottom("activity")
    assert "a log line" in app.log_text.get("1.0", "end")
    app._set_bottom("changes")
    assert "Changed" in app.log_text.get("1.0", "end")


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
    d = gui.BrowseDialog(app, [{"slug": "oos-c", "title": "Charlie"}], picks.extend)
    d.withdraw()
    d.picks = picks
    # Until _on_fetched has run on the main loop, not merely until the worker
    # has the catalog: its own filter pass would land inside the test.
    pump_until(app, lambda: d.loaded and d._filter_job is None)
    yield d
    try:
        d.destroy()
        app.update()
    except tk.TclError:
        pass


def shown(dialog):
    return [dialog.tree.item(i, "values")[0] for i in dialog.tree.get_children()]


def test_browse_defaults_to_out_of_stock_only(browse):
    browse._filter()
    assert shown(browse) == ["Bravo", "Charlie"]


def test_browse_show_in_stock_includes_everything(browse):
    browse._stock_var.set(True)
    browse._filter()
    assert shown(browse) == ["Alpha", "Bravo", "Charlie"]


def test_browse_narrowing(browse):
    browse._stock_var.set(True)
    browse.q.set("alpha")
    browse._filter()
    assert shown(browse) == ["Alpha"]


def test_browse_selection_survives_filtering(browse):
    browse._stock_var.set(True)
    browse._filter()
    browse._toggle("oos-b")
    browse.q.set("alpha")
    browse._filter()
    browse.q.set("")
    browse._filter()
    assert "oos-b" in browse.selected
    assert str(browse.tree.item("oos-b", "image")[0]) == str(app_image(browse, "check_on"))


def app_image(dialog, name):
    return dialog.T.image(name)


def test_browse_watched_rows_cannot_be_ticked(browse):
    browse._filter()
    browse._toggle("oos-c")
    assert "oos-c" not in browse.selected
    assert browse.tree.item("oos-c", "values")[3] == "On your watch list"


def test_browse_confirm_excludes_already_watched(browse):
    browse._stock_var.set(True)
    browse._filter()
    browse._toggle("oos-b")
    browse.selected.add("oos-c")                   # forced past the guard
    browse._confirm()
    assert [p["slug"] for p in browse.picks] == ["oos-b"]


def test_browse_add_button_follows_the_selection(browse):
    browse._filter()
    assert str(browse.add_btn.cget("state")) == "disabled"
    browse._toggle("oos-b")
    assert str(browse.add_btn.cget("state")) == "normal"


def test_browse_sorts_by_price(browse):
    browse._stock_var.set(True)
    browse._filter()
    browse._sort_by("price")
    assert shown(browse) == ["Alpha", "Bravo", "Charlie"]
    browse._sort_by("price")
    assert shown(browse) == ["Charlie", "Bravo", "Alpha"]


def test_browse_search_is_debounced(browse, app):
    browse._stock_var.set(True)
    browse._filter()
    calls = []
    real = browse._filter
    browse._filter = lambda: (calls.append(1), real())
    for text in ("b", "br", "bra", "brav", "bravo"):
        browse.q.set(text)
    assert calls == []                      # nothing ran while typing
    assert pump_until(app, lambda: len(calls) >= 1)
    pump(app, 0.4)
    assert len(calls) == 1                  # one coalesced pass
    assert shown(browse) == ["Bravo"]


def test_browse_renders_a_full_catalog_quickly(app, monkeypatch):
    catalog = [product(f"p{i}", f"Product {i:03d}", status="SoldOut") for i in range(600)]
    monkeypatch.setattr(gui, "fetch_all_products", lambda *a, **k: catalog)
    d = gui.BrowseDialog(app, [], lambda *_: None)
    d.withdraw()
    try:
        assert pump_until(app, lambda: d.loaded)
        started = time.perf_counter()
        d._filter()
        assert len(d.tree.get_children()) == 600
        assert time.perf_counter() - started < 1.5
    finally:
        d.destroy()


def test_browse_reports_fetch_failure(app, monkeypatch):
    """Regression: a deferred lambda closing over `ex` raised NameError."""
    def boom(*a, **k):
        raise RuntimeError("store unreachable")

    monkeypatch.setattr(gui, "get_build_id", boom)
    d = gui.BrowseDialog(app, [], lambda *_: None)
    d.withdraw()
    pump_until(app, lambda: "unreachable" in d.status_lbl.cget("text"))
    assert "store unreachable" in d.status_lbl.cget("text")
    d.destroy()


def test_browse_closed_mid_fetch_is_quiet(app, monkeypatch):
    errors = []
    app.report_callback_exception = lambda *a: errors.append(a)
    gate = threading.Event()
    monkeypatch.setattr(gui, "fetch_all_products", lambda *a, **k: gate.wait(5) and [])
    d = gui.BrowseDialog(app, [], lambda *_: None)
    d.withdraw()
    d.destroy()
    gate.set()
    pump(app, 0.4)
    assert errors == []


def test_wheel_binding_does_not_outlive_the_dialog(app, monkeypatch):
    """Regression: bind_all left a dead canvas receiving wheel events."""
    errors = []
    app.report_callback_exception = lambda *a: errors.append(a)
    d = gui.BrowseDialog(app, [], lambda *_: None)
    d.withdraw()
    pump(app, 0.3)
    d.destroy()
    app.update()
    for _ in range(5):
        app.event_generate("<MouseWheel>", delta=120, x=10, y=10)
    app.update()
    assert errors == []


# ── watched rows ─────────────────────────────────────────────────────────────

def test_watched_row_shows_stock_states(app):
    app.watched = items("s")
    app._refresh_list()
    row = app.rows["s"]

    row.update_status(True, "12:00", "$99.00")
    assert row.pill.cget("text") == "IN STOCK"
    assert row.open_btn.winfo_manager() == "grid"
    assert str(row.cget("style")) == "Hot.Tile.TFrame"

    row.update_status(False, "12:01", "$99.00", "back ~7 Sep")
    assert row.pill.cget("text") == "SOLD OUT"
    assert row.open_btn.winfo_manager() == ""
    assert "back ~7 Sep" in row.sub.cget("text")

    row.update_status(False, "12:02", None, "coming soon  ·  back ~3 days")
    assert row.pill.cget("text") == "COMING SOON"

    row.mark_delisted()
    assert row.pill.cget("text") == "DELISTED"
    assert row.in_stock is None


def test_favourites_are_listed_first(app):
    app.watched = items("plain", "starred", fav=("starred",))
    app._refresh_list()
    order = [w for w in app.list_frame.pack_slaves() if isinstance(w, gui.WatchedRow)]
    assert [r.item["slug"] for r in order] == ["starred", "plain"]


def test_toggling_a_favourite_is_saved(app):
    app.watched = items("s")
    app._refresh_list()
    app._toggle_fav(app.watched[0])
    assert unifi_core.load_watched()[0]["favourite"] is True


def test_the_title_counts_what_is_in_stock(app):
    app.watched = items("a", "b")
    app._refresh_list()
    app._update_row("a", True, "12:00")
    assert "1 in stock" in app.title()


# ── tabs, history, updates ───────────────────────────────────────────────────

def test_tabs_switch(app):
    for key in ("history", "settings", "watch"):
        app._show_tab(key)
        assert app._tab == key


def test_history_tab_lists_changes(app):
    gui.stock_history.clear()
    gui.stock_history.record("a", "Alpha", False)
    gui.stock_history.record("a", "Alpha", True)
    gui.stock_history.record("x", "Xeno", True, watched=False)
    app._show_tab("history")
    h = app._history_widget
    h.watched_only.set(True)
    h.refresh()
    changes = [h.tree.item(i, "values")[2] for i in h.tree.get_children()]
    assert changes == ["▲  In stock", "Added: sold out"]
    h.watched_only.set(False)
    h.refresh()
    assert len(h.tree.get_children()) == 3


def test_friendly_time():
    from datetime import datetime
    now = datetime(2026, 10, 28, 18, 0)
    assert gui.friendly_time("2026-10-28T16:50:00", now) == "Today  16:50"
    assert gui.friendly_time("2026-10-27T09:05:00", now) == "Yesterday  09:05"
    assert gui.friendly_time("2026-03-02T09:05:00", now) == "2 Mar  09:05"
    assert gui.friendly_time("2024-03-02T09:05:00", now) == "2 Mar 2024"
    assert gui.friendly_time("junk", now) == "junk"


def test_an_available_update_shows_a_banner(app):
    app.available_update = updater.Update("9.9.9", None, "installed",
                                          {"file": "x", "size": 1, "sha256": "0" * 64}, "src")
    app._refresh_update_ui()
    texts = [w.cget("text") for b in app.banner_holder.winfo_children()
             for w in b.winfo_children() if "text" in w.keys()]
    assert any("9.9.9" in t for t in texts)
    assert "Install and restart" in texts
    app._dismiss_banner()
    assert app.banner_holder.winfo_children() == []


def test_a_failed_update_check_is_reported_not_raised(app, monkeypatch):
    def boom(*a, **k):
        raise updater.UpdateError("latest.json isn't signed with the key this app trusts")
    monkeypatch.setattr(updater, "check", boom)
    app.check_for_updates()
    assert pump_until(app, lambda: not app.update_busy)
    assert "isn't signed" in app.update_status
    assert app.available_update is None


def test_the_tray_menu_reflects_state(app):
    app.watched = items("s")
    labels = [i.text for i in app._tray_menu()]
    assert "Start watching" in labels and "Quit" in labels
    app.watching = True
    assert "Stop watching" in [i.text for i in app._tray_menu()]
    app.watching = False
