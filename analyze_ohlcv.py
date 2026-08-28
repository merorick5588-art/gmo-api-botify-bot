from __future__ import annotations

import json
import math
import os
from typing import Any

from openai import OpenAI

from bot_config import MIN_RR
from llm_config import (
    DEFAULT_MODEL,
    BATCH_ANALYSIS_ENABLED,
    BATCH_MAX_SYMBOLS,
    MANAGEMENT_REASONING_EFFORT,
    MARKET_REASONING_EFFORT,
    log_usage,
    management_max_output_tokens,
    market_max_output_tokens,
)

ENTRY_RESULT_SCHEMA = {
    "type": "object",
    "properties": {
        "symbol": {"type": "string"},
        "trend_score": {"type": "number", "minimum": -1, "maximum": 1},
        "entry_quality": {"type": "number", "minimum": 0, "maximum": 1},
        "entry_plan": {"type": "string", "enum": ["ENTER_NOW", "PULLBACK_LIMIT", "BREAKOUT_STOP"]},
        "entry": {"type": "number"},
        "trend_invalidation": {"type": "number"},
        "take_profit": {"type": "number"},
        "reason": {"type": "string"},
    },
    "required": [
        "symbol", "trend_score", "entry_quality", "entry_plan", "entry",
        "trend_invalidation", "take_profit", "reason",
    ],
    "additionalProperties": False,
}
ENTRY_BATCH_SCHEMA = {
    "type": "object",
    "properties": {
        "results": {"type": "array", "minItems": 1, "maxItems": 30, "items": ENTRY_RESULT_SCHEMA}
    },
    "required": ["results"],
    "additionalProperties": False,
}

NULL_NUMBER = {"anyOf": [{"type": "number"}, {"type": "null"}]}
MGMT_RESULT_SCHEMA = {
    "type": "object",
    "properties": {
        "symbol": {"type": "string"},
        "action": {
            "type": "string",
            "enum": [
                "HOLD", "CLOSE", "TAKE_PARTIAL", "TIGHTEN_SL",
                "KEEP_ORDER", "CANCEL_ORDER", "REPRICE_ORDER", "REVIEW_MANUALLY",
            ],
        },
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
        "trend_invalidation": NULL_NUMBER,
        "recommended_order_price": NULL_NUMBER,
        "take_partial_pct": NULL_NUMBER,
        "reason": {"type": "string"},
    },
    "required": [
        "symbol", "action", "confidence", "trend_invalidation",
        "recommended_order_price", "take_partial_pct", "reason",
    ],
    "additionalProperties": False,
}
MGMT_BATCH_SCHEMA = {
    "type": "object",
    "properties": {
        "results": {"type": "array", "minItems": 1, "maxItems": 30, "items": MGMT_RESULT_SCHEMA}
    },
    "required": ["results"],
    "additionalProperties": False,
}

ENTRY_INSTRUCTIONS = f"""目的: 入力されたテクニカルだけを使い、各FX銘柄の今後4〜12時間の方向を予測し、デイトレ〜短期スイング向けの注文案を1つだけ返す。外部情報は禁止。
時間軸: 4h=大局とトレンド仮説、1h=予測の主軸とセットアップ、15m=約定タイミング。各symbolは完全に独立分析し、漏れ・重複なく返す。
凡例: tf.*.f の reg=レジーム,rsi=RSI14,adx=ADX14,pdi/mdi=DI,s20/s50=現在値のSMA20/50からのATR距離,
macd=MACD/ATR,mh=MACDヒストグラム/ATR,sl20/sl50=SMA20/50の5本変化÷ATR,atrp=ATR%,vr=直近/100本ボラ比,
h20/l20=現在値から20本高値/安値までのATR距離,ret20=平均20リターン%,up20=20本上昇比,last=直近リターン%,atr=ATR14。cは古い→新しい[O,H,L,C]。
trend_scoreは現状の説明ではなく「4〜12時間先の方向予測」と確信度。-1=強い下落予測、+1=強い上昇予測。根拠が拮抗するなら0へ寄せ、無理に強い値を付けない。
entry_qualityは方向予測とは別に「提案するentry_planとentry価格で注文する質」。現在値を追う必要はなく、高値追い/安値追い、直近20本の反対側余地不足、15m過熱、高ボラは減点する。15m逆行が4h/1h順張りの健全な押し目/戻りなら、その待ち注文のqualityを高くしてよい。
分析では4hのreg/ADX/DI/SMA傾き→1hの継続性→15mのタイミングの順に確認し、内部で上昇ケースと下落ケースを比較してから一方向を選ぶ。さらに、その方向について「現在値付近で入る」「押し目/戻りをLIMITで待つ」「ブレイクをSTOPで待つ」の3案を内部比較し、期待値が最も高い1案だけをentry_planにする。
entry_planはENTER_NOW / PULLBACK_LIMIT / BREAKOUT_STOP。PULLBACK_LIMITはBUYならAskより下の押し目買い、SELLならBidより上の戻り売り。BREAKOUT_STOPはBUYならAskより上の上抜け、SELLならBidより下の下抜け。押し目/戻りを待つ方が現在値追随より良いなら、必ずPULLBACK_LIMITを選ぶ。
entryはentry_planで実際に約定を狙う価格。4〜12時間内に合理的に約定し得る1価格にする。
trend_invalidationは単なる狭い損切り幅ではなく、その価格まで逆行すれば1h/4hの予測前提が崩れたと判断できる逆指値水準。主に1h/4hの構造、20本高安、SMA、ATRから置き、RRを良く見せるためだけに不自然に近づけない。
take_profitは4〜12時間の最初の現実的な到達目標。必ずtrend_invalidationを先に決め、その後に利確目標を決める。現実的なRRが{MIN_RR:.2f}未満なら数値を捏造せず、妥当な価格を返したうえでentry_qualityを低くする。
BUYはtrend_invalidation < entry < take_profit、SELLはtake_profit < entry < trend_invalidation。理由は予測根拠と約定タイミングを含む短い日本語1文。"""

