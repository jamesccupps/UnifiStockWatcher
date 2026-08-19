"""Store API: parsing, redirect handling, and stock resolution.

The regressions guarded here were all live defects, not hypotheticals - see
the commit history for the measurements that motivated each one.
"""

import pytest

import unifi_core
from unifi_core import ProductNotFound, StoreError
from conftest import FakeResponse, category_page, product, product_page


# ── price / availability parsing ─────────────────────────────────────────────

@pytest.mark.parametrize("value, expected", [
    ({"amount": 349900, "currency": "USD"}, "$3,499.00"),
    ({"amount": 19900, "currency": "EUR"}, "€199.00"),
    ({"amount": 9900, "currency": "GBP"}, "£99.00"),
    ({"amount": 12900, "currency": "CAD"}, "C$129.00"),
    ({"amount": 50000, "currency": "SEK"}, "500.00 SEK"),
    ({"amount": 0, "currency": "USD"}, "$0.00"),
    (199.0, "$199.00"),
    ("$199.00", "$199.00"),
])
def test_format_price(value, expected):
    assert unifi_core._format_price(value) == expected


def test_get_price_prefers_first_priced_variant():
    p = {"variants": [{"status": "SoldOut"},
                      {"displayPrice": {"amount": 4900, "currency": "USD"}}]}
    assert unifi_core.get_price(p) == "$49.00"


def test_get_price_returns_none_when_absent():
    assert unifi_core.get_price({"variants": [{"status": "SoldOut"}]}) is None
    assert unifi_core.get_price({}) is None


def test_is_available_needs_one_available_variant():
    assert unifi_core.is_available(product("a", status="Available"))
    assert not unifi_core.is_available(product("b", status="SoldOut"))
    assert not unifi_core.is_available({"variants": []})
    assert not unifi_core.is_available({})


# ── product resolution ───────────────────────────────────────────────────────

def test_find_product_matches_the_requested_slug_not_the_first():
    """Regression: the old DFS returned collection.products[0]'s variants.

    On the real ua-g2 page that is ua-g3, so an out-of-stock item reported
    the in-stock status of a different product.
    """
    page = product_page("ua-g2",
                        product("ua-g3", status="Available"),
                        product("ua-retrofit-reader", status="Available"),
                        product("ua-g2", status="SoldOut"))
    found = unifi_core._find_product(page["pageProps"], "ua-g2")
    assert found["slug"] == "ua-g2"
    assert not unifi_core.is_available(found)


def test_find_product_falls_back_to_current_product_id():
    page = product_page("b", product("a"), product("b"))
    page["pageProps"]["collection"]["products"][1]["slug"] = "renamed"
    found = unifi_core._find_product(page["pageProps"], "b")
    assert found["id"] == "id-b"


def test_find_product_honours_historical_slugs():
    page = product_page("new-slug",
                        product("new-slug", historicalSlugs=["old-slug"]))
    assert unifi_core._find_product(page["pageProps"], "old-slug")["slug"] == "new-slug"


def test_find_product_prefers_exact_slug_over_current_product_id():
    """currentProductId is a last resort; an exact slug match must win."""
    page = product_page("a", product("a"), product("b"))
    assert unifi_core._find_product(page["pageProps"], "b")["slug"] == "b"


def test_find_product_returns_none_when_page_has_no_products():
    assert unifi_core._find_product({"collection": {"products": []}}, "nope") is None
    assert unifi_core._find_product({}, "nope") is None


# ── redirect handling ────────────────────────────────────────────────────────

def test_redirect_path_strips_query_and_suffix():
    assert unifi_core._redirect_path(
        "/us/en/category/x/products/y.json?ref=1", "y") == "/us/en/category/x/products/y"


def test_redirect_path_to_404_means_product_gone():
    with pytest.raises(ProductNotFound):
        unifi_core._redirect_path("https://store.ui.com/404", "dead-slug")


def test_check_slug_follows_n_redirect_body(fake_session):
    """Regression: HTTP 200 whose body is a Next.js redirect directive.

    This shape covered roughly half the catalog and previously produced a
    hardcoded (False, None) - a permanent, silent "out of stock".
    """
    canonical = "/us/en/category/switching/collections/campus/products/ecs-48-poe"
    fake_session.routes = {
        "/products/ecs-48-poe.json": FakeResponse(
            200, {"pageProps": {"__N_REDIRECT": canonical,
                                "__N_REDIRECT_STATUS": 307}}),
        canonical + ".json": FakeResponse(
            200, product_page("ecs-48-poe",
                              product("ecs-24-poe", status="Available"),
                              product("ecs-48-poe", status="Available",
                                      amount=349900))),
    }
    assert unifi_core.check_slug("bid", "ecs-48-poe") == (True, "$3,499.00")


