"""Settings, watch list, stock history, and where they all live."""

import json

import pytest

import unifi_core
from conftest import write


# ── read_json / write_json ───────────────────────────────────────────────────

def test_round_trips_non_ascii(tmp_path):
    """Regression: locale-encoded I/O turned "Café" into mojibake on Windows."""
    path = tmp_path / "x.json"
    payload = [{"slug": "s", "title": "Café – Ubiquiti™ 24 PoE"}]
    unifi_core.write_json(path, payload)
    assert unifi_core.read_json(path, None) == payload
    assert "Café" in path.read_text(encoding="utf-8")


def test_read_json_returns_default_for_missing_file(tmp_path):
    assert unifi_core.read_json(tmp_path / "nope.json", {"d": 1}) == {"d": 1}


def test_read_json_returns_default_for_corrupt_file(tmp_path):
    path = tmp_path / "bad.json"
    path.write_text("{not json at all", encoding="utf-8")
    assert unifi_core.read_json(path, []) == []


def test_read_json_accepts_a_byte_order_mark(tmp_path):
    """Regression: Windows PowerShell's `Set-Content -Encoding utf8`, and some
    editors, start the file with a BOM, and the file read as unreadable."""
    path = tmp_path / "bom.json"
    path.write_bytes(b"\xef\xbb\xbf" + json.dumps({"title": "Café"}).encode("utf-8"))
    assert unifi_core.read_json(path, None) == {"title": "Café"}


def test_bom_data_files_load(isolated_files):
    """The settings and the watch list as Notepad or PowerShell save them,
    and the watch list then saved back without the mark."""
    bom = b"\xef\xbb\xbf"
    (isolated_files / "settings.json").write_bytes(bom + b'{"region": "uk"}')
    (isolated_files / "watched_items.json").write_bytes(
        bom + b'[{"slug": "udr7", "title": "Dream Router 7"}]')
    assert unifi_core.load_settings()["region"] == "uk"
    watched = unifi_core.load_watched()
    assert [w["slug"] for w in watched] == ["udr7"]
    unifi_core.save_watched(watched)
    assert not (isolated_files / "watched_items.json").read_bytes().startswith(bom)


def test_a_bom_watch_list_imports(isolated_files, tmp_path):
    shared = tmp_path / "shared.json"
    shared.write_bytes(b"\xef\xbb\xbf" + json.dumps(
        [{"slug": "udr7", "title": "Dream Router 7"}]).encode("utf-8"))
    assert unifi_core.import_watchlist(shared) == 1


def test_write_json_is_atomic(tmp_path):
    """A failed write must leave the previous contents intact."""
    path = tmp_path / "a.json"
    unifi_core.write_json(path, {"good": True})
    before = path.read_text(encoding="utf-8")

    class Unserialisable:
        pass

    with pytest.raises(TypeError):
        unifi_core.write_json(path, {"bad": Unserialisable()})

    assert path.read_text(encoding="utf-8") == before
    assert not (tmp_path / "a.json.tmp").exists()


def test_write_json_leaves_no_temp_file_on_success(tmp_path):
    path = tmp_path / "a.json"
    unifi_core.write_json(path, {"a": 1})
    assert [p.name for p in tmp_path.iterdir()] == ["a.json"]


def test_write_json_creates_the_folder(tmp_path):
    """The data folder in %APPDATA% doesn't exist until the first save."""
    path = tmp_path / "new" / "deeper" / "a.json"
    unifi_core.write_json(path, [1])
    assert unifi_core.read_json(path, None) == [1]


# ── settings ─────────────────────────────────────────────────────────────────

def test_load_settings_merges_over_defaults(isolated_files):
    unifi_core.write_json(unifi_core.SETTINGS_FILE, {"poll_interval": 300})
    s = unifi_core.load_settings()
    assert s["poll_interval"] == 300
    assert s["theme"] == unifi_core.DEFAULT_SETTINGS["theme"]


