"""Contract tests against the real Ubiquiti store.

Deliberately outside `tests/` so the normal suite never reaches the network:
pytest.ini sets `testpaths = tests`, and these run only when named explicitly
(`pytest tests_live`). CI runs them on a schedule, not per push.

Everything in `tests/` is mocked, which means a change to the store's response
shape leaves the suite green while the app quietly reports the wrong thing.
That is not hypothetical - it is exactly what happened: for roughly half the
catalog the store began answering `/products/<slug>.json` with a redirect
directive instead of product data, `check_slug` returned a fabricated
"out of stock", and nothing failed. These tests exist to catch that class of
change the day it ships rather than the day a restock is missed.

They are polite: one catalog fetch, then a small sample of product pages with
a delay between them.
"""

import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import unifi_core  # noqa: E402

REGION = "us"
SAMPLE_SIZE = 8
SAMPLE_DELAY = 1.0

# The catalog has sat around 420 products. Wide bounds - this is here to catch
# "parsed zero and reported success", not to police Ubiquiti's product range.
MIN_PRODUCTS = 150
MAX_PRODUCTS = 1200


@pytest.fixture(scope="module")
def build_id():
    try:
        return unifi_core.get_build_id(REGION)
    except Exception as e:
        pytest.fail(
            f"Could not read a buildId from the store homepage: {e}\n"
            "Either the store is unreachable or the "
            '\'"buildId":"..."\' pattern no longer appears in the page.')


@pytest.fixture(scope="module")
def catalog(build_id):
    errors = []
    products = unifi_core.fetch_all_products(
        build_id, REGION, error_cb=lambda cat, e: errors.append((cat, e)))
    assert not errors, f"category endpoints failed: {errors}"
    return products


# ── catalog shape ────────────────────────────────────────────────────────────

def test_catalog_is_a_plausible_size(catalog):
    assert MIN_PRODUCTS <= len(catalog) <= MAX_PRODUCTS, (
        f"got {len(catalog)} products; the category page shape has probably "
        "changed (products used to sit under pageProps.subCategories[].products)")


def test_every_product_has_the_fields_the_app_relies_on(catalog):
    missing = [p.get("slug") or "<no slug>" for p in catalog
               if not p.get("slug") or not p.get("title") or "variants" not in p]
    assert not missing, f"products missing slug/title/variants: {missing[:10]}"


def test_variant_status_values_are_still_recognised(catalog):
    """is_available() keys off the exact string "Available"."""
    seen = {v.get("status") for p in catalog for v in p.get("variants", [])}
    assert "Available" in seen, (
        f"no variant reported status 'Available'; statuses seen: {sorted(seen)}. "
        "is_available() would report the whole catalog as out of stock.")


def test_both_stock_states_are_present(catalog):
    """The regression that started all this looked exactly like this.

    If every product parses as out of stock, the watcher never fires and
    nothing errors. A catalog with zero availability either side is far more
    likely to be a parsing break than a real sell-out.
    """
    in_stock = sum(1 for p in catalog if unifi_core.is_available(p))
    out = len(catalog) - in_stock
    assert in_stock > 0, "no product parsed as in stock"
    assert out > 0, "no product parsed as out of stock"


def test_prices_parse_into_formatted_strings(catalog):
    priced = [unifi_core.get_price(p) for p in catalog]
    priced = [p for p in priced if p]
    assert len(priced) > len(catalog) // 2, "most products yielded no price"
    # Money is sent in minor units; a formatting change would show up as an
    # absurd magnitude rather than an exception.
    assert any(p.startswith("$") for p in priced), f"unexpected format: {priced[:5]}"


def test_categories_all_still_resolve(build_id):
    for cat in unifi_core.CATEGORIES:
        found = unifi_core._fetch_category(build_id, REGION, cat)
        assert found, f"category returned no products: {cat}"
        time.sleep(0.2)


# ── single-product lookups ───────────────────────────────────────────────────

def test_check_slug_agrees_with_the_catalog(catalog, build_id):
    """The original defect in one assertion.

    check_slug used to disagree with the catalog for about half the products
    because it did not follow the store's redirect directive. Sampling across
    the catalog catches that returning.
    """
    step = max(1, len(catalog) // SAMPLE_SIZE)
    sample = catalog[::step][:SAMPLE_SIZE]

    disagreements = []
    for p in sample:
        slug = p["slug"]
        expected = (unifi_core.is_available(p), unifi_core.get_price(p))
        try:
            got = unifi_core.check_slug(build_id, slug, REGION)
        except unifi_core.ProductNotFound:
            continue                      # legitimately delisted mid-run
        if tuple(got) != tuple(expected):
            disagreements.append((slug, got, expected))
        time.sleep(SAMPLE_DELAY)

    assert not disagreements, (
        "check_slug disagrees with the catalog for: "
        + "; ".join(f"{s}: got {g}, catalog says {e}" for s, g, e in disagreements))


def test_redirecting_products_still_resolve(catalog, build_id):
    """Around half the catalog answers with __N_REDIRECT rather than data."""
    redirected = []
    for p in catalog[:40]:
        page = unifi_core.get_session().get(
            unifi_core._data_url(
                build_id,
                f"/{unifi_core.STORE_REGIONS[REGION]['path']}/products/{p['slug']}"),
            timeout=15)
        if page.status_code == 200 and page.json().get(
                "pageProps", {}).get("__N_REDIRECT"):
            redirected.append(p)
            break
        time.sleep(0.2)

    if not redirected:
        pytest.skip("no redirecting product found in the sample")

    p = redirected[0]
    got = unifi_core.check_slug(build_id, p["slug"], REGION)
    assert got == (unifi_core.is_available(p), unifi_core.get_price(p)), (
        f"{p['slug']} redirects and check_slug no longer follows it")


def test_a_missing_slug_raises_rather_than_reporting_out_of_stock(build_id):
    with pytest.raises(unifi_core.ProductNotFound):
        unifi_core.check_slug(build_id, "definitely-not-a-real-product-xyz", REGION)


# ── restock estimates ────────────────────────────────────────────────────────

def test_restock_fields_are_still_populated(catalog):
    """These drive the restock estimates shown on watched rows."""
    oos = [p for p in catalog if not unifi_core.is_available(p)]
    if not oos:
        pytest.skip("nothing out of stock right now")
    with_info = [p for p in oos
                 if unifi_core.describe_restock(p)
                 or unifi_core.describe_sold_out_for(p)]
    assert with_info, (
        f"none of {len(oos)} out-of-stock products carry restockEtaAt or "
        "soldOutAt; the restock estimates have gone blank")
