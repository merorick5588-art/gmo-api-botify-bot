import os
import feedparser
from openai import OpenAI
import datetime
import requests
import argparse
import pandas as pd
from dateutil import parser as date_parser

# ====== 設定 ======
NEWS_FEEDS = [
    "https://feeds.reuters.com/reuters/businessNews",
    "https://feeds.bloomberg.com/markets/news.rss",
    "https://feeds.bbci.co.uk/news/business/rss.xml",
]

client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
DISCORD_WEBHOOK = os.getenv("DISCORD_FOREX_MAIN")

# ====== ニュース取得 ======
def fetch_news():
    # JST現在時刻
    now_jst = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=9)))
    today_7am = now_jst.replace(hour=7, minute=0, second=0, microsecond=0)
    if now_jst.hour < 7:
        # 朝7時前に実行された場合 → 前日の7:00～今日7:00まで
        start_jst = today_7am - datetime.timedelta(days=1)
        end_jst = today_7am
    else:
        # 朝7時以降に実行された場合 → 今日7:00～明日7:00まで
        start_jst = today_7am
        end_jst = today_7am + datetime.timedelta(days=1)

    print(f"📅 対象期間: {start_jst.strftime('%Y-%m-%d %H:%M')} ～ {end_jst.strftime('%Y-%m-%d %H:%M')} JST")

    news_items = []
    for url in NEWS_FEEDS:
        feed = feedparser.parse(url)
        for e in feed.entries:
            published_raw = getattr(e, "published", "") or getattr(e, "updated", "")
            try:
                published_dt = date_parser.parse(published_raw)
                if published_dt.tzinfo is None:
                    published_dt = published_dt.replace(tzinfo=datetime.timezone.utc)
                published_jst = published_dt.astimezone(datetime.timezone(datetime.timedelta(hours=9)))
            except Exception:
                continue

            # 対象期間に含まれるニュースのみ抽出
            if not (start_jst <= published_jst < end_jst):
                continue

            title = e.title
            news_items.append(f"・{title}（{published_jst.strftime('%Y-%m-%d %H:%M')} JST）")

    if not news_items:
        return "該当する期間内のニュースはありません。"

    return "\n".join(news_items[:30])  # 多すぎる場合は上限30件


# ====== GPTによる分析 ======
def analyze_news(news_text: str, symbols: list[str], model: str) -> str:
    today = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=9))).strftime("%Y-%m-%d（%a）")
    symbol_list = ", ".join(symbols)

    prompt = f"""
あなたは熟練した外国為替アナリストです。
以下の最新ニュースから、本日（{today} 7:00 JST〜翌日7:00 JST）の為替相場に影響しそうな
重要イベント・発言・経済指標を3つ挙げてください。
対象は次の通貨ペアに関連するものに絞ってください：{symbol_list}

それぞれについて以下を日本語でまとめてください：
1. 予想時刻（日本時間 JST）
2. 内容の要約（1行）
3. 想定される影響（例：円高方向、ドル安方向、ユーロ買いなど）

ニュース一覧：
{news_text}
"""

    response = client.chat.completions.create(
        model=model,
        messages=[{"role": "user", "content": prompt}],
    )
    return response.choices[0].message.content


# ====== Discord送信 ======
def send_discord(message: str):
    if not DISCORD_WEBHOOK:
        print("DISCORD_FOREX_MAIN not set.")
        return
    payload = {"content": message}
    requests.post(DISCORD_WEBHOOK, json=payload)


# ====== メイン処理 ======
def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbols_file", required=True)
    parser.add_argument("--model", default="gpt-5-mini")
    args = parser.parse_args()

    df = pd.read_csv(args.symbols_file)
    forex_symbols = df[df["type"] == "forex"]["symbol"].tolist()

    news_text = fetch_news()
    analysis = analyze_news(news_text, forex_symbols, args.model)

    header = "🌅 **本日の為替注目ニュース (7:00 JST〜翌7:00 JST)**\n"
    send_discord(header + analysis)


if __name__ == "__main__":
    main()
