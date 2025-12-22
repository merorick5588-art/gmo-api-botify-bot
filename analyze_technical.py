# analyze_technical.py

def evaluate_technical_risk(timeframes, direction=None):
    """
    テクニカルは以下2役割
    1) LLM呼び出し可否（llm_call_allowed）
    2) LLM後の拒否権（block）

    direction: "buy" / "sell" / None
    """

    warnings = []
    block = False

    # ===== 4h 最重要 =====
    tf_4h = timeframes.get("4h", {})
    fs_4h = tf_4h.get("features_summary", {})
    phase_4h = tf_4h.get("market_phase", {}).get("label", "")

    rsi_4h = fs_4h.get("rsi14", 50)

    # ===== 15m / 1h =====
    tf_15m = timeframes.get("15m", {})
    phase_15m = tf_15m.get("market_phase", {}).get("label", "")

    tf_1h = timeframes.get("1h", {})
    phase_1h = tf_1h.get("market_phase", {}).get("label", "")

    # ===== Stage1 : LLM呼び出し判定 =====
    llm_call_allowed = True

    if "range" in phase_4h or phase_4h == "":
        llm_call_allowed = False

    if ("uptrend" in phase_15m and "downtrend" in phase_1h) or \
       ("downtrend" in phase_15m and "uptrend" in phase_1h):
        llm_call_allowed = False

    # ===== Stage2 : 拒否権（LLM後） =====
    if direction:
        # --- RSI ---
        if direction == "buy" and rsi_4h >= 75:
            block = True
            warnings.append("4h RSIが過熱（買い危険）")

        if direction == "sell" and rsi_4h <= 25:
            block = True
            warnings.append("4h RSIが売られすぎ（売り危険）")

        # --- 上位足逆行 ---
        if direction == "buy" and "downtrend" in phase_4h:
            block = True
            warnings.append("4hが下降トレンド")

        if direction == "sell" and "uptrend" in phase_4h:
            block = True
            warnings.append("4hが上昇トレンド")

        # --- 短期警告 ---
        if direction == "buy" and "downtrend" in phase_15m:
            warnings.append("15mが逆行中")

        if direction == "sell" and "uptrend" in phase_15m:
            warnings.append("15mが逆行中")

    return {
        "llm_call_allowed": llm_call_allowed,
        "block": block,
        "warnings": warnings
    }


def analyze_ai_input(ai_input, symbol, asset_type, latest_price, llm_result=None):
    timeframes = ai_input.get("timeframes", {})
    direction = llm_result.get("direction") if llm_result else None

    return evaluate_technical_risk(timeframes, direction)
