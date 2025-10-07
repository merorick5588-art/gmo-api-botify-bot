# analyze_ohlcv.py
import os
import json
from openai import OpenAI

def analyze_ai_input(ai_input, symbol, asset_type, latest_price, model_name="gpt-4o-mini"):
    """
    ai_input: dict (ai_input.json の内容)
    symbol: "USD/JPY" など
    asset_type: "forex" or "crypto"
    latest_price: float, 最新価格
    """
    client = OpenAI(api_key=os.environ.get("OPENAI_API_KEY"))

    # JSON階層に合わせて取得
    recent_ohlc = {
        "15m": ai_input["timeframes"]["15m"]["recent_ohlc"],
        "1h": ai_input["timeframes"]["1h"]["recent_ohlc"],
        "4h": ai_input["timeframes"]["4h"]["recent_ohlc"]
    }
    features_summary = {
        "15m": ai_input["timeframes"]["15m"]["features_summary"],
        "1h": ai_input["timeframes"]["1h"]["features_summary"],
        "4h": ai_input["timeframes"]["4h"]["features_summary"]
    }

    # 資産タイプ別OCO幅 (Low=スキャル, Mid=デイトレ, High=スイング)
    if asset_type == "crypto":
        ranges = {"Low": 0.007, "Medium": 0.025, "High": 0.05}  # 0.7%, 2.5%, 5%
    else:  # forex
        ranges = {"Low": 0.002, "Medium": 0.006, "High": 0.012}  # 0.2%,0.6%,1.2%

    # === GPTプロンプト ===
    prompt = f"""
あなたはプロのトレーダーです。
以下は {symbol} の最新データです。

直近ローソク足（新しい順）:
{json.dumps(recent_ohlc, ensure_ascii=False)}

特徴量サマリ:
{json.dumps(features_summary, ensure_ascii=False)}

タスク:
1. 入力値をもとに今後の上昇確率(up_probability)と下落確率(down_probability)を0〜1で算出
2. 推奨IFD-OCO注文案を作成（entryは最新価格、利確/損切は比率ベース）
3. Low/Medium/Highはスキャルピング・デイトレード・スイング用
出力形式(JSON):
{{
  "up_probability": float,
  "down_probability": float,
  "ifd_oco": [
    {{"risk": "Low", "entry": float, "stop_loss": float, "take_profit": float}},
    {{"risk": "Medium", "entry": float, "stop_loss": float, "take_profit": float}},
    {{"risk": "High", "entry": float, "stop_loss": float, "take_profit": float}}
  ]
}}
必須条件: JSON形式のみで出力してください。
"""

    response = client.chat.completions.create(
        model=model_name,
        messages=[{"role": "user", "content": prompt}],
        temperature=0.7
    )

    choice = response.choices[0]
    ai_output = getattr(choice, "message", None)
    if ai_output:
        ai_output = getattr(choice.message, "content", "")
    else:
        ai_output = getattr(choice, "text", "")

    try:
        result = json.loads(ai_output.strip("```json").strip("```").strip())
    except json.JSONDecodeError:
        print(f"AIの出力をJSONに変換できませんでした:\n{ai_output}")
        return None

    # 確率を正規化して0〜1に収める
    up_prob = round(min(max(result.get("up_probability", 0), 0), 1), 3)
    down_prob = round(min(max(result.get("down_probability", 0), 0), 1), 3)
    result["up_probability"] = up_prob
    result["down_probability"] = down_prob

    # 最新価格を基準にIFD-OCO補正
    direction = "buy" if up_prob >= down_prob else "sell"
    adjusted_orders = []
    for order in result.get("ifd_oco", []):
        risk = order.get("risk", "Low")
        factor = ranges.get(risk.capitalize(), 0.01)
        entry = latest_price

        if direction == "buy":
            tp = entry * (1 + factor)
            sl = entry * (1 - factor)
        else:
            tp = entry * (1 - factor)
            sl = entry * (1 + factor)

        adjusted_orders.append({
            "risk": risk,
            "entry": round(entry, 3),
            "take_profit": round(tp, 3),
            "stop_loss": round(sl, 3)
        })

    result["ifd_oco"] = adjusted_orders
    result["direction"] = direction

    return result

# テスト用実行
if __name__ == "__main__":
    import argparse
    import pandas as pd

    parser = argparse.ArgumentParser()
    parser.add_argument("ai_input_file", type=str)
    parser.add_argument("--symbol", type=str, required=True)
    parser.add_argument("--asset_type", type=str, required=True)
    parser.add_argument("--latest_price", type=float, default=None)
    parser.add_argument("--model", type=str, default="gpt-4o-mini")
    args = parser.parse_args()

    with open(args.ai_input_file, "r", encoding="utf-8") as f:
        ai_input = json.load(f)

    latest_price = args.latest_price
    if latest_price is None:
        # CSVやAPIなどから取得する場合はここで代入
        latest_price = 150.0

    result = analyze_ai_input(ai_input, args.symbol, args.asset_type, latest_price, model_name=args.model)
    print(json.dumps(result, indent=2, ensure_ascii=False))
