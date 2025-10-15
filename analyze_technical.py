# ==== 簡易テクニカル解析関数 ====
def calc_trend_score(timeframes):
    """
    各時間足の特徴量をもとに -1〜1 の trend_score を計算
    """
    up_score = 0
    down_score = 0

    # 時間足ごとの重み
    weights = {"15m": 1, "1h": 3, "4h": 1}

    for tf, weight in weights.items():
        fs = timeframes.get(tf, {}).get("features_summary", {})

        # SMA
        if fs.get("sma20", 0) > fs.get("sma50", 0):
            up_score += 1 * weight
        else:
            down_score += 1 * weight

        # RSI
        rsi = fs.get("rsi14", 50)
        if rsi < 30:
            up_score += 1 * weight
        elif rsi > 70:
            down_score += 1 * weight

        # MACD
        if fs.get("macd", 0) > fs.get("macd_signal", 0):
            up_score += 0.5 * weight
        else:
            down_score += 0.5 * weight

    total = up_score + down_score
    if total == 0:
        return 0.0  # 中立

    # 上昇優勢なら正、下落優勢なら負（-1〜1に正規化）
    trend_score = round((up_score - down_score) / total, 3)
    return trend_score


# ==== IFD-OCO作成関数 ====
def create_ifd_oco(latest_price, trend_score, asset_type):
    side = "buy" if trend_score >= 0 else "sell"
    base_price = latest_price

    # 資産タイプ別OCO幅 (Low=スキャル, Mid=デイトレ, High=スイング)
    if asset_type == "crypto":
        ranges = [0.007, 0.025, 0.05]  # 0.7%, 2.5%, 5%
    else:
        ranges = [0.002, 0.006, 0.012]  # 0.2%, 0.6%, 1.2%

    ifd_oco = []
    for idx, factor in enumerate(ranges):
        risk_name = ["Low", "Medium", "High"][idx]
        if side == "buy":
            entry = base_price
            sl = entry * (1 - factor)
            tp = entry * (1 + factor)
        else:
            entry = base_price
            sl = entry * (1 + factor)
            tp = entry * (1 - factor)
        ifd_oco.append({
            "risk": risk_name,
            "entry": round(entry, 3),
            "stop_loss": round(sl, 3),
            "take_profit": round(tp, 3)
        })
    return ifd_oco, side


# ==== JSON解析メイン ====
def analyze_ai_input(ai_input, symbol, asset_type, latest_price):
    timeframes = ai_input.get("timeframes", {})

    # -1〜1のスコアを計算
    trend_score = calc_trend_score(timeframes)

    # 確率を算出（0〜100%）
    if trend_score >= 0:
        up_prob = trend_score
        down_prob = 0.0
    else:
        up_prob = 0.0
        down_prob = abs(trend_score)

    ifd_oco, direction = create_ifd_oco(latest_price, trend_score, asset_type)

    result = {
        "up_probability": up_prob,
        "down_probability": down_prob,
        "ifd_oco": ifd_oco,
        "direction": direction
    }
    return result
