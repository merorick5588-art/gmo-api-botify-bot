# analyze_ohlcv.py
import os
import pandas as pd
import json
from openai import OpenAI

def analyze_ai_input(csv_file, symbol, asset_type, model_name="gpt-3.5-turbo"):
    client = OpenAI(api_key=os.environ.get("OPENAI_API_KEY"))

    df = pd.read_csv(csv_file)

    def filter_columns(df, prefix, asset_type):
        cols = [c for c in df.columns if c.startswith(prefix)]
        if asset_type == "forex":
            cols = [c for c in cols if "Volume" not in c]
        return df[cols].tail(1).to_dict(orient="records")[0]

    latest_15m = filter_columns(df, "15M_", asset_type)
    latest_1h  = filter_columns(df, "1H_", asset_type)
    latest_4h  = filter_columns(df, "4H_", asset_type)

    prompt = f"""
    あなたはプロのFXトレーダーです。
    以下は{symbol}の各時間足の最新データと特徴量です。
    
    15分足:
    {json.dumps(latest_15m, ensure_ascii=False)}
    1時間足:
    {json.dumps(latest_1h, ensure_ascii=False)}
    4時間足:
    {json.dumps(latest_4h, ensure_ascii=False)}
    
    タスク:
    1. 上昇確率(up_probability)と下落確率(down_probability)を0〜1で算出
    2. 押し目買い・戻り売りを意識したIFD-OCO注文案を作る
    3. リスクごとのレンジ:
       - Low: ±0.1〜0.2%
       - Medium: ±0.2〜0.5%
       - High: ±0.5〜1%
    出力形式(JSON):
    {{
        "up_probability": float,
        "down_probability": float,
        "ifd_oco": [
            {{"risk": "low", "entry": float, "stop_loss": float, "take_profit": float}},
            {{"risk": "medium", "entry": float, "stop_loss": float, "take_profit": float}},
            {{"risk": "high", "entry": float, "stop_loss": float, "take_profit": float}}
        ]
    }}
    必須条件:
     いついかなる時も必ずJSONで返し、余分な言葉は出力しないでください。
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
        print("AIの出力をJSONに変換できませんでした:")
        print(ai_output)
        return None

    # 確率正規化とOCO補正
    up_prob = round(min(max(result.get("up_probability",0),0),1),3)
    down_prob = round(min(max(result.get("down_probability",0),0),1),3)
    result["up_probability"] = up_prob
    result["down_probability"] = down_prob

    if up_prob < down_prob:
        # 売り注文に反転
        for order in result["ifd_oco"]:
            entry = round(order["entry"],3)
            sl = round(order["stop_loss"],3)
            tp = round(order["take_profit"],3)
            order["entry"] = entry
            order["stop_loss"] = tp
            order["take_profit"] = sl
    else:
        for order in result["ifd_oco"]:
            order["entry"] = round(order["entry"],3)
            order["stop_loss"] = round(order["stop_loss"],3)
            order["take_profit"] = round(order["take_profit"],3)

    return result

# このファイルを直接実行した場合はテスト出力
if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("csv_file")
    parser.add_argument("--symbol")
    parser.add_argument("--asset_type")
    parser.add_argument("--model", default="gpt-3.5-turbo")
    args = parser.parse_args()

    res = analyze_ai_input(args.csv_file, args.symbol, args.asset_type, args.model)
    print(json.dumps(res, indent=2, ensure_ascii=False))
