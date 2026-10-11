"""Starting points for a new desk. A template is data; the user edits it afterwards like any desk."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from intel.desk import Brief, Desk, Schedule, SourceSpec

_CRYPTO_FEEDS: tuple[tuple[str, str, str], ...] = (
    ("cointelegraph", "Cointelegraph", "https://cointelegraph.com/rss"),
    ("decrypt", "Decrypt", "https://decrypt.co/feed"),
    ("coindesk", "CoinDesk", "https://www.coindesk.com/arc/outboundfeeds/rss/"),
    ("the-block", "The Block", "https://www.theblock.co/rss.xml"),
    ("cryptoslate", "CryptoSlate", "https://cryptoslate.com/feed/"),
    ("bitcoin-magazine", "Bitcoin Magazine", "https://bitcoinmagazine.com/feed"),
    ("u-today", "U.Today", "https://u.today/rss"),
    ("newsbtc", "NewsBTC", "https://www.newsbtc.com/feed/"),
    ("beincrypto", "BeInCrypto", "https://beincrypto.com/feed/"),
    ("dl-news", "DL News", "https://www.dlnews.com/arc/outboundfeeds/rss/"),
)


@dataclass(frozen=True)
class Template:
    id: str
    name: str
    summary: str
    build: Callable[[str, str], Desk]


def crypto_desk(desk_id: str, name: str) -> Desk:
    sources = [
        SourceSpec("fomo-alerts", "fomo", "FOMO alerts"),
        SourceSpec("tree-news", "tree", "Tree of Alpha"),
        SourceSpec("coingecko-trending", "coingecko-trending", "CoinGecko trending"),
        SourceSpec("coingecko-memes", "coingecko-memes", "CoinGecko meme tape"),
        SourceSpec("fear-greed", "fear-greed", "Fear and Greed"),
        SourceSpec("dexscreener-boosts", "dexscreener", "Dexscreener boosts", {"list": "boosts"}),
        SourceSpec("dexscreener-profiles", "dexscreener", "Dexscreener profiles", {"list": "profiles"}),
        SourceSpec("dexscreener-takeovers", "dexscreener", "Dexscreener takeovers", {"list": "takeovers"}),
        SourceSpec("geckoterminal-solana", "geckoterminal", "GeckoTerminal Solana", {"network": "solana"}),
        SourceSpec("geckoterminal-eth", "geckoterminal", "GeckoTerminal Ethereum", {"network": "eth"}),
        SourceSpec("geckoterminal-base", "geckoterminal", "GeckoTerminal Base", {"network": "base"}),
        SourceSpec("geckoterminal-bsc", "geckoterminal", "GeckoTerminal BNB Chain", {"network": "bsc"}),
        SourceSpec("binance-announcements", "binance", "Binance announcements"),
        SourceSpec("telegram-whales", "telegram", "Whale Alert", {"channel": "whale_alert_io"}),
        SourceSpec("bybit-announcements", "bybit", "Bybit announcements"),
        SourceSpec("okx-announcements", "okx", "OKX announcements"),
    ]
    sources.extend(SourceSpec(source_id, "rss", feed_name, {"url": url}) for source_id, feed_name, url in _CRYPTO_FEEDS)
    return Desk(
        id=desk_id,
        name=name,
        summary="Collect market and news feeds every hour and ask Jev whether a coin is a speculative buy right now.",
        sources=tuple(sources),
        schedule=Schedule(enabled=False),
        brief=Brief(
            picker="coins",
            question=(
                "Is {subject} a reasonable speculative buy right now? "
                "Crypto risk is expected. Use only the notes for that subject inside `reports`."
            ),
            when_true="The notes give a current reason for a speculative buy: fresh buying, a listing, unusual volume, or a sharp move in this coin.",
            when_false="The notes warn against buying, or they say nothing concrete about this coin right now.",
            window_hours=36,
            yes_at=0.40,
        ),
    )


def blank_desk(desk_id: str, name: str) -> Desk:
    return Desk(
        id=desk_id,
        name=name,
        summary="Your own URLs, your own question. Read regularly, index, let Jev decide.",
        schedule=Schedule(enabled=False),
        brief=Brief(picker="corpus"),
    )


TEMPLATES: tuple[Template, ...] = (
    Template(
        "blank",
        "Blank desk",
        "Starts without sources. You add URLs and write the question.",
        blank_desk,
    ),
    Template(
        "crypto",
        "Crypto Desk",
        "26 crypto sources, coin detection, buy-yes-or-no per coin.",
        crypto_desk,
    ),
)


def template(template_id: str) -> Template:
    found = next((item for item in TEMPLATES if item.id == template_id), None)
    if found is None:
        raise ValueError("Unknown template.")
    return found
