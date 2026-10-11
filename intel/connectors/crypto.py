"""Crypto market connectors. They ship with Wire as one example pack; a desk may use none of them."""

from __future__ import annotations

import re
import urllib.parse
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta

from intel.connectors.base import Balance, Connector, Context, Field, Item, Pull, shared_key
from intel.connectors.http import get_json, header_int, hour_bucket, iso_from_epoch, now_iso, number, plain, story
from intel.store import Store

FOMO_SECRET = "FOMO_API_KEY"
FOMO_CREDIT_FLOOR = 20_000
FOMO_PAGE = 20
FOMO_PAGE_CREDITS = 125
_FOMO_BALANCE_KEY = "fomo_credits_remaining"
_BINANCE_CATALOGS = {48, 49, 161}
_GECKO_NETWORKS = (("solana", "Solana"), ("eth", "Ethereum"), ("base", "Base"), ("bsc", "BNB Chain"))
_DEX_LISTS = (
    ("boosts", "Boosts", "https://api.dexscreener.com/token-boosts/latest/v1"),
    ("profiles", "Profiles", "https://api.dexscreener.com/token-profiles/latest/v1"),
    ("takeovers", "Community takeovers", "https://api.dexscreener.com/community-takeovers/latest/v1"),
)
_CREDITS = re.compile(r"^\d+$")


def parse_fomo(payload: dict[str, object], source: str) -> tuple[list[Item], str | None, bool]:
    raw_alerts = payload.get("alerts")
    alerts = raw_alerts if isinstance(raw_alerts, list) else []
    items: list[Item] = []
    for alert in alerts:
        if not isinstance(alert, dict):
            continue
        event_id = str(alert.get("eventId") or alert.get("id") or "")
        if not event_id:
            continue
        title = plain(alert.get("text") or "", 240)
        trader = plain(alert.get("trader") or "", 80)
        token = plain(alert.get("token") or "", 40)
        kind = plain(alert.get("type") or "", 40)
        chain = plain(alert.get("chain") or "", 40)
        usd = alert.get("usdValue")
        size = f"${usd:,.0f}" if isinstance(usd, (int, float)) and not isinstance(usd, bool) else ""
        body = " · ".join(part for part in (kind, token, trader, chain, size) if part)
        items.append(
            Item(
                source=source,
                external_id=event_id,
                title=title or body or event_id,
                body=body,
                url="",
                published_at=iso_from_epoch(alert.get("ts")),
            )
        )
    cursor = payload.get("nextCursor")
    next_cursor = cursor if isinstance(cursor, str) and cursor else None
    return items, next_cursor, bool(payload.get("hasMore"))


def fomo_blocked(remaining: int | None) -> bool:
    return remaining is not None and remaining < FOMO_CREDIT_FLOOR


def fomo_credits(store: Store) -> int | None:
    raw = store.get_state(shared_key(_FOMO_BALANCE_KEY))
    return int(raw) if raw and _CREDITS.match(raw) else None


def fomo_balance(store: Store) -> Balance:
    return Balance(label="FOMO", remaining=fomo_credits(store), per_pull=FOMO_PAGE_CREDITS, floor=FOMO_CREDIT_FLOOR)


def fetch_fomo(ctx: Context, _params: Mapping[str, str]) -> Pull:
    previous = ctx.last_run()
    if previous is not None and (str(previous["status"]) == "ok" or str(previous["note"]).startswith("already pulled")):
        finished = datetime.fromisoformat(str(previous["finished_at"]))
        if finished.tzinfo is None:
            finished = finished.replace(tzinfo=UTC)
        if datetime.now(UTC) - finished < timedelta(minutes=50):
            return Pull(items=[], note="already pulled this hour")
    remaining = fomo_credits(ctx.store)
    if fomo_blocked(remaining):
        return Pull(items=[], credits_remaining=remaining, note="credit floor, FOMO skipped")
    key = ctx.secret(FOMO_SECRET)
    if not key:
        return Pull(items=[], note=f"{FOMO_SECRET} missing")
    cursor = ctx.get("cursor")
    query = f"limit={FOMO_PAGE}"
    if cursor:
        query += f"&cursor={urllib.parse.quote(cursor, safe='')}"
    payload, headers = get_json(f"https://api.fomoapi.io/v2/alerts?{query}", {"Authorization": f"Bearer {key}"})
    cost = header_int(headers, "x-credits-cost")
    left = header_int(headers, "x-credits-remaining")
    if not isinstance(payload, dict):
        return Pull(items=[], credits_cost=cost, credits_remaining=left, note="unexpected payload")
    items, next_cursor, has_more = parse_fomo(payload, ctx.source_id)
    if next_cursor:
        ctx.set("cursor", next_cursor)
    if left is not None:
        ctx.shared_set(_FOMO_BALANCE_KEY, str(left))
    note = "one page, older alerts left for the next hour" if has_more else "one page"
    return Pull(items=items, credits_cost=cost, credits_remaining=left, note=note)


