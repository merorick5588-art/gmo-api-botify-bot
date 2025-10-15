# analyze_ohlcv.py
import os
import json
from openai import OpenAI

def analyze_ai_input(ai_input, symbol, asset_type, latest_price, model_name="gpt-4o-mini"):
    """
    ai_input: dict (ai_input.json の内容)
    symbol: "USD/JPY" など
    asset_type: "forex" or "crypto"
    latest_price: float, 最新価格（※現在は使わず、Bid/AskからAIが計算）
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

    # === 最新レート ===
    bid = ai_input.get("latest_rate", {}).get("bid", latest_price)
    ask = ai_input.get("latest_rate", {}).get("ask", latest_price)

    # 資産タイプ別の参考スケール
    if asset_type == "crypto":
        scale_hint = "通常1日の変動はおよそ ±5〜10% 程度"
    else:  # forex
        scale_hint = "通常1日の変動はおよそ ±0.5〜1.5% 程度"

    # === GPTプロンプト ===
    prompt = f"""
あなたはプロのトレーダーです。
以下は {symbol} の最新データです。

直近ローソク足（新しい順）:
{json.dumps(recent_ohlc, ensure_ascii=False)}

特徴量サマリ:
{json.dumps(features_summary, ensure_ascii=False)}

最新レート:
Bid={bid}, Ask={ask}

参考: {scale_hint}

タスク:
1. 今後の1~4時間の上昇・下落方向を -1～1 (小数点第2位まで)の範囲で評価せよ
   - +1 = 上昇確率100%（強い上昇トレンド予想）
   -  0 = 中立（上昇・下落が拮抗）
   - -1 = 下落確率100%（強い下落トレンド予想）
   この値を "trend_score" として出力すること。
2. 押し目買い・戻り売りを意識したIFD-OCO注文案を作成
   - 買いの場合("trend_score">0)はAskを基準にEntryを設定
   - 売りの場合("trend_score"<0)はBidを基準にEntryを設定
   - stop_loss, take_profit の水準は上記の変動レンジを考慮して自由に設計
3. Low/Medium/Highはスキャルピング・デイトレード・スイングに対応
出力形式(JSON):
{{
  "trend_score": float,
  "direction": "buy" or "sell",
  "ifd_oco": [
    {{"risk": "Low", "entry": float, "stop_loss": float, "take_profit": float}},
    {{"risk": "Medium", "entry": float, "stop_loss": float, "take_profit": float}},
    {{"risk": "High", "entry": float, "stop_loss": float, "take_profit": float}}
  ]
}}
必須条件: JSON形式のみで出力してください。
"""

    kwargs = {"model": model_name, "messages": [{"role": "user", "content": prompt}]}

    # gpt-5-mini 系は temperature を受け付けない
    if not model_name.startswith("gpt-5"):
        kwargs["temperature"] = 0.7

    response = client.chat.completions.create(**kwargs)

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

    # === trend_scoreから確率を算出 ===
    trend_score = result.get("trend_score", 0)

    if trend_score >= 0:
        up_prob = trend_score
        down_prob = 0.0
    else:
        up_prob = 0.0
        down_prob = abs(trend_score)

    result["up_probability"] = up_prob
    result["down_probability"] = down_prob
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

    latest_price = args.latest_price or 150.0

    result = analyze_ai_input(ai_input, args.symbol, args.asset_type, latest_price, model_name=args.model)
    print(json.dumps(result, indent=2, ensure_ascii=False))
