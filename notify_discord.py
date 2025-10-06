# notify_discord_full.py
import os
import pandas as pd
import json
import requests
from datetime import datetime
from zoneinfo import ZoneInfo  # JST対応
from analyze_ohlcv import analyze_ai_input as analyze_ai
from analyze_technical import analyze_ai_input as analyze_tech
import argparse

# ==== 引数処理 ====
parser = argparse.ArgumentParser()
parser.add_argument("csv_file", help="_ai_input.csv ファイル")
parser.add_argument("latest_rates_file", help="最新レート CSV")
parser.add_argument("--symbol", required=True, help="銘柄名 (例: USD_JPY, BTC_USD)")
parser.add_argument("--asset_type", required=True, choices=["forex","crypto"], help="資産タイプ")
parser.add_argument("--model", default="gpt-3.5-turbo", help="使用するGPTモデル")
args = parser.parse_args()

csv_file = args.csv_file
latest_rates_file = args.latest_rates_file
symbol = args.symbol
asset_type = args.asset_type
model_name = args.model

# ==== Discord Webhook 環境変数 ====
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
    payload = {"embeds": [embed]}
    requests.post(webhook_url, json=payload)

# ==== Embed作成関数 ====
def create_embed(symbol, ai_up, ai_down, tech_up, tech_down, ifd_oco, direction):
    # AI方向
    if ai_up >= ai_down:
        direction_str = "上昇"
        main_prob = ai_up
    else:
        direction_str = "下落"
        main_prob = ai_down

    # テクニカル方向
    if tech_up >= tech_down:
        tech_dir_str = "上昇"
        tech_prob = tech_up
    else:
        tech_dir_str = "下落"
        tech_prob = tech_down

    description = "=============================="
    fields = [
        {
            "name": "判定",
            "value": f"{direction_str}確率 {main_prob*100:.0f}% (テクニカル: {tech_dir_str}確率 {tech_prob*100:.0f}%)",
            "inline": True
        },
        {"name": f"推奨 (Low)", "value": f"指値: {ifd_oco[0]['entry']:.3f}\n利確: {ifd_oco[0]['take_profit']:.3f}\n損切: {ifd_oco[0]['stop_loss']:.3f}"},
        {"name": f"推奨 (Medium)", "value": f"指値: {ifd_oco[1]['entry']:.3f}\n利確: {ifd_oco[1]['take_profit']:.3f}\n損切: {ifd_oco[1]['stop_loss']:.3f}"},
        {"name": f"推奨 (High)", "value": f"指値: {ifd_oco[2]['entry']:.3f}\n利確: {ifd_oco[2]['take_profit']:.3f}\n損切: {ifd_oco[2]['stop_loss']:.3f}"}
    ]

    # JST時刻を表示
    jst_now = datetime.now(ZoneInfo("Asia/Tokyo")).strftime("%Y-%m-%d %H:%M:%S")

    embed = {
        "title": f"{'📈' if direction=='buy' else '📉'} シグナル通知 — {symbol}",
        "description": description,
        "color": 3066993,
        "fields": fields,
        "footer": {"text": f"受信時刻: {jst_now} JST"}
    }
    return embed

# ==== メイン処理 ====
def main():
    # AI解析（最新レート補正済み）
    ai_result = analyze_ai(csv_file, symbol, asset_type, model_name=model_name)

    # テクニカル解析（最新レート補正済み）
    tech_result = analyze_tech(csv_file, latest_rates_file, symbol, asset_type)

    # 方向確認
    ai_dir = ai_result["direction"]
    tech_dir = tech_result["direction"]

    # 確率確認
    ai_prob = max(ai_result["up_probability"], ai_result["down_probability"])
    tech_prob = max(tech_result["up_probability"], tech_result["down_probability"])

    if ai_dir == tech_dir and ai_prob >= 0.7 and tech_prob >= 0.7:
        # 条件一致 → main channel
        embed = create_embed(
            symbol,
            ai_up=ai_result["up_probability"],
            ai_down=ai_result["down_probability"],
            tech_up=tech_result["up_probability"],
            tech_down=tech_result["down_probability"],
            ifd_oco=ai_result["ifd_oco"],
            direction=ai_dir
        )
        send_discord(embed, DISCORD_WEBHOOKS[asset_type]["main"])
    else:
        # 条件不一致または確率不足 → other channel
        embed = create_embed(
            symbol,
            ai_up=ai_result["up_probability"],
            ai_down=ai_result["down_probability"],
            tech_up=tech_result["up_probability"],
            tech_down=tech_result["down_probability"],
            ifd_oco=ai_result["ifd_oco"],
            direction=ai_dir
        )
        send_discord(embed, DISCORD_WEBHOOKS[asset_type]["other"])

if __name__ == "__main__":
    main()
