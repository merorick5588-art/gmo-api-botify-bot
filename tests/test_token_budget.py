from __future__ import annotations

import json
import types
import unittest
from unittest.mock import patch

import analyze_ohlcv
from llm_config import market_max_output_tokens, management_max_output_tokens


class _Response:
    def __init__(self, text: str, *, status=None, reason=None):
        self.output_text = text
        self.usage = None
        self.status = status
        self.incomplete_details = types.SimpleNamespace(reason=reason) if reason else None


class _Responses:
    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return self._responses.pop(0)


class _Client:
    def __init__(self, responses):
        self.responses = _Responses(responses)


class TokenBudgetTests(unittest.TestCase):
    def test_medium_reasoning_has_visible_json_headroom(self):
        self.assertGreaterEqual(market_max_output_tokens(1), 1600)
        self.assertGreaterEqual(management_max_output_tokens(1), 1400)
        self.assertGreater(market_max_output_tokens(6), market_max_output_tokens(1))
        self.assertGreater(management_max_output_tokens(6), management_max_output_tokens(1))

    def test_malformed_json_retries_once_with_larger_budget(self):
        client = _Client([
            _Response('{"results":[{"symbol":"USD_JPY"', status="incomplete", reason="max_output_tokens"),
            _Response(json.dumps({"results": [{"symbol": "USD_JPY"}]}), status="completed"),
        ])
        with patch.object(analyze_ohlcv, "_client", return_value=client):
            parsed = analyze_ohlcv._response_json_with_retry(
                label="management-batch:1",
                create_kwargs={"model": "gpt-5.6-luna", "input": "{}"},
                initial_max_tokens=500,
            )
        self.assertEqual(parsed["results"][0]["symbol"], "USD_JPY")
        self.assertEqual(len(client.responses.calls), 2)
        self.assertEqual(client.responses.calls[0]["max_output_tokens"], 500)
        self.assertGreaterEqual(client.responses.calls[1]["max_output_tokens"], 1700)


if __name__ == "__main__":
    unittest.main()
