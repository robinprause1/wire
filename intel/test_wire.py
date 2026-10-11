import os
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

os.environ["WIRE_EMBEDDER"] = "lexicon"

from intel import accounts, desks, settings
from intel.clock import next_run_at, retarget
from intel.coins import pick_coins
from intel.connectors import Item, REGISTRY
from intel.connectors.crypto import fomo_blocked, parse_binance, parse_fomo
from intel.connectors.feeds import page_item, page_links, parse_rss, parse_telegram
from intel.cycle import Runner
from intel.desk import Schedule, parse
from intel.index import WireIndex, document_id
from intel.judge import decision_of
from intel.store import LEGACY_DESK, Store
from intel.subjects import CorpusPicker, WatchlistPicker, picker_for
from intel.templates import template


def fresh_store() -> Store:
    return Store(Path(tempfile.mkdtemp()) / "wire.sqlite")


class IndexTests(unittest.TestCase):
    def test_hybrid_search_keeps_one_copy_and_stays_inside_the_desk(self) -> None:
        index = WireIndex.open(None)
        store = fresh_store()
        meme = Item("tree-news", "1", "Solana meme coin rips", "A new meme coin on solana.", "https://example.com/1", "")
        macro = Item("coindesk", "2", "Fed minutes", "Interest rates stayed unchanged.", "https://example.com/2", "")
        known: set[str] = set()
        no_tags = picker_for(template("blank").build("other", "Other"), index).tags
        inserted, ids = index.ingest("crypto", "rss", [meme, macro], known, no_tags)
        self.assertEqual(inserted, 2)
        known.update(ids)
        store.remember(ids)
        again, more = index.ingest("crypto", "rss", [meme], store.known_ids(), no_tags)
        self.assertEqual(again, 0)
        self.assertEqual(more, [])
        index.ingest("other", "rss", [meme], set(), no_tags)
        self.assertEqual(index.count("crypto"), 2)
        self.assertEqual(index.count("other"), 1)
        hits = index.search("should we buy a solana meme coin", desk="crypto", limit=2)
        self.assertEqual(hits[0].source, "tree-news")
        self.assertEqual(document_id("crypto", "tree-news", "1"), hits[0].id)
        other_hits = index.search("fed minutes interest rates", desk="other", limit=2)
        self.assertEqual({hit.id for hit in other_hits}, {document_id("other", "tree-news", "1")})
        self.assertEqual(len(index.documents("crypto", within_hours=1)), 2)
        self.assertEqual(index.documents("missing", within_hours=1), [])


