from __future__ import annotations

import hashlib
import hmac
import os
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import requests

PUBLIC_BASE = "https://forex-api.coin.z.com/public"
PRIVATE_BASE = "https://forex-api.coin.z.com/private"


class GMOAPIError(RuntimeError):
    pass


@dataclass
class GMOClient:
    api_key: str | None = None
    api_secret: str | None = None
    timeout: int = 15

    def __post_init__(self) -> None:
        if self.api_key is None:
            self.api_key = os.getenv("GMO_FX_API_KEY")
        if self.api_secret is None:
            self.api_secret = os.getenv("GMO_FX_API_SECRET")
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": "fx-signal-bot/2.0"})
        self._last_private_call_at = 0.0

    @property
    def private_available(self) -> bool:
        return bool(self.api_key and self.api_secret)

    def _public_get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        resp = self.session.get(PUBLIC_BASE + path, params=params, timeout=self.timeout)
        resp.raise_for_status()
        payload = resp.json()
        if payload.get("status") != 0:
            raise GMOAPIError(f"GMO Public API error {path}: {payload}")
        return payload.get("data")

    def _private_get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        if not self.private_available:
            raise GMOAPIError("GMO_FX_API_KEY / GMO_FX_API_SECRET が未設定です")
        # GMO Private GETは1秒6回上限。余裕を持って最大約5回/秒へ抑える。
        elapsed = time.monotonic() - self._last_private_call_at
        if elapsed < 0.20:
            time.sleep(0.20 - elapsed)
        timestamp = str(int(time.time() * 1000))
        text = timestamp + "GET" + path
        signature = hmac.new(
            self.api_secret.encode("ascii"), text.encode("ascii"), hashlib.sha256
        ).hexdigest()
        headers = {
            "API-KEY": self.api_key,
            "API-TIMESTAMP": timestamp,
            "API-SIGN": signature,
        }
        resp = self.session.get(
            PRIVATE_BASE + path,
            headers=headers,
            params=params,
            timeout=self.timeout,
        )
        self._last_private_call_at = time.monotonic()
        resp.raise_for_status()
        payload = resp.json()
        if payload.get("status") != 0:
            raise GMOAPIError(f"GMO Private API error {path}: {payload}")
        return payload.get("data")

    def service_status(self) -> str:
        data = self._public_get("/v1/status") or {}
        return str(data.get("status", "UNKNOWN"))

    def symbols(self) -> dict[str, dict[str, Any]]:
        data = self._public_get("/v1/symbols") or []
        return {str(row["symbol"]): row for row in data if row.get("symbol")}

    def ticker(self) -> dict[str, dict[str, Any]]:
        data = self._public_get("/v1/ticker") or []
        result: dict[str, dict[str, Any]] = {}
        for row in data:
            try:
                result[str(row["symbol"])] = {
                    **row,
                    "bid": float(row["bid"]),
                    "ask": float(row["ask"]),
                }
            except (KeyError, TypeError, ValueError):
                continue
        return result

    def klines(self, symbol: str, interval: str, date_value: str, price_type: str = "BID") -> list[dict]:
        data = self._public_get(
            "/v1/klines",
            {
                "symbol": symbol,
                "interval": interval,
                "date": date_value,
                "priceType": price_type,
            },
        )
        return data or []

    def assets(self) -> dict[str, Any]:
        return self._private_get("/v1/account/assets") or {}

    def _paginate_private(self, path: str, id_field: str, max_pages: int = 20) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        prev_id = None
        for _ in range(max_pages):
            params: dict[str, Any] = {"count": 100}
            if prev_id is not None:
                params["prevId"] = prev_id
            data = self._private_get(path, params) or {}
            page = list(data.get("list", []))
            if not page:
                break
            rows.extend(page)
            if len(page) < 100:
                break
            try:
                next_prev = min(int(x[id_field]) for x in page if x.get(id_field) is not None)
            except (ValueError, TypeError):
                break
            if prev_id == next_prev:
                break
            prev_id = next_prev
        return rows

    def active_orders(self) -> list[dict[str, Any]]:
        return self._paginate_private("/v1/activeOrders", "orderId")

    def open_positions(self) -> list[dict[str, Any]]:
        return self._paginate_private("/v1/openPositions", "positionId")

    def position_summary(self) -> list[dict[str, Any]]:
        data = self._private_get("/v1/positionSummary") or {}
        return list(data.get("list", []))

    def latest_executions(self, symbol: str, count: int = 100) -> list[dict[str, Any]]:
        data = self._private_get(
            "/v1/latestExecutions", {"symbol": symbol, "count": count}
        ) or {}
        return list(data.get("list", []))


def parse_api_timestamp(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)
    except ValueError:
        return None