def test_load_settings_ignores_a_non_dict_file(isolated_files):
    unifi_core.write_json(unifi_core.SETTINGS_FILE, ["not", "a", "dict"])
    assert unifi_core.load_settings() == unifi_core.DEFAULT_SETTINGS


def test_settings_round_trip(isolated_files):
    s = unifi_core.load_settings()
    s["region"] = "uk"
    unifi_core.save_settings(s)
    assert unifi_core.load_settings()["region"] == "uk"


@pytest.mark.parametrize("key,bad", [
    ("region", "xx"), ("theme", "neon"), ("accent", "red"), ("accent", "#12345"),
    ("font_size", 40), ("notifications", "yes"), ("window", [1, 2, 3]),
    ("window", [0, 0, 0, 10]), ("max_retries", 0), ("auto_start", 1),
    ("phone_alerts", "on"), ("phone_service", "telegram"), ("ntfy_url", 42),
    ("ntfy_token", "tk_abc\r\nX-Evil: 1"), ("discord_url", "https://x/" + "a" * 3000),
    ("webhook_url", "https://x/\n"),
])
def test_an_invalid_setting_falls_back_to_its_default(isolated_files, key, bad):
    """Regression: an unknown region raised KeyError on every store call, and
    a bad colour stopped the window opening at all."""
    write(isolated_files / "settings.json", {key: bad})
    assert unifi_core.load_settings()[key] == unifi_core.DEFAULT_SETTINGS[key]


def test_an_invalid_phone_setting_is_logged_without_its_value(isolated_files, caplog):
    """Webhook addresses and keys are secrets, and the log is what people
    attach to bug reports."""
    write(isolated_files / "settings.json",
          {"discord_url": "https://discord.com/api/webhooks/1/SECRET\n"})
    with caplog.at_level("WARNING", logger="unifi_watcher"):
        assert unifi_core.load_settings()["discord_url"] == ""
    assert "discord_url" in caplog.text and "SECRET" not in caplog.text


def test_phone_settings_round_trip(isolated_files):
    s = unifi_core.load_settings()
    s.update(phone_alerts=True, phone_service="discord",
             discord_url="https://discord.com/api/webhooks/1/abc")
    unifi_core.save_settings(s)
    loaded = unifi_core.load_settings()
    assert loaded["phone_alerts"] is True and loaded["phone_service"] == "discord"
    assert loaded["discord_url"] == "https://discord.com/api/webhooks/1/abc"


def test_an_out_of_range_interval_is_clamped(isolated_files):
    write(isolated_files / "settings.json", {"poll_interval": 5})
    assert unifi_core.load_settings()["poll_interval"] == unifi_core.POLL_MIN
    write(isolated_files / "settings.json", {"poll_interval": 99999})
    assert unifi_core.load_settings()["poll_interval"] == unifi_core.POLL_MAX


def test_2x_colour_settings_are_dropped(isolated_files):
    write(isolated_files / "settings.json",
          {"bg": "#0d1117", "panel": "#161b22", "poll_interval": 120})
    s = unifi_core.load_settings()
    assert "bg" not in s and s["poll_interval"] == 120
    unifi_core.save_settings(s)
    assert "bg" not in unifi_core.read_json(isolated_files / "settings.json", {})


def test_valid_settings_survive(isolated_files):
    good = {"region": "eu", "theme": "dark", "accent": "#00897b", "font_size": 12,
            "window": [10, 20, 800, 600], "poll_interval": 300}
    write(isolated_files / "settings.json", good)
    s = unifi_core.load_settings()
    assert {k: s[k] for k in good} == good


# ── watch list ───────────────────────────────────────────────────────────────

def test_normalise_drops_malformed_entries():
    items = unifi_core._normalise_watched(
        [{"slug": "ok", "title": "Fine"}, "junk", {"no_slug": 1}, None, 42])
    assert [i["slug"] for i in items] == ["ok"]