class ConnectorTests(unittest.TestCase):
    def test_fomo_alerts_become_separate_documents(self) -> None:
        items, cursor, has_more = parse_fomo(
            {
                "nextCursor": "100.alrt_1",
                "hasMore": True,
                "alerts": [
                    {
                        "eventId": "evt-1",
                        "type": "buy",
                        "trader": "ada",
                        "token": "PEPE",
                        "chain": "solana",
                        "usdValue": 4200,
                        "text": "ada bought $PEPE",
                        "ts": 1_700_000_000_000,
                    }
                ],
            },
            "fomo-alerts",
        )
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].external_id, "evt-1")
        self.assertEqual(items[0].source, "fomo-alerts")
        self.assertIn("PEPE", items[0].body)
        self.assertEqual(cursor, "100.alrt_1")
        self.assertTrue(has_more)

    def test_rss_strips_markup(self) -> None:
        xml = """
        <rss><channel><item>
          <title>Desk note</title>
          <link>https://example.com/desk</link>
          <description><![CDATA[<p>Hello <b>there</b></p>]]></description>
          <pubDate>Wed, 07 Oct 2026 09:00:00 GMT</pubDate>
        </item></channel></rss>
        """
        items = parse_rss(xml, "coindesk")
        self.assertEqual(items[0].title, "Desk note")
        self.assertEqual(items[0].body, "Hello there")
        self.assertTrue(items[0].published_at.startswith("2026-10-07"))

    def test_page_becomes_one_document_whose_id_follows_the_text(self) -> None:
        html = "<html><head><title>Status</title><script>x()</script></head><body><h1>All good</h1><a href='/next'>n</a></body></html>"
        first = page_item("status", "https://example.com/status", html)
        same = page_item("status", "https://example.com/status", html)
        changed = page_item("status", "https://example.com/status", html.replace("All good", "Outage"))
        self.assertEqual(first.title, "Status")
        self.assertIn("All good", first.body)
        self.assertEqual(first.external_id, same.external_id)
        self.assertNotEqual(first.external_id, changed.external_id)

    def test_page_links_stay_on_the_host(self) -> None:
        html = '<a href="/a">a</a><a href="https://example.com/b.pdf">b</a><a href="https://other.org/c">c</a><a href="/a">dup</a>'
        self.assertEqual(page_links("https://example.com/", html), ["https://example.com/a"])

    def test_binance_keeps_listings(self) -> None:
        items = parse_binance(
            {
                "data": {
                    "catalogs": [
                        {"catalogId": 48, "articles": [{"code": "abc", "title": "Binance lists PEPE", "releaseDate": 2_000}]},
                        {"catalogId": 128, "articles": [{"code": "old", "title": "Airdrop", "releaseDate": 9_000}]},
                    ]
                }
            },
            "binance",
        )
        self.assertEqual([item.external_id for item in items], ["abc"])
        self.assertIn("announcement/abc", items[0].url)

    def test_telegram_reads_the_message(self) -> None:
        html = (
            'data-post="whale_alert_io/9">'
            '<div class="js-message_text">1,000 #BTC moved</div>'
            '<time datetime="2026-10-07T10:00:00+00:00">'
        )
        items = parse_telegram(html, "telegram-whales")
        self.assertEqual(items[0].external_id, "whale_alert_io/9")
        self.assertIn("#BTC", items[0].body)
        self.assertEqual(items[0].url, "https://t.me/whale_alert_io/9")

    def test_credit_floor(self) -> None:
        self.assertTrue(fomo_blocked(19_999))
        self.assertFalse(fomo_blocked(20_000))
        self.assertFalse(fomo_blocked(None))

    def test_registry_lists_generic_and_crypto_kinds(self) -> None:
        self.assertIn("rss", REGISTRY)
        self.assertIn("page", REGISTRY)
        self.assertEqual(REGISTRY["fomo"].secret, "FOMO_API_KEY")


