from __future__ import annotations

import argparse
import json
import math
import time
from datetime import datetime, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pandas as pd
import requests

from analyze_ohlcv import analyze_entry_batch, analyze_management_batch
from analyze_technical import post_validate_direction, stage1_filter
from bot_config import (
    DISCORD_FOREX_EVENT,
    DISCORD_FOREX_MAIN,
    DISCORD_FOREX_OTHER,
    ENTRY_QUALITY_THRESHOLD,
    ENTRY_SCORE_THRESHOLD,
    EVENT_FAIL_SAFE,
    EVENT_IMMINENT_MINUTES,
    EXECUTION_SYNC_INTERVAL_HOURS,
    PRIVATE_API_FAIL_SAFE,
    RISK_PER_TRADE_PCT,
    MIN_RR,
    SIGNAL_DEDUP_MINUTES,
    SYNC_EXECUTIONS,
)
from economic_calendar import (
    event_guard_for_symbol,
    fetch_calendar,
    newly_released_events,
    relevant_high_impact_events,
)
from gmo_client import GMOClient
from llm_config import DEFAULT_MODEL
from risk_engine import (
    account_equity_jpy,
    calculate_size,
    exposure_risk_ok,
    margin_ok,
    projected_currency_risk_units,
    quote_to_jpy_rate,
    total_risk_ok,
)
from state_db import StateDB
from symbol_config import load_symbols, split_symbol
from virtual_tracker import update_virtual_trades

JST = ZoneInfo("Asia/Tokyo")


def send_discord(embed: dict, webhook_url: str | None) -> bool:
    if not webhook_url:
        return False
    for attempt in range(3):
        try:
            resp = requests.post(webhook_url, json={"embeds": [embed]}, timeout=15)
            if resp.status_code == 429:
                try:
                    retry_after = float(resp.json().get("retry_after", 1.0))
                    # Discordは秒/ミリ秒表現が変わることがあるため極端な値を補正。
                    if retry_after > 100:
                        retry_after /= 1000.0
                except Exception:
                    retry_after = 1.0
                time.sleep(min(max(retry_after, 0.5), 10.0))
                continue
            resp.raise_for_status()
            return True
        except Exception as exc:
            if attempt == 2:
                print(f"Discord notify failed: {exc}")
                return False
            time.sleep(0.8 * (attempt + 1))
    return False


def _send_dedup(
    db: StateDB,
    key: str,
    dedup_minutes: int,
    embed: dict,
    webhook_url: str | None,
) -> bool:
    """重複抑制しつつ送信。失敗時は予約を解除して次回再試行可能にする。"""
    if not db.should_notify(key, dedup_minutes):
        return False
    if send_discord(embed, webhook_url):
        return True
    db.forget_notification(key)
    return False


def _footer(run_timestamp: str) -> dict:
    return {"text": run_timestamp}


def _fmt_price(value: float | None, tick_size: float | None = None) -> str:
    if value is None:
        return "-"
    if tick_size and tick_size > 0:
        decimals = max(0, -Decimal(str(tick_size)).normalize().as_tuple().exponent)
    else:
        decimals = 5
    return f"{float(value):.{decimals}f}"


def _round_tick(value: float, tick_size: float) -> float:
    if tick_size <= 0:
        return value
    v = Decimal(str(value))
    tick = Decimal(str(tick_size))
    return float((v / tick).quantize(Decimal("1"), rounding=ROUND_HALF_UP) * tick)


def _load_market_input(symbol: str) -> tuple[dict, dict] | None:
    ai_path = Path(f"{symbol}_ai_input.json")
    rate_path = Path(f"{symbol}_latest_rates.csv")
    if not ai_path.exists() or not rate_path.exists():
        return None
    try:
        ai = json.loads(ai_path.read_text(encoding="utf-8"))
        df = pd.read_csv(rate_path)
        row = df[df["symbol"] == symbol]
        if row.empty:
            return None
        r = row.iloc[0].to_dict()
        r["bid"] = float(r["bid"])
        r["ask"] = float(r["ask"])
        for key in ("tickSize", "minOpenOrderSize", "maxOrderSize", "sizeStep"):
            if key in r and pd.notna(r[key]):
                r[key] = float(r[key])
        return ai, r
    except Exception as exc:
        print(f"market input load failed {symbol}: {exc}")
        return None


def _group_by_symbol(rows: list[dict]) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {}
    for row in rows:
        symbol = str(row.get("symbol", ""))
        if symbol:
            out.setdefault(symbol, []).append(row)
    return out


def _account_snapshot(client: GMOClient) -> tuple[dict | None, list[dict], list[dict], list[dict], str | None]:
    if not client.private_available:
        return None, [], [], [], "GMO Private API Key/Secret未設定"
    try:
        return (
            client.assets(),
            client.active_orders(),
            client.open_positions(),
            client.position_summary(),
            None,
        )
    except Exception as exc:
        return None, [], [], [], str(exc)


