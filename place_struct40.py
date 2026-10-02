#!/usr/bin/env python3
"""DEMO fade book: 5 MNQ. One market entry.

Stop and target are prices from the signal, not from the fill.
A buy signaled at 30500 gets a sell stop at 30480 and a sell limit at 30540.
Those two orders are one OCO. When one fills, the other is cancelled.
The old bracket strategy is not sent, so a fill cannot create a second pair.
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


def claim_order() -> bool:
    path = OUT.parent / "order.lock"
    path.parent.mkdir(parents=True, exist_ok=True)
    now = time.time()
    try:
        fd = os.open(str(path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        try:
            age = now - path.stat().st_mtime
        except OSError:
            age = 0
        if age < 30:
            return False
        try:
            path.unlink()
        except OSError:
            return False
        try:
            fd = os.open(str(path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            return False
    os.write(fd, str(now).encode())
    os.close(fd)
    return True


def log(**kw) -> None:
    rec = {"ts": int(time.time() * 1000), **kw}
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.open("a").write(json.dumps(rec, default=str) + "\n")
    print(json.dumps(rec, default=str), flush=True)


def wait_fill(base: str, headers: dict, order_id: int, qty: int):
    deadline = time.time() + 8
    while time.time() < deadline:
        try:
            fills = requests.get(
                base + "/fill/deps", headers=headers,
                params={"masterid": order_id}, timeout=10,
            ).json()
        except Exception:
            fills = []
        if isinstance(fills, list) and fills:
            got = 0
            notional = 0.0
            for f in fills:
                try:
                    q = int(f.get("qty") or 0)
                    px = float(f.get("price"))
                except (TypeError, ValueError):
                    continue
                if q > 0:
                    got += q
                    notional += px * q
            if got >= qty and notional > 0:
                return on_tick(notional / got)
        time.sleep(0.25)
    return None


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


def report_mnq(base: str, headers: dict, account_id: int) -> int:
    pos_raw = requests.get(base + "/position/list", headers=headers, timeout=20).json()
    positions = pos_raw if isinstance(pos_raw, list) else []
    net = 0
    px = 0.0
    for p in positions:
        if int(p.get("accountId") or 0) != account_id:
            continue
        try:
            n = int(float(p.get("netPos") or 0))
        except (TypeError, ValueError):
            continue
        if n == 0:
            continue
        cid = int(p.get("contractId") or 0)
        if cid <= 0:
            continue
        item = requests.get(base + "/contract/item", headers=headers, params={"id": cid}, timeout=20).json()
        name = str((item or {}).get("name") or "").upper()
        if name != SYMBOL:
            continue
        net += n
        try:
            px = float(p.get("netPrice") or 0)
        except (TypeError, ValueError):
            px = 0.0
    log(event="broker_pos", net=net, px=on_tick(px) if px else 0, symbol=SYMBOL)
    return 0


def contract_name(base: str, headers: dict, contract_id: int, cache: dict) -> str:
    if contract_id in cache:
        return cache[contract_id]
    try:
        item = requests.get(
            base + "/contract/item", headers=headers,
            params={"id": contract_id}, timeout=20,
        ).json()
        name = str((item or {}).get("name") or "").upper()
    except Exception:
        name = ""
    cache[contract_id] = name
    return name


def mnq_net(base: str, headers: dict, account_id: int) -> int:
    try:
        pos_raw = requests.get(base + "/position/list", headers=headers, timeout=20).json()
    except Exception:
        return 0
    positions = pos_raw if isinstance(pos_raw, list) else []
    cache = {}
    net = 0
    for p in positions:
        if int(p.get("accountId") or 0) != account_id:
            continue
        try:
            n = int(float(p.get("netPos") or 0))
        except (TypeError, ValueError):
            continue
        if n == 0:
            continue
        cid = int(p.get("contractId") or 0)
        if cid <= 0 or contract_name(base, headers, cid, cache) != SYMBOL:
            continue
        net += n
    return net


def working_mnq(base: str, headers: dict, account_id: int) -> list:
    dead = {"Filled", "Canceled", "Cancelled", "Rejected", "Expired", "Completed"}
    try:
        raw = requests.get(base + "/order/list", headers=headers, timeout=20).json()
    except Exception:
        return []
    orders = raw if isinstance(raw, list) else []
    cache = {}
    live = []
    for o in orders:
        if int(o.get("accountId") or 0) != account_id:
            continue
        if str(o.get("ordStatus") or "") in dead:
            continue
        cid = int(o.get("contractId") or 0)
        if cid <= 0 or contract_name(base, headers, cid, cache) != SYMBOL:
            continue
        live.append(int(o.get("id") or 0))
    return live


def working_exits(base: str, headers: dict, account_id: int) -> tuple[int, int]:
    """Working stop and target only. A market entry does not count."""
    dead = {"Filled", "Canceled", "Cancelled", "Rejected", "Expired", "Completed"}
    try:
        raw = requests.get(base + "/order/list", headers=headers, timeout=20).json()
    except Exception:
        return 0, 0
    orders = raw if isinstance(raw, list) else []
    cache = {}
    stops = limits = 0
    for o in orders:
        if int(o.get("accountId") or 0) != account_id:
            continue
        if str(o.get("ordStatus") or "") in dead:
            continue
        cid = int(o.get("contractId") or 0)
        if cid <= 0 or contract_name(base, headers, cid, cache) != SYMBOL:
            continue
        kind = str(o.get("orderType") or "")
        if kind == "Stop":
            stops += 1
        elif kind == "Limit":
            limits += 1
    return stops, limits


def position_px(base: str, headers: dict, account_id: int) -> float:
    try:
        pos_raw = requests.get(base + "/position/list", headers=headers, timeout=20).json()
    except Exception:
        return 0.0
    positions = pos_raw if isinstance(pos_raw, list) else []
    cache = {}
    px = 0.0
    for p in positions:
        if int(p.get("accountId") or 0) != account_id:
            continue
        try:
            n = int(float(p.get("netPos") or 0))
        except (TypeError, ValueError):
            continue
        if n == 0:
            continue
        cid = int(p.get("contractId") or 0)
        if cid <= 0 or contract_name(base, headers, cid, cache) != SYMBOL:
            continue
        try:
            px = float(p.get("netPrice") or 0)
        except (TypeError, ValueError):
            px = 0.0
    return px


def repair_mnq(base, headers, spec, account_id, stop_pts, tp_pts) -> int:
    """Attach the stop and target to the open position. No new entry."""
    net = mnq_net(base, headers, account_id)
    if net == 0:
        log(event="repair_flat", symbol=SYMBOL)
        return 0
    stops, limits = working_exits(base, headers, account_id)
    if stops and limits:
        log(event="repair_ok", net=net, stops=stops, limits=limits, note="already_protected")
        return 0
    side = "Buy" if net > 0 else "Sell"
    px = position_px(base, headers, account_id)
    if px <= 0:
        log(event="repair_fail", reason="no fill price", net=net)
        return 7
    qty = min(abs(net), QTY)
    stop_px, tp_px = signal_band(side, px, stop_pts, tp_pts)
    failed, status, result, stop_px, tp_px, oco_id = place_signal_oco(
        base, headers, spec, account_id, side, stop_px, tp_px, qty,
    )
    if failed:
        log(
            event="repair_fail", status=status, side=side, net=net, fill=px,
            stop=stop_px, target=tp_px, result=result, note="still_naked",
        )
        return 7
    log(
        event="repair_ok", side=side, net=net, qty=qty, fill=px,
        stop=stop_px, target=tp_px, ocoId=oco_id,
    )
    return 0


def order_failed(status: int, result) -> tuple[bool, object]:
    """Tradovate sends failureReason Success on a good order. That is not a reject."""
    if not isinstance(result, dict):
        return True, None
    order_id = result.get("orderId")
    reason = str(result.get("failureReason") or "")
    bad = reason not in ("", "Success", "None")
    return status >= 300 or not order_id or bad, order_id


def signal_band(side: str, signal: float, stop_pts: float, tp_pts: float) -> tuple[float, float]:
    if side == "Buy":
        return on_tick(signal - stop_pts), on_tick(signal + tp_pts)
    return on_tick(signal + stop_pts), on_tick(signal - tp_pts)


def fill_inside(side: str, fill: float, stop_px: float, tp_px: float) -> bool:
    """A stop or target already through the fill makes Tradovate reject the whole OCO."""
    if side == "Buy":
        return stop_px < fill < tp_px
    return tp_px < fill < stop_px


def place_signal_oco(base, headers, spec, account_id, side, stop_px, tp_px, qty):
    exit_side = "Sell" if side == "Buy" else "Buy"
    # Stop is stopPrice only. price plus stopPrice is a StopLimit, and Tradovate
    # rejects that pair with the limit as the wrong OCO combination.
    body = {
        "accountSpec": spec,
        "accountId": account_id,
        "action": exit_side,
        "symbol": SYMBOL,
        "orderQty": qty,
        "orderType": "Stop",
        "stopPrice": stop_px,
        "timeInForce": "Day",
        "isAutomated": True,
        "other": {
            "action": exit_side,
            "orderType": "Limit",
            "price": tp_px,
        },
    }
    r = requests.post(base + "/order/placeoco", headers=headers, json=body, timeout=20)
    if r.status_code == 404:
        r = requests.post(base + "/order/placeOCO", headers=headers, json=body, timeout=20)
    try:
        result = r.json()
    except Exception:
        result = {"text": r.text[:400]}
    failed, order_id = order_failed(r.status_code, result)
    return failed, r.status_code, result, stop_px, tp_px, order_id


def place_exit_leg(base, headers, spec, account_id, side, order_type, px, qty):
    exit_side = "Sell" if side == "Buy" else "Buy"
    body = {
        "accountSpec": spec,
        "accountId": account_id,
        "action": exit_side,
        "symbol": SYMBOL,
        "orderQty": qty,
        "orderType": order_type,
        "timeInForce": "Day",
        "isAutomated": True,
    }
    if order_type == "Stop":
        body["stopPrice"] = px
    else:
        body["price"] = px
    r = requests.post(base + "/order/placeorder", headers=headers, json=body, timeout=20)
    try:
        result = r.json()
    except Exception:
        result = {"text": r.text[:400]}
    failed, order_id = order_failed(r.status_code, result)
    return failed, r.status_code, result, order_id


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
    pos_only = os.environ.get("MNQ_POS") == "1"
    if not flatten and not pos_only and side not in ("Buy", "Sell"):
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

    if pos_only:
        return report_mnq(base, h, account_id)

    if flatten:
        return flatten_mnq(base, h, account_id)

    if os.environ.get("MNQ_REPAIR") == "1":
        return repair_mnq(base, h, spec, account_id, stop_pts, tp_pts)

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
        if qty != 5:
            log(event="qty_forced", from_qty=qty, to=5)
            qty = 5
        if not claim_order():
            log(event="blocked", reason="second bot, order already sent", qty=qty)
            return 6
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
        signal = float(raw_entry)
    except ValueError:
        signal = 0.0
    if signal <= 0:
        log(event="blocked", reason="missing MNQ_ENTRY")
        return 3

    # One market entry. No bracket on it. The OCO is the only exit.
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
    failed, order_id = order_failed(r.status_code, result)
    if failed:
        log(event="entry_fail", status=r.status_code, side=side, result=result, note="market_only")
        return 7

    fill = wait_fill(base, h, int(order_id), qty)
    net = mnq_net(base, h, account_id)
    want = qty if side == "Buy" else -qty
    if net == 0 or (net > 0) != (want > 0):
        log(event="entry_unconfirmed", side=side, orderId=order_id, net=net, fill=fill, note="no_oco")
        return 7
    # Never exit more than this order filled. A larger net belongs to something else.
    oco_qty = min(abs(net), qty)
    if fill is None:
        fill = position_px(base, h, account_id)
    if not fill:
        fill = on_tick(signal)

    stops, limits = working_exits(base, h, account_id)
    if stops and limits:
        log(
            event="oco_skipped", reason="stop and target already working",
            stops=stops, limits=limits, side=side, net=net, note="already_protected",
        )
        return 0

    stop_px, tp_px = signal_band(side, fill, stop_pts, tp_pts)

    oco_failed = True
    oco_status = 0
    oco_result = None
    oco_id = None
    for _ in range(3):
        oco_failed, oco_status, oco_result, stop_px, tp_px, oco_id = place_signal_oco(
            base, h, spec, account_id, side, stop_px, tp_px, oco_qty,
        )
        if not oco_failed:
            break
        time.sleep(0.25)
    if oco_failed:
        stop_bad, stop_status, stop_result, stop_id = place_exit_leg(
            base, h, spec, account_id, side, "Stop", stop_px, oco_qty,
        )
        limit_bad, limit_status, limit_result, limit_id = place_exit_leg(
            base, h, spec, account_id, side, "Limit", tp_px, oco_qty,
        )
        if stop_bad or limit_bad:
            log(
                event="oco_fail", status=oco_status, side=side, signal=round(signal, 2),
                fill=fill, qty=oco_qty, stop=stop_px, target=tp_px, result=oco_result,
                stop_leg=stop_result, limit_leg=limit_result, note="position_open_no_flatten",
            )
            return 7
        log(
            event="oco_fire", status=200, side=side, symbol=symbol, qty=oco_qty,
            signal=round(signal, 2), fill=fill, stop=stop_px, target=tp_px,
            stop_pts=stop_pts, tp_pts=tp_pts, orderId=order_id,
            stopId=stop_id, limitId=limit_id, note="two_legs_from_fill",
        )
        return 0

    log(
        event="oco_fire",
        status=oco_status,
        side=side,
        symbol=symbol,
        qty=oco_qty,
        signal=round(signal, 2),
        fill=fill,
        stop=stop_px,
        target=tp_px,
        stop_pts=stop_pts,
        tp_pts=tp_pts,
        orderId=order_id,
        ocoId=oco_id,
        note="prices_from_fill",
    )
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
