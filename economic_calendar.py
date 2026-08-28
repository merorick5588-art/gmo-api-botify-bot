from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import requests

from bot_config import (
    CALENDAR_CACHE_MAX_AGE_HOURS,
    CALENDAR_CACHE_PATH,
    EVENT_POST_MINUTES,
    EVENT_PRE_MINUTES,
    EVENT_RELEASE_LOOKBACK_MINUTES,
    FOREX_FACTORY_CALENDAR_URL,
    ensure_state_dir,
)


@dataclass(frozen=True)
class EconomicEvent:
    title: str
    currency: str
    impact: str
    at: datetime
    forecast: str | None = None
    previous: str | None = None
    actual: str | None = None

    @property
    def event_id(self) -> str:
        return f"{self.currency}|{self.at.isoformat()}|{self.title}".lower()


def _parse_event(raw: dict[str, Any]) -> EconomicEvent | None:
    try:
        at = datetime.fromisoformat(str(raw.get("date")).replace("Z", "+00:00"))
        if at.tzinfo is None:
            at = at.replace(tzinfo=timezone.utc)
        at = at.astimezone(timezone.utc)
        currency = str(raw.get("country") or raw.get("currency") or "").strip().upper()
        title = str(raw.get("title") or raw.get("event") or "").strip()
        impact = str(raw.get("impact") or "").strip().title()
        if not currency or not title:
            return None
        return EconomicEvent(
            title=title,
            currency=currency,
            impact=impact,
            at=at,
            forecast=_none_if_blank(raw.get("forecast")),
            previous=_none_if_blank(raw.get("previous")),
            actual=_none_if_blank(raw.get("actual")),
        )
    except Exception:
        return None


def _none_if_blank(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _load_cache(path: Path) -> tuple[list[dict[str, Any]], datetime | None]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        fetched_at = datetime.fromisoformat(str(payload["fetched_at"]).replace("Z", "+00:00")).astimezone(timezone.utc)
        rows = payload.get("events", [])
        if isinstance(rows, list):
            return rows, fetched_at
    except Exception:
        pass
    return [], None


def _save_cache(path: Path, rows: list[dict[str, Any]], now: datetime) -> None:
    ensure_state_dir()
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(
        json.dumps(
            {"fetched_at": now.isoformat(), "events": rows},
            ensure_ascii=False,
            separators=(",", ":"),
        ),
        encoding="utf-8",
    )
    tmp.replace(path)


def fetch_calendar(
    now: datetime | None = None,
    url: str = FOREX_FACTORY_CALENDAR_URL,
    cache_path: Path = CALENDAR_CACHE_PATH,
) -> tuple[list[EconomicEvent], dict[str, Any]]:
    """無料・認証不要の週間JSONを取得。失敗時は新しめのキャッシュへフォールバック。"""
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    rows: list[dict[str, Any]] = []
    source = "live"
    error: str | None = None

    try:
        resp = requests.get(
            url,
            timeout=15,
            headers={"User-Agent": "fx-signal-bot/2.0"},
        )
        resp.raise_for_status()
        payload = resp.json()
        if not isinstance(payload, list):
            raise ValueError("calendar response is not a list")

        # HTTP 200でもCDNが前週データを返すことがある。
        # 鮮度を確認してからキャッシュへ保存し、正常な旧キャッシュを壊さない。
        live_events = [e for raw in payload if (e := _parse_event(raw)) is not None]
        live_max_at = max((e.at for e in live_events), default=None)
        if not live_events or not live_max_at or live_max_at < now - timedelta(hours=18):
            raise ValueError("calendar live feed does not cover current time")

        rows = payload
        fetched_at = now
        _save_cache(cache_path, rows, now)
    except Exception as exc:
        error = str(exc)
        rows, fetched_at = _load_cache(cache_path)
        source = "cache" if rows else "unavailable"

    cache_age_hours = None
    usable = False
    if rows:
        if fetched_at:
            cache_age_hours = max(0.0, (now - fetched_at).total_seconds() / 3600)
        usable = source == "live" or (
            cache_age_hours is not None and cache_age_hours <= CALENDAR_CACHE_MAX_AGE_HOURS
        )

    events = [e for raw in rows if (e := _parse_event(raw)) is not None]
    # キャッシュ側も現在時刻をカバーしているか確認する。
    # 当日の最終イベント後まで即座に無効化しないよう18時間の猶予を持たせる。
    max_event_at = max((e.at for e in events), default=None)
    coverage_ok = bool(max_event_at and max_event_at >= now - timedelta(hours=18))
    usable = bool(usable and events and coverage_ok)
    if not coverage_ok and rows and error is None:
        error = "calendar feed does not cover current time"
    return events, {
        "usable": usable,
        "source": source,
        "error": error,
        "cache_age_hours": cache_age_hours,
        "fetched_at": fetched_at.isoformat() if fetched_at else None,
        "max_event_at": max_event_at.isoformat() if max_event_at else None,
    }


def relevant_high_impact_events(
    events: list[EconomicEvent],
    currencies: set[str],
    now: datetime | None = None,
) -> list[EconomicEvent]:
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    lower = now - timedelta(minutes=max(EVENT_POST_MINUTES, EVENT_RELEASE_LOOKBACK_MINUTES))
    upper = now + timedelta(minutes=EVENT_PRE_MINUTES)
    return sorted(
        [
            e
            for e in events
            if (e.currency in currencies or e.currency == "ALL")
            and e.impact.lower() == "high"
            and lower <= e.at <= upper
        ],
        key=lambda e: e.at,
    )


def event_guard_for_symbol(
    symbol: str,
    events: list[EconomicEvent],
    now: datetime | None = None,
) -> list[EconomicEvent]:
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    base, quote = symbol.split("_", 1)
    start = now - timedelta(minutes=EVENT_POST_MINUTES)
    end = now + timedelta(minutes=EVENT_PRE_MINUTES)
    return [
        e
        for e in events
        if e.impact.lower() == "high"
        and e.currency in {base, quote, "ALL"}
        and start <= e.at <= end
    ]


def newly_released_events(
    events: list[EconomicEvent], now: datetime | None = None
) -> list[EconomicEvent]:
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    start = now - timedelta(minutes=EVENT_RELEASE_LOOKBACK_MINUTES)
    return [
        e
        for e in events
        if e.impact.lower() == "high"
        and e.actual is not None
        and start <= e.at <= now
    ]