class SubjectTests(unittest.TestCase):
    def test_coin_picker_names_the_coin_from_alerts_and_keeps_the_pair_quote_out(self) -> None:
        picked = pick_coins(
            [
                {"source": "fomo-alerts", "kind": "fomo", "title": "ada bought $PEPE", "text": "buy · PEPE · ada · solana · $4000", "url": "https://alerts.example/pepe", "tags": ["PEPE"]},
                {"source": "coingecko-memes", "kind": "coingecko-memes", "title": "Pepe meme tape", "text": "Pepe (PEPE) price 0.00001", "url": "https://www.coingecko.com/en/coins/pepe", "tags": ["PEPE"]},
                {"source": "geckoterminal-solana", "kind": "geckoterminal", "title": "SI276 / SOL trending pool", "text": "Solana pool SI276 / SOL price", "tags": []},
                {"source": "coingecko-trending", "kind": "coingecko-trending", "title": "Bitcoin leads", "text": "Bitcoin (BTC) is up", "tags": ["BTC"]},
                {"source": "beincrypto", "kind": "rss", "title": "Bitcoin again", "text": "Bitcoin (BTC) holds the range", "tags": ["BTC"]},
            ]
        )
        self.assertEqual(picked[0].symbol, "PEPE")
        self.assertEqual(picked[0].label, "Pepe (PEPE)")
        self.assertEqual(picked[0].url, "https://www.coingecko.com/en/coins/pepe")
        self.assertIn("BTC", [coin.symbol for coin in picked])
        self.assertNotIn("SOL", [coin.symbol for coin in picked])

    def test_watchlist_picker_matches_whole_words_and_collects_notes(self) -> None:
        picker = WatchlistPicker(("Qdrant", "Haystack"))
        documents = [
            {"source": "blog", "title": "Qdrant 2.0 released", "text": "Qdrant ships quantization.", "url": "https://example.com/q"},
            {"source": "blog", "title": "Haystacks in the field", "text": "Farming news.", "url": ""},
        ]
        self.assertEqual(picker.tags("rss", "Qdrant 2.0 released", "text"), ["Qdrant"])
        subjects = picker.pick(documents)
        self.assertEqual([subject.label for subject in subjects], ["Qdrant"])
        self.assertEqual(subjects[0].url, "https://example.com/q")
        self.assertIn("Qdrant 2.0 released", picker.notes(documents, subjects[0])[0])

    def test_corpus_picker_asks_once_about_the_window(self) -> None:
        index = WireIndex.open(None)
        desk = template("blank").build("status", "Status Desk")
        picker = CorpusPicker(index, desk)
        self.assertEqual(picker.pick([]), [])
        subjects = picker.pick([{"source": "a", "title": "t", "text": "x"}, {"source": "b", "title": "t", "text": "y"}])
        self.assertEqual(subjects[0].key, "window")
        self.assertEqual(subjects[0].label, "Status Desk")
        self.assertEqual(subjects[0].mentions, 2)


class VerdictTests(unittest.TestCase):
    def test_yes_probability_is_the_answer_shown(self) -> None:
        self.assertEqual(decision_of(0.68, 0.40), ("yes", 0.68))
        self.assertEqual(decision_of(0.40, 0.40)[0], "yes")
        verdict, probability = decision_of(0.21, 0.40)
        self.assertEqual(verdict, "no")
        self.assertAlmostEqual(probability, 0.79)
        self.assertEqual(decision_of(0.30, 0.25)[0], "yes")


