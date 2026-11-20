"""The command-line watcher (unifi_watcher.py)."""

import time
from types import SimpleNamespace

import pytest

import unifi_core
import unifi_watcher as cli
from conftest import FakeResponse, product
from unifi_core import ProductNotFound, StoreError


def refused(status):
    e = unifi_core.requests.exceptions.HTTPError(f"{status} error")
    e.response = FakeResponse(status)
    return e


class Store:
    """The store as the watcher sees it: a catalog, categories that fail, and
    product pages for what the catalog doesn't carry."""

    def __init__(self):
        self.catalog = []
        self.failed = {}            # category -> its error
        self.down = None            # no category answers
        self.pages = {}             # slug -> (in_stock, price), or an error
        self.asked = []             # (slug, retries) per product page asked for

    def fetch_all_products(self, build_id, region="us", error_cb=None, **kw):
        if self.down:
            raise self.down
        for cat, e in self.failed.items():
            error_cb(cat, e)
        return list(self.catalog)

    def check_slug(self, build_id, slug, region="us", retries=3):
        self.asked.append((slug, retries))
        answer = self.pages.get(slug, ProductNotFound(slug))
        if isinstance(answer, Exception):
            raise answer
        return answer


@pytest.fixture
def watch(monkeypatch, isolated_files):
    store, notes, opened = Store(), [], []
    monkeypatch.setattr(cli, "get_build_id", lambda *a, **k: "bid")
    monkeypatch.setattr(cli, "refresh_category_coverage", lambda *a, **k: [])
    monkeypatch.setattr(cli, "fetch_all_products", store.fetch_all_products)
    monkeypatch.setattr(cli, "check_slug", store.check_slug)
    monkeypatch.setattr(cli, "notify_windows", lambda title, text: notes.append(title))
    monkeypatch.setattr(cli, "play_sound", lambda: None)
    monkeypatch.setattr(cli.webbrowser, "open", lambda url, *a, **k: opened.append(url))
    monkeypatch.setattr(cli, "stock_history",
                        unifi_core.StockHistory(isolated_files / "history.json"))
    settings = dict(unifi_core.load_settings(), poll_interval=60, sound_alerts=False)

    def watcher(*items):
        return cli.Watcher(settings, list(items), phone=Phone())
    return SimpleNamespace(store=store, notes=notes, opened=opened, settings=settings,
                           watcher=watcher, history=lambda: cli.stock_history)


A = {"slug": "a", "title": "A"}
B = {"slug": "b", "title": "B"}


class Phone:
    def __init__(self):
        self.sent = []

    def submit(self, settings, alert, on_result=None):
        self.sent.append(alert)


def test_a_price_drop_alerts_only_when_asked(monkeypatch, capsys):
    notes, phone = [], Phone()
    monkeypatch.setattr(cli, "notify_windows", lambda title, text: notes.append(title))
    item = {"slug": "udr7", "title": "Dream Router 7"}
    settings = {"price_alerts": False, "phone_alerts": True}

    cli.price_changed(settings, phone, None, item, "$299.00", "$279.00", "us")
    assert "PRICE DOWN" in capsys.readouterr().out
    assert notes == [] and phone.sent == []

    settings["price_alerts"] = True
    cli.price_changed(settings, phone, None, item, "$279.00", "$289.00", "us")
    assert "price changed" in capsys.readouterr().out and notes == []
    cli.price_changed(settings, phone, None, item, "$289.00", "$259.00", "us")
    assert notes == ["Price drop: Dream Router 7"]
    assert [a.price_was for a in phone.sent] == ["$289.00"]

    cli.price_changed(settings, phone, None, {**item, "muted": True}, "$259.00", "$249.00", "us")
    assert len(notes) == 1


def test_the_first_run_lists_products_with_odd_titles(monkeypatch, capsys, isolated_files):
    """Review 2026-11-15: a missing or null title ended the setup with a
    traceback, before anything was watched."""
    no_title = product("no-title", status="SoldOut")
    del no_title["title"]
    monkeypatch.setattr(cli, "get_build_id", lambda *a, **k: "bid")
    monkeypatch.setattr(cli, "fetch_all_products", lambda *a, **k: [
        product("named", "Named", status="SoldOut"),
        dict(product("untitled", status="SoldOut"), title=None), no_title])
    monkeypatch.setattr("builtins.input", lambda *a: "1, 2, 3")
    watched = cli.run_setup()
    assert [w["title"] for w in watched] == ["Named", "no-title", "untitled"]
    assert "untitled" in capsys.readouterr().out


# ── The watch loop ───────────────────────────────────────────────────────────

