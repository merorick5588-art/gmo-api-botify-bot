from __future__ import annotations

import math
from datetime import datetime

from bot_config import MAX_SPREAD_ATR_RATIO


def _reg(ai_input: dict, tf: str) -> str:
    return str(ai_input.get("tf", {}).get(tf, {}).get("f", {}).get("reg", ""))


def _feat(ai_input: dict, tf: str, key: str, default=0.0):
    try:
        return float(ai_input.get("tf", {}).get(tf, {}).get("f", {}).get(key, default))
    except (TypeError, ValueError):
        return default


def stage1_filter(ai_input: dict, bid: float, ask: float, quote_time: str | None = None) -> dict:
    reasons: list[str] = []
    warnings: list[str] = []
    reg4 = _reg(ai_input, "4h")
    reg1 = _reg(ai_input, "1h")
    reg15 = _reg(ai_input, "15m")

    if not all(math.isfinite(float(v)) and float(v) > 0 for v in (bid, ask)) or float(bid) > float(ask):
        reasons.append("Bid/Askが不正")
    if quote_time is not None:
        try:
            quote = datetime.fromisoformat(quote_time.replace("Z", "+00:00"))
            if quote.tzinfo is None:
                raise ValueError("quote timezone missing")
            for tf, minutes in (("15m", 15), ("1h", 60)):
                closed = datetime.fromisoformat(ai_input.get("tf", {}).get(tf, {}).get("close_time", "").replace("Z", "+00:00"))
                if closed.tzinfo is None:
                    raise ValueError("candle timezone missing")
                age = (quote - closed).total_seconds()
                # 最新完成足の遅延を1本分許容。休日明け等も古い短期足で推奨しない。
                if age < 0 or age > minutes * 120 + 180:
                    reasons.append(f"{tf}完成足が古い、またはquoteより未来")
        except (ValueError, TypeError, AttributeError):
            reasons.append("短期足の時刻不足: 入力を再生成")

    if reg4 not in {"TREND_UP", "TREND_DOWN"}:
        reasons.append(f"4hレジーム={reg4 or 'UNKNOWN'}")

    # 4hと1hが真正面から反対の場合のみ除外。15m逆行は押し目/戻り候補として残す。
    if reg4 == "TREND_UP" and reg1 == "TREND_DOWN":
        reasons.append("4h上昇に対し1hが下降トレンド")
    if reg4 == "TREND_DOWN" and reg1 == "TREND_UP":
        reasons.append("4h下降に対し1hが上昇トレンド")

    if reg15 in {"HIGH_VOL"}:
        warnings.append("15mが高ボラティリティ")

    atr15 = _feat(ai_input, "15m", "atr", 0)
    spread = max(0.0, float(ask) - float(bid))
    if not math.isfinite(atr15) or atr15 <= 0:
        reasons.append("15m ATRを取得できない")
    elif spread / atr15 > MAX_SPREAD_ATR_RATIO:
        reasons.append(f"スプレッド/15mATR={spread/atr15:.2f} が過大")

    return {
        "llm_call_allowed": not reasons,
        "stage1_reasons": reasons,
        "warnings": warnings,
        "regime_4h": reg4,
        "regime_1h": reg1,
        "regime_15m": reg15,
    }


def post_validate_direction(ai_input: dict, direction: str) -> tuple[bool, list[str]]:
    reg4 = _reg(ai_input, "4h")
    warnings: list[str] = []
    if direction == "buy" and reg4 != "TREND_UP":
        return False, [f"BUYだが4hレジーム={reg4}"]
    if direction == "sell" and reg4 != "TREND_DOWN":
        return False, [f"SELLだが4hレジーム={reg4}"]

    rsi15 = _feat(ai_input, "15m", "rsi", 50)
    if direction == "buy" and rsi15 >= 80:
        warnings.append("15m RSI>=80で高値追いリスク")
    if direction == "sell" and rsi15 <= 20:
        warnings.append("15m RSI<=20で安値追いリスク")
    return True, warnings
