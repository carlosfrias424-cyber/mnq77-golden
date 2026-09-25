#!/usr/bin/env python3
"""Listen to Webull fills and copy each finished NQ order into the current Tradovate DEMO.

Same side, same contract count. No stop and no target. The next Webull fill is the exit.
Does not place Webull orders. Does not start the sniper. Demo only.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

from webull.trade.events.types import EVENT_TYPE_ORDER, ORDER_STATUS_CHANGED
from webull.trade.trade_events_client import TradeEventsClient

KEEP = {"FILLED", "FINAL_FILLED"}
LOG = Path(os.environ.get("WEBULL_LOG", "/tmp/webull_fills.jsonl"))
ROOT = Path("/home/administrator/.openclaw/workspace/mnq_hybrid")
PY = ROOT / ".venv/bin/python"
SUBMIT = ROOT / "apps/tradovate/place_struct40.py"
SENT: set[str] = set()


def emit(rec: dict) -> None:
    rec["ts"] = int(time.time() * 1000)
    line = json.dumps(rec, default=str)
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with LOG.open("a") as f:
        f.write(line + "\n")
    print(line, flush=True)


def need(name: str) -> str:
    val = os.environ.get(name, "").strip()
    if not val:
        print(f"missing {name}", file=sys.stderr)
        raise SystemExit(2)
    return val


def copy_sim(payload: dict) -> None:
    order_id = str(payload.get("order_id") or "")
    scene = str(payload.get("scene_type") or "")
    if scene != "FINAL_FILLED":
        emit({"event": "copy_skip", "reason": "wait_for_full_fill", "scene": scene, "order_id": order_id})
        return
    if not order_id or order_id in SENT:
        emit({"event": "copy_skip", "reason": "already_sent", "order_id": order_id})
        return
    symbol = str(payload.get("symbol") or "").upper()
    if "NQ" not in symbol:
        emit({"event": "copy_skip", "reason": "not_nq", "symbol": symbol})
        return
    raw_side = str(payload.get("side") or "").upper()
    side = "Buy" if raw_side == "BUY" else "Sell" if raw_side == "SELL" else ""
    if not side:
        emit({"event": "copy_skip", "reason": "bad_side", "side": raw_side, "order_id": order_id})
        return
    try:
        qty = int(float(payload.get("filled_qty") or payload.get("qty") or 0))
    except (TypeError, ValueError):
        qty = 0
    if qty < 1:
        emit({"event": "copy_skip", "reason": "no_qty", "order_id": order_id})
        return
    SENT.add(order_id)
    env = os.environ.copy()
    env["MNQ_PLAIN"] = "1"
    env["MNQ_SIDE"] = side
    env["MNQ_QTY"] = str(qty)
    env["TRADOVATE_ENV"] = "demo"
    env["TRADOVATE_SYMBOL"] = "MNQZ6"
    env.pop("MNQ_STOP_PTS", None)
    env.pop("MNQ_T40", None)
    if "live.tradovateapi.com" in env.get("TRADOVATE_BASE", ""):
        emit({"event": "copy_skip", "reason": "live_forbidden", "order_id": order_id})
        return
    try:
        r = subprocess.run(
            [str(PY), str(SUBMIT)], cwd=str(ROOT), env=env,
            capture_output=True, text=True, timeout=60,
        )
    except Exception as exc:
        emit({"event": "copy_fail", "order_id": order_id, "error": str(exc)[:300]})
        return
    emit({
        "event": "copy_sent",
        "order_id": order_id,
        "side": side,
        "qty": qty,
        "symbol": "MNQZ6",
        "rc": r.returncode,
        "out": (r.stdout or r.stderr or "")[-400:],
    })


def on_log(level, msg):
    text = str(msg)
    emit({"event": "log", "level": str(level), "msg": text[:500]})
    if "NumOfConnExceed" in text or "AuthError" in text:
        emit({"event": "stream_fail", "msg": text[:500]})


def on_event(event_type, subscribe_type, payload, raw):
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except Exception:
            emit({"event": "raw", "text": payload[:500]})
            return
    if not isinstance(payload, dict):
        emit({"event": "raw", "text": str(payload)[:500]})
        return
    scene = str(payload.get("scene_type") or "")
    if event_type == EVENT_TYPE_ORDER and subscribe_type == ORDER_STATUS_CHANGED and scene in KEEP:
        emit({
            "event": "fill",
            "scene": scene,
            "symbol": payload.get("symbol"),
            "side": payload.get("side"),
            "category": payload.get("category"),
            "qty": payload.get("filled_qty") or payload.get("qty"),
            "price": payload.get("filled_price"),
            "order_id": payload.get("order_id"),
            "filled_time": payload.get("filled_time"),
        })
        copy_sim(payload)
        return
    if scene or payload.get("order_status"):
        emit({
            "event": "order",
            "scene": scene,
            "status": payload.get("order_status"),
            "symbol": payload.get("symbol"),
            "side": payload.get("side"),
            "category": payload.get("category"),
        })


def main():
    key = need("WEBULL_APP_KEY")
    secret = need("WEBULL_APP_SECRET")
    account = need("WEBULL_ACCOUNT_ID")
    emit({"event": "subscribe", "account": account, "host": "events-api.webull.com"})
    client = TradeEventsClient(key, secret, "us")
    client.on_log = on_log
    client.on_events_message = on_event
    client.do_subscribe([account])


if __name__ == "__main__":
    main()
