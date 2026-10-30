"""Alerts on your phone (webhooks.py).

http_post is the only call that reaches the network; conftest blocks it, and
these tests replace it with a recorder that answers like each service does.
"""

import threading

import pytest

import unifi_core
import webhooks
from conftest import FakeResponse

requests = unifi_core.requests

NTFY = {"phone_service": "ntfy", "ntfy_url": "https://ntfy.sh/usw-alerts-7f3k", "ntfy_token": ""}
PUSHOVER = {"phone_service": "pushover", "pushover_user": "u" * 30, "pushover_token": "a" * 30}
DISCORD = {"phone_service": "discord",
           "discord_url": "https://discord.com/api/webhooks/123456/SECRETTOKEN"}
SLACK = {"phone_service": "slack",
         "slack_url": "https://hooks.slack.com/services/T000/B000/SECRETTOKEN"}
JSON = {"phone_service": "webhook",
        "webhook_url": "http://homeassistant.local:8123/api/webhook/SECRETTOKEN"}


def alert(**kw):
    return webhooks.in_stock_alert(kw.pop("title", "Dream Router 7"), "udr7", "us",
                                   kw.pop("price", "$279.00"), **kw)


class Recorder:
    """Stands in for http_post: records each call, answers from a list."""

    def __init__(self, *answers):
        self.answers = list(answers) or [FakeResponse(200)]
        self.calls = []

    def __call__(self, url, body, headers):
        self.calls.append((url, body, headers))
        answer = self.answers.pop(0) if len(self.answers) > 1 else self.answers[0]
        if isinstance(answer, Exception):
            raise answer
        return answer


@pytest.fixture
def post(monkeypatch):
    def install(*answers):
        rec = Recorder(*answers)
        monkeypatch.setattr(webhooks, "http_post", rec)
        return rec
    return install


# ── the alert ────────────────────────────────────────────────────────────────

def test_an_in_stock_alert_names_the_product_store_and_price():
    a = alert()
    assert a.title == "Back in stock: Dream Router 7"
    assert a.text == "Available on the United States store for $279.00."
    assert a.url == unifi_core.product_url("udr7", "us")
    assert alert(price=None).text == "Available on the United States store."
    assert alert(favourite=True).title.startswith("★ ")


def test_a_test_alert_links_the_store():
    a = webhooks.test_alert("uk")
    assert a.test and a.url == unifi_core.store_home("uk")


# ── settings ─────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("settings", [NTFY, PUSHOVER, DISCORD, SLACK, JSON, {
    "phone_service": "ntfy", "ntfy_url": "https://ntfy.example.com/alerts/"}, {
    "phone_service": "slack", "slack_url": "https://hooks.slack.com/triggers/T0/1/abc"}, {
    "phone_service": "discord", "discord_url": "https://discordapp.com/api/webhooks/1/x"}])
def test_a_complete_setup_has_no_problem(settings):
    assert webhooks.problem(settings) is None


@pytest.mark.parametrize("settings", [
    {},
    {"phone_service": "telegram"},
    {"phone_service": "ntfy", "ntfy_url": ""},
    {"phone_service": "ntfy", "ntfy_url": "ntfy.sh/topic"},
    {"phone_service": "ntfy", "ntfy_url": "https://ntfy.sh/"},
    {"phone_service": "ntfy", "ntfy_url": "https://ntfy.sh/not a topic"},
    {"phone_service": "ntfy", "ntfy_url": "https://ntfy.sh/topic?x=1 2"},
    {"phone_service": "pushover", "pushover_user": "short", "pushover_token": "a" * 30},
    {"phone_service": "pushover", "pushover_user": "u" * 30, "pushover_token": ""},
    {"phone_service": "discord", "discord_url": "https://discord.com/channels/1/2"},
    {"phone_service": "discord", "discord_url": "http://discord.com/api/webhooks/1/x"},
    {"phone_service": "discord", "discord_url": "https://evil.example/api/webhooks/1/x"},
    {"phone_service": "slack", "slack_url": "https://slack.com/services/T/B/X"},
    {"phone_service": "webhook", "webhook_url": "ftp://example.com/x"},
    {"phone_service": "webhook", "webhook_url": "https://[::1/x"},
])
def test_an_incomplete_setup_says_what_is_missing(settings):
    why = webhooks.problem(settings)
    assert isinstance(why, str) and why


def test_the_label_is_a_name_or_a_host_never_the_address():
    assert webhooks.label(NTFY) == "ntfy"
    assert webhooks.label({"phone_service": "ntfy",
                           "ntfy_url": "https://push.example.com/t"}) == "push.example.com"
    assert webhooks.label(DISCORD) == "Discord"
    assert webhooks.label(JSON) == "homeassistant.local"
    assert "SECRET" not in webhooks.label(JSON)


# ── what each service is sent ────────────────────────────────────────────────

