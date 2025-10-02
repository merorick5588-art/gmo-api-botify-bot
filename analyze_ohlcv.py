import os
import pandas as pd
import json
import argparse
from openai import OpenAI

# ==== 引数処理 ====
parser = argparse.ArgumentParser()
parser.add_argument("csv_file", type=str, help="解析対象CSVファイル")
parser.add_argument("--model", type=str, default="gpt-3.5-turbo", help="使用するモデル名")
parser.add_argument("--symbol", type=str, required=True, help="銘柄名 (例: USD_JPY, BTC_JPY)")
parser.add_argument("--asset_type", type=str, required=True, choices=["forex","crypto"], help="資産タイプ: forexまたはcrypto")
args = parser.parse_args()

csv_file = args.csv_file
model_name = args.model
symbol = args.symbol.upper()
asset_type = args.asset_type.lower()

# ==== OpenAIクライアント設定 ====
client = OpenAI(api_key=os.environ.get("OPENAI_API_KEY"))

# ==== CSV読み込み ====
df = pd.read_csv(csv_file)

def filter_columns(df, prefix, asset_type):
    """接頭辞で列を抽出。forexの場合はVolume除外"""
    cols = [c for c in df.columns if c.startswith(prefix)]
    if asset_type == "forex":
        cols = [c for c in cols if "Volume" not in c]
    return df[cols].tail(1).to_dict(orient="records")[0]

# ==== 各時間足の最新データ抽出 ====
latest_15m = filter_columns(df, "15M_", asset_type)
latest_1h  = filter_columns(df, "1H_", asset_type)
latest_4h  = filter_columns(df, "4H_", asset_type)

# ==== プロンプト作成 ====
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
1. 各時間足の情報を統合して解析する
2. 補完情報（経済指標・ニュース・市場心理）も考慮する
3. 今後1時間の上昇確率(up_probability)と下落確率(down_probability)を0〜1の範囲で算出する
4. 上昇/下落トレンドに応じ、押し目買い・戻り売りを意識したIFD-OCO注文案を作る
5. IFD-OCOのリスクごとのレンジは以下とする:
   - Lowリスク (L)：現在価格の ±0.1〜0.2% 程度（スキャルピング向け、損小利小）
   - Mediumリスク (M)：現在価格の ±0.2〜0.5% 程度（デイトレード向け、標準的）
   - Highリスク (H)：現在価格の ±0.5〜1% 程度（スイング〜大胆トレード向け）
6. 上昇確率が高い場合は買い注文、下落確率が高い場合は売り注文となるようにする

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
必ずJSONで返してください。
"""


# ==== AI呼び出し ====
response = client.chat.completions.create(
    model=model_name,
    messages=[{"role": "user", "content": prompt}],
    temperature=0.7
)

# ==== AI出力取得（マルチモデル対応） ====
def get_ai_output(response):
    choice = response.choices[0]
    if hasattr(choice, "message") and hasattr(choice.message, "content"):
        return choice.message.content
    elif hasattr(choice, "text"):
        return choice.text
    return None

ai_output = get_ai_output(response)
if not ai_output:
    print("AI出力を取得できませんでした。")
    ai_output = ""

# ==== JSONとしてパース ====
try:
    result = json.loads(ai_output.strip("```json").strip("```").strip())
except json.JSONDecodeError:
    print("AIの出力をJSONに変換できませんでした:")
    print(ai_output)
    result = None

# ==== 確率正規化とOCO補正 ====
if result:
    # 確率を0~1、3桁に正規化
    up_prob = round(min(max(result.get("up_probability",0),0),1),3)
    down_prob = round(min(max(result.get("down_probability",0),0),1),3)
    result["up_probability"] = up_prob
    result["down_probability"] = down_prob

    # OCO補正
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
        # 買い注文は丸めるだけ
        for order in result["ifd_oco"]:
            order["entry"] = round(order["entry"],3)
            order["stop_loss"] = round(order["stop_loss"],3)
            order["take_profit"] = round(order["take_profit"],3)

# ==== 結果表示 ====
if result:
    print(json.dumps(result, indent=2, ensure_ascii=False))
