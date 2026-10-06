from __future__ import annotations

import json
import sys
import types
import unittest
from unittest.mock import patch

try:
    from openai import OpenAI as _OpenAI  # noqa: F401
except Exception:
    stub = types.ModuleType("openai")
    stub.OpenAI = object
    sys.modules["openai"] = stub

import analyze_ohlcv


class _Usage:
    input_tokens = 10
    output_tokens = 5
    total_tokens = 15
    input_tokens_details = None
    output_tokens_details = None


class _Response:
    usage = _Usage()
    output_text = json.dumps({
        "results":{"USD_JPY":{
            "trend_score":0.8,"entry_quality":0.8,
            "entry_plan":"ENTER_NOW","entry":150.0,"trend_invalidation":149.5,"take_profit":150.8,"reason":"test"
        }}
    })


class _Responses:
    def __init__(self):
        self.kwargs = None
    def create(self, **kwargs):
        self.kwargs = kwargs
        return _Response()


class _Client:
    def __init__(self):
        self.responses = _Responses()


class LLMContractTests(unittest.TestCase):
    def test_entry_request_uses_responses_structured_output(self):
        client = _Client()
        item = {
            "symbol":"USD_JPY","bid":149.99,"ask":150.0,
            "quote_time": "2026-09-09T00:00:00Z",
            "ai_input":{"tf":{"15m":{"f":{"atr":0.2}},"1h":{"f":{}},"4h":{"f":{}}}},
        }
        with patch.object(analyze_ohlcv, "_client", return_value=client):
            valid, invalid, failed = analyze_ohlcv._request_entry([item], "gpt-5.6-luna")
        self.assertFalse(failed)
        self.assertEqual(invalid, [])
        self.assertIn("USD_JPY", valid)
        self.assertEqual(valid["USD_JPY"]["trend_invalidation"], 149.5)
        self.assertEqual(valid["USD_JPY"]["stop_loss"], 149.5)
        kwargs = client.responses.kwargs
        self.assertEqual(json.loads(kwargs["input"])["markets"][0]["quote_time"], item["quote_time"])
        self.assertEqual(kwargs["model"], "gpt-5.6-luna")
        self.assertEqual(kwargs["reasoning"]["context"], "current_turn")
        self.assertEqual(kwargs["text"]["format"]["type"], "json_schema")
        self.assertTrue(kwargs["text"]["format"]["strict"])
        self.assertFalse(kwargs["store"])
        self.assertIn("4〜12時間", kwargs["instructions"])
        self.assertIn("1d=長期背景", kwargs["instructions"])
        self.assertIn("s100/s200", kwargs["instructions"])
        props = kwargs["text"]["format"]["schema"]["properties"]["results"]["properties"]["USD_JPY"]["properties"]
        self.assertIn("trend_invalidation", props)
        self.assertIn("entry_plan", props)
        self.assertIn("PULLBACK_LIMIT", props["entry_plan"]["enum"])
        self.assertIn("押し目買い", kwargs["instructions"])
        self.assertIn("戻り売り", kwargs["instructions"])

    def test_management_single_and_multiple_symbols(self):
        row = dict(action="HOLD", confidence=0.7, trend_invalidation=1.12,
                   recommended_order_price=None, take_partial_pct=None,
                   reason="継続", news_refs=[])
        for symbols in (["EUR_USD"], ["EUR_USD", "USD_JPY"]):
            with self.subTest(symbols=symbols):
                client = _Client()
                response = types.SimpleNamespace(status="completed", usage=None,
                    output_text=json.dumps({"results": {s: row for s in symbols}}))
                with patch.object(client.responses, "create", return_value=response) as create, \
                     patch.object(analyze_ohlcv, "_client", return_value=client):
                    result = analyze_ohlcv._request_management(
                        [{"symbol": s, "kind": "position"} for s in symbols], "gpt-5.6-luna")
                self.assertEqual(set(result), set(symbols))
                self.assertTrue(all(result[s]["action"] == "HOLD" and
                                    result[s]["symbol"] == s for s in symbols))
                create.assert_called_once()
                schema = create.call_args.kwargs["text"]["format"]["schema"]["properties"]["results"]
                self.assertEqual(schema["required"], symbols)
                self.assertFalse(schema["additionalProperties"])
                self.assertNotIn("symbol", schema["properties"][symbols[0]]["properties"])

    def test_management_rejects_invalid_envelopes_without_retry(self):
        texts = [
            '{"results":{"EUR_USD":{"action":"HOLD"},"EUR_USD":{"action":"CLOSE"}}}',
            '{"results":{}}',
            '{"results":{"EUR_USD":{},"USD_JPY":{}}}',
            '{"results":{"EUR_USD":null}}',
            '{"results":{"EUR_USD":{"symbol":"USD_JPY"}}}',
            '{"results":[{"symbol":"EUR_USD"},{"symbol":"EUR_USD"}]}',
        ]
        for output_text in texts:
            with self.subTest(output_text=output_text):
                client = _Client()
                response = types.SimpleNamespace(status="completed", usage=None, output_text=output_text)
                with patch.object(client.responses, "create", return_value=response) as create, \
                     patch.object(analyze_ohlcv, "_client", return_value=client):
                    result = analyze_ohlcv._request_management(
                        [{"symbol": "EUR_USD", "kind": "position"}], "gpt-5.6-luna")
                self.assertEqual(result, {})
                create.assert_called_once()

    def test_pullback_plan_semantics(self):
        item = {
            "symbol":"USD_JPY","bid":149.99,"ask":150.0,
            "ai_input":{"tf":{"15m":{"f":{"atr":0.2}}}},
        }
        buy_pullback = {
            "symbol":"USD_JPY","trend_score":0.8,"entry_quality":0.8,
            "entry_plan":"PULLBACK_LIMIT","entry":149.8,
            "trend_invalidation":149.4,"take_profit":150.5,"reason":"押し目"
        }
        ok, reason = analyze_ohlcv._validate_entry(buy_pullback, item)
        self.assertTrue(ok, reason)
        bad = dict(buy_pullback, entry=150.1, trend_invalidation=149.4, take_profit=151.3)
        ok, reason = analyze_ohlcv._validate_entry(bad, item)
        self.assertFalse(ok)
        self.assertIn("PULLBACK_LIMIT", reason)


if __name__ == "__main__":
    unittest.main()