def _protective_stops(position: dict, orders: list[dict]) -> list[dict]:
    pos_side = str(position.get("side", "")).upper()
    close_side = "SELL" if pos_side == "BUY" else "BUY"
    return [
        o for o in orders
        if str(o.get("settleType", "")).upper() == "CLOSE"
        and str(o.get("executionType", "")).upper() == "STOP"
        and str(o.get("side", "")).upper() == close_side
    ]


def _estimate_existing_risk(
    summaries: list[dict], orders: list[dict], ticker: dict[str, dict], equity: float
) -> tuple[float, dict[str, float], list[str]]:
    by_order = _group_by_symbol(orders)
    total_loss = 0.0
    currency_units: dict[str, float] = {}
    warnings: list[str] = []

    for pos in summaries:
        symbol = str(pos.get("symbol"))
        try:
            size = float(pos.get("sumPositionSize", 0) or 0)
            avg = float(pos.get("averagePositionRate", 0) or 0)
            side = str(pos.get("side", "")).upper()
        except (TypeError, ValueError):
            continue
        if size <= 0:
            continue
        stops = _protective_stops(pos, by_order.get(symbol, []))
        covered = sum(float(x.get("size", 0) or 0) for x in stops)
        conv = quote_to_jpy_rate(symbol, ticker)
        if conv is None or covered + 1e-9 < size:
            warnings.append(f"{symbol} {side}: 保護STOPが建玉全量をカバーしていない")
            risk_pct = RISK_PER_TRADE_PCT
        else:
            remaining = size
            risk_jpy = 0.0
            # より厳しいSTOPから割当てる。数量が多くても建玉分まで。
            stops_sorted = sorted(
                stops,
                key=lambda x: float(x.get("price", 0) or 0),
                reverse=(side == "BUY"),
            )
            for stop in stops_sorted:
                portion = min(remaining, float(stop.get("size", 0) or 0))
                stop_price = float(stop.get("price", 0) or 0)
                distance = max(0.0, avg - stop_price) if side == "BUY" else max(0.0, stop_price - avg)
                risk_jpy += portion * distance * conv
                remaining -= portion
                if remaining <= 0:
                    break
            total_loss += risk_jpy
            risk_pct = risk_jpy / equity * 100 if equity else RISK_PER_TRADE_PCT

        # 通貨集中は完全なVaRではなく「同一方向リスク単位」で管理。
        base, quote = split_symbol(symbol)
        unit = max(risk_pct, RISK_PER_TRADE_PCT * 0.5)
        sign = 1.0 if side == "BUY" else -1.0
        currency_units[base] = currency_units.get(base, 0.0) + sign * unit
        currency_units[quote] = currency_units.get(quote, 0.0) - sign * unit

    return (total_loss / equity * 100 if equity else 0.0), currency_units, warnings


def _classify_symbols(
    symbols: list[str],
    summaries: list[dict],
    orders: list[dict],
    positions: list[dict],
) -> tuple[list[str], list[dict], list[dict]]:
    ps = _group_by_symbol(summaries)
    od = _group_by_symbol(orders)
    raw_pos = _group_by_symbol(positions)
    flat: list[str] = []
    management: list[dict] = []
    manual: list[dict] = []

    for symbol in symbols:
        pos = ps.get(symbol, [])
        raw = raw_pos.get(symbol, [])
        ords = od.get(symbol, [])
        open_orders = [o for o in ords if str(o.get("settleType", "")).upper() == "OPEN"]
        close_orders = [o for o in ords if str(o.get("settleType", "")).upper() == "CLOSE"]

        # API取得の途中で約定が走った場合など、一覧とサマリーが食い違う時は安全側。
        if bool(pos) != bool(raw):
            manual.append({"symbol": symbol, "reason": "建玉一覧と建玉サマリーが不一致。約定直後の可能性があるため手動確認"})
            continue
        if len(pos) > 1:
            manual.append({"symbol": symbol, "reason": "同一銘柄でBUY/SELL両方向の建玉が存在"})
            continue

        if pos:
            if open_orders:
                manual.append({"symbol": symbol, "reason": "既存建玉に対する追加の新規注文が存在"})
                continue
            management.append({
                "symbol": symbol,
                "kind": "position",
                "position": pos[0],
                "position_count": len(raw),
                "orders": close_orders,
            })
            continue

        if open_orders:
            roots = {str(o.get("rootOrderId") or o.get("orderId")) for o in open_orders}
            if len(roots) > 1:
                manual.append({"symbol": symbol, "reason": "複数の独立した新規注文が存在"})
                continue
            # IFD-OCOでは建玉前でも同一rootの決済子注文が存在し得るため許容する。
            unrelated_close = [
                o for o in close_orders
                if str(o.get("rootOrderId") or o.get("orderId")) not in roots
            ]
            if unrelated_close:
                manual.append({"symbol": symbol, "reason": "新規注文と無関係な決済注文が存在"})
                continue
            management.append({"symbol": symbol, "kind": "order", "orders": ords})
            continue

        if close_orders:
            manual.append({"symbol": symbol, "reason": "建玉が無いのに決済注文だけが存在"})
            continue

        flat.append(symbol)
    return flat, management, manual