def fetch_tree(ctx: Context, _params: Mapping[str, str]) -> Pull:
    payload, _headers = get_json("https://news.treeofalpha.com/api/news?limit=12")
    rows = payload if isinstance(payload, list) else []
    items: list[Item] = []
    for row in rows[:12]:
        if not isinstance(row, dict):
            continue
        coins = []
        suggestions = row.get("suggestions")
        if isinstance(suggestions, list):
            coins = [str(entry["coin"]) for entry in suggestions[:4] if isinstance(entry, dict) and entry.get("coin")]
        body = plain(row.get("title") or "")
        if coins:
            body = f"{body} Coins: {', '.join(coins)}."
        item = story(
            ctx.source_id,
            str(row.get("_id") or row.get("url") or ""),
            str(row.get("title") or ""),
            f"{row.get('source') or 'Tree'} · {body}",
            str(row.get("url") or ""),
            iso_from_epoch(row.get("time")),
        )
        if item:
            items.append(item)
    return Pull(items=items)


def fetch_trending(ctx: Context, _params: Mapping[str, str]) -> Pull:
    payload, _headers = get_json("https://api.coingecko.com/api/v3/search/trending")
    coins = payload.get("coins") if isinstance(payload, dict) else None
    items: list[Item] = []
    bucket = hour_bucket()
    for entry in coins or []:
        if not isinstance(entry, dict):
            continue
        coin = entry.get("item") if isinstance(entry.get("item"), dict) else entry
        if not isinstance(coin, dict):
            continue
        data = coin.get("data") if isinstance(coin.get("data"), dict) else {}
        symbol = str(coin.get("symbol") or "")
        body = (
            f"{coin.get('name')} ({symbol}) rank {coin.get('market_cap_rank')} "
            f"24h {number(data.get('price_change_percentage_24h'))} mcap {number(data.get('market_cap'))}"
        )
        item = story(
            ctx.source_id,
            f"{coin.get('id')}:{bucket}",
            f"{coin.get('name')} trending",
            body,
            f"https://www.coingecko.com/en/coins/{coin.get('id')}",
            now_iso(),
        )
        if item:
            items.append(item)
    return Pull(items=items)


def fetch_memes(ctx: Context, _params: Mapping[str, str]) -> Pull:
    payload, _headers = get_json(
        "https://api.coingecko.com/api/v3/coins/markets"
        "?vs_currency=usd&category=meme-token&order=volume_desc&per_page=100&page=1"
        "&sparkline=false&price_change_percentage=1h,24h"
    )
    rows = payload if isinstance(payload, list) else []
    items: list[Item] = []
    bucket = hour_bucket()
    for coin in rows[:100]:
        if not isinstance(coin, dict):
            continue
        change_1h = number(coin.get("price_change_percentage_1h_in_currency"))
        change_24h = number(coin.get("price_change_percentage_24h_in_currency") or coin.get("price_change_percentage_24h"))
        body = (
            f"{coin.get('name')} ({coin.get('symbol')}) ${number(coin.get('current_price'))} "
            f"1h {change_1h} 24h {change_24h} vol {number(coin.get('total_volume'))} mcap {number(coin.get('market_cap'))}"
        )
        item = story(
            ctx.source_id,
            f"{coin.get('id')}:{bucket}",
            f"{coin.get('name')} meme tape",
            body,
            f"https://www.coingecko.com/en/coins/{coin.get('id')}",
            now_iso(),
        )
        if item:
            items.append(item)
    return Pull(items=items)


