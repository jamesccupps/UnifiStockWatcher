"""Alerts on your phone: an in-stock (or price drop) alert sent to ntfy,
Pushover, Discord or Slack, or as JSON to any address (Home Assistant, n8n, ...).

Each service gets the message in its own format, so it reads well in that
service's app. Delivery runs off the caller's thread (`Sender`), retries the
failures that pass (no connection, a timeout, 429, 5xx), and reports back a
short reason. The reason never contains the address: Discord, Slack and ntfy
put the secret in it, and reasons go to the log and the activity panel. That
includes requests' own exception text, which quotes the URL.
"""

import queue
import re
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from urllib.parse import urlsplit, urlunsplit

try:
    import requests
except ImportError:                 # the app says so at start-up (_bootstrap)
    requests = None

from unifi_core import (
    __version__, APP_NAME, REPO_URL, STORE_REGIONS, PHONE_SERVICES,
    product_url, store_home,
)

SERVICES = dict(zip(PHONE_SERVICES, (
    "ntfy", "Pushover", "Discord", "Slack", "Any address (JSON)")))

TIMEOUT = 10                        # seconds per attempt
ATTEMPTS = 3                        # for real alerts; a test tries once
RETRY_WAITS = (5, 20)               # seconds before the 2nd and 3rd attempts
MAX_RETRY_AFTER = 60                # cap on a service's own Retry-After
IDLE_EXIT = 30                      # the sender thread ends after this long idle

PUSHOVER_API = "https://api.pushover.net/1/messages.json"
DISCORD_HOSTS = {"discord.com", "discordapp.com", "ptb.discord.com", "canary.discord.com"}
AVATAR_URL = "https://jamesccupps.github.io/UnifiStockWatcher/images/icon.png"
USER_AGENT = f"UniFiStockWatcher/{__version__} (+{REPO_URL})"

_NTFY_TOPIC = re.compile(r"^[-_A-Za-z0-9]{1,64}$")
_PUSHOVER_KEY = re.compile(r"^[A-Za-z0-9]{30}$")


@dataclass
class Alert:
    title: str                      # "Back in stock: UniFi Dream Router 7"
    text: str                       # "Available on the United States store for $279.00."
    url: str                        # the product's page (the store's, for a test)
    product: str = ""
    slug: str = ""
    price: str | None = None
    store: str = "us"
    favourite: bool = False
    test: bool = False
    event: str = "in_stock"         # or "price_drop"
    price_was: str | None = None


def in_stock_alert(title, slug, region="us", price=None, favourite=False):
    store = STORE_REGIONS.get(region, STORE_REGIONS["us"])["label"]
    text = f"Available on the {store} store" + (f" for {price}." if price else ".")
    return Alert(title=f"{'★ ' if favourite else ''}Back in stock: {title}", text=text,
                 url=product_url(slug, region), product=title, slug=slug, price=price,
                 store=region, favourite=favourite)


def price_drop_alert(title, slug, region, price, was, favourite=False):
    store = STORE_REGIONS.get(region, STORE_REGIONS["us"])["label"]
    return Alert(title=f"{'★ ' if favourite else ''}Price drop: {title}",
                 text=f"Now {price} on the {store} store, down from {was}.",
                 url=product_url(slug, region), product=title, slug=slug, price=price,
                 store=region, favourite=favourite, event="price_drop", price_was=was)


def test_alert(region="us"):
    return Alert(title=f"Test from {APP_NAME}",
                 text="When a product you watch is back in stock, you'll get an alert "
                      "like this one, with a link to the product's page.",
                 url=store_home(region), store=region, test=True)


# ── Settings ─────────────────────────────────────────────────────────────────

def _url(text):
    """The parts of an http(s) address with a host, or None."""
    if not text or any(ch.isspace() for ch in text):
        return None
    try:
        parts = urlsplit(text)
        host = parts.hostname
    except ValueError:
        return None
    if parts.scheme not in ("http", "https") or not host:
        return None
    return parts