class DeskTests(unittest.TestCase):
    def test_parse_validates_sources_and_brief(self) -> None:
        desk = parse(
            {
                "name": "  Status  Desk ",
                "sources": [
                    {"kind": "rss", "params": {"url": "https://example.com/feed"}},
                    {"kind": "page", "name": "Status page", "params": {"url": "https://example.com/status", "depth": "links"}},
                ],
                "steps": {"judge": False},
                "schedule": {"mode": "interval", "everyMinutes": 5},
                "brief": {"picker": "watchlist", "watchlist": "Qdrant, Haystack\nqdrant", "question": "Is {subject} down?", "yesAt": 0.7, "windowHours": 6},
            },
            desk_id="status",
        )
        self.assertEqual(desk.name, "Status Desk")
        self.assertEqual([item.id for item in desk.sources], ["rss-feed-example-com-feed", "status-page"])
        self.assertEqual(desk.sources[1].params["depth"], "links")
        self.assertFalse(desk.enabled("judge"))
        self.assertEqual(desk.schedule.every_minutes, 15)
        self.assertEqual(desk.brief.watchlist, ("Qdrant", "Haystack"))
        self.assertEqual(desk.brief.instructions("Qdrant"), "Is Qdrant down?")
        self.assertEqual(desk.brief.yes_at, 0.7)
        self.assertEqual(desk.brief.window_hours, 6)
        with self.assertRaises(ValueError):
            parse({"name": "x", "sources": [{"kind": "rss", "params": {"url": "ftp://nope"}}]}, desk_id="x")
        with self.assertRaises(ValueError):
            parse({"name": "x", "sources": [{"kind": "nope"}]}, desk_id="x")
        with self.assertRaises(ValueError):
            parse({"name": "x", "brief": {"picker": "watchlist", "watchlist": ""}}, desk_id="x")
        with self.assertRaises(ValueError):
            parse({"sources": []}, desk_id="x")

    def test_hourly_target_is_kept_until_it_fires(self) -> None:
        before = datetime.fromisoformat("2026-10-09T19:59:59+02:00")
        after = datetime.fromisoformat("2026-10-09T20:00:00.400000+02:00")
        schedule = Schedule()
        self.assertEqual(schedule.next_run_iso(None, before), "2026-10-09T20:00:00+02:00")
        self.assertEqual(schedule.next_run_iso(None, after), "2026-10-09T21:00:00+02:00")
        store = fresh_store()
        desk = template("blank").build("status", "Status")
        target = retarget(store, desk)
        self.assertEqual(next_run_at(store, desk), target)
        paused = parse({**desk.to_json(), "schedule": {"enabled": False}}, desk_id="status")
        self.assertEqual(next_run_at(store, paused), "")

    def test_desks_are_created_from_templates_replaced_and_deleted(self) -> None:
        store = fresh_store()
        created = desks.bootstrap(store)
        self.assertEqual([desk.id for desk in created], [LEGACY_DESK])
        self.assertEqual(created[0].brief.picker, "coins")
        self.assertEqual(len(created[0].sources), 26)
        own = desks.create(store, "blank", "Status Desk")
        self.assertEqual(own.id, "status-desk")
        self.assertEqual(desks.create(store, "blank", "Status Desk").id, "status-desk-2")
        body = {**own.to_json(), "sources": [{"kind": "rss", "params": {"url": "https://example.com/feed"}}]}
        saved = desks.replace(store, own.id, body)
        self.assertEqual(len(saved.sources), 1)
        self.assertEqual(desks.get(store, own.id).sources[0].kind, "rss")
        store.record_run(own.id, "rss-feed-example-com-feed", "2026-10-09T10:00:00+00:00", "ok", 3, 3, "")
        desks.delete(store, own.id)
        with self.assertRaises(LookupError):
            desks.get(store, own.id)
        self.assertEqual(store.last_runs(own.id), {})
        with self.assertRaises(ValueError):
            desks.create(store, "nope", "x")

    def test_legacy_settings_carry_over_to_the_crypto_desk(self) -> None:
        store = fresh_store()
        store.set_state(
            "pipeline",
            '{"disabledSources": ["fear-greed"], "steps": {"judge": false}, "scheduleEnabled": false, "scheduleMode": "interval", "everyMinutes": 30, "yesAt": 0.55}',
        )
        desk = desks.bootstrap(store)[0]
        self.assertFalse(next(item for item in desk.sources if item.id == "fear-greed").enabled)
        self.assertFalse(desk.enabled("judge"))
        self.assertFalse(desk.schedule.enabled)
        self.assertEqual(desk.schedule.every_minutes, 30)
        self.assertEqual(desk.brief.yes_at, 0.55)
        self.assertEqual(store.get_state("pipeline"), "")

    def test_runner_refuses_a_second_cycle(self) -> None:
        store = fresh_store()
        runner = Runner(store, WireIndex.open(None))
        runner.running = "other"
        self.assertFalse(runner.start(template("blank").build("status", "Status")))
        self.assertTrue(runner.busy())
        self.assertTrue(runner.busy("other"))
        self.assertFalse(runner.busy("status"))