def test_ntfy_gets_json_at_the_server_root_with_the_topic_inside():
    url, body, headers = webhooks.build_request(NTFY, alert())
    assert url == "https://ntfy.sh/"
    assert body["topic"] == "usw-alerts-7f3k"
    assert body["title"] == "Back in stock: Dream Router 7"
    assert body["click"] == body["actions"][0]["url"] == unifi_core.product_url("udr7")
    assert headers == {}


def test_ntfy_keeps_a_path_prefix_a_query_and_a_token():
    s = {"phone_service": "ntfy", "ntfy_url": "https://example.com/ntfy/alerts?auth=abc",
         "ntfy_token": "tk_secret"}
    url, body, headers = webhooks.build_request(s, alert())
    assert url == "https://example.com/ntfy/?auth=abc" and body["topic"] == "alerts"
    assert headers["Authorization"] == "Bearer tk_secret"


def test_pushover_gets_both_keys_and_the_link():
    url, body, _ = webhooks.build_request(PUSHOVER, alert())
    assert url == webhooks.PUSHOVER_API
    assert body["user"] == "u" * 30 and body["token"] == "a" * 30
    assert body["url"] == unifi_core.product_url("udr7")


def test_discord_mentions_nobody_and_escapes_markdown():
    url, body, _ = webhooks.build_request(DISCORD, alert(title="Cable *2-pack* @everyone"))
    assert url == DISCORD["discord_url"]
    assert body["allowed_mentions"] == {"parse": []}
    assert r"Cable \*2-pack\* @everyone" in body["content"]
    assert unifi_core.product_url("udr7") in body["content"]


def test_slack_text_is_escaped_and_links_the_store():
    _, body, _ = webhooks.build_request(SLACK, alert(title="A <b> & C"))
    assert "A &lt;b&gt; &amp; C" in body["text"]
    assert f"<{unifi_core.product_url('udr7')}|Open in the store>" in body["text"]


def test_the_json_body_describes_the_product():
    _, body, _ = webhooks.build_request(JSON, alert(favourite=True))
    assert body["event"] == "in_stock" and body["app"] == unifi_core.APP_NAME
    assert body["product"] == {"title": "Dream Router 7", "slug": "udr7",
                               "price": "$279.00", "price_was": None, "favourite": True}
    assert body["store"] == "us" and body["url"] == unifi_core.product_url("udr7")
    _, body, _ = webhooks.build_request(JSON, webhooks.test_alert())
    assert body["event"] == "test" and body["product"] is None


def drop(**kw):
    return webhooks.price_drop_alert("Dream Router 7", "udr7", "uk", "£229.00", "£249.00", **kw)


def test_a_price_drop_alert_says_from_what_to_what():
    a = drop()
    assert a.title == "Price drop: Dream Router 7"
    assert a.text == "Now £229.00 on the United Kingdom store, down from £249.00."
    assert a.url == unifi_core.product_url("udr7", "uk")
    assert drop(favourite=True).title.startswith("★ ")


def test_a_price_drop_is_its_own_event():
    _, body, _ = webhooks.build_request(JSON, drop())
    assert body["event"] == "price_drop"
    assert body["product"]["price"] == "£229.00" and body["product"]["price_was"] == "£249.00"
    _, body, _ = webhooks.build_request(NTFY, drop(favourite=True))
    assert body["priority"] == 3 and body["tags"] == ["star", "chart_with_downwards_trend"]
    _, body, _ = webhooks.build_request(NTFY, alert())
    assert body["priority"] == 4 and body["tags"] == ["package"]


# ── delivery ─────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("settings", [NTFY, PUSHOVER, DISCORD, SLACK, JSON])
def test_a_2xx_is_delivered(post, settings):
    rec = post(FakeResponse(204))
    assert webhooks.send(settings, alert()) is None
    assert rec.calls[0][2]["User-Agent"].startswith("UniFiStockWatcher/")


def test_an_incomplete_setup_sends_nothing(post):
    rec = post()
    assert webhooks.send({"phone_service": "ntfy", "ntfy_url": ""}, alert())
    assert rec.calls == []


@pytest.mark.parametrize("response,expect", [
    (FakeResponse(404, {"message": "Unknown Webhook", "code": 10015}), "HTTP 404 (Unknown Webhook)"),
    (FakeResponse(400, {"status": 0, "errors": ["user identifier is invalid"]}),
     "HTTP 400 (user identifier is invalid)"),
    (FakeResponse(401, {"code": 40101, "error": "unauthorized"}), "HTTP 401 (unauthorized)"),
    (FakeResponse(403, text="invalid_token"), "HTTP 403 (invalid_token)"),
    (FakeResponse(404, text="<html>" + "x" * 500), "HTTP 404"),
    (FakeResponse(301, headers={"Location": "https://elsewhere"}), "HTTP 301 (the address redirects"),
])
def test_a_refusal_is_explained_and_not_retried(post, response, expect):
    rec = post(response)
    reason = webhooks.send(DISCORD, alert(), attempts=3, sleep=lambda s: None)
    assert reason.startswith(expect)
    assert len(rec.calls) == 1