def problem(settings):
    """What's missing or wrong for the chosen service, or None if it can send."""
    service = settings.get("phone_service")
    if service == "ntfy":
        parts = _url(settings.get("ntfy_url", ""))
        if not parts:
            return "Enter your topic's address, like https://ntfy.sh/your-topic."
        if not _NTFY_TOPIC.match(parts.path.rstrip("/").rpartition("/")[2]):
            return "The address should end in the topic's name (letters, digits, - and _)."
    elif service == "pushover":
        if not _PUSHOVER_KEY.match(settings.get("pushover_user", "")):
            return "Enter your Pushover user key (30 letters and digits)."
        if not _PUSHOVER_KEY.match(settings.get("pushover_token", "")):
            return "Enter the API token of an application you made on pushover.net."
    elif service == "discord":
        parts = _url(settings.get("discord_url", ""))
        if not (parts and parts.scheme == "https" and parts.hostname in DISCORD_HOSTS
                and parts.path.startswith("/api/webhooks/")):
            return "Enter a Discord webhook address: https://discord.com/api/webhooks/…"
    elif service == "slack":
        parts = _url(settings.get("slack_url", ""))
        if not (parts and parts.scheme == "https" and parts.hostname == "hooks.slack.com"
                and parts.path.startswith(("/services/", "/triggers/"))):
            return "Enter a Slack webhook address: https://hooks.slack.com/services/…"
    elif service == "webhook":
        if not _url(settings.get("webhook_url", "")):
            return "Enter the address to send to, starting with https:// or http://."
    else:
        return "Choose a service."
    return None


def label(settings):
    """Where alerts go, for messages: a service's name, or a host. Never more
    of the address than the host."""
    service = settings.get("phone_service")
    if service in ("ntfy", "webhook"):
        parts = _url(settings.get(f"{service}_url", ""))
        host = parts.hostname if parts else ""
        if service == "ntfy" and host in ("", "ntfy.sh"):
            return "ntfy"
        return host or SERVICES[service]
    return SERVICES.get(service, "your phone")


# ── Requests ─────────────────────────────────────────────────────────────────

def _discord_md(text):
    return re.sub(r"([\\*_~`|])", r"\\\1", text)


def _slack_escape(text):
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def build_request(settings, alert):
    """(url, JSON body, headers) for the chosen service. Call problem() first."""
    service = settings["phone_service"]
    headers = {}
    if service == "ntfy":
        # ntfy takes JSON at the server's root, with the topic in the body;
        # JSON posted to the topic's own address would arrive as the message.
        p = urlsplit(settings["ntfy_url"])
        base, _, topic = p.path.rstrip("/").rpartition("/")
        url = urlunsplit((p.scheme, p.netloc, base + "/", p.query, ""))
        drop = alert.event == "price_drop"
        tags = ["chart_with_downwards_trend"] if drop else ["package"]
        body = {"topic": topic, "title": alert.title, "message": alert.text,
                "click": alert.url, "priority": 3 if drop else 4,
                "tags": ["star", *tags] if alert.favourite else tags,
                "actions": [{"action": "view", "label": "Open in the store", "url": alert.url}]}
        if settings.get("ntfy_token"):
            headers["Authorization"] = f"Bearer {settings['ntfy_token']}"
    elif service == "pushover":
        url = PUSHOVER_API
        body = {"token": settings["pushover_token"], "user": settings["pushover_user"],
                "title": alert.title[:250], "message": alert.text[:1024],
                "url": alert.url, "url_title": "Open in the store"}
    elif service == "discord":
        url = settings["discord_url"]
        content = f"**{_discord_md(alert.title)}**\n{_discord_md(alert.text)}\n{alert.url}"
        body = {"username": APP_NAME, "avatar_url": AVATAR_URL, "content": content[:2000],
                "allowed_mentions": {"parse": []}}
    elif service == "slack":
        url = settings["slack_url"]
        body = {"text": f"*{_slack_escape(alert.title)}*\n{_slack_escape(alert.text)}\n"
                        f"<{alert.url}|Open in the store>"}
    else:
        url = settings["webhook_url"]
        body = {"event": "test" if alert.test else alert.event,
                "app": APP_NAME, "version": __version__,
                "time": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "title": alert.title, "text": alert.text, "url": alert.url,
                "store": alert.store,
                "product": None if alert.test else {
                    "title": alert.product, "slug": alert.slug, "price": alert.price,
                    "price_was": alert.price_was, "favourite": alert.favourite}}
    return url, body, headers


# ── Sending ──────────────────────────────────────────────────────────────────

def http_post(url, body, headers):
    """The one call that reaches the network (tests replace it). Redirects are
    not followed: requests would turn a POST into a GET on the way."""
    return requests.post(url, json=body, headers=headers, timeout=TIMEOUT,
                         allow_redirects=False)


