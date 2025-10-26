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

    # === データ抽出 ===
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

    # === 資産タイプ別プロンプト ===
    if asset_type == "forex":
        scale_hint = "通常1日の変動は ±0.5〜1.5% 程度。金利動向・経済指標・地政学リスクの影響を受けやすい。"
        strategy_context = """
対象は外国為替（FX）です。
・短期では経済指標（雇用統計、CPI、FOMC発言など）が方向性を左右する。
・テクニカル指標（移動平均・RSI・MACD）とローソク足パターンを重視。
・円高/円安、ドル高/ドル安などの通貨強弱を前提に判断せよ。
"""
    else:
        scale_hint = "通常1日の変動は ±5〜10% 程度。ボラティリティが高く、BTC価格や投資家センチメントに影響されやすい。"
        strategy_context = """
対象は暗号資産（Crypto）です。
・BTCやETHの価格連動、アルトコイン間の相関、ハッシュレートやETFニュースに注目。
・テクニカル要因（ボラティリティ・出来高・RSI）を中心に分析。
・短期トレンドを重視し、オーバーシュートを前提としたトレード戦略を考える。
"""

    # === プロンプト生成 ===
    prompt = f"""
あなたはプロのトレーダー兼アナリストです。
以下は {symbol} の最新データです。

直近ローソク足（新しい順）:
{json.dumps(recent_ohlc, ensure_ascii=False, indent=2)}

特徴量サマリ:
{json.dumps(features_summary, ensure_ascii=False, indent=2)}

最新レート:
Bid={bid}, Ask={ask}

{strategy_context}

参考変動レンジ: {scale_hint}

タスク:
1. 今後の1〜4時間の上昇・下落方向を -1〜1 (小数点第2位まで)で評価
   - +1 = 強い上昇傾向
   -  0 = 中立
   - -1 = 強い下落傾向
   この値を "trend_score" として出力。
2. IFD-OCO注文案を3種類作成：
   - "Low" = リスク低めの安全トレード
   - "Medium" = 通常リスク
   - "High" = ボラティリティを活かした攻めのトレード
   - trend_score>0 の場合はAskを基準にエントリー、<0 の場合はBidを基準にエントリー。
   - stop_loss / take_profit は上記変動レンジを考慮。
3. 出力は下記JSON形式で、コメントや説明文は一切含めない。

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
"""

    kwargs = {"model": model_name, "messages": [{"role": "user", "content": prompt}]}
    if not model_name.startswith("gpt-5"):
        kwargs["temperature"] = 0.7

    response = client.chat.completions.create(**kwargs)
    content = response.choices[0].message.content.strip()

    try:
        result = json.loads(content.strip("```json").strip("```").strip())
    except json.JSONDecodeError:
        print(f"AI出力のJSON変換に失敗しました:\n{content}")
        return None

    # === trend_scoreから確率換算 ===
    score = result.get("trend_score", 0)
    result["up_probability"] = max(score, 0)
    result["down_probability"] = abs(min(score, 0))

    return result


# テスト用実行
if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("ai_input_file", type=str)
    parser.add_argument("--symbol", type=str, required=True)
    parser.add_argument("--asset_type", type=str, required=True)
    parser.add_argument("--latest_price", type=float, default=None)
    parser.add_argument("--model", type=str, default="gpt-4o-mini")
    args = parser.parse_args()

    with open(args.ai_input_file, "r", encoding="utf-8") as f:
        ai_input = json.load(f)

    result = analyze_ai_input(ai_input, args.symbol, args.asset_type, args.latest_price or 150.0, args.model)
    print(json.dumps(result, indent=2, ensure_ascii=False))