def test_check_slug_follows_x_nextjs_redirect_header(fake_session):
    canonical = "/us/en/category/wifi/products/u7-pro"
    fake_session.routes = {
        "/products/u7-pro.json": FakeResponse(
            307, headers={"x-nextjs-redirect": canonical}),
        canonical + ".json": FakeResponse(
            200, product_page("u7-pro", product("u7-pro", amount=18900))),
    }
    assert unifi_core.check_slug("bid", "u7-pro") == (True, "$189.00")


def test_check_slug_raises_when_redirected_to_404(fake_session):
    fake_session.routes = {
        "/products/gone.json": FakeResponse(
            307, headers={"x-nextjs-redirect": "https://store.ui.com/404"}),
    }
    with pytest.raises(ProductNotFound):
        unifi_core.check_slug("bid", "gone")


def test_check_slug_gives_up_on_a_redirect_loop(fake_session):
    fake_session.routes = {
        "/products/": FakeResponse(
            200, {"pageProps": {"__N_REDIRECT": "/us/en/products/loop"}}),
    }
    with pytest.raises(StoreError):
        unifi_core.check_slug("bid", "loop", retries=1)


def test_check_slug_never_fabricates_out_of_stock(fake_session):
    """A page with no recognisable product must raise, not report False."""
    fake_session.routes = {
        "/products/weird.json": FakeResponse(200, {"pageProps": {"other": 1}}),
    }
    with pytest.raises(ProductNotFound):
        unifi_core.check_slug("bid", "weird")


def test_check_slug_404_with_unchanged_build_id_means_gone(fake_session, monkeypatch):
    monkeypatch.setattr(unifi_core, "get_build_id", lambda *a, **k: "bid")
    fake_session.routes = {"/products/gone.json": FakeResponse(404)}
    with pytest.raises(ProductNotFound):
        unifi_core.check_slug("bid", "gone")


def test_check_slug_404_retries_when_build_id_rotated(fake_session, monkeypatch):
    monkeypatch.setattr(unifi_core, "get_build_id", lambda *a, **k: "newbid")

    def route(url):
        if "newbid" in url:
            return FakeResponse(200, product_page("x", product("x", amount=1000)))
        return FakeResponse(404)

    fake_session.routes = {"/products/x.json": route}
    assert unifi_core.check_slug("oldbid", "x") == (True, "$10.00")


# ── catalog fetch ────────────────────────────────────────────────────────────

def test_fetch_all_products_dedupes_across_categories(fake_session):
    fake_session.default = FakeResponse(
        200, category_page(product("shared"), product("unique")))
    products = unifi_core.fetch_all_products("bid", "us")
    slugs = [p["slug"] for p in products]
    assert sorted(slugs) == ["shared", "unique"]


def test_fetch_all_products_survives_one_bad_category(fake_session):
    good = FakeResponse(200, category_page(product("ok")))
    fake_session.routes = {"all-switching": FakeResponse(500)}
    fake_session.default = good
    errors = []
    products = unifi_core.fetch_all_products(
        "bid", "us", error_cb=lambda cat, e: errors.append(cat))
    assert [p["slug"] for p in products] == ["ok"]
    assert errors == ["category/all-switching"]


def test_fetch_all_products_reports_progress_to_100(fake_session):
    fake_session.default = FakeResponse(200, category_page(product("a")))
    seen = []
    unifi_core.fetch_all_products("bid", "us", progress_cb=seen.append)
    assert seen[-1] == 100
    assert len(seen) == len(unifi_core.CATEGORIES)


def test_fetch_all_products_tolerates_a_raising_callback(fake_session):
    fake_session.default = FakeResponse(200, category_page(product("a")))

    def boom(_):
        raise RuntimeError("callback exploded")

    assert unifi_core.fetch_all_products("bid", "us", progress_cb=boom)


def test_fetch_all_products_reads_a_flat_products_list(fake_session):
    fake_session.default = FakeResponse(
        200, {"pageProps": {"products": [product("flat")]}})
    assert [p["slug"] for p in unifi_core.fetch_all_products("bid", "us")] == ["flat"]


# ── build id cache ───────────────────────────────────────────────────────────

HOMEPAGE = '<script>{"buildId":"abc123"}</script>'


