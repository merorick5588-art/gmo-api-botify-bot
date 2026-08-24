# analyze_ohlcv.py
import os
import json
import math
from openai import OpenAI

from llm_config import (
    DEFAULT_MODEL,
    MARKET_REASONING_EFFORT,
    market_max_output_tokens,
    log_usage,
)


OCO_ITEM_SCHEMA = {
    "type": "object",
    "properties": {
        "risk": {"type": "string", "enum": ["Low", "Medium", "High"]},
        "entry": {"type": "number"},
        "stop_loss": {"type": "number"},
        "take_profit": {"type": "number"},
    },
    "required": ["risk", "entry", "stop_loss", "take_profit"],
    "additionalProperties": False,
}

MARKET_RESULT_SCHEMA = {
    "type": "object",
    "properties": {
        "symbol": {"type": "string"},
        "trend_score": {"type": "number", "minimum": -1, "maximum": 1},
        "direction": {"type": "string", "enum": ["buy", "sell"]},
        "ifd_oco": {
            "type": "array",
            "minItems": 3,
            "maxItems": 3,
            "items": OCO_ITEM_SCHEMA,
        },
    },
    "required": ["symbol", "trend_score", "direction", "ifd_oco"],
    "additionalProperties": False,
}

MARKET_BATCH_SCHEMA = {
    "type": "object",
    "properties": {
        "results": {
            "type": "array",
            "minItems": 1,
            "maxItems": 20,
            "items": MARKET_RESULT_SCHEMA,
        }
    },
    "required": ["results"],
    "additionalProperties": False,
}

MARKET_ANALYST_INSTRUCTIONS = """短期FXテクニカル分析。入力だけを根拠に今後1〜4時間を評価する。
各symbolは完全に独立して分析し、別symbolの値・方向・特徴を混ぜない。入力されたsymbolを各1件、漏れ・重複なく返す。
trend_score=-1〜1（絶対値0.1〜0.3弱、0.4〜0.6明確、0.7〜1強）。正ならbuy、負ならsell。0付近はdominant_tfと1hを優先。
dominant_tf順張りを基本に、時間足整合、RSI、MACD、ATR、価格位置、ボラティリティ、直近足を総合。外部情報は使わない。
buyのentryはAsk基準でSL<Entry<TP、sellはBid基準でTP<Entry<SL。
IFD-OCOはLow/Medium/High各1件。ATRとボラティリティに応じ、Low→Highの順にSL距離・TP距離を広げる。"""


def _finite_number(value):
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        if not math.isfinite(float(value)):
            return None
        return float(f"{float(value):.7g}")
    return value


def _compact_value(value):
    if isinstance(value, dict):
        result = {}
        for key, raw in value.items():
            compacted = _compact_value(raw)
            if compacted is not None:
                result[key] = compacted
        return result
    if isinstance(value, list):
        return [_compact_value(v) for v in value]
    return _finite_number(value)


def build_analysis_payload(ai_input, symbol, bid, ask):
    """LLMに必要なFX情報だけを短いJSONに再構成する。"""
    payload = {
        "symbol": symbol,
        "bid": bid,
        "ask": ask,
        "tf": {},
        "rel": ai_input.get("timeframe_relationship"),
    }

    for tf in ("15m", "1h", "4h"):
        src = ai_input.get("timeframes", {}).get(tf)
        if not src:
            continue

        payload["tf"][tf] = {
            "phase": src.get("market_phase", {}).get("label"),
            "tags": src.get("market_phase", {}).get("tags", []),
            "ohlc": [
                {k: v for k, v in candle.items() if k != "v"}
                for candle in src.get("recent_ohlc", [])
            ],
            "feat": src.get("features_summary", {}),
            "pos": src.get("price_context", {}),
            "vol": src.get("volatility_state", {}),
        }

    return _compact_value(payload)


def _validate_result(result):
    try:
        score = float(result.get("trend_score", 0))
        direction = result.get("direction")
        oco = result.get("ifd_oco", [])

        if direction not in {"buy", "sell"} or len(oco) != 3:
            return False
        if score > 0 and direction != "buy":
            return False
        if score < 0 and direction != "sell":
            return False
        if {x.get("risk") for x in oco} != {"Low", "Medium", "High"}:
            return False

        sl_distances = []
        tp_distances = []
        by_risk = {item["risk"]: item for item in oco}
        for risk in ("Low", "Medium", "High"):
            item = by_risk[risk]
            entry = float(item["entry"])
            sl = float(item["stop_loss"])
            tp = float(item["take_profit"])
            if direction == "buy" and not (sl < entry < tp):
                return False
            if direction == "sell" and not (tp < entry < sl):
                return False
            sl_distances.append(abs(entry - sl))
            tp_distances.append(abs(tp - entry))

        if sl_distances != sorted(sl_distances) or tp_distances != sorted(tp_distances):
            return False
        return True
    except (TypeError, ValueError, KeyError):
        return False