def test_normalise_fills_optional_fields():
    (item,) = unifi_core._normalise_watched([{"slug": "s"}])
    assert item == {"slug": "s", "title": "s", "favourite": False, "muted": False,
                    "price": None, "added_at": None}


@pytest.mark.parametrize("item, settings, alert, phone", [
    ({}, {}, True, True),
    ({"muted": True}, {}, False, False),
    ({"muted": True, "favourite": True}, {}, False, False),
    ({}, {"alert_favourites_only": True}, False, False),
    ({"favourite": True}, {"alert_favourites_only": True}, True, True),
    ({}, {"phone_favourites_only": True}, True, False),
    ({"favourite": True}, {"phone_favourites_only": True}, True, True),
])
def test_wants_alert(item, settings, alert, phone):
    item = {"slug": "s", **item}
    assert unifi_core.wants_alert(settings, item) is alert
    assert unifi_core.wants_alert(settings, item, phone=True) is phone


def test_load_watched_tolerates_a_non_list_file(isolated_files):
    unifi_core.write_json(unifi_core.CONFIG_FILE, {"not": "a list"})
    assert unifi_core.load_watched() == []


def test_watched_round_trip(isolated_files):
    unifi_core.save_watched([{"slug": "a", "title": "A", "favourite": True,
                              "price": "$1.00", "added_at": "t"}])
    (item,) = unifi_core.load_watched()
    assert item["slug"] == "a" and item["favourite"] is True


# ── slugs ────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("slug", ["u7-pro", "usw-pro-max-24-poe", "ua-g2", "x", "UDM-SE"])
def test_valid_slugs(slug):
    assert unifi_core.is_valid_slug(slug)


@pytest.mark.parametrize("slug", ["", "../etc", "a/b", "a?b=c", "-leading", "a b",
                                  "x" * 201, None, 7, "a..b", "%2e%2e"])
def test_invalid_slugs(slug):
    """A slug goes into store URLs: from a shared watch list it is untrusted."""
    assert not unifi_core.is_valid_slug(slug)


def test_a_watch_list_entry_with_a_bad_slug_is_dropped():
    items = unifi_core._normalise_watched([{"slug": "../../x"}, {"slug": "ok"}])
    assert [i["slug"] for i in items] == ["ok"]


def test_product_url_never_builds_a_url_from_a_bad_slug():
    assert unifi_core.product_url("u7-pro", "eu").endswith("/eu/en/products/u7-pro")
    assert unifi_core.product_url("../x", "us") == unifi_core.store_home("us")


# ── export / import ──────────────────────────────────────────────────────────

def test_import_merges_without_duplicating(isolated_files, tmp_path):
    unifi_core.save_watched([{"slug": "have", "title": "Have"}])
    src = tmp_path / "in.json"
    unifi_core.write_json(src, [{"slug": "have", "title": "Dup"},
                                {"slug": "new", "title": "New"}])
    assert unifi_core.import_watchlist(src) == 1
    assert sorted(w["slug"] for w in unifi_core.load_watched()) == ["have", "new"]


def test_import_rejects_a_non_list_file(isolated_files, tmp_path):
    src = tmp_path / "in.json"
    unifi_core.write_json(src, {"slug": "x"})
    with pytest.raises(ValueError):
        unifi_core.import_watchlist(src)


def test_import_constrains_imported_fields(isolated_files, tmp_path):
    """Imported data is untrusted: only known keys, bounded lengths."""
    src = tmp_path / "in.json"
    unifi_core.write_json(src, [{
        "slug": "fine-slug", "title": "y" * 500,
        "favourite": "truthy", "muted": 1, "price": {"not": "a string"},
        "added_at": {"nor": "this"}, "extra_key": "should not survive",
    }])
    unifi_core.import_watchlist(src)
    (item,) = unifi_core.load_watched()
    assert item["slug"] == "fine-slug"
    assert len(item["title"]) == 200
    assert item["favourite"] is True
    assert item["muted"] is True
    assert item["price"] is None
    assert isinstance(item["added_at"], str)
    assert "extra_key" not in item


