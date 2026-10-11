"""Connectors that work for any topic: RSS feeds, web pages, public Telegram channels."""

from __future__ import annotations

import hashlib
import re
import urllib.parse
import xml.etree.ElementTree as ET
from collections.abc import Mapping
from html import unescape

from intel.connectors.base import Connector, Context, Field, Item, Pull
from intel.connectors.http import get_text, parse_http_date, plain, story

_SCRIPT = re.compile(r"<(script|style|noscript|svg)[^>]*>.*?</\1>", re.S | re.I)
_TITLE = re.compile(r"<title[^>]*>(.*?)</title>", re.S | re.I)
_HREF = re.compile(r'href="(https?://[^"#]+|/[^"#]+)"', re.I)
_MESSAGE = re.compile(r'js-message_text[^>]*>(.*?)</div>', re.S)
_WHEN = re.compile(r'datetime="([^"]+)"')
_TAG = re.compile(r"<[^>]+>")
_SPACE = re.compile(r"\s+")
PAGE_LINKS = 10


def parse_rss(xml: str, source: str, limit: int = 8) -> list[Item]:
    try:
        root = ET.fromstring(xml)
    except ET.ParseError:
        return []
    items: list[Item] = []
    for node in list(root.iter("item"))[:limit]:
        title = plain(node.findtext("title") or "", 240)
        link = plain(node.findtext("link") or "", 500)
        body = plain(node.findtext("description") or "")
        if not title and not body:
            continue
        items.append(
            Item(
                source=source,
                external_id=link or title,
                title=title or body[:120],
                body=body,
                url=link,
                published_at=parse_http_date(node.findtext("pubDate") or ""),
            )
        )
    return items


def fetch_rss(ctx: Context, params: Mapping[str, str]) -> Pull:
    return Pull(items=parse_rss(get_text(params["url"]), ctx.source_id))


def page_item(source: str, url: str, html: str) -> Item | None:
    """One page becomes one document. The id follows the text, so an edited page is re-indexed once."""
    stripped = _SCRIPT.sub(" ", html)
    title_match = _TITLE.search(stripped)
    title = plain(title_match.group(1) if title_match else "", 240)
    text = plain(stripped, 4000)
    if not text:
        return None
    digest = hashlib.sha256(text.encode()).hexdigest()[:16]
    return story(source, f"{url}#{digest}", title or url, text, url, "")


def page_links(base: str, html: str, limit: int = PAGE_LINKS) -> list[str]:
    """Same-host links on the page, in document order, without the page itself."""
    host = urllib.parse.urlparse(base).netloc
    found: list[str] = []
    for raw in _HREF.findall(html):
        url = urllib.parse.urljoin(base, unescape(raw))
        parsed = urllib.parse.urlparse(url)
        if parsed.netloc != host or url.rstrip("/") == base.rstrip("/") or url in found:
            continue
        if parsed.path.lower().endswith((".png", ".jpg", ".jpeg", ".gif", ".svg", ".css", ".js", ".pdf", ".xml")):
            continue
        found.append(url)
        if len(found) >= limit:
            break
    return found


def fetch_page(ctx: Context, params: Mapping[str, str]) -> Pull:
    url = params["url"]
    html = get_text(url, browser=True)
    items: list[Item] = []
    first = page_item(ctx.source_id, url, html)
    if first:
        items.append(first)
    if params.get("depth") != "links":
        return Pull(items=items)
    failed = 0
    for link in page_links(url, html):
        try:
            item = page_item(ctx.source_id, link, get_text(link, browser=True))
        except RuntimeError:
            failed += 1
            continue
        if item:
            items.append(item)
    note = f"{failed} links failed" if failed else ""
    return Pull(items=items, note=note)


def parse_telegram(html: str, source: str, limit: int = 12) -> list[Item]:
    items: list[Item] = []
    for chunk in html.split('data-post="')[1:]:
        post = chunk.split('"', 1)[0]
        if "/" not in post:
            continue
        text_match = _MESSAGE.search(chunk)
        if text_match is None:
            continue
        text = unescape(_TAG.sub(" ", text_match.group(1)))
        text = _SPACE.sub(" ", text).strip()
        if not text:
            continue
        time_match = _WHEN.search(chunk)
        published = time_match.group(1) if time_match else ""
        item = story(source, post, text[:180], text[:600], f"https://t.me/{post}", published[:80])
        if item:
            items.append(item)
        if len(items) >= limit:
            break
    return items


def fetch_telegram(ctx: Context, params: Mapping[str, str]) -> Pull:
    channel = params["channel"].strip().lstrip("@")
    return Pull(items=parse_telegram(get_text(f"https://t.me/s/{channel}", browser=True), ctx.source_id))


CONNECTORS: tuple[Connector, ...] = (
    Connector(
        kind="rss",
        label="RSS feed",
        summary="Reads the latest entries of an RSS or Atom feed.",
        fetch=fetch_rss,
        fields=(Field("url", "Feed URL", "https://example.com/feed"),),
    ),
    Connector(
        kind="page",
        label="Web page",
        summary="Loads a page as text. When the text changes, the page is indexed again.",
        fetch=fetch_page,
        fields=(
            Field("url", "Page URL", "https://example.com/news"),
            Field("depth", "Depth", options=(("page", "This page only"), ("links", f"Plus up to {PAGE_LINKS} linked pages"))),
        ),
        defaults={"depth": "page"},
    ),
    Connector(
        kind="telegram",
        label="Telegram channel",
        summary="Reads the latest posts of a public channel via t.me/s.",
        fetch=fetch_telegram,
        fields=(Field("channel", "Channel", "whale_alert_io"),),
    ),
)