def test_build_id_is_cached_per_region(fake_session):
    fake_session.routes = {
        "/us/en": FakeResponse(200, text='{"buildId":"us-build"}'),
        "/eu/en": FakeResponse(200, text='{"buildId":"eu-build"}'),
    }
    assert unifi_core.get_build_id("us") == "us-build"
    assert unifi_core.get_build_id("eu") == "eu-build"
    # cached: no further homepage requests
    before = len(fake_session.calls)
    assert unifi_core.get_build_id("us") == "us-build"
    assert len(fake_session.calls) == before


def test_build_id_force_refetches(fake_session):
    fake_session.routes = {"/us/en": FakeResponse(200, text=HOMEPAGE)}
    unifi_core.get_build_id("us")
    before = len(fake_session.calls)
    unifi_core.get_build_id("us", force=True)
    assert len(fake_session.calls) == before + 1


def test_build_id_invalidate_is_region_scoped(fake_session):
    fake_session.routes = {
        "/us/en": FakeResponse(200, text='{"buildId":"us-build"}'),
        "/eu/en": FakeResponse(200, text='{"buildId":"eu-build"}'),
    }
    unifi_core.get_build_id("us")
    unifi_core.get_build_id("eu")
    unifi_core.invalidate_build_id("us")
    before = len(fake_session.calls)
    unifi_core.get_build_id("eu")            # still cached
    assert len(fake_session.calls) == before
    unifi_core.get_build_id("us")            # refetched
    assert len(fake_session.calls) == before + 1


def test_build_id_missing_raises_store_error(fake_session):
    fake_session.routes = {"/us/en": FakeResponse(200, text="<html>nope</html>")}
    with pytest.raises(StoreError):
        unifi_core.get_build_id("us")


# ── restock estimates ────────────────────────────────────────────────────────

from datetime import datetime, timezone  # noqa: E402

NOW = datetime(2026, 8, 19, 12, 0, tzinfo=timezone.utc)


def _oos(**variant_extra):
    p = product("x", status="SoldOut")
    p["variants"][0].update(variant_extra)
    return p


def test_parse_ts_accepts_trailing_z():
    """Python 3.10's fromisoformat rejects Z, and CI covers 3.10."""
    got = unifi_core._parse_ts("2026-08-06T14:50:25.663Z")
    assert got.tzinfo is not None
    assert got.year, got.month == (2026, 8)


def test_parse_ts_accepts_a_bare_date():
    assert unifi_core._parse_ts("2026-09-07").date().isoformat() == "2026-09-07"


@pytest.mark.parametrize("value", [None, "", "not a date", 12345, {}])
def test_parse_ts_rejects_junk(value):
    assert unifi_core._parse_ts(value) is None


def test_restock_eta_takes_the_earliest_variant():
    p = product("x", status="SoldOut")
    p["variants"] = [{"restockEtaAt": "2026-10-01"}, {"restockEtaAt": "2026-09-07"}]
    assert unifi_core.get_restock_eta(p).date().isoformat() == "2026-09-07"


def test_sold_out_at_takes_the_most_recent_variant():
    p = product("x", status="SoldOut")
    p["variants"] = [{"soldOutAt": "2026-08-01T00:00:00Z"},
                     {"soldOutAt": "2026-08-06T00:00:00Z"}]
    assert unifi_core.get_sold_out_at(p).day == 6


@pytest.mark.parametrize("eta, expected", [
    ("2026-08-19", "back ~today"),
    ("2026-08-20", "back ~tomorrow"),
    ("2026-08-26", "back ~7 days"),
    ("2026-09-07", "back ~7 Sep"),
    ("2026-08-01", "restock date passed"),
])
def test_describe_restock(eta, expected):
    assert unifi_core.describe_restock(_oos(restockEtaAt=eta), now=NOW) == expected


@pytest.mark.parametrize("since, expected", [
    ("2026-08-19T06:00:00Z", "sold out today"),
    ("2026-08-18T06:00:00Z", "sold out 1 day"),
    ("2026-08-06T14:50:00Z", "sold out 13 days"),
    ("2026-05-01T00:00:00Z", "sold out 3 months"),
])
def test_describe_sold_out_for(since, expected):
    assert unifi_core.describe_sold_out_for(_oos(soldOutAt=since), now=NOW) == expected


def test_descriptions_are_none_without_data():
    p = product("x", status="SoldOut")
    assert unifi_core.describe_restock(p, now=NOW) is None
    assert unifi_core.describe_sold_out_for(p, now=NOW) is None


# ── category discovery ───────────────────────────────────────────────────────