def test_import_skips_bad_slugs(isolated_files, tmp_path):
    src = tmp_path / "in.json"
    write(src, [{"slug": "fine"}, {"slug": "evil/../path"}, {"slug": "a?b"},
                {"slug": "x" * 500}])
    unifi_core.save_watched([])
    assert unifi_core.import_watchlist(src) == 1


def test_export_then_import_round_trips(isolated_files, tmp_path):
    unifi_core.save_watched([{"slug": "a", "title": "A", "favourite": True,
                              "price": "$9.00", "added_at": "t"}])
    dest = tmp_path / "out.json"
    assert unifi_core.export_watchlist(dest) == 1
    unifi_core.save_watched([])
    assert unifi_core.import_watchlist(dest) == 1
    assert unifi_core.load_watched()[0]["price"] == "$9.00"


# ── stock history ────────────────────────────────────────────────────────────

def test_history_buffers_writes(tmp_path):
    path = tmp_path / "h.json"
    h = unifi_core.StockHistory(path=path, autosave_after=5)
    for i in range(4):
        h.record(f"s{i}", "T", False)
    assert not path.exists()          # still buffered
    h.record("s4", "T", True)
    assert len(unifi_core.read_json(path, {})["events"]) == 5


def test_history_flush_persists_remainder(tmp_path):
    path = tmp_path / "h.json"
    h = unifi_core.StockHistory(path=path, autosave_after=100)
    h.record("s", "T", True)
    h.flush()
    assert len(unifi_core.read_json(path, {})["events"]) == 1


def test_history_flush_is_a_noop_when_clean(tmp_path):
    path = tmp_path / "h.json"
    h = unifi_core.StockHistory(path=path)
    h.flush()
    assert not path.exists()


def test_history_logs_changes_not_checks(tmp_path):
    """Regression: 2.x logged every check, so 50 items polled each minute
    filled the 2000-event file in 40 minutes and pushed every real change
    out of it."""
    h = unifi_core.StockHistory(path=tmp_path / "h.json")
    for _ in range(5):
        h.record("a", "A", False)
    h.record("a", "A", True)
    h.record("a", "A", True)
    h.record("a", "A", False)
    assert [e["in_stock"] for e in h.get_events()] == [False, True, False]
    assert [e["first"] for e in h.get_events()] == [True, False, False]
    assert h.get_stats()["total_checks"] == 8


def test_history_counts_alerts_not_in_stock_checks(tmp_path):
    """Regression: "in-stock alerts" counted every check that found stock."""
    h = unifi_core.StockHistory(path=tmp_path / "h.json")
    for _ in range(10):
        h.record("a", "A", True)
    assert h.get_stats() == {"total_checks": 10, "alerts": 0}
    h.record_alert()
    assert h.get_stats()["alerts"] == 1


def test_store_wide_changes_are_not_checks_or_first_sightings(tmp_path):
    """Regression: a store-wide change was shown as "Added: sold out"."""
    h = unifi_core.StockHistory(path=tmp_path / "h.json")
    h.record("x", "X", True, watched=False)
    ev = h.get_events()[-1]
    assert ev["watched"] is False and ev["first"] is False
    assert h.get_stats()["total_checks"] == 0
    assert h.get_events(watched_only=True) == []


def test_history_converts_a_2x_file(tmp_path):
    path = tmp_path / "h.json"
    old = [{"ts": f"2025-01-01T00:0{i}:00", "slug": "a", "title": "A",
            "in_stock": s, "price": None}
           for i, s in enumerate([False, False, True, True, False])]
    path.write_text(json.dumps({"events": old,
                                "stats": {"total_checks": 5, "in_stock_alerts": 2}}),
                    encoding="utf-8")
    h = unifi_core.StockHistory(path=path)
    assert [e["in_stock"] for e in h.get_events()] == [False, True, False]
    assert h.get_stats() == {"total_checks": 5, "alerts": 0}
    h.record("a", "A", False)                 # same as the last converted state
    assert len(h.get_events()) == 3


