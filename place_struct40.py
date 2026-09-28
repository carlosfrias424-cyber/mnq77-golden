#!/usr/bin/env python3
"""DEMO fade book: 5 MNQ. Market entry. Stop and target are prices.

Stop is 20 points from the signal price (MNQ_ENTRY), not from the fill.
Target is 40 points from that same price. The two exit orders are OCO.
Demo URL only. Qty from MNQ_QTY env, default 5.
Symbol is hard-locked to MNQZ6 (Dec). MNQU is forbidden.
"""
from __future__ import annotations

import json
import os
import time
import uuid
from pathlib import Path

import requests

ROOT = Path("/home/administrator/.openclaw/workspace/mnq_hybrid")
OUT = ROOT / "logs/valor_atm.jsonl"
DEMO = "https://demo.tradovateapi.com/v1"
TP_PTS = 40.0
STOP_PTS = 20.0
QTY = 5
SYMBOL = "MNQZ6"
TICK = 0.25


def on_tick(px: float) -> float:
    return round(round(float(px) / TICK) * TICK, 2)


def envload() -> None:
    p = ROOT / ".env"
    if not p.exists():
        return
    for raw in p.read_text().splitlines():
        if not raw.strip() or raw.strip().startswith("#") or "=" not in raw:
            continue
        k, _, v = raw.partition("=")
        k, v = k.strip(), v.strip().strip('"').strip("'")
        if k and k not in os.environ:
            os.environ[k] = v


def log(**kw) -> None:
    rec = {"ts": int(time.time() * 1000), **kw}
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.open("a").write(json.dumps(rec, default=str) + "\n")
    print(json.dumps(rec, default=str), flush=True)


def flatten_mnq(base: str, headers: dict, account_id: int) -> int:
    pos_raw = requests.get(base + "/position/list", headers=headers, timeout=20).json()
    positions = pos_raw if isinstance(pos_raw, list) else []
    targets = []
    for p in positions:
        if int(p.get("accountId") or 0) != account_id:
            continue
        try:
            net = float(p.get("netPos") or 0)
        except (TypeError, ValueError):
            continue
        if net == 0:
            continue
        cid = int(p.get("contractId") or 0)
        if cid <= 0:
            continue
        item = requests.get(base + "/contract/item", headers=headers, params={"id": cid}, timeout=20).json()
        name = str((item or {}).get("name") or "").upper()
        if name != SYMBOL:
            log(event="flatten_skip", contract=name, net=net)
            continue
        targets.append(cid)
    if not targets:
        log(event="flat_already", symbol=SYMBOL)
        return 0
    rc = 0
    for cid in targets:
        body = {
            "accountId": account_id,
            "contractId": cid,
            "admin": False,
            "isAutomated": True,
        }
        r = requests.post(base + "/order/liquidateposition", headers=headers, json=body, timeout=20)
        try:
            result = r.json()
        except Exception:
            result = {"text": r.text[:400]}
        failed = r.status_code >= 300 or (isinstance(result, dict) and result.get("failureReason"))
        log(event="eod_flat", status=r.status_code, contractId=cid, result=result, symbol=SYMBOL)
        if failed:
            rc = 7
    return rc


