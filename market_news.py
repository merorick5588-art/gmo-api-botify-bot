"""実行時に公開RSSを取得し、時刻・出典付きの短い市場材料を作る。"""
from __future__ import annotations

import hashlib
import html
import json
import re
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from urllib.parse import urlparse

import requests

from bot_config import (NEWS_CACHE_PATH, NEWS_MAX_AGE_HOURS,
                        NEWS_CACHE_MAX_AGE_MINUTES, NEWS_MAX_ITEMS_PER_SYMBOL)

SOURCES = (
    ("FXStreet", "https://www.fxstreet.com/rss/news", None),
    ("Fed-policy", "https://www.federalreserve.gov/feeds/press_monetary.xml", "USD"),
    ("Fed-speech", "https://www.federalreserve.gov/feeds/speeches.xml", "USD"),
    ("ECB", "https://www.ecb.europa.eu/rss/press.html", "EUR"),
    ("BOJ", "https://www.boj.or.jp/en/rss/whatsnew.xml", "JPY"),
)
CCY_TERMS = {
    "USD": r"\b(?:USD|dollar|Fed|FOMC|Federal Reserve|Treasury|US|U\.S\.)\b",
    "EUR": r"\b(?:EUR|euro|ECB|Eurozone|European Central Bank|France|Germany)\b",
    "JPY": r"\b(?:JPY|yen|BOJ|Japan|Bank of Japan)\b",
    "GBP": r"\b(?:GBP|sterling|pound|BOE|UK|Britain|Bank of England)\b",
    "AUD": r"\b(?:AUD|Aussie|Australia|RBA|China)\b",
    "NZD": r"\b(?:NZD|Kiwi|New Zealand|RBNZ|China)\b",
    "CAD": r"\b(?:CAD|Canada|Canadian|BOC|oil)\b",
    "CHF": r"\b(?:CHF|Swiss|franc|SNB)\b",
}
GLOBAL_TERMS = r"\b(?:war|tariffs?|geopolitic\w*|risk.off|risk.on|safe.haven|Middle East)\b"


def _text(value: str, limit: int) -> str:
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]*>", " ", value))).strip()[:limit]


def _utc(value: str) -> datetime:
    try:
        stamp = parsedate_to_datetime(value)
    except (ValueError, TypeError):
        stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if stamp.tzinfo is None:
        raise ValueError("news publication timezone missing")
    return stamp.astimezone(timezone.utc)


def _recent(rows: list[dict], now: datetime) -> list[dict]:
    lower = now - timedelta(hours=NEWS_MAX_AGE_HOURS)
    valid = []
    for row in rows:
        try:
            if lower <= _utc(row["published_at"]) <= now:
                valid.append(row)
        except (ValueError, TypeError, KeyError):
            continue
    return valid


def parse_feed(content: bytes, source: str, currency: str | None) -> list[dict]:
    root = ET.fromstring(content)
    rows = []
    dated = 0
    nodes = [node for node in root.iter() if node.tag.rsplit("}", 1)[-1] in {"item", "entry"}]
    for node in nodes:
        values = {child.tag.rsplit("}", 1)[-1]: "".join(child.itertext()).strip() for child in node}
        title = _text(values.get("title", ""), 160)
        link = values.get("link", "")
        if not link:
            link = next((c.get("href", "") for c in node if c.tag.rsplit("}", 1)[-1] == "link"
                         and c.get("rel", "alternate") == "alternate"), "")
        try:
            published = _utc(values.get("pubDate") or values.get("published") or values.get("date") or "")
        except (ValueError, TypeError, OverflowError):
            continue
        dated += 1
        # BOJのRSSはhttpリンク。公式ページへはhttpsで参照する。
        if link.startswith("http://"):
            link = "https://" + link[7:]
        if not title or urlparse(link).scheme != "https" or not urlparse(link).hostname:
            continue
        summary = _text(values.get("description", values.get("summary", "")), 180)
        combined = title + " " + summary
        currencies = [currency] if currency else [c for c, pattern in CCY_TERMS.items()
                                                  if re.search(pattern, combined, re.I)]
        if not currency and re.search(GLOBAL_TERMS, combined, re.I):
            currencies.append("ALL")
        if not currencies:
            continue
        rows.append({"id": hashlib.sha256(link.encode()).hexdigest()[:12],
                     "source": source, "title": title, "summary": summary if summary != title else "",
                     "url": link, "published_at": published.isoformat(), "currencies": currencies})
    if not nodes or not dated:
        raise ValueError("feed has no dated articles")
    return rows


def fetch_market_news(now: datetime | None = None, cache_path: Path = NEWS_CACHE_PATH,
                      sources=SOURCES) -> dict:
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    try:
        cached = json.loads(cache_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        cached = {}
    if not isinstance(cached, dict):
        cached = {}

    def fetch(source):
        name, url, currency = source
        try:
            response = requests.get(url, timeout=8, headers={"User-Agent": "fx-signal-bot/3.1"})
            response.raise_for_status()
            rows = _recent(parse_feed(response.content, name, currency), now)
            return name, {"status": "live", "fetched_at": now.isoformat(), "headlines": rows}
        except (requests.RequestException, ValueError, ET.ParseError) as exc:
            previous = cached.get(name, {})
            try:
                age = now - _utc(previous["fetched_at"])
                if timedelta(0) <= age <= timedelta(minutes=NEWS_CACHE_MAX_AGE_MINUTES):
                    return name, {"status": "cache", "fetched_at": previous["fetched_at"],
                                  "headlines": _recent(previous["headlines"], now)}
            except (KeyError, ValueError, TypeError):
                pass
            return name, {"status": "unavailable", "headlines": [], "error": type(exc).__name__}

    with ThreadPoolExecutor(max_workers=min(5, max(1, len(sources)))) as pool:
        fetched = dict(pool.map(fetch, sources))
    # 障害時のキャッシュ時刻を更新しない。
    save = {name: {"fetched_at": data["fetched_at"], "headlines": data["headlines"]}
            for name, data in fetched.items() if data["status"] in {"live", "cache"}}
    try:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = cache_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(save, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
        tmp.replace(cache_path)
    except OSError:
        pass
    return {"retrieved_at": now.isoformat(), "sources": [
        {"name": name, "status": data["status"], "fetched_at": data.get("fetched_at")}
        for name, data in fetched.items()], "headlines": [
        row for data in fetched.values() for row in data["headlines"]]}


def context_for_symbol(snapshot: dict, symbol: str) -> dict:
    currencies = {*symbol.split("_"), "ALL"}
    rows = sorted((r for r in snapshot["headlines"] if currencies.intersection(r["currencies"])),
                  key=lambda r: r["published_at"], reverse=True)
    # 同じ配信元だけで入力枠を埋めず、中央銀行の材料も残す。
    selected, seen, counts = [], set(), {}
    for row in rows:
        if row["id"] in seen or counts.get(row["source"], 0) >= 2:
            continue
        selected.append(row)
        seen.add(row["id"])
        counts[row["source"]] = counts.get(row["source"], 0) + 1
        if len(selected) >= NEWS_MAX_ITEMS_PER_SYMBOL:
            break
    return {"retrieved_at": snapshot["retrieved_at"], "sources": snapshot["sources"], "headlines": selected}
