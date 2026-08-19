"""Shared fixtures.

No HTTP library beyond requests itself: the store is faked with a small stub
session so the suite runs offline and deterministically.
"""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import unifi_core  # noqa: E402


class FakeResponse:
    def __init__(self, status_code=200, json_data=None, text="", headers=None):
        self.status_code = status_code
        self._json = json_data
        self.text = text
        self.headers = headers or {}

    def json(self):
        if self._json is None:
            raise ValueError("No JSON object could be decoded")
        return self._json

    def raise_for_status(self):
        if 400 <= self.status_code < 600:
            err = unifi_core.requests.exceptions.HTTPError(
                f"{self.status_code} error")
            err.response = self
            raise err


class FakeSession:
    """Maps URL substrings to responses, and records what was requested."""

    def __init__(self, routes=None, default=None):
        self.routes = routes or {}
        self.default = default
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append(url)
        # Longest fragment first: a canonical product URL contains the bare
        # /products/<slug>.json fragment too, and matching that first would
        # loop a redirect test back onto its own starting point.
        for fragment in sorted(self.routes, key=len, reverse=True):
            if fragment in url:
                response = self.routes[fragment]
                return response(url) if callable(response) else response
        if self.default is not None:
            return self.default
        return FakeResponse(404, text="not found")


@pytest.fixture
def fake_session(monkeypatch):
    """Install a FakeSession; tests populate .routes."""
    session = FakeSession()
    monkeypatch.setattr(unifi_core, "get_session", lambda: session)
    unifi_core.invalidate_build_id()
    yield session
    unifi_core.invalidate_build_id()


@pytest.fixture
def isolated_files(tmp_path, monkeypatch):
    """Point every on-disk artefact at a temp directory."""
    monkeypatch.setattr(unifi_core, "CONFIG_FILE", tmp_path / "watched_items.json")
    monkeypatch.setattr(unifi_core, "SETTINGS_FILE", tmp_path / "settings.json")
    monkeypatch.setattr(unifi_core, "HISTORY_FILE", tmp_path / "stock_history.json")
    monkeypatch.setattr(unifi_core, "CATEGORY_CACHE_FILE",
                        tmp_path / "category_cache.json")
    return tmp_path


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    """Fail loudly if a mocked test reaches for the real network.

    The suite is supposed to be offline; tests_live/ is where real traffic
    belongs. Without this, adding a call like refresh_category_coverage to a
    startup path silently turns the suite into an integration test - which is
    how it went from 22s to 54s once.
    """
    def blocked(*a, **k):
        raise AssertionError(
            "test attempted a real HTTP request; mock it, or move the test "
            "into tests_live/")
    monkeypatch.setattr(unifi_core, "get_session", blocked)


def variant(status="Available", amount=19900, currency="USD", **extra):
    v = {"status": status, "displayPrice": {"amount": amount, "currency": currency}}
    v.update(extra)
    return v


def product(slug, title=None, status="Available", amount=19900, **extra):
    p = {
        "slug": slug,
        "title": title or slug.replace("-", " ").title(),
        "id": f"id-{slug}",
        "variants": [variant(status, amount)],
    }
    p.update(extra)
    return p


def category_page(*products):
    return {"pageProps": {"subCategories": [{"products": list(products)}]}}


def product_page(current_slug, *collection_products):
    current = next(p for p in collection_products if p["slug"] == current_slug)
    return {"pageProps": {
        "currentProductId": current["id"],
        "collection": {"products": list(collection_products)},
    }}


def write(path, data):
    Path(path).write_text(json.dumps(data), encoding="utf-8")
