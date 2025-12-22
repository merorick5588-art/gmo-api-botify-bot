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
    if not webhook_url:
        return
    requests.post(webhook_url, json={"embeds": [embed]})

def create_embed(symbol, ai_result, tech_result, latest_price):
    up = round(ai_result["up_probability"] * 100)
    down = round(ai_result["down_probability"] * 100)

    label = "上昇確率" if up >= down else "下落確率"
    value = max(up, down)
    icon = "📈" if label == "上昇確率" else "📉"

    fields = [{
        "name": "AI判定",
        "value": f"{label} {value}%",
        "inline": False
    }]

    if tech_result.get("warnings"):
        fields.append({
            "name": "⚠ テクニカル注意",
            "value": "\n".join(f"・{w}" for w in tech_result["warnings"]),
            "inline": False
        })

    for oco in ai_result["ifd_oco"]:
        fields.append({
            "name": f"IFD-OCO ({oco['risk']})",
            "value": (
                f"Entry:{oco['entry']:.5f}\n"
                f"TP:{oco['take_profit']:.5f}\n"
                f"SL:{oco['stop_loss']:.5f}"
            ),
            "inline": True
        })

    return {
        "title": f"{icon} シグナル通知 — {symbol}",
        "color": 3066993,
        "fields": fields,
        "footer": {
            "text": datetime.now(ZoneInfo("Asia/Tokyo")).strftime('%Y-%m-%d %H:%M:%S JST')
        }
    }

def create_skip_embed(symbol, reason):
    return {
        "title": f"⛔ 判定スキップ — {symbol}",
        "color": 15158332,
        "fields": [{
            "name": "理由",
            "value": reason,
            "inline": False
        }],
        "footer": {
            "text": datetime.now(ZoneInfo("Asia/Tokyo")).strftime('%Y-%m-%d %H:%M:%S JST')
        }
    }

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--ai_input_file", required=True)
    parser.add_argument("--latest_rates_file", required=True)
    parser.add_argument("--symbol", required=True)
    parser.add_argument("--asset_type", required=True)
    parser.add_argument("--model", default="gpt-5-mini")
    args = parser.parse_args()

    other_webhook = DISCORD_WEBHOOKS[args.asset_type]["other"]
    main_webhook = DISCORD_WEBHOOKS[args.asset_type]["main"]

    # ===== 入力ロード =====
    with open(args.ai_input_file, "r", encoding="utf-8") as f:
        ai_input = json.load(f)

    latest_df = pd.read_csv(args.latest_rates_file)
    row = latest_df[latest_df["symbol"] == args.symbol]
    if row.empty:
        embed = create_skip_embed(args.symbol, "最新レートが取得できませんでした")
        send_discord(embed, other_webhook)
        return

    latest_price = (row.iloc[0]["bid"] + row.iloc[0]["ask"]) / 2

    # ===== Stage1 : テクニカル事前判定 =====
    tech_pre = analyze_tech(ai_input, args.symbol, args.asset_type, latest_price)

    if not tech_pre["llm_call_allowed"]:
        embed = create_skip_embed(
            args.symbol,
            "Stage1 テクニカル条件により LLM 呼び出し不可"
        )
        send_discord(embed, other_webhook)
        return

    # ===== Stage2 : AI判定 =====
    ai_result = analyze_ai(
        ai_input,
        args.symbol,
        args.asset_type,
        latest_price,
        model_name=args.model
    )

    if not ai_result:
        embed = create_skip_embed(
            args.symbol,
            "AI分析結果が取得できませんでした"
        )
        send_discord(embed, other_webhook)
        return

    tech_post = analyze_tech(
        ai_input,
        args.symbol,
        args.asset_type,
        latest_price,
        ai_result
    )

    embed = create_embed(args.symbol, ai_result, tech_post, latest_price)

    # ===== ★ 履歴通知（必ず送る）★ =====
    send_discord(embed, other_webhook)

    # ===== block は main に送らない =====
    if tech_post["block"]:
        return

    prob = max(ai_result["up_probability"], ai_result["down_probability"])

    if prob < 0.5:
        return

    if prob >= 0.65:
        send_discord(embed, main_webhook)

if __name__ == "__main__":
    main()