def _refusal(resp, url):
    """Why a service said no, short: its status and its own explanation."""
    code = resp.status_code
    if 300 <= code < 400:
        return f"HTTP {code} (the address redirects somewhere else; check it)"
    detail = ""
    try:
        data = resp.json()
    except ValueError:
        data = None
    if isinstance(data, dict):
        errors = data.get("errors")                    # Pushover
        if isinstance(errors, list) and errors:
            detail = str(errors[0])
        elif isinstance(data.get("message"), str):     # Discord
            detail = data["message"]
        elif isinstance(data.get("error"), str):       # ntfy
            detail = data["error"]
    elif isinstance(resp.text, str) and len(resp.text) <= 200 and "<" not in resp.text:
        detail = resp.text                             # Slack: "invalid_token"
    path = urlsplit(url).path
    if len(path) > 1:
        detail = detail.replace(path, "…")
    detail = " ".join(detail.split())[:120]
    return f"HTTP {code}" + (f" ({detail})" if detail else "")


def _retry_after(resp):
    try:
        wait = float(resp.headers.get("Retry-After", 0))
    except (TypeError, ValueError):
        wait = 0
    return min(max(wait, 0), MAX_RETRY_AFTER)


def _attempt(url, body, headers):
    """(None, None) when delivered, else (reason, wait): wait None means don't
    retry, 0 the usual pause, more the service's own Retry-After."""
    host = urlsplit(url).hostname
    if requests is None:
        return "the requests package isn't installed", None
    try:
        resp = http_post(url, body, headers)
    except requests.exceptions.Timeout:
        return f"{host} didn't answer within {TIMEOUT} s", 0
    except requests.exceptions.SSLError:
        return f"couldn't make a secure connection to {host}", None
    except requests.exceptions.ConnectionError:
        return f"couldn't connect to {host}", 0
    except requests.exceptions.RequestException as e:
        return f"couldn't send to {host} ({type(e).__name__})", None
    if 200 <= resp.status_code < 300:
        return None, None
    reason = _refusal(resp, url)
    if resp.status_code == 429 or resp.status_code >= 500:
        return reason, _retry_after(resp)
    return reason, None


def send(settings, alert, attempts=1, sleep=time.sleep):
    """Deliver one alert, on this thread. None when the service took it, else
    why not."""
    why = problem(settings)
    if why:
        return why
    url, body, headers = build_request(settings, alert)
    headers = {"User-Agent": USER_AGENT, **headers}
    reason = None
    for n in range(attempts):
        reason, wait = _attempt(url, body, headers)
        if reason is None or wait is None or n == attempts - 1:
            return reason
        sleep(wait or RETRY_WAITS[min(n, len(RETRY_WAITS) - 1)])
    return reason


PHONE_KEYS = ("phone_service", "ntfy_url", "ntfy_token", "pushover_user",
              "pushover_token", "discord_url", "slack_url", "webhook_url")


class Sender:
    """Sends alerts one at a time on a thread of its own, in order, and calls
    each one's on_result(label, reason) there when it's done (reason None:
    delivered). The thread starts with the first alert and ends when idle."""

    def __init__(self):
        self._queue = queue.Queue()
        self._lock = threading.Lock()
        self._thread = None

    def submit(self, settings, alert, attempts=ATTEMPTS, on_result=None):
        # A copy of the settings as they are now: the user may be typing a
        # new address while this one is still being retried.
        job = ({k: settings.get(k, "") for k in PHONE_KEYS}, alert, attempts, on_result)
        with self._lock:
            self._queue.put(job)
            if self._thread is None:
                self._thread = threading.Thread(target=self._run, name="webhook-sender",
                                                daemon=True)
                self._thread.start()

    def _run(self):
        while True:
            try:
                settings, alert, attempts, on_result = self._queue.get(timeout=IDLE_EXIT)
            except queue.Empty:
                with self._lock:
                    if self._queue.empty():
                        self._thread = None
                        return
                continue
            try:
                reason = send(settings, alert, attempts)
            except Exception as e:                     # never let one alert end the thread
                reason = f"couldn't send ({type(e).__name__})"
            if on_result:
                try:
                    on_result(label(settings), reason)
                except Exception:
                    pass
