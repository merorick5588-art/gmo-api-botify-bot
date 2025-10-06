import os
import pandas as pd
import json
import requests
from datetime import datetime
from analyze_technical import analyze_ai_input as analyze_tech
from analyze_ohlcv import analyze_ai_input as analyze_ai  # import your AI function

# ==== 環境変数から Webhook を取得 ====
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
    payload = {"embeds": [embed]}
    requests.post(webhook_url, json=payload)

# 以下は元の create_embed、calc_ifd_oco 関数はそのまま
# ...

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
