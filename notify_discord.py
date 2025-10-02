import os
import pandas as pd
import json
import requests
from datetime import datetime
from analyze_technical import analyze_ai_input as analyze_tech
from analyze_ohlcv import analyze_ai_input as analyze_ai  # import your AI function

DISCORD_WEBHOOKS = {
    "forex": {"main": "https://discord.com/api/webhooks/XXXX/XXXX",
              "other": "https://discord.com/api/webhooks/XXXX/XXXX"},
    "crypto": {"main": "https://discord.com/api/webhooks/YYYY/YYYY",
               "other": "https://discord.com/api/webhooks/YYYY/YYYY"}
}

def send_discord(embed, webhook_url):
    payload = {"embeds": [embed]}
    requests.post(webhook_url, json=payload)

def create_embed(symbol, direction, up_prob, down_prob, ifd_oco):
    title_icon = "📈" if direction == "up" else "📉"
    title = f"{title_icon} シグナル通知 — {symbol}"
    description = "=============================="
    fields = [
        {"name": "判定", "value": f"{'上昇' if direction=='up' else '下落'}確率 {up_prob*100:.0f if direction=='up' else down_prob*100:.0f}%", "inline": True},
        {"name": f"推奨 (Low)", "value": f"指値: {ifd_oco['low']['entry']:.3f}\n利確: {ifd_oco['low']['take_profit']:.3f}\n損切: {ifd_oco['low']['stop_loss']:.3f}"},
        {"name": f"推奨 (Medium)", "value": f"指値: {ifd_oco['medium']['entry']:.3f}\n利確: {ifd_oco['medium']['take_profit']:.3f}\n損切: {ifd_oco['medium']['stop_loss']:.3f}"},
        {"name": f"推奨 (High)", "value": f"指値: {ifd_oco['high']['entry']:.3f}\n利確: {ifd_oco['high']['take_profit']:.3f}\n損切: {ifd_oco['high']['stop_loss']:.3f}"}
    ]
    embed = {
        "title": title,
        "description": description,
        "color": 3066993,
        "fields": fields,
        "footer": {"text": f"受信時刻: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"}
    }
    return embed

def calc_ifd_oco(price, direction):
    """方向に応じてIFD-OCOを計算"""
    sign = 1 if direction == "up" else -1
    return {
        "low": {"entry": price*(1+sign*0.0015), "stop_loss": price*(1-sign*0.0015), "take_profit": price*(1+sign*0.003)},
        "medium": {"entry": price*(1+sign*0.0035), "stop_loss": price*(1-sign*0.0035), "take_profit": price*(1+sign*0.006)},
        "high": {"entry": price*(1+sign*0.0075), "stop_loss": price*(1-sign*0.0075), "take_profit": price*(1+sign*0.01)}
    }

def main():
    for f in os.listdir("."):
        if not f.endswith("_ai_input.csv"):
            continue
        df = pd.read_csv(f)
        symbol = f.replace("_ai_input.csv","")
        asset_type = pd.read_csv("symbols.csv").query(f"symbol=='{symbol}'")["type"].values[0]

        # AI解析
        ai_result = analyze_ai(f)
        # テクニカル解析
        tech_result = analyze_tech(f)

        # 方向確認
        ai_dir = "up" if ai_result["up_probability"] > ai_result["down_probability"] else "down"
        tech_dir = "up" if tech_result["up_probability"] > tech_result["down_probability"] else "down"

        # 確率70%以上
        ai_prob = max(ai_result["up_probability"], ai_result["down_probability"])
        tech_prob = max(tech_result["up_probability"], tech_result["down_probability"])

        if ai_dir == tech_dir and ai_prob >= 0.7 and tech_prob >= 0.7:
            # 条件一致 → main channel
            direction = ai_dir
            price = ai_result["ifd_oco"]["medium"]["entry"]
            ifd_oco = calc_ifd_oco(price, direction)
            embed = create_embed(symbol, direction, ai_result["up_probability"], ai_result["down_probability"], ifd_oco)
            send_discord(embed, DISCORD_WEBHOOKS[asset_type]["main"])
        else:
            # 条件不一致または確率不足 → other channel
            embed = {
                "title": f"⚠️ シグナル注意 — {symbol}",
                "description": f"AIとテクニカルで方向が一致しないか確率不足",
                "color": 15158332,
                "footer": {"text": f"受信時刻: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"}
            }
            send_discord(embed, DISCORD_WEBHOOKS[asset_type]["other"])

if __name__ == "__main__":
    main()