def _management_context(
    state: dict,
    ai: dict,
    rate: dict,
    events: list,
    now: datetime,
    db: StateDB,
) -> dict:
    symbol = state["symbol"]
    ctx: dict[str, Any] = {
        "prev_action": db.latest_decision_action(symbol, "MANAGEMENT"),
        "events": [],
    }
    for event in event_guard_for_symbol(symbol, events, now):
        ctx["events"].append({
            "ccy": event.currency,
            "title": event.title,
            "min": round((event.at - now).total_seconds() / 60),
        })

    if state.get("kind") == "position":
        pos = state.get("position") or {}
        side = str(pos.get("side", "")).upper()
        try:
            avg = float(pos.get("averagePositionRate", pos.get("price", 0)) or 0)
            current = float(rate["bid"] if side == "BUY" else rate["ask"])
            atr1h = float(ai.get("tf", {}).get("1h", {}).get("f", {}).get("atr", 0) or 0)
            if avg > 0 and atr1h > 0:
                signed_move = (current - avg) if side == "BUY" else (avg - current)
                ctx["move_atr_1h"] = round(signed_move / atr1h, 3)
        except (TypeError, ValueError, KeyError):
            pass
        stops = _protective_stops(pos, state.get("orders", []))
        try:
            stop_prices = [float(o["price"]) for o in stops if o.get("price") is not None]
            if stop_prices:
                ctx["current_stop"] = max(stop_prices) if side == "BUY" else min(stop_prices)
        except (TypeError, ValueError):
            pass
        ctx["position_count"] = int(state.get("position_count") or 1)
    return ctx

def _event_embed(event, now: datetime, released: bool, run_timestamp: str) -> dict:
    local = event.at.astimezone(JST)
    if released:
        title = f"🚨 指標発表 — {event.currency} {event.title}"
        value = f"Actual: {event.actual or '-'}\nForecast: {event.forecast or '-'}\nPrevious: {event.previous or '-'}"
        color = 15158332
    else:
        mins = max(0, round((event.at - now).total_seconds() / 60))
        title = f"⚠ 重要指標まで約{mins}分 — {event.currency}"
        value = f"{event.title}\n{local:%Y-%m-%d %H:%M} JST\nForecast: {event.forecast or '-'} / Previous: {event.previous or '-'}"
        color = 16753920
    return {"title": title, "color": color, "description": value, "footer": _footer(run_timestamp)}


def _skip_embed(symbol: str, reasons: list[str], run_timestamp: str) -> dict:
    return {
        "title": f"⛔ 新規Entry見送り — {symbol}",
        "color": 9807270,
        "description": "\n".join(f"・{x}" for x in reasons),
        "footer": _footer(run_timestamp),
    }




def _entry_plan_label(plan: str, direction: str) -> str:
    plan = str(plan or "").upper()
    direction = str(direction or "").lower()
    if plan == "PULLBACK_LIMIT":
        return "押し目買いLIMIT" if direction == "buy" else "戻り売りLIMIT"
    if plan == "BREAKOUT_STOP":
        return "上抜けSTOP" if direction == "buy" else "下抜けSTOP"
    if plan == "ENTER_NOW":
        return "現在値付近"
    return plan or "-"

def _entry_embed(decision: dict, rate: dict, account: dict | None, run_timestamp: str) -> dict:
    buy = decision["direction"] == "buy"
    tick = rate.get("tickSize")
    icon = "📈" if buy else "📉"
    invalidation = decision.get("trend_invalidation", decision.get("stop_loss"))
    fields = [
        {"name": "4〜12h予測 / Entry品質", "value": f"{decision['direction'].upper()}  score {abs(decision['trend_score']):.2f}\nEntry quality {decision['entry_quality']:.2f}", "inline": True},
        {"name": "推奨注文 / 約定値", "value": f"{_entry_plan_label(decision.get('entry_plan'), decision['direction'])}\n{_fmt_price(decision['entry'], tick)}", "inline": True},
        {"name": "トレンド崩壊逆指値 / 利確", "value": f"逆指値 {_fmt_price(invalidation, tick)}\nTP {_fmt_price(decision['take_profit'], tick)}\nRR {decision['rr']:.2f}", "inline": True},
        {"name": "推奨数量", "value": f"{decision.get('suggested_size', 0):,.0f} 通貨\n想定損失 ¥{decision.get('estimated_loss_jpy', 0):,.0f}", "inline": True},
        {"name": "4h regime", "value": str(decision.get("regime", "-")), "inline": True},
        {"name": "理由", "value": decision.get("reason") or "-", "inline": False},
    ]
    if account:
        fields.append({
            "name": "口座", "value": f"Equity ¥{float(account.get('equity', 0)):,.0f}\nMargin ratio {float(account.get('marginRatio', 0)):.1f}%", "inline": True
        })
    return {"title": f"{icon} NEW ENTRY候補 — {decision['symbol']}", "color": 3066993, "fields": fields, "footer": _footer(run_timestamp)}


