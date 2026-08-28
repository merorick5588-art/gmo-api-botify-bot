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
        "results":[{
            "symbol":"USD_JPY","trend_score":0.8,"entry_quality":0.8,
            "entry_plan":"ENTER_NOW","entry":150.0,"trend_invalidation":149.5,"take_profit":150.8,"reason":"test"
        }]
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
        self.assertEqual(kwargs["model"], "gpt-5.6-luna")
        self.assertEqual(kwargs["reasoning"]["context"], "current_turn")
        self.assertEqual(kwargs["text"]["format"]["type"], "json_schema")
        self.assertTrue(kwargs["text"]["format"]["strict"])
        self.assertFalse(kwargs["store"])
        self.assertIn("4〜12時間", kwargs["instructions"])
        props = kwargs["text"]["format"]["schema"]["properties"]["results"]["items"]["properties"]
        self.assertIn("trend_invalidation", props)
        self.assertIn("entry_plan", props)
        self.assertIn("PULLBACK_LIMIT", props["entry_plan"]["enum"])
        self.assertIn("押し目/戻り", kwargs["instructions"])

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
