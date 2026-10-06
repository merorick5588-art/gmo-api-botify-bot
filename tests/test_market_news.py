from __future__ import annotations

import json
import sys
import tempfile
import types
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import Mock, patch

try:
    from openai import OpenAI
except ImportError:
    stub = types.ModuleType("openai")
    stub.OpenAI = object
    sys.modules["openai"] = stub

import requests
import market_news as news
from analyze_ohlcv import _batch_payload, _entry_payload, _management_payload, _validate_entry
from economic_calendar import EconomicEvent
from notify_discord_all import _entry_embed, _released_market_events


class MarketNewsTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 10, 6, 12, tzinfo=timezone.utc)
        self.xml = b'''\xef\xbb\xbf<?xml version="1.0" encoding="utf-8"?>
        <rss><channel><item><title>EUR/USD rebounds after Fed comments</title>
        <link>https://example.com/fx</link><pubDate>Tue, 06 Oct 2026 11:00:00 GMT</pubDate>
        <description><![CDATA[<p>Euro &amp; dollar outlook.</p>]]></description>
        </item></channel></rss>'''
        self.rows = news.parse_feed(self.xml, "test", None)
        self.context = {"retrieved_at": self.now.isoformat(), "headlines": self.rows,
                        "sources": [{"name": "test", "status": "live", "fetched_at": self.now.isoformat()}]}

    def test_parse_bom_html_currency_and_timezone(self):
        row = self.rows[0]
        self.assertEqual(row["currencies"], ["USD", "EUR"])
        self.assertEqual(row["summary"], "Euro & dollar outlook.")
        self.assertEqual(row["published_at"], "2026-10-06T11:00:00+00:00")
        with self.assertRaises(ValueError):
            news.parse_feed(self.xml.replace(b"11:00:00 GMT", b"11:00:00"), "test", None)

    def test_old_and_future_articles_are_removed(self):
        old = {**self.rows[0], "published_at": (self.now - timedelta(days=2)).isoformat()}
        future = {**self.rows[0], "published_at": (self.now + timedelta(seconds=1)).isoformat()}
        self.assertEqual(news._recent([old, future, self.rows[0]], self.now), self.rows)

    def test_symbol_filter_dedup_and_source_limit(self):
        duplicate = {**self.rows[0]}
        unrelated = {**duplicate, "id": "unrelated", "currencies": ["GBP"]}
        context = news.context_for_symbol({**self.context, "headlines": [*self.rows, duplicate, unrelated]}, "EUR_USD")
        self.assertEqual(context["headlines"], self.rows)

    def test_live_fetch_and_short_cache_fallback_do_not_reset_age(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "news.json"
            response = Mock(content=self.xml)
            sources = (("test", "https://example.com/feed", None),)
            with patch.object(news.requests, "get", return_value=response):
                live = news.fetch_market_news(self.now, path, sources)
            self.assertEqual(live["sources"][0]["status"], "live")
            with patch.object(news.requests, "get", side_effect=requests.RequestException("offline")):
                cached = news.fetch_market_news(self.now + timedelta(minutes=10), path, sources)
                self.assertEqual(cached["sources"][0]["status"], "cache")
                self.assertEqual(cached["sources"][0]["fetched_at"], self.now.isoformat())
                unavailable = news.fetch_market_news(self.now + timedelta(hours=1), path, sources)
            self.assertEqual(unavailable["sources"][0]["status"], "unavailable")
            self.assertEqual(unavailable["headlines"], [])

    def test_news_shared_once_in_entry_and_management_batches(self):
        items = [{"symbol": symbol, "bid": 1, "ask": 1.001, "ai_input": {"tf": {}},
                  "market_context": self.context, "kind": "position"} for symbol in ("EUR_USD", "USD_JPY")]
        for key, payload_fn in (("markets", _entry_payload), ("items", _management_payload)):
            payload = _batch_payload(items, key, payload_fn)
            decoded = json.loads(payload)
            self.assertEqual(len(decoded["market_context"]["headlines"]), 1)
            self.assertEqual(payload.count(self.rows[0]["title"]), 1)
            self.assertNotIn("https://example.com/fx", payload)
            self.assertEqual(decoded[key][0]["news_ids"], [self.rows[0]["id"]])

    def test_unknown_news_reference_cannot_be_adopted(self):
        result = {"trend_score": -0.7, "entry_quality": 0, "entry_plan": "NO_TRADE",
                  "entry": None, "trend_invalidation": None, "take_profit": None, "news_refs": ["made-up"]}
        item = {"market_context": self.context}
        self.assertFalse(_validate_entry(result, item)[0])
        result["news_refs"] = [self.rows[0]["id"]]
        self.assertTrue(_validate_entry(result, item)[0])

    def test_compact_notification_links_only_referenced_article(self):
        decision = {"symbol": "EUR_USD", "direction": "sell", "entry_plan": "PULLBACK_LIMIT",
                    "entry": 1.11975, "stop_loss": 1.1215, "take_profit": 1.1168,
                    "rr": 1.69, "suggested_size": 24039, "estimated_loss_jpy": 6652,
                    "market_context": self.context, "news_refs": [self.rows[0]["id"]], "reason": "短い理由"}
        embed = _entry_embed(decision, {"tickSize": 0.00001}, {"equity": 886944}, "test")
        self.assertEqual(len(embed["fields"]), 4)
        self.assertEqual(embed["description"], "短い理由")
        self.assertIn("https://example.com/fx", embed["fields"][-1]["value"])
        self.assertNotIn("Equity", json.dumps(embed))

    def test_released_events_do_not_include_future_actual_or_other_currency(self):
        at = self.now - timedelta(hours=1)
        events = [EconomicEvent("CPI", "USD", "High", at, "3%", "2%", "4%"),
                  EconomicEvent("future", "USD", "High", self.now + timedelta(hours=1), actual="5%"),
                  EconomicEvent("unknown", "USD", "High", at),
                  EconomicEvent("other", "JPY", "High", at, actual="1%")]
        rows = _released_market_events("EUR_USD", events, self.now)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["actual"], "4%")


if __name__ == "__main__":
    unittest.main()
