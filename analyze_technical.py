import pandas as pd
import sys
import json

# ==== 簡易テクニカル解析関数 ====
def calc_prob(latest_row):
    """
    SMA, RSI, MACDを組み合わせた簡易ルールで確率計算
    確率は0〜1に正規化
    """
    up_score = 0
    down_score = 0

    # 15分足
    if latest_row["15M_SMA_20"] > latest_row["15M_SMA_50"]:
        up_score += 2
    else:
        down_score += 2

    if latest_row["15M_RSI_14"] < 30:
        up_score += 1
    elif latest_row["15M_RSI_14"] > 70:
        down_score += 1

    if latest_row["15M_MACD"] > latest_row["15M_MACD_signal"]:
        up_score += 1
    else:
        down_score += 1

    # 1時間足
    if latest_row["1H_SMA_20"] > latest_row["1H_SMA_50"]:
        up_score += 3
    else:
        down_score += 3

    if latest_row["1H_RSI_14"] < 30:
        up_score += 1
    elif latest_row["1H_RSI_14"] > 70:
        down_score += 1

    if latest_row["1H_MACD"] > latest_row["1H_MACD_signal"]:
        up_score += 1
    else:
        down_score += 1

    # 4時間足
    if latest_row["4H_SMA_20"] > latest_row["4H_SMA_50"]:
        up_score += 1
    else:
        down_score += 1

    if latest_row["4H_RSI_14"] < 30:
        up_score += 0.5
    elif latest_row["4H_RSI_14"] > 70:
        down_score += 0.5

    if latest_row["4H_MACD"] > latest_row["4H_MACD_signal"]:
        up_score += 0.5
    else:
        down_score += 0.5

    total = up_score + down_score
    up_prob = round(up_score / total, 2)
    down_prob = round(down_score / total, 2)
    return up_prob, down_prob

# ==== IFD-OCO作成関数 ====
def create_ifd_oco(current_price, up_prob, down_prob):
    """
    確率に応じて買い/売り注文を作成
    Low: ±0.1〜0.2%
    Medium: ±0.2〜0.5%
    High: ±0.5〜1%
    """
    side = "buy" if up_prob >= down_prob else "sell"
    if side == "buy":
        sign = 1
    else:
        sign = -1

    ifd_oco = [
        {
            "risk": "low",
            "entry": round(current_price * (1 + sign * 0.0015), 3),
            "stop_loss": round(current_price * (1 - sign * 0.0015), 3),
            "take_profit": round(current_price * (1 + sign * 0.003), 3)
        },
        {
            "risk": "medium",
            "entry": round(current_price * (1 + sign * 0.0035), 3),
            "stop_loss": round(current_price * (1 - sign * 0.0035), 3),
            "take_profit": round(current_price * (1 + sign * 0.0075), 3)
        },
        {
            "risk": "high",
            "entry": round(current_price * (1 + sign * 0.0075), 3),
            "stop_loss": round(current_price * (1 - sign * 0.0075), 3),
            "take_profit": round(current_price * (1 + sign * 0.01), 3)
        }
    ]
    return ifd_oco

# ==== CSV解析メイン ====
def analyze_ai_input(csv_file):
    df = pd.read_csv(csv_file)
    latest_row = df.iloc[0]  # 最新行のみ

    up_prob, down_prob = calc_prob(latest_row)
    current_price = latest_row.get("1H_Close", latest_row.get("Close", 0))
    ifd_oco = create_ifd_oco(current_price, up_prob, down_prob)

    result = {
        "up_probability": up_prob,
        "down_probability": down_prob,
        "ifd_oco": ifd_oco
    }
    return result

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python analyze_technical.py _ai_input.csv")
        sys.exit(1)

    csv_file = sys.argv[1]
    result = analyze_ai_input(csv_file)
    print(json.dumps(result, indent=2))