MGMT_INSTRUCTIONS = """FXデイトレ〜短期スイングの既存建玉/未約定注文を、今後4〜12時間の市場構造を基準に管理する。外部情報は禁止、入力だけを使う。
目的は年間期待値とドローダウン管理。ctx.prev_actionと現在構造を比較し、有意な変化がなければHOLD/KEEP_ORDERを優先する。含み損を理由に逆指値を損失側へ広げない。ctx.eventsに重要指標が近ければ急変リスクも考慮する。
4h=大局とトレンド仮説、1h=管理判断の主軸、15m=短期変化。
position: HOLD/CLOSE/TAKE_PARTIAL/TIGHTEN_SL/REVIEW_MANUALLY。トレンドがまだ有効ならtrend_invalidationに「ここを抜けたら保有前提が崩れる価格」を返す。CLOSE/REVIEW_MANUALLYで有効な水準を定義できない場合はnull可。TIGHTEN_SLではこの水準を実際の提案逆指値として扱う。
order: KEEP_ORDER/CANCEL_ORDER/REPRICE_ORDER/REVIEW_MANUALLY。未約定注文がまだ有効ならrecommended_order_priceに「現在の構造から最も合理的に約定を狙う価格」を必ず返す。KEEP_ORDERでも現在注文価格が妥当か比較できるよう数値を返す。CANCEL_ORDER/REVIEW_MANUALLYで新規約定自体を推奨しない場合のみnull可。
注文価格はorders内のOPEN注文のside/type/priceと現在Bid/Askを踏まえ、LIMITなら押し目/戻り、STOPならブレイク水準として考える。注文種別を暗黙に逆転させる価格は出さない。
take_partial_pctはTAKE_PARTIAL時だけ数値、それ以外null。曖昧・複雑ならREVIEW_MANUALLY。理由は日本語で短く1文。"""


def _client() -> OpenAI:
    key = os.getenv("OPENAI_API_KEY")
    if not key:
        raise RuntimeError("OPENAI_API_KEY が未設定です")
    return OpenAI(api_key=key)


def _compact(obj: Any) -> Any:
    if isinstance(obj, dict):
        return {k: _compact(v) for k, v in obj.items() if v is not None}
    if isinstance(obj, list):
        return [_compact(v) for v in obj]
    if isinstance(obj, float):
        if not math.isfinite(obj):
            return None
        return float(f"{obj:.8g}")
    return obj


def _entry_payload(item: dict) -> dict:
    return _compact({
        "symbol": item["symbol"],
        "bid": float(item["bid"]),
        "ask": float(item["ask"]),
        "tf": item["ai_input"].get("tf", {}),
    })