def _normalize_result(result):
    score = max(-1.0, min(1.0, float(result.get("trend_score", 0))))
    result["trend_score"] = score
    result["signal_strength"] = abs(score)
    risk_order = {"Low": 0, "Medium": 1, "High": 2}
    result["ifd_oco"].sort(key=lambda x: risk_order[x["risk"]])

    # 旧通知コードとの互換用。確率ではなく方向スコア由来。
    result["up_probability"] = max(score, 0)
    result["down_probability"] = abs(min(score, 0))
    return result


def _request_batch(items, model_name=DEFAULT_MODEL):
    """指定された銘柄群を1回のResponses APIで分析する内部関数。"""
    if not items:
        return {}

    expected_symbols = [item["symbol"] for item in items]
    if len(set(expected_symbols)) != len(expected_symbols):
        raise ValueError("batch items contain duplicate symbols")

    markets = [
        build_analysis_payload(
            item["ai_input"],
            item["symbol"],
            float(item["bid"]),
            float(item["ask"]),
        )
        for item in items
    ]
    compact_input = json.dumps({"markets": markets}, ensure_ascii=False, separators=(",", ":"))

    client = OpenAI(api_key=os.environ.get("OPENAI_API_KEY"))
    try:
        response = client.responses.create(
            model=model_name,
            instructions=MARKET_ANALYST_INSTRUCTIONS,
            input=compact_input,
            reasoning={"effort": MARKET_REASONING_EFFORT},
            text={
                "verbosity": "low",
                "format": {
                    "type": "json_schema",
                    "name": "fx_market_batch_analysis",
                    "strict": True,
                    "schema": MARKET_BATCH_SCHEMA,
                },
            },
            max_output_tokens=market_max_output_tokens(len(items)),
            store=False,
        )
        log_usage(response, f"market-batch:{len(items)}")
        parsed = json.loads(response.output_text)
    except Exception as exc:
        print(f"OpenAI一括分析に失敗しました: {exc}")
        return {}

    results = parsed.get("results", [])
    by_symbol = {}
    for result in results:
        symbol = result.get("symbol")
        if symbol not in expected_symbols or symbol in by_symbol:
            print(f"AI出力に不正なsymbolがあります: {symbol}")
            return {}
        if not _validate_result(result):
            print(f"AI出力の整合性チェックに失敗しました ({symbol}): {result}")
            return {}
        by_symbol[symbol] = _normalize_result(result)

    if set(by_symbol) != set(expected_symbols):
        missing = sorted(set(expected_symbols) - set(by_symbol))
        print(f"AI一括分析で銘柄が欠落しました: {missing}")
        return {}

    return by_symbol


def analyze_ai_inputs_batch(items, model_name=DEFAULT_MODEL):
    """
    Stage1通過済みの複数FX銘柄を原則1回のResponses APIで独立分析する。
    バッチ応答が不正な場合だけ、信頼性優先で単銘柄呼び出しへフォールバックする。
    """
    if not items:
        return {}

    results = _request_batch(items, model_name=model_name)
    if results or len(items) == 1:
        return results

    print("一括分析が不正だったため、単銘柄分析へフォールバックします。")
    recovered = {}
    for item in items:
        single = _request_batch([item], model_name=model_name)
        if item["symbol"] in single:
            recovered[item["symbol"]] = single[item["symbol"]]
    return recovered


def analyze_ai_input(
    ai_input,
    symbol,
    latest_price=None,
    model_name=DEFAULT_MODEL,
    latest_bid=None,
    latest_ask=None,
):
    """単一銘柄用の互換ラッパー。内部ではバッチAPIを1件で使用する。"""
    fallback_bid = ai_input.get("latest_rate", {}).get("bid", latest_price)
    fallback_ask = ai_input.get("latest_rate", {}).get("ask", latest_price)
    if latest_bid is None:
        latest_bid = fallback_bid
    if latest_ask is None:
        latest_ask = fallback_ask
    if latest_bid is None or latest_ask is None:
        raise ValueError("bid/ask is required")

    results = analyze_ai_inputs_batch([
        {
            "symbol": symbol,
            "ai_input": ai_input,
            "bid": float(latest_bid),
            "ask": float(latest_ask),
        }
    ], model_name=model_name)
    return results.get(symbol)


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("ai_input_file", type=str)
    parser.add_argument("--symbol", type=str, required=True)
    parser.add_argument("--latest_bid", type=float, required=True)
    parser.add_argument("--latest_ask", type=float, required=True)
    parser.add_argument("--model", type=str, default=DEFAULT_MODEL)
    args = parser.parse_args()

    with open(args.ai_input_file, "r", encoding="utf-8") as f:
        ai_input = json.load(f)

    result = analyze_ai_input(
        ai_input,
        args.symbol,
        model_name=args.model,
        latest_bid=args.latest_bid,
        latest_ask=args.latest_ask,
    )
    print(json.dumps(result, indent=2, ensure_ascii=False))