def test_history_trims_to_the_cap(tmp_path, monkeypatch):
    monkeypatch.setattr(unifi_core, "MAX_HISTORY_EVENTS", 10)
    path = tmp_path / "h.json"
    h = unifi_core.StockHistory(path=path, autosave_after=1)
    for i in range(25):
        h.record(f"s{i}", "T", False)
    events = unifi_core.read_json(path, {})["events"]
    assert len(events) == 10
    assert events[-1]["slug"] == "s24"          # newest kept


def test_history_repairs_a_partial_file(tmp_path):
    path = tmp_path / "h.json"
    path.write_text(json.dumps({"events": [{"slug": "a", "in_stock": True, "ts": "t"},
                                           "junk", {"slug": 3}]}), encoding="utf-8")
    h = unifi_core.StockHistory(path=path)
    assert h.get_stats() == {"total_checks": 0, "alerts": 0}
    assert len(h.get_events()) == 1


def test_history_survives_a_corrupt_file(tmp_path):
    path = tmp_path / "h.json"
    path.write_text("<<<not json>>>", encoding="utf-8")
    h = unifi_core.StockHistory(path=path)
    assert h.get_events() == []


def test_history_filters_events_by_slug(tmp_path):
    h = unifi_core.StockHistory(path=tmp_path / "h.json")
    h.record("a", "A", True)
    h.record("b", "B", False)
    assert [e["slug"] for e in h.get_events(slug="a")] == ["a"]


def test_history_last_in_stock(tmp_path):
    h = unifi_core.StockHistory(path=tmp_path / "h.json")
    h.record("a", "A", False)
    h.record("a", "A", True)
    h.record("a", "A", False)
    assert h.last_in_stock("a") is not None
    assert h.last_in_stock("never-seen") is None


def test_history_clear_resets_and_persists(tmp_path):
    path = tmp_path / "h.json"
    h = unifi_core.StockHistory(path=path, autosave_after=1)
    h.record("a", "A", True)
    h.clear()
    assert h.get_stats()["total_checks"] == 0
    assert unifi_core.read_json(path, {})["events"] == []
    h.record("a", "A", True)                  # a first sighting again
    assert h.get_events()[-1]["first"] is True


def test_history_logs_a_watched_products_price_change(tmp_path):
    h = unifi_core.StockHistory(path=tmp_path / "h.json")
    assert h.record("a", "A", False, "$299.00")["first"] is True
    assert h.record("a", "A", False, "$299.00") is None
    ev = h.record("a", "A", False, "$279.00")
    assert ev["price"] == "$279.00" and ev["price_was"] == "$299.00" and not ev["first"]
    assert h.record("a", "A", False, None) is None          # no price isn't a change
    assert h.record("a", "A", False, "$279.00") is None
    ev = h.record("a", "A", True, "$289.00")                 # a stock change says only that
    assert ev["in_stock"] is True and "price_was" not in ev
    assert [e.get("price_was") for e in h.get_events()] == [None, "$299.00", None]


def test_history_price_changes_survive_a_restart(tmp_path):
    path = tmp_path / "h.json"
    h = unifi_core.StockHistory(path=path)
    h.record("a", "A", False, "$299.00")
    h.flush()
    h = unifi_core.StockHistory(path=path)
    assert h.record("a", "A", False, "$279.00")["price_was"] == "$299.00"


def test_history_ignores_other_currencies_and_store_wide_prices(tmp_path):
    h = unifi_core.StockHistory(path=tmp_path / "h.json")
    h.record("a", "A", False, "$299.00")
    assert h.record("a", "A", False, "€279.00") is None      # switched to the EU store
    assert h.record("a", "A", False, "€269.00")["price_was"] == "€279.00"
    h.record("x", "X", True, "$10.00", watched=False)
    assert h.record("x", "X", True, "$9.00", watched=False) is None


