"""Settings, watch list, and stock history persistence."""

import json

import pytest

import unifi_core


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


# ── settings ─────────────────────────────────────────────────────────────────

def test_load_settings_merges_over_defaults(isolated_files):
    unifi_core.write_json(unifi_core.SETTINGS_FILE, {"poll_interval": 300})
    s = unifi_core.load_settings()
    assert s["poll_interval"] == 300
    assert s["font_family"] == unifi_core.DEFAULT_SETTINGS["font_family"]


def test_load_settings_ignores_a_non_dict_file(isolated_files):
    unifi_core.write_json(unifi_core.SETTINGS_FILE, ["not", "a", "dict"])
    assert unifi_core.load_settings() == unifi_core.DEFAULT_SETTINGS


def test_settings_round_trip(isolated_files):
    s = unifi_core.load_settings()
    s["region"] = "uk"
    unifi_core.save_settings(s)
    assert unifi_core.load_settings()["region"] == "uk"


# ── watch list ───────────────────────────────────────────────────────────────

def test_normalise_drops_malformed_entries():
    items = unifi_core._normalise_watched(
        [{"slug": "ok", "title": "Fine"}, "junk", {"no_slug": 1}, None, 42])
    assert [i["slug"] for i in items] == ["ok"]


def test_normalise_fills_optional_fields():
    (item,) = unifi_core._normalise_watched([{"slug": "s"}])
    assert item == {"slug": "s", "title": "s", "favourite": False,
                    "price": None, "added_at": None}


def test_load_watched_tolerates_a_non_list_file(isolated_files):
    unifi_core.write_json(unifi_core.CONFIG_FILE, {"not": "a list"})
    assert unifi_core.load_watched() == []


def test_watched_round_trip(isolated_files):
    unifi_core.save_watched([{"slug": "a", "title": "A", "favourite": True,
                              "price": "$1.00", "added_at": "t"}])
    (item,) = unifi_core.load_watched()
    assert item["slug"] == "a" and item["favourite"] is True


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
        "slug": "x" * 500, "title": "y" * 500,
        "favourite": "truthy", "price": {"not": "a string"},
        "extra_key": "should not survive",
    }])
    unifi_core.import_watchlist(src)
    (item,) = unifi_core.load_watched()
    assert len(item["slug"]) == 200
    assert len(item["title"]) == 200
    assert item["favourite"] is True
    assert item["price"] is None
    assert "extra_key" not in item


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
        h.record_check(f"s{i}", "T", False)
    assert not path.exists()          # still buffered
    h.record_check("s4", "T", True)
    assert len(unifi_core.read_json(path, {})["events"]) == 5


def test_history_flush_persists_remainder(tmp_path):
    path = tmp_path / "h.json"
    h = unifi_core.StockHistory(path=path, autosave_after=100)
    h.record_check("s", "T", True)
    h.flush()
    assert len(unifi_core.read_json(path, {})["events"]) == 1


def test_history_flush_is_a_noop_when_clean(tmp_path):
    path = tmp_path / "h.json"
    h = unifi_core.StockHistory(path=path)
    h.flush()
    assert not path.exists()


def test_history_stats_count_alerts(tmp_path):
    h = unifi_core.StockHistory(path=tmp_path / "h.json")
    h.record_check("a", "A", True)
    h.record_check("b", "B", False)
    h.record_check("c", "C", True)
    assert h.get_stats() == {"total_checks": 3, "in_stock_alerts": 2}


def test_history_trims_to_the_cap(tmp_path, monkeypatch):
    monkeypatch.setattr(unifi_core, "MAX_HISTORY_EVENTS", 10)
    path = tmp_path / "h.json"
    h = unifi_core.StockHistory(path=path, autosave_after=1)
    for i in range(25):
        h.record_check(f"s{i}", "T", False)
    events = unifi_core.read_json(path, {})["events"]
    assert len(events) == 10
    assert events[-1]["slug"] == "s24"          # newest kept


def test_history_repairs_a_partial_file(tmp_path):
    path = tmp_path / "h.json"
    path.write_text(json.dumps({"events": [{"slug": "a", "in_stock": True,
                                            "ts": "t"}]}), encoding="utf-8")
    h = unifi_core.StockHistory(path=path)
    assert h.get_stats() == {"total_checks": 0, "in_stock_alerts": 0}
    assert len(h.get_events()) == 1


def test_history_survives_a_corrupt_file(tmp_path):
    path = tmp_path / "h.json"
    path.write_text("<<<not json>>>", encoding="utf-8")
    h = unifi_core.StockHistory(path=path)
    assert h.get_events() == []


def test_history_filters_events_by_slug(tmp_path):
    h = unifi_core.StockHistory(path=tmp_path / "h.json")
    h.record_check("a", "A", True)
    h.record_check("b", "B", False)
    assert [e["slug"] for e in h.get_events(slug="a")] == ["a"]


def test_history_last_in_stock(tmp_path):
    h = unifi_core.StockHistory(path=tmp_path / "h.json")
    h.record_check("a", "A", False)
    h.record_check("a", "A", True)
    h.record_check("a", "A", False)
    assert h.last_in_stock("a") is not None
    assert h.last_in_stock("never-seen") is None


def test_history_clear_resets_and_persists(tmp_path):
    path = tmp_path / "h.json"
    h = unifi_core.StockHistory(path=path, autosave_after=1)
    h.record_check("a", "A", True)
    h.clear()
    assert h.get_stats()["total_checks"] == 0
    assert unifi_core.read_json(path, {})["events"] == []
