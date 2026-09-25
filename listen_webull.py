#!/usr/bin/env python3
"""Listen to Webull order fills. Does not place orders.

Uses a second app key. Do not put the firing bot's key here.
Production events host: events-api.webull.com
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

from webull.core.client import ApiClient
from webull.trade.events.types import EVENT_TYPE_ORDER, ORDER_STATUS_CHANGED
from webull.trade.trade_client import TradeClient
from webull.trade.trade_events_client import TradeEventsClient

KEEP = {"FILLED", "FINAL_FILLED"}
LOG = Path(os.environ.get("WEBULL_LOG", "/tmp/webull_fills.jsonl"))


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
    account = os.environ.get("WEBULL_ACCOUNT_ID", "").strip()
    api = ApiClient(key, secret, "us")
    res = TradeClient(api).account_v2.get_account_list()
    if res.status_code != 200:
        emit({"event": "account_list_fail", "status": res.status_code, "body": (res.text or "")[:300]})
        raise SystemExit(1)
    body = res.json()
    emit({"event": "account_list", "body": body})
    if not account:
        print("set WEBULL_ACCOUNT_ID from the account_list above", file=sys.stderr)
        raise SystemExit(2)
    emit({"event": "subscribe", "account": account, "host": "events-api.webull.com"})
    client = TradeEventsClient(key, secret, "us")
    client.on_log = on_log
    client.on_events_message = on_event
    client.do_subscribe([account])


if __name__ == "__main__":
    main()