def fetch_fear(ctx: Context, _params: Mapping[str, str]) -> Pull:
    payload, _headers = get_json("https://api.alternative.me/fng/?limit=1")
    rows = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(rows, list) or not rows or not isinstance(rows[0], dict):
        return Pull(items=[])
    row = rows[0]
    value = plain(row.get("value") or "", 8)
    label = plain(row.get("value_classification") or "", 40)
    stamp = iso_from_epoch(row.get("timestamp"))
    item = story(
        ctx.source_id,
        stamp or value,
        f"Fear and Greed {value} {label}",
        f"Crypto Fear and Greed Index is {value} ({label}).",
        "https://alternative.me/crypto/fear-and-greed-index/",
        stamp,
    )
    return Pull(items=[item] if item else [])


def fetch_dexscreener(ctx: Context, params: Mapping[str, str]) -> Pull:
    kind = params.get("list", "boosts")
    url = next((address for key, _label, address in _DEX_LISTS if key == kind), _DEX_LISTS[0][2])
    payload, _headers = get_json(url)
    rows = payload if isinstance(payload, list) else []
    items: list[Item] = []
    for row in rows[:12]:
        if not isinstance(row, dict):
            continue
        chain = plain(row.get("chainId") or "", 40)
        address = plain(row.get("tokenAddress") or "", 80)
        description = plain(row.get("description") or "")
        amount = row.get("amount") or row.get("totalAmount")
        body = f"{kind} on {chain} {address} {description} boost {amount or ''}".strip()
        item = story(ctx.source_id, f"{chain}:{address}:{kind}", f"{chain} {kind}", body, str(row.get("url") or ""), now_iso())
        if item:
            items.append(item)
    return Pull(items=items)


def fetch_geckoterminal(ctx: Context, params: Mapping[str, str]) -> Pull:
    network = params.get("network", "solana")
    label = next((name for key, name in _GECKO_NETWORKS if key == network), network)
    pages = 2 if network == "solana" else 1
    items: list[Item] = []
    bucket = hour_bucket()
    for page in range(1, pages + 1):
        payload, _headers = get_json(f"https://api.geckoterminal.com/api/v2/networks/{network}/trending_pools?page={page}")
        rows = payload.get("data") if isinstance(payload, dict) else None
        for row in rows if isinstance(rows, list) else []:
            if not isinstance(row, dict):
                continue
            attributes = row.get("attributes") if isinstance(row.get("attributes"), dict) else {}
            change = attributes.get("price_change_percentage") if isinstance(attributes.get("price_change_percentage"), dict) else {}
            volume = attributes.get("volume_usd")
            volume_h24 = volume.get("h24") if isinstance(volume, dict) else volume
            name = plain(attributes.get("name") or "", 80)
            body = (
                f"{label} pool {name} price {number(attributes.get('base_token_price_usd'))} "
                f"1h {change.get('h1')}% 24h {change.get('h24')}% "
                f"vol {number(volume_h24)} fdv {number(attributes.get('fdv_usd'))} liq {number(attributes.get('reserve_in_usd'))}"
            )
            pool_id = str(row.get("id") or attributes.get("address") or name)
            item = story(
                ctx.source_id,
                f"{pool_id}:{bucket}",
                f"{name} trending pool",
                body,
                f"https://www.geckoterminal.com/{network}/pools/{attributes.get('address') or ''}",
                str(attributes.get("pool_created_at") or now_iso()),
            )
            if item:
                items.append(item)
    return Pull(items=items)


def parse_binance(payload: dict[str, object], source: str) -> list[Item]:
    data = payload.get("data") if isinstance(payload, dict) else None
    catalogs = data.get("catalogs") if isinstance(data, dict) else None
    ranked: list[tuple[float, Item]] = []
    for catalog in catalogs or []:
        if not isinstance(catalog, dict) or catalog.get("catalogId") not in _BINANCE_CATALOGS:
            continue
        articles = catalog.get("articles")
        if not isinstance(articles, list):
            continue
        for article in articles:
            if not isinstance(article, dict):
                continue
            code = str(article.get("code") or "")
            title = str(article.get("title") or "")
            if not code or not title:
                continue
            stamp = article.get("releaseDate")
            when = float(stamp) if isinstance(stamp, (int, float)) and not isinstance(stamp, bool) else 0.0
            item = story(source, code, title, title, f"https://www.binance.com/en/support/announcement/{code}", iso_from_epoch(stamp))
            if item:
                ranked.append((when, item))
    ranked.sort(key=lambda pair: pair[0], reverse=True)
    return [item for _when, item in ranked[:12]]