def test_a_refusal_never_repeats_the_secret_path(post):
    path = "/api/webhooks/123456/SECRETTOKEN"
    post(FakeResponse(404, {"message": f"no webhook at {path}"}))
    reason = webhooks.send(DISCORD, alert())
    assert "SECRETTOKEN" not in reason


@pytest.mark.parametrize("exc,expect", [
    (requests.exceptions.ConnectTimeout, "didn't answer"),
    (requests.exceptions.ReadTimeout, "didn't answer"),
    (requests.exceptions.SSLError, "secure connection"),
    (requests.exceptions.ConnectionError, "couldn't connect"),
    (requests.exceptions.InvalidURL, "InvalidURL"),
])
def test_a_network_failure_names_the_host_only(post, exc, expect):
    # requests' own messages quote the whole URL, secret and all.
    post(exc(f"HTTPSConnectionPool(host='discord.com'): url: "
             f"{DISCORD['discord_url']} (Caused by ...)"))
    reason = webhooks.send(DISCORD, alert())
    assert expect in reason and "discord.com" in reason
    assert "SECRETTOKEN" not in reason and "/api/" not in reason


def test_passing_failures_are_retried(post):
    rec = post(requests.exceptions.ConnectionError("x"), FakeResponse(502), FakeResponse(200))
    waits = []
    assert webhooks.send(NTFY, alert(), attempts=3, sleep=waits.append) is None
    assert len(rec.calls) == 3 and waits == list(webhooks.RETRY_WAITS)


def test_a_retry_honours_retry_after_up_to_a_cap(post):
    post(FakeResponse(429, headers={"Retry-After": "7"}),
         FakeResponse(429, headers={"Retry-After": "9999"}), FakeResponse(200))
    waits = []
    assert webhooks.send(DISCORD, alert(), attempts=3, sleep=waits.append) is None
    assert waits == [7, webhooks.MAX_RETRY_AFTER]


def test_retries_give_up_with_the_last_reason(post):
    rec = post(FakeResponse(503))
    reason = webhooks.send(SLACK, alert(), attempts=3, sleep=lambda s: None)
    assert reason == "HTTP 503" and len(rec.calls) == 3


def test_a_test_send_tries_once(post):
    rec = post(FakeResponse(503))
    webhooks.send(SLACK, webhooks.test_alert(), sleep=lambda s: None)
    assert len(rec.calls) == 1


# ── the sender thread ────────────────────────────────────────────────────────

def test_the_sender_delivers_in_order_and_reports_back(post, monkeypatch):
    monkeypatch.setattr(webhooks, "IDLE_EXIT", 0.05)
    rec = post(FakeResponse(200), FakeResponse(404, {"message": "Unknown Webhook"}))
    results, done = [], threading.Event()

    def on_result(where, reason):
        results.append((where, reason))
        if len(results) == 2:
            done.set()
    sender = webhooks.Sender()
    sender.submit(DISCORD, alert(title="One"), 1, on_result)
    sender.submit(DISCORD, alert(title="Two"), 1, on_result)
    assert done.wait(5)
    assert [b["content"].split("\n")[0] for _, b, _ in rec.calls] == [
        "**Back in stock: One**", "**Back in stock: Two**"]
    assert results == [("Discord", None), ("Discord", "HTTP 404 (Unknown Webhook)")]


def test_the_sender_uses_the_settings_as_they_were(post, monkeypatch):
    """The user may be typing a new address while an alert is retried."""
    monkeypatch.setattr(webhooks, "IDLE_EXIT", 0.05)
    gate = threading.Event()
    rec = post(FakeResponse(200))
    original = webhooks.send

    def slow_send(*a, **k):
        gate.wait(5)
        return original(*a, **k)
    monkeypatch.setattr(webhooks, "send", slow_send)
    settings, done = dict(NTFY), threading.Event()
    webhooks.Sender().submit(settings, alert(), 1, lambda *a: done.set())
    settings["ntfy_url"] = "https://ntfy.sh/changed"
    gate.set()
    assert done.wait(5)
    assert rec.calls[0][1]["topic"] == "usw-alerts-7f3k"


def test_the_sender_survives_an_error_and_ends_when_idle(post, monkeypatch):
    monkeypatch.setattr(webhooks, "IDLE_EXIT", 0.05)
    monkeypatch.setattr(webhooks, "build_request", lambda *a: 1 / 0)
    results, done = [], threading.Event()
    sender = webhooks.Sender()
    sender.submit(NTFY, alert(), 1, lambda w, r: (results.append(r), done.set()))
    assert done.wait(5)
    assert results == ["couldn't send (ZeroDivisionError)"]
    for _ in range(100):
        if sender._thread is None:
            break
        threading.Event().wait(0.02)
    assert sender._thread is None
    done.clear()
    sender.submit(NTFY, alert(), 1, lambda w, r: done.set())     # starts a new one
    assert done.wait(5)