def main() -> int:
    envload()
    if os.environ.get("TRADOVATE_ENV", "demo").lower() != "demo":
        log(event="refused", reason="not demo")
        return 2
    base = os.environ.get("TRADOVATE_BASE", DEMO).rstrip("/")
    if "live.tradovateapi.com" in base:
        log(event="refused", reason="live url forbidden")
        return 2

    side = os.environ.get("MNQ_SIDE", "")
    flatten = os.environ.get("MNQ_FLATTEN") == "1"
    if not flatten and side not in ("Buy", "Sell"):
        log(event="blocked", reason="MNQ_SIDE must be Buy or Sell", side=side)
        return 3

    try:
        qty = int(os.environ.get("MNQ_QTY") or QTY)
    except ValueError:
        qty = QTY
    if qty < 1:
        qty = QTY

    try:
        stop_pts = abs(float(os.environ.get("MNQ_STOP_PTS") or STOP_PTS))
    except ValueError:
        stop_pts = STOP_PTS
    try:
        tp_pts = abs(float(os.environ.get("MNQ_T40") or TP_PTS))
    except ValueError:
        tp_pts = TP_PTS
    if stop_pts < 0.25:
        stop_pts = STOP_PTS
    if tp_pts < 0.25:
        tp_pts = TP_PTS

    name = os.environ.get("TRADOVATE_NAME") or ""
    password = os.environ.get("TRADOVATE_PASSWORD") or ""
    if not name or not password:
        log(event="blocked", reason="missing creds")
        return 3

    auth_body = {
        "name": name,
        "password": password,
        "appId": os.environ.get("TRADOVATE_APP_ID", "MNQHybrid"),
        "appVersion": os.environ.get("TRADOVATE_APP_VERSION", "0.1"),
        "deviceId": os.environ.get("TRADOVATE_DEVICE_ID", str(uuid.uuid4())),
    }
    if os.environ.get("TRADOVATE_CID"):
        try:
            auth_body["cid"] = int(os.environ["TRADOVATE_CID"])
        except ValueError:
            auth_body["cid"] = os.environ["TRADOVATE_CID"]
    if os.environ.get("TRADOVATE_SEC"):
        auth_body["sec"] = os.environ["TRADOVATE_SEC"]

    auth = requests.post(base + "/auth/accesstokenrequest", json=auth_body, timeout=20).json()
    token = auth.get("accessToken")
    if not token:
        log(event="auth_fail", err=auth.get("errorText"))
        return 4
    h = {
        "Authorization": "Bearer " + token,
        "Accept": "application/json",
        "Content-Type": "application/json",
    }

    account_id = os.environ.get("TRADOVATE_ACCOUNT_ID")
    if not account_id:
        acc = requests.get(base + "/account/list", headers=h, timeout=20).json()
        pick = None
        if isinstance(acc, list):
            for a in acc:
                if "DEMO" in str(a.get("name") or "").upper():
                    pick = a
                    break
            if pick is None and acc:
                pick = acc[0]
        if not pick:
            log(event="blocked", reason="no account")
            return 5
        account_id = pick.get("id")
    account_id = int(account_id)
    spec = os.environ.get("TRADOVATE_ACCOUNT_SPEC") or name
    env_sym = (os.environ.get("TRADOVATE_SYMBOL") or "").upper()
    symbol = SYMBOL
    if env_sym.startswith("MNQU"):
        log(event="symbol_override", from_env=env_sym, to=symbol)

    if flatten:
        return flatten_mnq(base, h, account_id)

    if os.environ.get("MNQ_PLAIN") == "1":
        body = {
            "accountId": account_id,
            "accountSpec": spec,
            "symbol": symbol,
            "action": side,
            "orderQty": qty,
            "orderType": "Market",
            "timeInForce": "Day",
            "isAutomated": True,
        }
        r = requests.post(base + "/order/placeorder", headers=h, json=body, timeout=20)
        try:
            result = r.json()
        except Exception:
            result = {"text": r.text[:400]}
        log(event="plain_fire", status=r.status_code, side=side, symbol=symbol, qty=qty, result=result, note="webull_copy")
        return 0 if r.status_code < 300 else 7

    if os.environ.get("MNQ_ALLOW_ADD") != "1":
        pos_raw = requests.get(base + "/position/list", headers=h, timeout=20).json()
        positions = pos_raw if isinstance(pos_raw, list) else []
        net = 0
        for p in positions:
            if int(p.get("accountId") or 0) != account_id:
                continue
            try:
                net += int(float(p.get("netPos") or p.get("net") or 0))
            except (TypeError, ValueError):
                pass
        if net != 0:
            log(event="blocked", reason="already in position — flatten first", net=net)
            return 6

    raw_entry = os.environ.get("MNQ_ENTRY") or ""
    try:
        entry = float(raw_entry)
    except ValueError:
        entry = 0.0
    if entry <= 0:
        log(event="blocked", reason="missing MNQ_ENTRY")
        return 3
    entry = on_tick(entry)
    if side == "Buy":
        stop_px = on_tick(entry - stop_pts)
        tp_px = on_tick(entry + tp_pts)
        exit_side = "Sell"
    else:
        stop_px = on_tick(entry + stop_pts)
        tp_px = on_tick(entry - tp_pts)
        exit_side = "Buy"

    body = {
        "accountId": account_id,
        "accountSpec": spec,
        "symbol": symbol,
        "action": side,
        "orderQty": qty,
        "orderType": "Market",
        "timeInForce": "Day",
        "isAutomated": True,
        "bracket1": {
            "action": exit_side,
            "orderType": "Stop",
            "stopPrice": stop_px,
            "orderQty": qty,
            "timeInForce": "Day",
            "isAutomated": True,
        },
        "bracket2": {
            "action": exit_side,
            "orderType": "Limit",
            "price": tp_px,
            "orderQty": qty,
            "timeInForce": "Day",
            "isAutomated": True,
        },
    }
    r = requests.post(base + "/order/placeoso", headers=h, json=body, timeout=20)
    try:
        result = r.json()
    except Exception:
        result = {"text": r.text[:400]}
    failed = r.status_code >= 300 or (isinstance(result, dict) and result.get("failureReason"))
    log(
        event="oso_fire",
        status=r.status_code,
        side=side,
        symbol=symbol,
        qty=qty,
        entry=entry,
        stop_px=stop_px,
        tp_px=tp_px,
        stop_pts=stop_pts,
        tp_pts=tp_pts,
        result=result,
        note="oso_signal_20_40",
    )
    return 0 if not failed else 7


if __name__ == "__main__":
    raise SystemExit(main())