def _management_requires_main(state: dict, result: dict) -> bool:
    """人が売買/注文変更/手動確認を行う必要がある管理判断だけMAINへ送る。"""
    action = str(result.get("action") or "").upper()
    if state.get("kind") == "position":
        return action in {"CLOSE", "TAKE_PARTIAL", "TIGHTEN_SL", "REVIEW_MANUALLY"}
    if state.get("kind") == "order":
        return action in {"CANCEL_ORDER", "REPRICE_ORDER", "REVIEW_MANUALLY"}
    return action not in {"", "HOLD", "KEEP_ORDER"}


def _management_embed(symbol: str, state: dict, result: dict, rate: dict, run_timestamp: str) -> dict:
    fields = [
        {"name": "状態", "value": state["kind"].upper(), "inline": True},
        {"name": "判断", "value": str(result.get("action")), "inline": True},
        {"name": "Confidence", "value": f"{float(result.get('confidence', 0)):.2f}", "inline": True},
    ]
    tick = rate.get("tickSize")
    if state.get("kind") == "order":
        open_orders = [o for o in state.get("orders", []) if str(o.get("settleType", "")).upper() == "OPEN"]
        current_prices = [float(o["price"]) for o in open_orders if o.get("price") not in (None, "")]
        current_text = ", ".join(_fmt_price(x, tick) for x in current_prices) if current_prices else "-"
        recommended = result.get("recommended_order_price")
        recommended_text = _fmt_price(float(recommended), tick) if recommended is not None else "なし"
        fields.append({"name": "現在注文 / 推奨約定値", "value": f"現在 {current_text}\n推奨 {recommended_text}", "inline": True})
    else:
        invalidation = result.get("trend_invalidation")
        current_stops = _protective_stops(state.get("position") or {}, state.get("orders", []))
        stop_prices = [float(o["price"]) for o in current_stops if o.get("price") not in (None, "")]
        current_stop_text = ", ".join(_fmt_price(x, tick) for x in stop_prices) if stop_prices else "なし"
        invalidation_text = _fmt_price(float(invalidation), tick) if invalidation is not None else "-"
        fields.append({"name": "現在逆指値 / トレンド崩壊", "value": f"現在 {current_stop_text}\n崩壊 {_fmt_price(float(invalidation), tick) if invalidation is not None else '-'}", "inline": True})
    if result.get("take_partial_pct") is not None:
        fields.append({"name": "部分利確", "value": f"{float(result['take_partial_pct']):.0f}%", "inline": True})
    fields.append({"name": "理由", "value": str(result.get("reason") or "-"), "inline": False})
    urgent = _management_requires_main(state, result)
    return {
        "title": f"{'🛡' if not urgent else '⚠'} POSITION/ORDER — {symbol}",
        "color": 3447003 if not urgent else 16753920,
        "fields": fields,
        "footer": _footer(run_timestamp),
    }


def _manual_embed(item: dict, run_timestamp: str) -> dict:
    return {
        "title": f"⚠ 手動確認 — {item['symbol']}",
        "color": 15105570,
        "description": item["reason"],
        "footer": _footer(run_timestamp),
    }