HOMEPAGE_WITH_CATS = """
<a href="/us/en/category/all-switching">Switching</a>
<a href="/us/en/category/all-wifi">WiFi</a>
<a href="/us/en/category/brand-new-thing">New</a>
<a href="/us/en/category/all-switching">dupe</a>
"""


def test_discover_finds_category_paths(fake_session):
    fake_session.routes = {"/us/en": FakeResponse(200, text=HOMEPAGE_WITH_CATS)}
    assert unifi_core.discover_category_paths("us") == [
        "category/all-switching", "category/all-wifi", "category/brand-new-thing"]


def test_category_following_a_redirect_still_returns_products(fake_session):
    """Regression: a renamed category silently dropped its whole product line."""
    fake_session.routes = {
        "/category/old-name.json": FakeResponse(
            200, {"pageProps": {"__N_REDIRECT": "/us/en/category/new-name"}}),
        "/category/new-name.json": FakeResponse(
            200, category_page(product("cam-1"), product("cam-2"))),
    }
    found = unifi_core._fetch_category("bid", "us", "category/old-name")
    assert sorted(found) == ["cam-1", "cam-2"]


def test_category_redirecting_off_the_category_space_is_empty(fake_session):
    """whats-new redirects to the homepage; that is not a category."""
    fake_session.routes = {
        "/category/whats-new.json": FakeResponse(
            200, {"pageProps": {"__N_REDIRECT": "/us/en"}}),
    }
    assert unifi_core._fetch_category("bid", "us", "category/whats-new") == {}


def test_coverage_adopts_a_category_that_adds_products(isolated_files, fake_session):
    def route(url):
        if "brand-new-thing" in url:
            return FakeResponse(200, category_page(product("exclusive-item")))
        if "/category/" in url:
            return FakeResponse(200, category_page(product("known-item")))
        return FakeResponse(200, text=HOMEPAGE_WITH_CATS)

    fake_session.routes = {"/us/en": route}
    adopted = unifi_core.refresh_category_coverage("bid", "us")
    assert adopted == ["category/brand-new-thing"]
    assert "category/brand-new-thing" in unifi_core.effective_categories("us")


def test_coverage_ignores_a_category_that_adds_nothing(isolated_files, fake_session):
    """Most discovered paths are sub-views returning the parent's set."""
    fake_session.routes = {
        "/us/en": lambda url: (
            FakeResponse(200, category_page(product("same-item")))
            if "/category/" in url else
            FakeResponse(200, text=HOMEPAGE_WITH_CATS))
    }
    assert unifi_core.refresh_category_coverage("bid", "us") == []
    assert "category/brand-new-thing" not in unifi_core.effective_categories("us")
    cache = unifi_core.read_json(unifi_core.CATEGORY_CACHE_FILE, {})
    assert "category/brand-new-thing" in cache["redundant"]


def test_coverage_respects_its_ttl(isolated_files, fake_session):
    fake_session.routes = {"/us/en": FakeResponse(200, text=HOMEPAGE_WITH_CATS)}
    unifi_core.write_json(unifi_core.CATEGORY_CACHE_FILE, {
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "extra": [], "redundant": [], "seen": []})
    before = len(fake_session.calls)
    assert unifi_core.refresh_category_coverage("bid", "us") == []
    assert len(fake_session.calls) == before, "made requests despite a fresh cache"


def test_coverage_survives_a_discovery_failure(isolated_files, fake_session):
    fake_session.routes = {"/us/en": FakeResponse(500)}
    assert unifi_core.refresh_category_coverage("bid", "us") == []
    assert unifi_core.effective_categories("us") == list(unifi_core.CATEGORIES)


# ── unpriced / coming-soon products ──────────────────────────────────────────

def test_zero_price_reads_as_unpriced_not_free():
    """Announced products carry amount 0; "$0.00" reads as free."""
    p = product("x", status="ComingSoon", amount=0)
    assert unifi_core.get_price(p) is None


def test_a_real_price_still_wins_over_an_unpriced_variant():
    p = product("x")
    p["variants"] = [{"displayPrice": {"amount": 0, "currency": "USD"}},
                     {"displayPrice": {"amount": 4900, "currency": "USD"}}]
    assert unifi_core.get_price(p) == "$49.00"


def test_is_coming_soon():
    assert unifi_core.is_coming_soon(product("x", status="ComingSoon"))
    assert not unifi_core.is_coming_soon(product("x", status="SoldOut"))
    assert not unifi_core.is_coming_soon(product("x", status="Available"))
    assert not unifi_core.is_coming_soon({"variants": []})


def test_coming_soon_is_not_available():
    assert not unifi_core.is_available(product("x", status="ComingSoon"))
