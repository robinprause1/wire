"""Who or what Jev is asked about.

A picker turns the documents of one window into subjects and, per subject, the
notes Jev may read. Three pickers ship: coins (ticker detection, the crypto
pack), watchlist (your own terms), corpus (one question about the whole window,
with hybrid retrieval choosing the passages).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Protocol

from intel import coins
from intel.desk import Desk, slug
from intel.index import WireIndex

Document = dict[str, object]
CORPUS_KEY = "window"


@dataclass(frozen=True)
class Subject:
    key: str
    label: str
    mentions: int
    sources: tuple[str, ...]
    url: str = ""


class Picker(Protocol):
    notes_limit: int

    def tags(self, kind: str, title: str, body: str) -> list[str]:
        """Terms worth storing with a document at ingest. Empty when the picker needs none."""

    def pick(self, documents: list[Document]) -> list[Subject]: ...

    def notes(self, documents: list[Document], subject: Subject) -> list[str]: ...


class CoinPicker:
    notes_limit = 1

    def tags(self, kind: str, title: str, body: str) -> list[str]:
        return [symbol for symbol, _name, _primary in coins.symbols_in(kind, title, body)]

    def pick(self, documents: list[Document]) -> list[Subject]:
        return [
            Subject(key=coin.symbol, label=coin.label, mentions=coin.mentions, sources=coin.sources, url=coin.url)
            for coin in coins.pick_coins(documents)
        ]

    def notes(self, documents: list[Document], subject: Subject) -> list[str]:
        return coins.briefs(documents, subject.key, subject.label, limit=self.notes_limit)


class WatchlistPicker:
    notes_limit = 3

    def __init__(self, words: tuple[str, ...]) -> None:
        self.words = words
        self._patterns = {word: re.compile(rf"(?<!\w){re.escape(word)}(?!\w)", re.I) for word in words}

    def tags(self, kind: str, title: str, body: str) -> list[str]:
        blob = f"{title}\n{body}"
        return [word for word, pattern in self._patterns.items() if pattern.search(blob)]

    def pick(self, documents: list[Document]) -> list[Subject]:
        found: list[Subject] = []
        for word in self.words:
            hits = [document for document in documents if self._matches(document, word)]
            if not hits:
                continue
            sources = tuple(dict.fromkeys(str(document.get("source") or "") for document in hits))
            url = next((str(document.get("url")) for document in hits if str(document.get("url") or "").startswith("http")), "")
            found.append(Subject(key=slug(word, "term"), label=word, mentions=len(hits), sources=sources, url=url))
        found.sort(key=lambda subject: -subject.mentions)
        return found

    def notes(self, documents: list[Document], subject: Subject) -> list[str]:
        lines: list[str] = []
        for document in documents:
            if not self._matches(document, subject.label):
                continue
            lines.append(_line(document))
            if len(lines) >= self.notes_limit:
                break
        return lines

    def _matches(self, document: Document, word: str) -> bool:
        return bool(self._patterns[word].search(f"{document.get('title') or ''}\n{document.get('text') or ''}"))


class CorpusPicker:
    """One subject: the window itself. Hybrid retrieval picks the passages for the question."""

    notes_limit = 6

    def __init__(self, index: WireIndex, desk: Desk) -> None:
        self.index = index
        self.desk = desk

    def tags(self, kind: str, title: str, body: str) -> list[str]:
        return []

    def pick(self, documents: list[Document]) -> list[Subject]:
        if not documents:
            return []
        sources = tuple(dict.fromkeys(str(document.get("source") or "") for document in documents))
        return [Subject(key=CORPUS_KEY, label=self.desk.name, mentions=len(documents), sources=sources)]

    def notes(self, documents: list[Document], subject: Subject) -> list[str]:
        hits = self.index.search(
            self.desk.brief.question,
            limit=self.notes_limit,
            within_hours=self.desk.brief.window_hours,
            desk=self.desk.id,
        )
        return [" ".join(f"{hit.title}. {hit.text}".split())[:240] for hit in hits]


def picker_for(desk: Desk, index: WireIndex) -> Picker:
    picker = desk.brief.picker
    if picker == "coins":
        return CoinPicker()
    if picker == "watchlist":
        return WatchlistPicker(desk.brief.watchlist)
    if picker == "corpus":
        return CorpusPicker(index, desk)
    raise ValueError(f"Unknown picker: {picker}")


def describe() -> list[dict[str, str]]:
    return [
        {
            "id": "corpus",
            "label": "Whole window",
            "summary": "One question across all new stories. Retrieval picks the matching passages.",
        },
        {
            "id": "watchlist",
            "label": "Watchlist",
            "summary": "Your own terms, names or products. One question per term that appears in the window.",
        },
        {
            "id": "coins",
            "label": "Coins",
            "summary": "Detects tickers in the stories and asks per coin. For the crypto case.",
        },
    ]


def _line(document: Document) -> str:
    return " ".join(f"{document.get('title') or ''}. {document.get('text') or ''}".split())[:180]
