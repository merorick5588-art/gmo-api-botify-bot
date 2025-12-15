# notify_discord_all.py
import os
import json
import pandas as pd
from datetime import datetime
from zoneinfo import ZoneInfo
import requests
import argparse
from analyze_ohlcv import analyze_ai_input as analyze_ai
from analyze_technical import analyze_ai_input as analyze_tech

# ==== Discord Webhook設定 ====
DISCORD_WEBHOOKS = {
    "forex": {
        "main": os.environ.get("DISCORD_FOREX_MAIN"),
        "other": os.environ.get("DISCORD_FOREX_OTHER")
    },
    "crypto": {
        "main": os.environ.get("DISCORD_CRYPTO_MAIN"),
        "other": os.environ.get("DISCORD_CRYPTO_OTHER")
    }
}

# ==== Discord送信関数 ====
def send_discord(embed, webhook_url):
    if not webhook_url:
        print("Webhook URLが未設定です")
        return
    payload = {"embeds": [embed]}
    try:
        response = requests.post(webhook_url, json=payload)
        if response.status_code != 204:
            print(f"Discord通知失敗: {response.status_code}, {response.text}")
    except Exception as e:
        print(f"Discord通知例外: {e}")

# ==== Embed作成関数 ====
def create_embed(symbol, ai_result, tech_result, latest_price):
    """
    Discord Embed 作成
    """
    # AI判定確率（パーセント表示）
    ai_up_pct = round(ai_result["up_probability"] * 100)
    ai_down_pct = round(ai_result["down_probability"] * 100)
    main_label = "上昇確率" if ai_up_pct >= ai_down_pct else "下落確率"
    main_value = max(ai_up_pct, ai_down_pct)

    # テクニカル判定確率（パーセント表示）
    tech_up_pct = round(tech_result["up_probability"] * 100)
    tech_down_pct = round(tech_result["down_probability"] * 100)
    tech_label = "上昇確率" if tech_up_pct >= tech_down_pct else "下落確率"
    tech_value = max(tech_up_pct, tech_down_pct)

    title_icon = "📈" if main_label == "上昇確率" else "📉"
    title = f"{title_icon} シグナル通知 — {symbol}"
    description = "=============================="

    # フィールド構築
    fields = [
        {"name": "判定", "value": f"{main_label} {main_value}% (テクニカル: {tech_label} {tech_value}%)", "inline": False}
    ]

    # OCO情報
    for oco in ai_result["ifd_oco"]:
        fields.append({
            "name": f"推奨 ({oco['risk']})",
            "value": f"指値: {oco['entry']:.5f}\n利確: {oco['take_profit']:.5f}\n損切: {oco['stop_loss']:.5f}"
        })

    # JST受信時刻
    jst_now = datetime.now(ZoneInfo("Asia/Tokyo")).strftime('%Y-%m-%d %H:%M:%S JST')

    embed = {
        "title": title,
        "description": description,
        "color": 3066993,
        "fields": fields,
        "footer": {"text": f"受信時刻: {jst_now}"}
    }
    return embed

# ==== メイン処理 ====
def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--ai_input_file", type=str, required=True, help="AI入力 JSONファイル")
    parser.add_argument("--latest_rates_file", type=str, required=True, help="最新レート CSV")
    parser.add_argument("--symbol", type=str, required=True, help="銘柄名")
    parser.add_argument("--asset_type", type=str, required=True, help="forex or crypto")
    parser.add_argument("--model", type=str, default="gpt-4o-mini", help="使用するOpenAIモデル")
    args = parser.parse_args()

    # JSON読み込み
    if not os.path.exists(args.ai_input_file):
        print(f"{args.ai_input_file} が見つかりません")
        return
    with open(args.ai_input_file, "r", encoding="utf-8") as f:
        ai_input = json.load(f)

    # 最新レート取得
    latest_df = pd.read_csv(args.latest_rates_file)
    rate_row = latest_df[latest_df["symbol"] == args.symbol]
    if rate_row.empty:
        print(f"{args.symbol} の最新レートが見つかりません")
        return
    bid = float(rate_row.iloc[0]["bid"])
    ask = float(rate_row.iloc[0]["ask"])
    latest_price = (ask + bid) / 2  # 中間値をベースにOCO作成

    # AI解析
    ai_result = analyze_ai(ai_input, args.symbol, args.asset_type, latest_price, model_name=args.model)

    # テクニカル解析
    tech_result = analyze_tech(ai_input, args.symbol, args.asset_type, latest_price)

    # Embed作成
    embed = create_embed(args.symbol, ai_result, tech_result, latest_price)

    # Discord通知
    # 確率が70%以上で一致していれば main webhook、それ以外は other
    ai_dir = "up" if ai_result["up_probability"] >= ai_result["down_probability"] else "down"
    tech_dir = "up" if tech_result["up_probability"] >= tech_result["down_probability"] else "down"
    ai_prob = max(ai_result["up_probability"], ai_result["down_probability"])
    tech_prob = max(tech_result["up_probability"], tech_result["down_probability"])

    webhook_url = DISCORD_WEBHOOKS[args.asset_type]["main"] if ai_dir == tech_dir and ai_prob >= 0.5 and tech_prob >= 0.5 else DISCORD_WEBHOOKS[args.asset_type]["other"]
    send_discord(embed, webhook_url)

if __name__ == "__main__":
    main()