@pytest.mark.parametrize("text, amount", [
    ("$1,299.00", 1299.0), ("C$199.50", 199.5), ("€279.00", 279.0), ("1,050.00 SEK", 1050.0),
    (None, None), ("", None), ("free", None)])
def test_price_amount(text, amount):
    assert unifi_core.price_amount(text) == amount


def test_record_check_keeps_its_2x_name(tmp_path):
    h = unifi_core.StockHistory(path=tmp_path / "h.json")
    assert h.record_check("a", "A", True) is True
    assert h.record_check("a", "A", True) is False


# ── data folder ──────────────────────────────────────────────────────────────

def test_data_dir_override(monkeypatch, tmp_path):
    monkeypatch.setenv("USW_DATA_DIR", str(tmp_path))
    assert unifi_core._base_dir() == tmp_path


def test_a_portable_copy_keeps_its_files_beside_it(monkeypatch, tmp_path):
    monkeypatch.delenv("USW_DATA_DIR", raising=False)
    monkeypatch.setattr(unifi_core, "program_dir", lambda: tmp_path)
    assert unifi_core._base_dir() != tmp_path
    (tmp_path / unifi_core.PORTABLE_MARKER).write_text("portable")
    assert unifi_core._base_dir() == tmp_path


def _legacy(monkeypatch, tmp_path):
    old, new = tmp_path / "old", tmp_path / "new"
    old.mkdir()
    monkeypatch.delenv("USW_DATA_DIR", raising=False)
    monkeypatch.setattr(unifi_core, "program_dir", lambda: old)
    monkeypatch.setattr(unifi_core, "BASE_DIR", new)
    return old, new


def test_migration_copies_2x_files_once(monkeypatch, tmp_path):
    old, new = _legacy(monkeypatch, tmp_path)
    write(old / "watched_items.json", [{"slug": "a"}])
    write(old / "settings.json", {"region": "uk"})
    assert sorted(unifi_core.migrate_legacy_files()) == ["settings.json", "watched_items.json"]
    assert unifi_core.read_json(new / "watched_items.json", None) == [{"slug": "a"}]
    assert (old / "watched_items.json").exists()           # copied, not moved
    write(old / "watched_items.json", [{"slug": "changed"}])
    assert unifi_core.migrate_legacy_files() == []          # never twice


def test_migration_never_overwrites(monkeypatch, tmp_path):
    old, new = _legacy(monkeypatch, tmp_path)
    write(old / "settings.json", {"region": "uk"})
    write(new / "settings.json", {"region": "ca"})
    unifi_core.migrate_legacy_files()
    assert unifi_core.read_json(new / "settings.json", None) == {"region": "ca"}


def test_migration_reloads_the_history(monkeypatch, tmp_path):
    """The history object exists before migration runs; without a reload its
    first flush would overwrite the copied file with an empty one."""
    old, new = _legacy(monkeypatch, tmp_path)
    write(old / "watched_items.json", [{"slug": "a"}])
    write(old / "stock_history.json", {"version": 2, "stats": {"total_checks": 7},
                                       "events": [{"ts": "t", "slug": "a", "in_stock": True}]})
    h = unifi_core.StockHistory(path=new / "stock_history.json")
    monkeypatch.setattr(unifi_core, "stock_history", h)
    unifi_core.migrate_legacy_files()
    assert h.get_stats()["total_checks"] == 7 and len(h.get_events()) == 1


def test_migration_skips_when_the_data_folder_is_the_program_folder(monkeypatch, tmp_path):
    monkeypatch.delenv("USW_DATA_DIR", raising=False)
    monkeypatch.setattr(unifi_core, "program_dir", lambda: tmp_path)
    monkeypatch.setattr(unifi_core, "BASE_DIR", tmp_path)
    write(tmp_path / "watched_items.json", [])
    assert unifi_core.migrate_legacy_files() == []