def _validate_management_result(state: dict, result: dict, rate: dict) -> dict:
    """AIは提案だけ。危険な逆指値拡大や注文種別と矛盾する約定値をPythonで拒否する。"""
    out = dict(result)
    tick = float(rate.get("tickSize") or 0.00001)

    if state["kind"] == "position":
        action = out.get("action")
        raw = out.get("trend_invalidation")
        if raw is None:
            if action in {"HOLD", "TIGHTEN_SL", "TAKE_PARTIAL"}:
                return {**out, "action": "REVIEW_MANUALLY", "reason": "保有継続判断だがトレンド崩壊逆指値が未提示"}
            return out
        try:
            proposed = _round_tick(float(raw), tick)
        except (TypeError, ValueError):
            return {**out, "action": "REVIEW_MANUALLY", "reason": "トレンド崩壊逆指値が不正"}
        out["trend_invalidation"] = proposed
        pos = state["position"]
        side = str(pos.get("side", "")).upper()
        current_price = float(rate["bid"] if side == "BUY" else rate["ask"])
        stops = _protective_stops(pos, state.get("orders", []))
        existing = [float(o.get("price")) for o in stops if o.get("price") is not None]

        if side == "BUY":
            if proposed >= current_price:
                return {**out, "action": "REVIEW_MANUALLY", "reason": "トレンド崩壊逆指値が現在Bid以上で不正"}
            if action == "TIGHTEN_SL" and existing and proposed < max(existing) - 1e-12:
                return {**out, "action": "REVIEW_MANUALLY", "reason": "既存逆指値を損失側へ広げる提案を拒否"}
        elif side == "SELL":
            if proposed <= current_price:
                return {**out, "action": "REVIEW_MANUALLY", "reason": "トレンド崩壊逆指値が現在Ask以下で不正"}
            if action == "TIGHTEN_SL" and existing and proposed > min(existing) + 1e-12:
                return {**out, "action": "REVIEW_MANUALLY", "reason": "既存逆指値を損失側へ広げる提案を拒否"}
        return out

    if state["kind"] == "order":
        action = out.get("action")
        recommended = out.get("recommended_order_price")
        if action in {"KEEP_ORDER", "REPRICE_ORDER"} and recommended is None:
            return {**out, "action": "REVIEW_MANUALLY", "reason": "注文継続判断だが推奨約定値が未提示"}
        if recommended is None:
            return out
        try:
            recommended = _round_tick(float(recommended), tick)
        except (TypeError, ValueError):
            return {**out, "action": "REVIEW_MANUALLY", "reason": "推奨約定値が不正"}
        out["recommended_order_price"] = recommended

        open_orders = [o for o in state.get("orders", []) if str(o.get("settleType", "")).upper() == "OPEN"]
        if not open_orders:
            return {**out, "action": "REVIEW_MANUALLY", "reason": "未約定OPEN注文を特定できない"}
        order = open_orders[0]
        side = str(order.get("side", "")).upper()
        execution_type = str(order.get("executionType", "")).upper()
        bid, ask = float(rate["bid"]), float(rate["ask"])
        try:
            atr15 = float(state.get("tf", {}).get("15m", {}).get("f", {}).get("atr", 0) or 0)
        except (TypeError, ValueError):
            atr15 = 0.0
        current_ref = ask if side == "BUY" else bid
        if atr15 > 0 and abs(recommended - current_ref) > atr15 * 2.0:
            return {**out, "action": "REVIEW_MANUALLY", "reason": "推奨約定値が現在値から2ATR超離れているため手動確認"}
        if execution_type == "LIMIT":
            if side == "BUY" and recommended > ask + tick:
                return {**out, "action": "REVIEW_MANUALLY", "reason": "BUY LIMITの推奨約定値が現在Askより上で注文種別と矛盾"}
            if side == "SELL" and recommended < bid - tick:
                return {**out, "action": "REVIEW_MANUALLY", "reason": "SELL LIMITの推奨約定値が現在Bidより下で注文種別と矛盾"}
        if execution_type == "STOP":
            if side == "BUY" and recommended < ask - tick:
                return {**out, "action": "REVIEW_MANUALLY", "reason": "BUY STOPの推奨約定値が現在Askより下で注文種別と矛盾"}
            if side == "SELL" and recommended > bid + tick:
                return {**out, "action": "REVIEW_MANUALLY", "reason": "SELL STOPの推奨約定値が現在Bidより上で注文種別と矛盾"}

        try:
            current_order_price = float(order.get("price"))
        except (TypeError, ValueError):
            current_order_price = None
        if current_order_price is not None:
            spread = max(0.0, ask - bid)
            tolerance = max(tick * 2, spread * 1.5)
            diff = abs(recommended - current_order_price)
            if action == "KEEP_ORDER" and diff > tolerance:
                out["action"] = "REPRICE_ORDER"
                out["reason"] = (str(out.get("reason") or "") + " / 現注文価格と推奨約定値の差が大きいため価格見直し").strip(" / ")
            elif action == "REPRICE_ORDER" and diff <= tolerance:
                out["action"] = "KEEP_ORDER"
                out["reason"] = (str(out.get("reason") or "") + " / 現注文価格は推奨約定値とほぼ一致").strip(" / ")
        return out

    return {**out, "action": "REVIEW_MANUALLY", "reason": "未知の管理状態"}


def _sync_executions(client: GMOClient, db: StateDB, symbols: list[str]) -> None:
    if not SYNC_EXECUTIONS or not client.private_available:
        return
    last = db.get_meta("last_execution_sync")
    if last:
        try:
            if datetime.now(timezone.utc) - datetime.fromisoformat(last) < timedelta(hours=EXECUTION_SYNC_INTERVAL_HOURS):
                return
        except ValueError:
            pass
    rows: list[dict] = []
    failed: list[str] = []
    for symbol in symbols:
        try:
            rows.extend(client.latest_executions(symbol, 100))
        except Exception as exc:
            failed.append(symbol)
            print(f"execution sync failed {symbol}: {exc}")
    inserted = db.save_executions(rows)
    # 一部でも失敗した場合は成功時刻を更新せず、次のcron実行で再試行する。
    # INSERT OR IGNOREなので成功済み銘柄を再取得してもDBは重複しない。
    if not failed:
        db.set_meta("last_execution_sync", datetime.now(timezone.utc).isoformat())
    print(f"GMO executions synced: new={inserted} failed={len(failed)}")