def test_a_product_in_stock_alerts_once_and_again_after_it_sold_out(watch):
    w = watch.watcher(A)
    watch.store.catalog = [product("a")]
    assert w.cycle() == 60
    assert w.cycle() == 60
    assert watch.notes == ["IN STOCK: A"]
    assert watch.opened == [unifi_core.product_url("a", "us")]
    watch.store.catalog = [product("a", status="SoldOut")]
    w.cycle()
    watch.store.catalog = [product("a")]
    w.cycle()
    assert watch.notes == ["IN STOCK: A", "IN STOCK: A"]
    assert watch.history().get_stats()["alerts"] == 2


def test_a_restarted_watcher_doesnt_alert_for_what_was_already_in_stock(watch):
    watch.history().record("a", "A", True)
    watch.store.catalog = [product("a"), product("b")]
    watch.watcher(A, B).cycle()
    assert watch.notes == ["IN STOCK: B"]


def test_a_muted_product_doesnt_alert(watch, capsys):
    w = watch.watcher({**A, "muted": True})
    watch.store.catalog = [product("a")]
    w.cycle()
    assert watch.notes == [] and w.notified["a"] is True
    assert "(no alert: muted)" in capsys.readouterr().out


def test_a_refusing_store_isnt_asked_product_by_product(watch, capsys):
    """The app stopped asking a refusing store (429/503) for each product it
    didn't send, and waited longer; the command-line watcher asked for each
    one, with full retries, every check, and reset its backoff."""
    w = watch.watcher(A, B)
    watch.store.catalog = [product("a")]
    watch.store.failed = {"category/all-wifi": refused(429)}
    watch.store.pages = {"b": (True, "$99.00")}
    assert w.cycle() == 120
    assert w.cycle() == 240
    assert watch.store.asked == []
    assert watch.notes == ["IN STOCK: A"]           # what it did send still counts
    out = capsys.readouterr().out
    assert out.count("couldn't read 1 of the store's categories (category/all-wifi)") == 1
    assert "the store is refusing requests: next check in 240s" in out

    watch.store.failed = {}
    assert w.cycle() == 60
    assert watch.store.asked == [("b", 3)]
    assert watch.notes == ["IN STOCK: A", "IN STOCK: B"]


def test_an_incomplete_catalog_asks_for_whats_missing_once(watch):
    w = watch.watcher(A, B)
    watch.store.catalog = [product("a", status="SoldOut")]
    watch.store.failed = {"category/all-wifi": refused(500)}
    watch.store.pages = {"b": (False, None)}
    assert w.cycle() == 60
    assert watch.store.asked == [("b", 1)]


def test_an_unreachable_store_is_asked_less_often_until_it_answers(watch, capsys):
    w = watch.watcher(A)
    watch.store.down = StoreError("no category could be read")
    assert [w.cycle() for _ in range(6)] == [120, 240, 480, 960, 1800, 1800]
    assert "could not reach the store: no category could be read" in capsys.readouterr().out
    watch.store.down = None
    watch.store.catalog = [product("a")]
    assert w.cycle() == 60
    assert w.failures == 0


def test_a_delisted_product_is_asked_for_again_only_later(watch, monkeypatch, capsys):
    w = watch.watcher(B)
    w.cycle()
    w.cycle()
    assert watch.store.asked == [("b", 3)]
    monkeypatch.setattr(cli, "DELISTED_RECHECK", 0)
    w.cycle()
    assert len(watch.store.asked) == 2
    watch.store.catalog = [product("b", status="SoldOut")]
    w.cycle()
    out = capsys.readouterr().out
    assert out.count("B — no longer on the store") == 1
    assert "B is on the store again" in out and "b" not in w.delisted


def test_one_product_that_cant_be_checked_doesnt_stop_the_others(watch, capsys):
    w = watch.watcher(B, A)
    watch.store.catalog = [product("a")]
    watch.store.pages = {"b": ValueError("an odd page")}
    assert w.cycle() == 60
    assert watch.notes == ["IN STOCK: A"]
    assert "B: couldn't check it (an odd page)" in capsys.readouterr().out


def test_the_watcher_sleeps_what_each_check_says_and_waits_for_phone_alerts(watch, monkeypatch):
    slept = []

    def sleep(seconds):
        slept.append(seconds)
        if len(slept) == 2:
            raise KeyboardInterrupt
    monkeypatch.setattr(cli, "time", SimpleNamespace(sleep=sleep, monotonic=time.monotonic))
    monkeypatch.setattr(cli, "load_settings", lambda: watch.settings)
    monkeypatch.setattr(cli, "load_watched", lambda: [A])
    waited = []
    monkeypatch.setattr(cli.webhooks.Sender, "wait",
                        lambda self, timeout: waited.append(timeout) or True)
    watch.store.down = StoreError("down")
    with pytest.raises(KeyboardInterrupt):
        cli.main()
    assert slept == [120, 240]
    assert waited == [cli.PHONE_QUIT_WAIT]