class StoreTests(unittest.TestCase):
    def test_answers_and_runs_are_scoped_by_desk(self) -> None:
        store = fresh_store()
        for desk_id in ("a", "b"):
            store.save_answer(
                desk=desk_id,
                question="q",
                status="ok",
                verdict="yes",
                model="m",
                max_score=0.7,
                usage={},
                passages=[],
                signature="s",
                note="",
                batch=f"batch-{desk_id}",
            )
        self.assertEqual(len(store.latest_batch("a")), 1)
        self.assertEqual(store.latest_batch("a")[0]["batch"], "batch-a")
        self.assertEqual(store.latest_batch("c"), [])

    def test_secret_is_masked_and_removable(self) -> None:
        store = fresh_store()
        previous = os.environ.get("FOMO_API_KEY")
        try:
            settings.save_secret(store, "FOMO_API_KEY", "fapi_testvalue1234")
            saved = next(item for item in settings.view(store)["credentials"] if item["key"] == "FOMO_API_KEY")
            self.assertEqual(saved["hint"], "••••1234")
            self.assertTrue(saved["set"])
            self.assertIn("source “FOMO alerts”", saved["usedBy"])
            self.assertNotIn("fapi_testvalue1234", str(settings.view(store)))
            settings.save_secret(store, "FOMO_API_KEY", "")
            self.assertNotIn("FOMO_API_KEY", store.secret_map())
            with self.assertRaises(ValueError):
                settings.save_secret(store, "OTHER", "x")
        finally:
            if previous is None:
                os.environ.pop("FOMO_API_KEY", None)
            else:
                os.environ["FOMO_API_KEY"] = previous


class AccountTests(unittest.TestCase):
    def test_registration_waits_until_an_admin_activates_it(self) -> None:
        store = fresh_store()
        self.assertTrue(accounts.needs_setup(store))
        admin, token = self._admin(store)
        self.assertEqual(admin["role"], "admin")
        self.assertEqual(accounts.user_from_token(store, token)["email"], admin["email"])
        self.assertFalse(accounts.needs_setup(store))
        with self.assertRaises(ValueError):
            accounts.setup_admin(store, "Other", "other@example.com", "long-enough")

        member = accounts.register(store, "Ada", "ada@example.com", "long-enough")
        self.assertEqual(member["status"], "pending")
        self.assertEqual(accounts.clearance(member, "GET", "/"), "pending")
        self.assertEqual(accounts.clearance(None, "GET", "/manage"), "login")
        opened, member_token = accounts.open_session(store, "ada@example.com", "long-enough")
        self.assertEqual(opened["status"], "pending")

        active = accounts.set_status(store, str(admin["id"]), member["id"], "active")
        self.assertEqual(accounts.clearance(active, "GET", "/"), "ok")
        self.assertEqual(accounts.clearance(active, "GET", "/manage"), "forbidden")
        self.assertEqual(accounts.clearance(active, "POST", "/api/desks/crypto-desk/run"), "forbidden")
        self.assertEqual(accounts.clearance(active, "POST", "/api/desks/crypto-desk/ask"), "ok")
        self.assertEqual(accounts.clearance(admin, "POST", "/api/desks/crypto-desk/run"), "ok")
        self.assertEqual(accounts.clearance(None, "GET", "/static/wire.css"), "ok")
        self.assertEqual(accounts.safe_next("/manage?desk=crypto-desk"), "/manage%3Fdesk%3Dcrypto-desk")
        self.assertEqual(accounts.safe_next("https://evil.example"), "/")

        accounts.set_status(store, str(admin["id"]), member["id"], "blocked")
        self.assertIsNone(accounts.user_from_token(store, member_token))
        with self.assertRaises(ValueError):
            accounts.open_session(store, "ada@example.com", "long-enough")
        with self.assertRaises(ValueError):
            accounts.set_status(store, str(admin["id"]), admin["id"], "blocked")
        accounts.remove_user(store, str(admin["id"]), member["id"])
        self.assertEqual(len(accounts.list_users(store)), 1)
        accounts.close_session(store, token)
        self.assertIsNone(accounts.user_from_token(store, token))

    def _admin(self, store):
        accounts.setup_admin(store, "Robin", "robin@example.com", "long-enough")
        return accounts.open_session(store, "robin@example.com", "long-enough")


if __name__ == "__main__":
    unittest.main()
