import os
import feedparser
import openai
import datetime
import requests
import argparse
import pandas as pd

# ====== 設定 ======
NEWS_FEEDS = [
    "https://feeds.reuters.com/reuters/businessNews",
    "https://feeds.bloomberg.com/markets/news.rss",
    "https://feeds.bbci.co.uk/news/business/rss.xml",
]

openai.api_key = os.getenv("OPENAI_API_KEY")
DISCORD_WEBHOOK = os.getenv("DISCORD_FOREX_MAIN")

# ====== ニュース取得 ======
def fetch_news():
    news_items = []
    for url in NEWS_FEEDS:
        feed = feedparser.parse(url)
        for e in feed.entries[:8]:
            title = e.title
            published = getattr(e, "published", "")
            news_items.append(f"・{title}（{published}）")
    return "\n".join(news_items)

# ====== GPTによる分析 ======
def analyze_news(news_text: str, symbols: list[str]) -> str:
    today = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=9))).strftime("%Y-%m-%d（%a）")
    symbol_list = ", ".join(symbols)
    
    prompt = f"""
あなたは熟練した外国為替アナリストです。
以下の最新ニュースから、本日（{today}）の為替相場に影響しそうな重要イベント・発言・経済指標を3つ挙げてください。
対象は次の通貨ペアに関連するものに絞ってください：{symbol_list}

それぞれについて以下を日本語でまとめてください：
1. 予想時刻（日本時間 JST）
2. 内容の要約（1行）
3. 想定される影響（例：円高方向、ドル安方向、ユーロ買いなど）

ニュース一覧：
{news_text}
"""

    response = openai.ChatCompletion.create(
        model="gpt-5-mini",
        messages=[{"role": "user", "content": prompt}],
    )
    return response.choices[0].message["content"]

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
    analysis = analyze_news(news_text, forex_symbols)

    header = "🌅 **本日の為替注目ニュース (7:00 JST)**\n"
    send_discord(header + analysis)

if __name__ == "__main__":
    main()
