"""Small HTTP and text helpers shared by all connectors. No retries, short timeouts."""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from html import unescape

from intel.connectors.base import Item

_TAG = re.compile(r"<[^>]+>")
_SPACE = re.compile(r"\s+")
_USER_AGENT = "Wire/1.0 (+https://github.com/robinprause/wire)"
_BROWSER_AGENT = "Mozilla/5.0"


def get(url: str, headers: dict[str, str] | None = None, *, browser: bool = False) -> tuple[bytes, dict[str, str]]:
    request = urllib.request.Request(
        url,
        headers={"User-Agent": _BROWSER_AGENT if browser else _USER_AGENT, "Accept": "*/*", **(headers or {})},
    )
    try:
        with urllib.request.urlopen(request, timeout=16) as response:
            return response.read(), {key.lower(): value for key, value in response.headers.items()}
    except urllib.error.HTTPError as error:
        raise RuntimeError(f"HTTP {error.code}") from None
    except urllib.error.URLError:
        raise RuntimeError("network") from None


def get_text(url: str, headers: dict[str, str] | None = None, *, browser: bool = False) -> str:
    body, _headers = get(url, headers, browser=browser)
    return body.decode("utf-8", "replace")


def get_json(url: str, headers: dict[str, str] | None = None) -> tuple[object, dict[str, str]]:
    body, headers_out = get(url, headers)
    return json.loads(body.decode("utf-8", "replace")), headers_out


def header_int(headers: dict[str, str], name: str) -> int | None:
    raw = headers.get(name)
    if raw is None:
        return None
    try:
        return int(float(raw))
    except ValueError:
        return None


def plain(value: object, limit: int = 600) -> str:
    text = unescape(_TAG.sub(" ", str(value or "")))
    text = _SPACE.sub(" ", text).strip()
    return text[:limit]


def hour_bucket(moment: datetime | None = None) -> str:
    current = moment or datetime.now(UTC)
    return current.strftime("%Y%m%d%H")


def now_iso() -> str:
    return datetime.now(UTC).isoformat()


def parse_http_date(value: str) -> str:
    try:
        parsed = parsedate_to_datetime(value)
    except (TypeError, ValueError, IndexError, OverflowError):
        return value[:80]
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.isoformat()


def iso_from_epoch(value: object) -> str:
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        return ""
    try:
        number = float(value)
    except ValueError:
        return str(value)[:80]
    if number > 10_000_000_000:
        number /= 1000
    try:
        return datetime.fromtimestamp(number, UTC).isoformat()
    except (OverflowError, OSError, ValueError):
        return ""


def number(value: object) -> str:
    if isinstance(value, dict):
        picked = value.get("usd")
        if picked is None and value:
            picked = next(iter(value.values()))
        value = picked
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        return "n/a"
    try:
        parsed = float(value)
    except ValueError:
        return "n/a"
    if abs(parsed) >= 100:
        return f"{parsed:,.0f}"
    if abs(parsed) >= 1:
        return f"{parsed:,.2f}"
    return f"{parsed:.6f}"


def story(source: str, external_id: str, title: str, body: str, url: str, published_at: str) -> Item | None:
    """Build an Item from raw strings, or None when there is nothing to read."""
    title = plain(title, 240)
    body = plain(body)
    if not title and not body:
        return None
    return Item(
        source=source,
        external_id=plain(external_id, 240) or title,
        title=title or body[:120],
        body=body,
        url=plain(url, 500),
        published_at=published_at[:80],
    )