def fetch_binance(ctx: Context, _params: Mapping[str, str]) -> Pull:
    payload, _headers = get_json(
        "https://www.binance.com/bapi/apex/v1/public/apex/cms/article/list/query?type=1&pageNo=1&pageSize=20",
        {"User-Agent": "Mozilla/5.0", "Accept": "application/json", "clienttype": "web", "lang": "en"},
    )
    return Pull(items=parse_binance(payload, ctx.source_id) if isinstance(payload, dict) else [])


def fetch_bybit(ctx: Context, _params: Mapping[str, str]) -> Pull:
    payload, _headers = get_json("https://api.bybit.com/v5/announcements/index?locale=en-US&limit=8")
    result = payload.get("result") if isinstance(payload, dict) else None
    rows = result.get("list") if isinstance(result, dict) else None
    items: list[Item] = []
    for row in (rows or [])[:8]:
        if not isinstance(row, dict):
            continue
        item = story(
            ctx.source_id,
            str(row.get("url") or row.get("title") or ""),
            str(row.get("title") or ""),
            f"{row.get('type') or 'Bybit'}: {row.get('description') or row.get('title') or ''}",
            str(row.get("url") or ""),
            iso_from_epoch(row.get("publishTime") or row.get("dateTimestamp") or row.get("createdAt")),
        )
        if item:
            items.append(item)
    return Pull(items=items)


def fetch_okx(ctx: Context, _params: Mapping[str, str]) -> Pull:
    payload, _headers = get_json("https://www.okx.com/api/v5/support/announcements?page=1")
    groups = payload.get("data") if isinstance(payload, dict) else None
    items: list[Item] = []
    for group in groups or []:
        details = group.get("details") if isinstance(group, dict) else None
        for row in details if isinstance(details, list) else []:
            if not isinstance(row, dict):
                continue
            url = str(row.get("url") or "")
            if url.startswith("/"):
                url = f"https://www.okx.com{url}"
            item = story(
                ctx.source_id,
                url or str(row.get("title") or ""),
                str(row.get("title") or ""),
                f"{row.get('annType') or 'OKX'}: {row.get('title') or ''}",
                url,
                iso_from_epoch(row.get("pTime") or row.get("businessPTime") or row.get("ts")),
            )
            if item:
                items.append(item)
            if len(items) >= 8:
                return Pull(items=items)
    return Pull(items=items)


CONNECTORS: tuple[Connector, ...] = (
    Connector(
        kind="fomo",
        label="FOMO alerts",
        summary="Smart-money alerts. One page per hour, stops below the credit floor.",
        fetch=fetch_fomo,
        cost=f"{FOMO_PAGE_CREDITS} credits",
        secret=FOMO_SECRET,
        group="Crypto",
        balance=fomo_balance,
    ),
    Connector("tree", "Tree of Alpha", "Fast crypto headlines with coin suggestions.", fetch_tree, group="Crypto"),
    Connector("coingecko-trending", "CoinGecko trending", "The most searched coins right now.", fetch_trending, group="Crypto"),
    Connector("coingecko-memes", "CoinGecko meme tape", "Top 100 meme coins by volume, as an hourly snapshot.", fetch_memes, group="Crypto"),
    Connector("fear-greed", "Fear and Greed", "The daily sentiment index.", fetch_fear, group="Crypto"),
    Connector(
        kind="dexscreener",
        label="Dexscreener",
        summary="Freshly promoted tokens on decentralised exchanges.",
        fetch=fetch_dexscreener,
        fields=(Field("list", "List", options=tuple((key, label) for key, label, _url in _DEX_LISTS)),),
        defaults={"list": "boosts"},
        group="Crypto",
    ),
    Connector(
        kind="geckoterminal",
        label="GeckoTerminal",
        summary="Trending pools of one network with price, volume and liquidity.",
        fetch=fetch_geckoterminal,
        fields=(Field("network", "Network", options=_GECKO_NETWORKS),),
        defaults={"network": "solana"},
        group="Crypto",
    ),
    Connector("binance", "Binance announcements", "Listings and delistings.", fetch_binance, group="Crypto"),
    Connector("bybit", "Bybit announcements", "New spot and derivatives listings.", fetch_bybit, group="Crypto"),
    Connector("okx", "OKX announcements", "Listings and product announcements.", fetch_okx, group="Crypto"),
)