def _validate_entry(result: dict, item: dict) -> tuple[bool, str | None]:
    try:
        score = float(result["trend_score"])
        quality = float(result["entry_quality"])
        entry_plan = str(result["entry_plan"])
        entry = float(result["entry"])
        invalidation = float(result["trend_invalidation"])
        tp = float(result["take_profit"])
        if not (-1 <= score <= 1 and 0 <= quality <= 1):
            return False, "score range"
        direction = "buy" if score > 0 else "sell" if score < 0 else None
        if direction is None:
            return False, "trend_score=0"
        if direction == "buy" and not (invalidation < entry < tp):
            return False, "buy price ordering"
        if direction == "sell" and not (tp < entry < invalidation):
            return False, "sell price ordering"
        risk = abs(entry - invalidation)
        reward = abs(tp - entry)
        if risk <= 0 or reward / risk < MIN_RR - 1e-9:
            return False, "RR不足"
        atr = float(item["ai_input"].get("tf", {}).get("15m", {}).get("f", {}).get("atr", 0) or 0)
        bid = float(item["bid"])
        ask = float(item["ask"])
        current = ask if direction == "buy" else bid
        if entry_plan == "PULLBACK_LIMIT":
            if direction == "buy" and not entry < ask:
                return False, "BUY PULLBACK_LIMITはAskより下である必要がある"
            if direction == "sell" and not entry > bid:
                return False, "SELL PULLBACK_LIMITはBidより上である必要がある"
        elif entry_plan == "BREAKOUT_STOP":
            if direction == "buy" and not entry > ask:
                return False, "BUY BREAKOUT_STOPはAskより上である必要がある"
            if direction == "sell" and not entry < bid:
                return False, "SELL BREAKOUT_STOPはBidより下である必要がある"
        elif entry_plan == "ENTER_NOW":
            # 「今入る」と言いながら現在値から大きく離れた価格を出す矛盾を防ぐ。
            if atr > 0 and abs(entry - current) > atr * 0.25:
                return False, "ENTER_NOWの約定値が現在値から遠すぎる"
        else:
            return False, "unknown entry_plan"
        if atr > 0 and abs(entry - current) > atr * 1.75:
            return False, "Entryが現在値から遠すぎる"
        # トレンド崩壊ラインが15mノイズ内に極端に近すぎる場合は採用しない。
        if atr > 0 and risk < atr * 0.35:
            return False, "trend_invalidationが15m ATRに対して近すぎる"
        return True, None
    except (KeyError, TypeError, ValueError):
        return False, "parse error"


def _normalize_entry(result: dict) -> dict:
    score = float(result["trend_score"])
    direction = "buy" if score > 0 else "sell"
    entry = float(result["entry"])
    invalidation = float(result["trend_invalidation"])
    tp = float(result["take_profit"])
    result = dict(result)
    result.update({
        "trend_score": score,
        "entry_quality": float(result["entry_quality"]),
        "entry_plan": str(result["entry_plan"]),
        "direction": direction,
        "entry": entry,
        "trend_invalidation": invalidation,
        # DB/リスク計算/仮想追跡との後方互換。意味は「トレンド前提が崩れる逆指値」。
        "stop_loss": invalidation,
        "take_profit": tp,
        "rr": abs(tp - entry) / abs(entry - invalidation),
    })
    return result


def _request_entry(items: list[dict], model_name: str) -> tuple[dict[str, dict], list[str], bool]:
    if not items:
        return {}, [], False
    expected = [x["symbol"] for x in items]
    payload = json.dumps({"markets": [_entry_payload(x) for x in items]}, ensure_ascii=False, separators=(",", ":"))
    try:
        response = _client().responses.create(
            model=model_name,
            instructions=ENTRY_INSTRUCTIONS,
            input=payload,
            reasoning={"effort": MARKET_REASONING_EFFORT, "context": "current_turn"},
            text={
                "verbosity": "low",
                "format": {"type": "json_schema", "name": "fx_entry_batch", "strict": True, "schema": ENTRY_BATCH_SCHEMA},
            },
            max_output_tokens=market_max_output_tokens(len(items)),
            store=False,
        )
        log_usage(response, f"entry-batch:{len(items)}")
        parsed = json.loads(response.output_text)
    except Exception as exc:
        print(f"OpenAI Entry batch failed: {exc}")
        return {}, expected, True

    item_map = {x["symbol"]: x for x in items}
    valid: dict[str, dict] = {}
    invalid: list[str] = []
    seen: set[str] = set()
    for row in parsed.get("results", []):
        symbol = row.get("symbol")
        if symbol not in item_map or symbol in seen:
            continue
        seen.add(symbol)
        ok, reason = _validate_entry(row, item_map[symbol])
        if ok:
            valid[symbol] = _normalize_entry(row)
        else:
            print(f"Entry semantic validation failed {symbol}: {reason}")
            invalid.append(symbol)
    invalid.extend(s for s in expected if s not in seen)
    return valid, list(dict.fromkeys(invalid)), False


def analyze_entry_batch(items: list[dict], model_name: str = DEFAULT_MODEL) -> dict[str, dict]:
    if not items:
        return {}
    if not BATCH_ANALYSIS_ENABLED and len(items) > 1:
        out: dict[str, dict] = {}
        for item in items:
            out.update(analyze_entry_batch([item], model_name))
        return out
    if len(items) > BATCH_MAX_SYMBOLS:
        out: dict[str, dict] = {}
        for i in range(0, len(items), BATCH_MAX_SYMBOLS):
            out.update(analyze_entry_batch(items[i:i + BATCH_MAX_SYMBOLS], model_name))
        return out
    valid, invalid, transport_error = _request_entry(items, model_name)
    if transport_error or not invalid:
        return valid
    by_symbol = {x["symbol"]: x for x in items}
    for symbol in invalid:
        one, _, failed = _request_entry([by_symbol[symbol]], model_name)
        if not failed and symbol in one:
            valid[symbol] = one[symbol]
    return valid


