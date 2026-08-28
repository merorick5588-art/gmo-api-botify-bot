from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

from bot_config import (
    BASE_CAPITAL_JPY,
    MAX_CURRENCY_EXPOSURE_RISK,
    MAX_TOTAL_RISK_PCT,
    MIN_MARGIN_RATIO,
    RISK_PER_TRADE_PCT,
)
from symbol_config import split_symbol


@dataclass
class SizePlan:
    allowed: bool
    size: float
    estimated_loss_jpy: float | None
    risk_pct: float | None
    reason: str | None = None


def account_equity_jpy(assets: dict[str, Any] | None) -> float:
    if assets:
        try:
            equity = float(assets.get("equity"))
            if equity > 0:
                return equity
        except (TypeError, ValueError):
            pass
    return BASE_CAPITAL_JPY


def quote_to_jpy_rate(symbol: str, ticker: dict[str, dict[str, Any]]) -> float | None:
    _, quote = split_symbol(symbol)
    if quote == "JPY":
        return 1.0
    direct = ticker.get(f"{quote}_JPY")
    if direct:
        return (float(direct["bid"]) + float(direct["ask"])) / 2
    inverse = ticker.get(f"JPY_{quote}")
    if inverse:
        mid = (float(inverse["bid"]) + float(inverse["ask"])) / 2
        if mid > 0:
            return 1 / mid
    return None


def calculate_size(
    symbol: str,
    entry: float,
    stop_loss: float,
    equity_jpy: float,
    rule: dict[str, Any],
    ticker: dict[str, dict[str, Any]],
) -> SizePlan:
    distance = abs(float(entry) - float(stop_loss))
    if distance <= 0:
        return SizePlan(False, 0, None, None, "EntryとSLが同値")
    conv = quote_to_jpy_rate(symbol, ticker)
    if not conv:
        return SizePlan(False, 0, None, None, "JPY換算レートを取得できない")

    allowed_loss = equity_jpy * RISK_PER_TRADE_PCT / 100.0
    loss_per_unit_jpy = distance * conv
    raw_size = allowed_loss / loss_per_unit_jpy

    min_size = float(rule.get("minOpenOrderSize", 0) or 0)
    max_size = float(rule.get("maxOrderSize", raw_size) or raw_size)
    step = float(rule.get("sizeStep", 1) or 1)
    if step <= 0:
        step = 1
    size = math.floor(min(raw_size, max_size) / step) * step

    if size < min_size:
        min_loss = min_size * loss_per_unit_jpy
        return SizePlan(
            False,
            0,
            min_loss,
            (min_loss / equity_jpy * 100) if equity_jpy else None,
            "最小注文数量でも1トレード許容損失を超える",
        )

    loss = size * loss_per_unit_jpy
    return SizePlan(True, size, loss, loss / equity_jpy * 100 if equity_jpy else None)


def margin_ok(assets: dict[str, Any] | None) -> tuple[bool, str | None]:
    if not assets:
        return False, "口座情報を取得できない"
    try:
        ratio = float(assets.get("marginRatio"))
    except (TypeError, ValueError):
        return False, "証拠金維持率を取得できない"
    if ratio < MIN_MARGIN_RATIO:
        return False, f"証拠金維持率 {ratio:.1f}% < {MIN_MARGIN_RATIO:.0f}%"
    return True, None


def aggregate_currency_exposure(
    position_summary: list[dict[str, Any]],
) -> dict[str, float]:
    """数量ベースの相対露出。厳密なVaRではなく、集中チェック用。"""
    exposure: dict[str, float] = {}
    for row in position_summary:
        try:
            symbol = str(row["symbol"])
            base, quote = split_symbol(symbol)
            size = float(row.get("sumPositionSize", 0) or 0)
            side = str(row.get("side", "")).upper()
            sign = 1.0 if side == "BUY" else -1.0 if side == "SELL" else 0.0
            exposure[base] = exposure.get(base, 0.0) + sign * size
            exposure[quote] = exposure.get(quote, 0.0) - sign * size
        except Exception:
            continue
    return exposure


def projected_currency_risk_units(
    symbol: str,
    direction: str,
    new_risk_pct: float,
    existing_risk_units: dict[str, float],
) -> dict[str, float]:
    base, quote = split_symbol(symbol)
    sign = 1.0 if direction == "buy" else -1.0
    projected = dict(existing_risk_units)
    projected[base] = projected.get(base, 0.0) + sign * new_risk_pct
    projected[quote] = projected.get(quote, 0.0) - sign * new_risk_pct
    return projected


def exposure_risk_ok(projected: dict[str, float]) -> tuple[bool, str | None]:
    for currency, units in projected.items():
        if abs(units) > MAX_CURRENCY_EXPOSURE_RISK:
            return False, f"{currency}方向の集中リスク {units:+.2f}% が上限を超過"
    return True, None


def total_risk_ok(current_risk_pct: float, new_risk_pct: float) -> tuple[bool, str | None]:
    total = current_risk_pct + new_risk_pct
    if total > MAX_TOTAL_RISK_PCT:
        return False, f"口座合計リスク {total:.2f}% が上限 {MAX_TOTAL_RISK_PCT:.2f}% を超過"
    return True, None