def run(symbols_file: str = "symbols.csv", model: str = DEFAULT_MODEL) -> None:
    now = datetime.now(timezone.utc)
    run_timestamp = now.astimezone(JST).strftime("%Y-%m-%d %H:%M:%S JST")
    symbols = load_symbols(symbols_file)
    db = StateDB()
    client = GMOClient()

    # 過去の仮想シグナルを先に評価。
    for done in update_virtual_trades(db):
        print(f"virtual trade closed: {done}")

    market: dict[str, tuple[dict, dict]] = {}
    for symbol in symbols:
        loaded = _load_market_input(symbol)
        if loaded:
            market[symbol] = loaded
        else:
            send_discord(_skip_embed(symbol, ["市場入力ファイル不足"], run_timestamp), DISCORD_FOREX_OTHER)

    # 無料・認証不要の経済カレンダー。ニュース本文や有料APIは使わない。
    events, calendar_status = fetch_calendar(now)
    currencies = {c for s in symbols for c in split_symbol(s)}
    if calendar_status["usable"]:
        for event in relevant_high_impact_events(events, currencies, now):
            if event.at < now:
                continue
            mins = (event.at - now).total_seconds() / 60
            if mins <= EVENT_IMMINENT_MINUTES:
                key = f"event-imminent:{event.event_id}"
                embed = _event_embed(event, now, False, run_timestamp)
                embed["title"] = embed["title"].replace("重要指標まで", "重要指標【直前】まで")
                _send_dedup(db, key, 7 * 24 * 60, embed, DISCORD_FOREX_EVENT)
            else:
                key = f"event-pre:{event.event_id}"
                _send_dedup(
                    db, key, 7 * 24 * 60,
                    _event_embed(event, now, False, run_timestamp),
                    DISCORD_FOREX_EVENT,
                )
        # 無料フィード側にActualが含まれる場合だけ発表後速報も出す。
        for event in newly_released_events(events, now):
            if event.currency in currencies or event.currency == "ALL":
                _send_dedup(
                    db, f"event-release:{event.event_id}", 7 * 24 * 60,
                    _event_embed(event, now, True, run_timestamp),
                    DISCORD_FOREX_EVENT,
                )
    else:
        print(f"economic calendar unavailable: {calendar_status}")

    assets, orders, positions, summaries, private_error = _account_snapshot(client)
    if private_error:
        print(f"private API unavailable: {private_error}")
    elif assets:
        # 年間収支/DDの検証用。参照値を保存するだけで売買判断には直接使わない。
        db.save_account_snapshot(assets, now.isoformat())
    _sync_executions(client, db, symbols)

    flat, management_states, manual_states = _classify_symbols(symbols, summaries, orders, positions)
    for item in manual_states:
        embed = _manual_embed(item, run_timestamp)
        send_discord(embed, DISCORD_FOREX_OTHER)
        _send_dedup(
            db, f"manual:{item['symbol']}:{item['reason']}", SIGNAL_DEDUP_MINUTES,
            embed, DISCORD_FOREX_MAIN,
        )

    # --- 既存建玉/注文管理: 新規Entryと別バッチ ---
    mgmt_items: list[dict] = []
    mgmt_state_map: dict[str, dict] = {}
    for state in management_states:
        symbol = state["symbol"]
        loaded = market.get(symbol)
        if not loaded:
            continue
        ai, rate = loaded
        item = {
            **state,
            "bid": rate["bid"],
            "ask": rate["ask"],
            "tf": ai.get("tf", {}),
            "ctx": _management_context(state, ai, rate, events if calendar_status["usable"] else [], now, db),
        }
        mgmt_items.append(item)
        mgmt_state_map[symbol] = state
    mgmt_results = analyze_management_batch(mgmt_items, model) if mgmt_items else {}
    for item in mgmt_items:
        symbol = item["symbol"]
        result = mgmt_results.get(symbol)
        if not result:
            continue
        state = mgmt_state_map[symbol]
        rate = market[symbol][1]
        result = _validate_management_result(item, result, rate)
        decision = {
            "created_at": now.isoformat(), "symbol": symbol, "decision_type": "MANAGEMENT",
            "action": result.get("action"), "reason": result.get("reason"),
            "regime": market[symbol][0].get("tf", {}).get("4h", {}).get("f", {}).get("reg"),
            "management": result,
        }
        db.save_decision(decision)
        embed = _management_embed(symbol, state, result, rate, run_timestamp)
        send_discord(embed, DISCORD_FOREX_OTHER)
        if _management_requires_main(state, result):
            key = f"mgmt:{symbol}:{result.get('action')}:{result.get('trend_invalidation')}:{result.get('recommended_order_price')}"
            _send_dedup(db, key, SIGNAL_DEDUP_MINUTES, embed, DISCORD_FOREX_MAIN)

    # --- 新規Entry ---
    hard_reasons: list[str] = []
    if PRIVATE_API_FAIL_SAFE and private_error:
        hard_reasons.append("GMO口座/建玉情報を取得できないため新規Entryを停止")
    m_ok, m_reason = margin_ok(assets)
    if not m_ok and PRIVATE_API_FAIL_SAFE:
        hard_reasons.append(m_reason or "証拠金状態不明")
    if EVENT_FAIL_SAFE and not calendar_status["usable"]:
        hard_reasons.append("重要指標カレンダーを取得できないため新規Entryを停止")

    # 最新tickerはJPY換算にも使う。失敗時はサイズ計算でWAITに落ちる。
    try:
        all_ticker = client.ticker()
        rules = client.symbols()
    except Exception as exc:
        print(f"GMO ticker/rules reload failed: {exc}")
        all_ticker, rules = {}, {}

    equity = account_equity_jpy(assets)
    current_risk_pct, currency_risk_units, protection_warnings = _estimate_existing_risk(
        summaries, orders, all_ticker, equity
    )
    if protection_warnings:
        hard_reasons.extend(protection_warnings)

    eligible: list[dict] = []
    pre_reasons: dict[str, list[str]] = {}
    for symbol in flat:
        loaded = market.get(symbol)
        reasons = list(hard_reasons)
        if not loaded:
            reasons.append("市場データ不足")
            pre_reasons[symbol] = reasons
            continue
        ai, rate = loaded
        blockers = event_guard_for_symbol(symbol, events, now) if calendar_status["usable"] else []
        if blockers:
            reasons.append("重要指標前後: " + ", ".join(f"{e.currency} {e.title}" for e in blockers[:3]))
        tech = stage1_filter(ai, rate["bid"], rate["ask"])
        reasons.extend(tech["stage1_reasons"])
        if reasons:
            pre_reasons[symbol] = reasons
            continue
        eligible.append({"symbol": symbol, "ai_input": ai, "bid": rate["bid"], "ask": rate["ask"]})

    for symbol, reasons in pre_reasons.items():
        print(f"Entry Stage1 skip {symbol}: " + " / ".join(reasons))
        send_discord(_skip_embed(symbol, reasons, run_timestamp), DISCORD_FOREX_OTHER)

    results = analyze_entry_batch(eligible, model) if eligible else {}
    candidates: list[dict[str, Any]] = []

    # まず各候補を単体検証。口座全体のリスク配分は品質順に後段で行う。
    for item in eligible:
        symbol = item["symbol"]
        result = results.get(symbol)
        if not result:
            send_discord(_skip_embed(symbol, ["AI分析結果を取得できない"], run_timestamp), DISCORD_FOREX_OTHER)
            continue

        ai, rate = market[symbol]
        tick = float(rate.get("tickSize") or rules.get(symbol, {}).get("tickSize") or 0.00001)
        for key in ("entry", "trend_invalidation", "take_profit"):
            result[key] = _round_tick(float(result[key]), tick)
        # 既存DB・数量計算・仮想追跡ではstop_loss列を利用。意味はトレンド崩壊逆指値。
        result["stop_loss"] = result["trend_invalidation"]

        direction = result["direction"]
        entry, sl, tp = result["entry"], result["trend_invalidation"], result["take_profit"]
        ordering_ok = (sl < entry < tp) if direction == "buy" else (tp < entry < sl)
        rr = abs(tp - entry) / abs(entry - sl) if abs(entry - sl) > 0 else 0
        result["rr"] = rr
        dir_ok, post_warnings = post_validate_direction(ai, direction)
        spread = max(0.0, float(rate["ask"]) - float(rate["bid"]))
        entry_plan = str(result.get("entry_plan") or "ENTER_NOW").upper()
        entry_mode = {
            "ENTER_NOW": "MARKET_LIKE",
            "PULLBACK_LIMIT": "PENDING_LIMIT",
            "BREAKOUT_STOP": "PENDING_STOP",
        }.get(entry_plan, "PENDING_LIMIT")

        decision = {
            **result,
            "created_at": now.isoformat(),
            "symbol": symbol,
            "decision_type": "ENTRY",
            "regime": ai.get("tf", {}).get("4h", {}).get("f", {}).get("reg"),
            "action": "WAIT",
            "suggested_size": 0,
            "estimated_loss_jpy": 0,
            "warnings": post_warnings,
            "spread": spread,
            "entry_mode": entry_mode,
        }

        reject: list[str] = []
        # tickSize丸め後もentry_planと現在Bid/Askの位置関係が保たれているか確認。
        if entry_plan == "PULLBACK_LIMIT":
            if direction == "buy" and not entry < float(rate["ask"]):
                reject.append("押し目買いLIMITの推奨約定値が現在Ask以上")
            if direction == "sell" and not entry > float(rate["bid"]):
                reject.append("戻り売りLIMITの推奨約定値が現在Bid以下")
        elif entry_plan == "BREAKOUT_STOP":
            if direction == "buy" and not entry > float(rate["ask"]):
                reject.append("上抜けSTOPの推奨約定値が現在Ask以下")
            if direction == "sell" and not entry < float(rate["bid"]):
                reject.append("下抜けSTOPの推奨約定値が現在Bid以上")
        if not ordering_ok:
            reject.append("tick丸め後のEntry/トレンド崩壊逆指値/TP整合性が不正")
        if rr < MIN_RR:
            reject.append(f"RR {rr:.2f} < {MIN_RR:.2f}")
        if not dir_ok:
            reject.extend(post_warnings)
        if abs(float(result["trend_score"])) < ENTRY_SCORE_THRESHOLD:
            reject.append(f"trend_score {abs(result['trend_score']):.2f} < {ENTRY_SCORE_THRESHOLD:.2f}")
        if float(result["entry_quality"]) < ENTRY_QUALITY_THRESHOLD:
            reject.append(f"entry_quality {result['entry_quality']:.2f} < {ENTRY_QUALITY_THRESHOLD:.2f}")

        rule = rules.get(symbol) or {
            "minOpenOrderSize": rate.get("minOpenOrderSize", 0),
            "maxOrderSize": rate.get("maxOrderSize", 0),
            "sizeStep": rate.get("sizeStep", 1),
        }
        size_plan = None
        if not reject:
            size_plan = calculate_size(symbol, entry, sl, equity, rule, all_ticker)
            if not size_plan.allowed:
                reject.append(size_plan.reason or "数量計算不可")
            else:
                decision["suggested_size"] = size_plan.size
                decision["estimated_loss_jpy"] = size_plan.estimated_loss_jpy

        if reject:
            decision["reason"] = (decision.get("reason") or "") + " / " + " / ".join(reject)
            db.save_decision(decision)
            send_discord(_skip_embed(symbol, reject, run_timestamp), DISCORD_FOREX_OTHER)
            continue

        candidates.append({"decision": decision, "rate": rate, "size_plan": size_plan})

    # 同一実行の候補同士も合計リスクへ加算する。品質の高い候補から限られたRisk Budgetを配る。
    candidates.sort(
        key=lambda x: (
            float(x["decision"].get("entry_quality", 0)),
            abs(float(x["decision"].get("trend_score", 0))),
            min(float(x["decision"].get("rr", 0)), 3.0),
        ),
        reverse=True,
    )
    allocated_risk_pct = current_risk_pct
    allocated_currency_units = dict(currency_risk_units)

    for candidate in candidates:
        decision = candidate["decision"]
        rate = candidate["rate"]
        size_plan = candidate["size_plan"]
        symbol = decision["symbol"]
        direction = decision["direction"]
        new_risk_pct = float(size_plan.risk_pct or 0)
        reject: list[str] = []

        ok_total, reason_total = total_risk_ok(allocated_risk_pct, new_risk_pct)
        if not ok_total:
            reject.append(reason_total or "合計リスク超過")
        projected = projected_currency_risk_units(
            symbol, direction, new_risk_pct, allocated_currency_units
        )
        ok_exp, reason_exp = exposure_risk_ok(projected)
        if not ok_exp:
            reject.append(reason_exp or "通貨集中リスク超過")

        if reject:
            decision["action"] = "WAIT"
            decision["reason"] = (decision.get("reason") or "") + " / " + " / ".join(reject)
            db.save_decision(decision)
            send_discord(_skip_embed(symbol, reject, run_timestamp), DISCORD_FOREX_OTHER)
            continue

        decision["action"] = "ENTER"
        allocated_risk_pct += new_risk_pct
        allocated_currency_units = projected
        decision_id = db.save_decision(decision)
        embed = _entry_embed(decision, rate, assets, run_timestamp)
        send_discord(embed, DISCORD_FOREX_OTHER)
        signal_key = f"entry:{symbol}:{direction}"
        if _send_dedup(db, signal_key, SIGNAL_DEDUP_MINUTES, embed, DISCORD_FOREX_MAIN):
            if not db.has_active_virtual_trade(symbol):
                db.create_virtual_trade(decision_id, decision)

    if not eligible:
        print("OpenAI Entry API call skipped: eligible=0")
        print(
            f"新規EntryのLLM対象銘柄なし "
            f"(flat={len(flat)}, management={len(management_states)}, manual={len(manual_states)}, skipped={len(pre_reasons)})"
        )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbols_file", default="symbols.csv")
    parser.add_argument("--model", default=DEFAULT_MODEL)
    args = parser.parse_args()
    run(args.symbols_file, args.model)