def _management_payload(item: dict) -> dict:
    """Private APIの生JSONをそのまま送らず、管理判断に必要な項目だけ渡す。"""
    out = {
        "symbol": item["symbol"],
        "kind": item.get("kind"),
        "bid": item.get("bid"),
        "ask": item.get("ask"),
        "tf": item.get("tf", {}),
        "ctx": item.get("ctx", {}),
    }
    if item.get("kind") == "position":
        p = item.get("position") or {}
        out["position"] = {
            "side": p.get("side"),
            "size": p.get("sumPositionSize", p.get("size")),
            "avg": p.get("averagePositionRate", p.get("price")),
            "lossGain": p.get("positionLossGain", p.get("lossGain")),
            "swap": p.get("sumTotalSwap", p.get("totalSwap")),
        }
    orders = []
    for o in item.get("orders", []):
        orders.append({
            "id": o.get("orderId"),
            "root": o.get("rootOrderId"),
            "side": o.get("side"),
            "type": o.get("executionType"),
            "settle": o.get("settleType"),
            "size": o.get("size"),
            "price": o.get("price"),
            "status": o.get("status"),
        })
    if orders:
        out["orders"] = orders
    return _compact(out)


def _request_management(items: list[dict], model_name: str) -> dict[str, dict]:
    if not items:
        return {}
    expected = {x["symbol"] for x in items}
    payload = json.dumps({"items": [_management_payload(x) for x in items]}, ensure_ascii=False, separators=(",", ":"))
    try:
        response = _client().responses.create(
            model=model_name,
            instructions=MGMT_INSTRUCTIONS,
            input=payload,
            reasoning={"effort": MANAGEMENT_REASONING_EFFORT, "context": "current_turn"},
            text={
                "verbosity": "low",
                "format": {"type": "json_schema", "name": "fx_management_batch", "strict": True, "schema": MGMT_BATCH_SCHEMA},
            },
            max_output_tokens=management_max_output_tokens(len(items)),
            store=False,
        )
        log_usage(response, f"management-batch:{len(items)}")
        rows = json.loads(response.output_text).get("results", [])
    except Exception as exc:
        print(f"OpenAI Management batch failed: {exc}")
        return {}

    out: dict[str, dict] = {}
    item_map = {x["symbol"]: x for x in items}
    for row in rows:
        symbol = row.get("symbol")
        if symbol not in expected or symbol in out:
            continue
        kind = item_map[symbol].get("kind")
        action = row.get("action")
        allowed = {
            "position": {"HOLD", "CLOSE", "TAKE_PARTIAL", "TIGHTEN_SL", "REVIEW_MANUALLY"},
            "order": {"KEEP_ORDER", "CANCEL_ORDER", "REPRICE_ORDER", "REVIEW_MANUALLY"},
        }.get(kind, {"REVIEW_MANUALLY"})
        if action not in allowed:
            row["action"] = "REVIEW_MANUALLY"
            row["reason"] = "AI actionが現在状態に適合しないため手動確認"
        out[symbol] = row
    return out


def analyze_management_batch(items: list[dict], model_name: str = DEFAULT_MODEL) -> dict[str, dict]:
    if not BATCH_ANALYSIS_ENABLED and len(items) > 1:
        out: dict[str, dict] = {}
        for item in items:
            out.update(_request_management([item], model_name))
        return out
    if len(items) > BATCH_MAX_SYMBOLS:
        out: dict[str, dict] = {}
        for i in range(0, len(items), BATCH_MAX_SYMBOLS):
            out.update(analyze_management_batch(items[i:i + BATCH_MAX_SYMBOLS], model_name))
        return out
    return _request_management(items, model_name)


# 旧呼び出しとの互換用
def analyze_ai_inputs_batch(items, model_name=DEFAULT_MODEL):
    return analyze_entry_batch(items, model_name)


def analyze_ai_input(ai_input, symbol, latest_price=None, model_name=DEFAULT_MODEL, latest_bid=None, latest_ask=None):
    bid = latest_bid if latest_bid is not None else latest_price
    ask = latest_ask if latest_ask is not None else latest_price
    if bid is None or ask is None:
        raise ValueError("bid/ask is required")
    return analyze_entry_batch([{"symbol": symbol, "ai_input": ai_input, "bid": bid, "ask": ask}], model_name).get(symbol)
