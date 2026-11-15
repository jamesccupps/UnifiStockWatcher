"""The command-line watcher (unifi_watcher.py)."""

import unifi_watcher as cli
from conftest import product


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
