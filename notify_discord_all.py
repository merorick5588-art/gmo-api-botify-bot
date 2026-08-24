# notify_discord_all.py
import os
import json
import argparse
from datetime import datetime
from zoneinfo import ZoneInfo

import pandas as pd
import requests

from analyze_ohlcv import analyze_ai_input, analyze_ai_inputs_batch
from analyze_technical import analyze_ai_input as analyze_tech
from llm_config import BATCH_ANALYSIS_ENABLED, DEFAULT_MODEL

DISCORD_WEBHOOKS = {
    "main": os.environ.get("DISCORD_FOREX_MAIN"),
    "other": os.environ.get("DISCORD_FOREX_OTHER"),
}


def send_discord(embed, webhook_url):
    if not webhook_url:
        return
    requests.post(webhook_url, json={"embeds": [embed]}, timeout=15).raise_for_status()


def create_embed(symbol, ai_result, tech_result, run_timestamp):
    score = float(ai_result.get("trend_score", 0))
    value = round(abs(score) * 100)
    is_buy = score > 0 or (score == 0 and ai_result.get("direction") == "buy")
    label = "上昇スコア" if is_buy else "下落スコア"
    icon = "📈" if is_buy else "📉"

    fields = [{"name": "AI判定", "value": f"{label} {value}/100", "inline": False}]

    if tech_result.get("warnings"):
        fields.append({
            "name": "⚠ テクニカル注意",
            "value": "\n".join(f"・{w}" for w in tech_result["warnings"]),
            "inline": False,
        })

    for oco in ai_result["ifd_oco"]:
        fields.append({
            "name": f"IFD-OCO ({oco['risk']})",
            "value": (
                f"Entry:{oco['entry']:.5f}\n"
                f"TP:{oco['take_profit']:.5f}\n"
                f"SL:{oco['stop_loss']:.5f}"
            ),
            "inline": True,
        })

    return {
        "title": f"{icon} シグナル通知 — {symbol}",
        "color": 3066993,
        "fields": fields,
        "footer": {
            "text": run_timestamp
        },
    }


def create_skip_embed(symbol, reasons, run_timestamp):
    reason_text = "\n".join(f"・{r}" for r in reasons) if reasons else "条件不一致"
    return {
        "title": f"⛔ 判定スキップ — {symbol}",
        "color": 15158332,
        "fields": [{"name": "Stage1 スキップ理由", "value": reason_text, "inline": False}],
        "footer": {
            "text": run_timestamp
        },
    }


def load_symbol_inputs(symbols_file, run_timestamp):
    df = pd.read_csv(symbols_file)
    symbols = df["symbol"].dropna().astype(str).tolist()
    eligible = []

    for symbol in symbols:
        ai_input_file = f"{symbol}_ai_input.json"
        latest_rates_file = f"{symbol}_latest_rates.csv"

        if not os.path.exists(ai_input_file) or not os.path.exists(latest_rates_file):
            send_discord(
                create_skip_embed(symbol, ["分析用ファイルまたは最新レートがありません"], run_timestamp),
                DISCORD_WEBHOOKS["other"],
            )
            continue

        with open(ai_input_file, "r", encoding="utf-8") as f:
            ai_input = json.load(f)

        latest_df = pd.read_csv(latest_rates_file)
        row = latest_df[latest_df["symbol"] == symbol]
        if row.empty:
            send_discord(
                create_skip_embed(symbol, ["最新レート取得失敗"], run_timestamp),
                DISCORD_WEBHOOKS["other"],
            )
            continue

        latest_bid = float(row.iloc[0]["bid"])
        latest_ask = float(row.iloc[0]["ask"])

        tech_pre = analyze_tech(ai_input)
        if not tech_pre["llm_call_allowed"]:
            send_discord(
                create_skip_embed(symbol, tech_pre.get("stage1_reasons", []), run_timestamp),
                DISCORD_WEBHOOKS["other"],
            )
            continue

        eligible.append({
            "symbol": symbol,
            "ai_input": ai_input,
            "bid": latest_bid,
            "ask": latest_ask,
        })

    return eligible



def notify_analysis_results(eligible, results, run_timestamp):
    """分析方式に関係なく、Discord通知を同一ルール・同一順序で処理する。"""
    for item in eligible:
        symbol = item["symbol"]
        ai_result = results.get(symbol) if results else None
        if not ai_result:
            send_discord(
                create_skip_embed(symbol, ["AI分析結果が取得できませんでした"], run_timestamp),
                DISCORD_WEBHOOKS["other"],
            )
            continue

        tech_post = analyze_tech(item["ai_input"], ai_result)
        embed = create_embed(symbol, ai_result, tech_post, run_timestamp)

        # otherは全分析結果の履歴。mainは最終条件を満たす強シグナルのみ。
        send_discord(embed, DISCORD_WEBHOOKS["other"])
        if not tech_post["block"] and abs(float(ai_result.get("trend_score", 0))) >= 0.65:
            send_discord(embed, DISCORD_WEBHOOKS["main"])

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbols_file", default="symbols.csv")
    parser.add_argument("--model", default=DEFAULT_MODEL)
    args = parser.parse_args()

    run_timestamp = datetime.now(ZoneInfo("Asia/Tokyo")).strftime("%Y-%m-%d %H:%M:%S JST")
    eligible = load_symbol_inputs(args.symbols_file, run_timestamp)
    if not eligible:
        print("Stage1通過銘柄なし。OpenAI APIは呼び出しません。")
        return

    if BATCH_ANALYSIS_ENABLED:
        print(f"OpenAI一括分析: {len(eligible)}銘柄")
        results = analyze_ai_inputs_batch(eligible, model_name=args.model)
    else:
        print(f"OpenAI単銘柄分析モード: {len(eligible)}銘柄")
        results = {}
        for item in eligible:
            result = analyze_ai_input(
                item["ai_input"],
                item["symbol"],
                model_name=args.model,
                latest_bid=item["bid"],
                latest_ask=item["ask"],
            )
            if result:
                results[item["symbol"]] = result

    notify_analysis_results(eligible, results, run_timestamp)


if __name__ == "__main__":
    main()
