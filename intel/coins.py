"""Pick a few concrete coins out of the latest documents. No model call. Part of the crypto pack."""

from __future__ import annotations

import re
from dataclasses import dataclass

COIN_LIMIT = 80

_STOP = {"AI", "ALL", "AND", "API", "CEO", "ETF", "FOR", "NEW", "NFT", "NOT", "SEC", "THE", "USD", "YOU"}
_MAJORS = {"BTC", "ETH", "SOL"}
# Keyed by connector kind, so a renamed source keeps its weight.
_LINK_RANK = {"dexscreener": 6, "geckoterminal": 6, "coingecko-memes": 4, "coingecko-trending": 4, "fomo": 3}
_WEIGHT = {
    "fomo": 3.0,
    "coingecko-memes": 2.0,
    "coingecko-trending": 2.0,
    "geckoterminal": 2.0,
    "binance": 2.0,
    "telegram": 2.0,
    "dexscreener": 2.0,
    "tree": 2.0,
}
_CASHTAG = re.compile(r"\$([A-Za-z][A-Za-z0-9]{1,9})\b")
_HASH = re.compile(r"#([A-Z][A-Z0-9]{1,9})\b")
_PAIR = re.compile(r"\b([A-Za-z][A-Za-z0-9]{1,11})\s*/\s*[A-Za-z]{2,6}\b")
_PAREN = re.compile(r"\b([A-Za-z][A-Za-z0-9]{1,40})\s+\(([A-Za-z][A-Za-z0-9]{1,9})\)")
_COINS = re.compile(r"Coins:\s*([A-Za-z0-9, ]+)")


@dataclass(frozen=True)
class Coin:
    symbol: str
    name: str
    weight: float
    mentions: int
    sources: tuple[str, ...]
    url: str = ""

    @property
    def label(self) -> str:
        if self.name and self.name != self.symbol:
            return f"{self.name} ({self.symbol})"
        return self.symbol


def pick_coins(documents: list[dict[str, object]], limit: int = COIN_LIMIT) -> list[Coin]:
    totals: dict[str, float] = {}
    names: dict[str, str] = {}
    counts: dict[str, int] = {}
    origins: dict[str, list[str]] = {}
    links: dict[str, tuple[int, str]] = {}
    for document in documents:
        source = str(document.get("source") or "")
        kind = str(document.get("kind") or "")
        title = str(document.get("title") or "")
        text = str(document.get("text") or "")
        listed = document.get("tags") if isinstance(document.get("tags"), list) else []
        for symbol, name, primary in symbols_in(kind, title, text, [str(item) for item in listed]):
            weight = _WEIGHT.get(kind, 1.0)
            if symbol in _MAJORS and not primary:
                weight = 0.5
            totals[symbol] = totals.get(symbol, 0.0) + weight
            counts[symbol] = counts.get(symbol, 0) + 1
            if name and symbol not in names:
                names[symbol] = name
            bucket = origins.setdefault(symbol, [])
            if source and source not in bucket:
                bucket.append(source)
            url = str(document.get("url") or "")
            rank = _LINK_RANK.get(kind, 1)
            if url.startswith("http") and rank >= links.get(symbol, (0, ""))[0]:
                links[symbol] = (rank, url)
    ranked = sorted(totals, key=lambda symbol: (symbol in _MAJORS, -totals[symbol], -counts[symbol]))
    chosen: list[Coin] = []
    for symbol in ranked:
        if len(symbol) < 3:
            continue
        chosen.append(
            Coin(
                symbol=symbol,
                name=names.get(symbol, ""),
                weight=totals[symbol],
                mentions=counts[symbol],
                sources=tuple(origins.get(symbol, [])),
                url=links.get(symbol, (0, ""))[1],
            )
        )
        if len(chosen) >= limit:
            break
    return chosen


def briefs(documents: list[dict[str, object]], symbol: str, label: str, limit: int = 3) -> list[str]:
    """Lines that name the coin, by symbol or by the name inside its label."""
    notes: list[str] = []
    symbol = symbol.lower()
    name = label.split(" (")[0].lower() if " (" in label else ""
    for document in documents:
        title = str(document.get("title") or "")
        text = str(document.get("text") or "")
        blob = f"{title}\n{text}".lower()
        named = len(name) > 3 and name in blob
        if re.search(rf"\b{re.escape(symbol)}\b", blob) or named:
            notes.append(" ".join(f"{title}. {text}".split())[:180])
        if len(notes) >= limit:
            break
    return notes


def symbols_in(kind: str, title: str, text: str, listed: list[str] | None = None) -> list[tuple[str, str, bool]]:
    found: list[tuple[str, str, bool]] = []
    seen: set[str] = set()
    blob = f"{title}\n{text}"

    def add(raw: str, name: str = "", *, primary: bool) -> None:
        symbol = _clean(raw)
        if symbol is None:
            return
        if symbol in seen:
            if not name:
                return
            for index, (known, known_name, known_primary) in enumerate(found):
                if known == symbol and not known_name:
                    found[index] = (known, name, known_primary or primary)
                    return
            return
        seen.add(symbol)
        found.append((symbol, name, primary))

    for raw in listed or []:
        add(raw, primary=True)
    if kind == "fomo":
        parts = [part.strip() for part in blob.split("·")]
        if len(parts) >= 2:
            add(parts[1], primary=True)
    for name, raw in _PAREN.findall(blob):
        add(raw, name, primary=True)
    for raw in _CASHTAG.findall(blob):
        add(raw, primary=True)
    for raw in _HASH.findall(blob):
        add(raw, primary=True)
    for raw in _PAIR.findall(blob):
        add(raw, primary=True)
    for line in _COINS.findall(blob):
        for raw in line.split(","):
            add(raw, primary=True)
    return found


def _clean(raw: str) -> str | None:
    symbol = raw.strip().upper().lstrip("$")
    if not re.fullmatch(r"[A-Z][A-Z0-9]{1,9}", symbol):
        return None
    letters = sum(char.isalpha() for char in symbol)
    if letters < 2 or symbol in _STOP:
        return None
    return symbol
