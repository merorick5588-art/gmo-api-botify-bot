# notify_discord.py
import os
import pandas as pd
import json
import requests
from datetime import datetime
from analyze_ohlcv import analyze_ai_input as analyze_ai
from analyze_technical import analyze_ai_input as analyze_tech
import argparse

# ==== Discord Webhook環境変数 ====
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

def send_discord(embed, webhook_url):
    if webhook_url is None:
        print("Webhook URLが未設定です")
        return
    payload = {"embeds": [embed]}
    try:
        response = requests.post(webhook_url, json=payload)
        if response.status_code != 204:
            print(f"Discord通知失敗: {response.status_code}, {response.text}")
    except Exception as e:
        print(f"Discord通知例外: {e}")

def create_embed(symbol, direction, up_prob, down_prob, ifd_oco):
    title_icon = "📈" if direction == "up" else "📉"
    title = f"{title_icon} シグナル通知 — {symbol}"
    description = "=============================="

    # 判定確率を安全に計算
    prob_value = up_prob if direction == "up" else down_prob
    prob_str = f"{prob_value*100:.0f}%"

    fields = [
        {"name": "判定", "value": f"{'上昇' if direction=='up' else '下落'}確率 {prob_str}", "inline": True},
        {"name": f"推奨 (Low)", "value": f"指値: {ifd_oco[0]['entry']:.3f}\n利確: {ifd_oco[0]['take_profit']:.3f}\n損切: {ifd_oco[0]['stop_loss']:.3f}"},
        {"name": f"推奨 (Medium)", "value": f"指値: {ifd_oco[1]['entry']:.3f}\n利確: {ifd_oco[1]['take_profit']:.3f}\n損切: {ifd_oco[1]['stop_loss']:.3f}"},
        {"name": f"推奨 (High)", "value": f"指値: {ifd_oco[2]['entry']:.3f}\n利確: {ifd_oco[2]['take_profit']:.3f}\n損切: {ifd_oco[2]['stop_loss']:.3f}"}
    ]
    embed = {
        "title": title,
        "description": description,
        "color": 3066993,
        "fields": fields,
        "footer": {"text": f"受信時刻: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"}
    }
    return embed

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("csv_file", type=str, help="_ai_input.csv ファイル")
    parser.add_argument("--symbol", type=str, required=True)
    parser.add_argument("--asset_type", type=str, required=True, choices=["forex","crypto"])
    args = parser.parse_args()

    csv_file = args.csv_file
    symbol = args.symbol.upper()
    asset_type = args.asset_type.lower()

    # AI解析
    ai_result = analyze_ai(csv_file, symbol, asset_type)
    # テクニカル解析
    tech_result = analyze_tech(csv_file)

    # 方向確認
    ai_dir = "up" if ai_result["up_probability"] > ai_result["down_probability"] else "down"
    tech_dir = "up" if tech_result["up_probability"] > tech_result["down_probability"] else "down"

    # 確率70%以上
    ai_prob = max(ai_result["up_probability"], ai_result["down_probability"])
    tech_prob = max(tech_result["up_probability"], tech_result["down_probability"])

    # Embed作成
    embed = create_embed(symbol, ai_dir, ai_result["up_probability"], ai_result["down_probability"], ai_result["ifd_oco"])

    if ai_dir == tech_dir and ai_prob >= 0.7 and tech_prob >= 0.7:
        # 条件一致 → main channel
        send_discord(embed, DISCORD_WEBHOOKS[asset_type]["main"])
    else:
        # 条件不一致または確率不足 → other channel
        send_discord(embed, DISCORD_WEBHOOKS[asset_type]["other"])

if __name__ == "__main__":
    main()
