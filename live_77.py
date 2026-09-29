#!/usr/bin/env python3
"""Fade. Demo only. The +961 card.

10:00–16:00 CT. Stop 20, target 40, 5 MNQZ6.
Hold bar trades the rail, closes within 15 of it, and closes on the hold side.
Sellers larger than buyers on a long. Buyers larger than sellers on a short.
The next 1-minute bar lifts off the rail. That close is the entry, even if it is more than 15 away.
Databento B is buying, A is selling. Delta is buy size minus sell size.
One position. A stop or a target ends it. If neither has traded by 16:00, flatten.
The rail goes quiet when the trade ends, until a later bar closes 20 points away.
The next trade can be the bar after the exit. No 120-second lock.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import urllib.request
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path("/home/administrator/.openclaw/workspace/mnq_hybrid")
POI = ROOT / "logs/tv_poi.jsonl"
OUT = ROOT / "logs/seven.jsonl"
STATE = ROOT / "logs/fade_state.json"
PY = ROOT / ".venv/bin/python"
SUBMIT = ROOT / "apps/tradovate/place_struct40.py"
sys.path.insert(0, str(ROOT / "apps" / "watcher7"))

TZ = ZoneInfo("America/Chicago")
SYMBOL = "MNQZ6"
QTY, STOP, TP, NEAR = 5, 20.0, 40.0, 15.0
SESSION_START, SESSION_END = 10 * 60, 16 * 60
SKIP = ("ONH", "ONL", "EMA", "OPEN")
SUPPORT = {"H4L", "H1L", "PDL", "PWL", "SUPPORT"}
RESIST = {"H4H", "H1H", "PDH", "PWH", "RESISTANCE"}
BARE = {"H4", "H1"}
NOTE = "fade_hold15_10_16"


def envload():
    p = ROOT / ".env"
    if not p.exists():
        return
    for raw in p.read_text().splitlines():
        if not raw.strip() or raw.startswith("#") or "=" not in raw:
            continue
        k, _, v = raw.partition("=")
        k, v = k.strip(), v.strip().strip('"').strip("'")
        if k and k not in os.environ:
            os.environ[k] = v


def emit(**kw):
    OUT.parent.mkdir(parents=True, exist_ok=True)
    rec = {"ts": int(time.time() * 1000), **kw}
    OUT.open("a").write(json.dumps(rec, default=str) + "\n")
    print(json.dumps(rec, default=str), flush=True)



def discord(text):
    url = (os.environ.get("DISCORD_WEBHOOK_URL") or "").strip()
    if not url:
        return False
    body = json.dumps({"content": text[:1900]}).encode()
    try:
        req = urllib.request.Request(
            url, data=body, method="POST",
            headers={"Content-Type": "application/json", "User-Agent": "maximus-fade"},
        )
        with urllib.request.urlopen(req, timeout=8) as resp:
            resp.read()
        return True
    except Exception as e:
        emit(event="discord_fail", err=str(e)[:200])
        return False


def discord_in(side, name, rail, entry):
    discord(
        f"IN  {side}  {QTY} {SYMBOL}\n"
        f"{name} @ {rail:.2f}\n"
        f"fill {entry:.2f}\n"
        f"{datetime.now(TZ).strftime('%H:%M')} CT"
    )


def discord_out(side, name, rail, entry, how, px=None):
    if how == "stop":
        label, pts = "stop", -STOP
    elif how == "tp":
        label, pts = "target", TP
    elif how == "eod":
        label = "flat 16:00"
        pts = None if px is None else ((px - entry) if side == "Buy" else (entry - px))
    else:
        label = how
        pts = None if px is None else ((px - entry) if side == "Buy" else (entry - px))
    extra = "" if pts is None else f"  {pts:+.1f}"
    discord(
        f"OUT  {side}  {label}{extra}\n"
        f"{name} @ {rail:.2f}\n"
        f"entry {entry:.2f}\n"
        f"{datetime.now(TZ).strftime('%H:%M')} CT"
    )



def _state():
    if not STATE.exists():
        return {}
    try:
        o = json.loads(STATE.read_text())
    except Exception:
        return {}
    return o if isinstance(o, dict) else {}


def eod_sent(day):
    return str(_state().get("eod") or "") == day


def mark_eod(day):
    o = _state()
    o["eod"] = day
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(o))


DEMO = "https://demo.tradovateapi.com/v1"
HERMES_DAY = "2026-09-28"  # card already sent: 5 trades, W 3, L 2, +80


def _get(url, headers):
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=20) as resp:
        return json.loads(resp.read().decode())


def _post(url, payload, headers=None):
    data = json.dumps(payload).encode()
    h = {"Content-Type": "application/json", "Accept": "application/json"}
    if headers:
        h.update(headers)
    req = urllib.request.Request(url, data=data, headers=h, method="POST")
    with urllib.request.urlopen(req, timeout=20) as resp:
        return json.loads(resp.read().decode())


def tv_session():
    base = os.environ.get("TRADOVATE_BASE", DEMO).rstrip("/")
    if "live.tradovateapi.com" in base:
        raise RuntimeError("live url")
    name = os.environ.get("TRADOVATE_NAME") or ""
    password = os.environ.get("TRADOVATE_PASSWORD") or ""
    if not name or not password:
        raise RuntimeError("missing creds")
    body = {
        "name": name,
        "password": password,
        "appId": os.environ.get("TRADOVATE_APP_ID", "MNQHybrid"),
        "appVersion": os.environ.get("TRADOVATE_APP_VERSION", "0.1"),
        "deviceId": os.environ.get("TRADOVATE_DEVICE_ID") or "maximus-eod",
    }
    if os.environ.get("TRADOVATE_CID"):
        try:
            body["cid"] = int(os.environ["TRADOVATE_CID"])
        except ValueError:
            body["cid"] = os.environ["TRADOVATE_CID"]
    if os.environ.get("TRADOVATE_SEC"):
        body["sec"] = os.environ["TRADOVATE_SEC"]
    auth = _post(base + "/auth/accesstokenrequest", body)
    token = auth.get("accessToken")
    if not token:
        raise RuntimeError(auth.get("errorText") or "auth_fail")
    headers = {"Authorization": "Bearer " + token, "Accept": "application/json"}
    account_id = os.environ.get("TRADOVATE_ACCOUNT_ID")
    if account_id:
        account_id = int(account_id)
    else:
        acc = _get(base + "/account/list", headers)
        pick = None
        if isinstance(acc, list):
            for a in acc:
                if "DEMO" in str(a.get("name") or "").upper():
                    pick = a
                    break
            if pick is None and acc:
                pick = acc[0]
        if not pick:
            raise RuntimeError("no account")
        account_id = int(pick["id"])
    found = _get(base + "/contract/find?name=" + SYMBOL, headers)
    contract_id = int((found or {}).get("id") or 0)
    if contract_id <= 0:
        raise RuntimeError("no contract")
    return base, headers, account_id, contract_id


def fill_when(raw):
    if not raw:
        return None
    text = str(raw).replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=ZoneInfo("UTC"))
    return dt.astimezone(TZ)


def pair_fills(fills):
    """One trade each time the account goes flat. Points are the real fill prices."""
    net = 0
    avg = 0.0
    side = None
    opened = None
    exit_notional = 0.0
    exit_qty = 0
    out = []

    def emit_trade():
        nonlocal exit_notional, exit_qty, avg, side, opened
        if exit_qty <= 0 or side is None:
            return
        exit_px = exit_notional / exit_qty
        pts = (exit_px - avg) if side == "Buy" else (avg - exit_px)
        out.append({
            "when": opened.strftime("%H:%M") if opened else "",
            "side": side,
            "qty": exit_qty,
            "entry": avg,
            "exit": exit_px,
            "pts": round(pts, 1),
        })
        exit_notional = 0.0
        exit_qty = 0

    for f in fills:
        action = f.get("action")
        if action not in ("Buy", "Sell"):
            continue
        try:
            qty = int(f.get("qty") or 0)
            px = float(f.get("price"))
        except (TypeError, ValueError):
            continue
        if qty <= 0:
            continue
        signed = qty if action == "Buy" else -qty
        when = fill_when(f.get("timestamp"))
        if net == 0 or (net > 0 and signed > 0) or (net < 0 and signed < 0):
            new = abs(net) + abs(signed)
            avg = (avg * abs(net) + px * abs(signed)) / new
            if net == 0:
                side = action
                opened = when
            net += signed
            continue
        prev = net
        closing = min(abs(signed), abs(prev))
        exit_notional += px * closing
        exit_qty += closing
        net = prev + signed
        crossed = net == 0 or (prev > 0 > net) or (prev < 0 < net)
        if not crossed:
            continue
        emit_trade()
        if net == 0:
            avg = 0.0
            side = None
            opened = None
        else:
            side = "Buy" if net > 0 else "Sell"
            avg = px
            opened = when
    return out


def day_trades(day):
    base, headers, account_id, contract_id = tv_session()
    raw = _get(base + "/fill/list", headers)
    if not isinstance(raw, list):
        raise RuntimeError("fill list")
    chosen = []
    for f in raw:
        if int(f.get("contractId") or 0) != contract_id:
            continue
        if f.get("accountId") not in (None, account_id, str(account_id)):
            continue
        when = fill_when(f.get("timestamp"))
        if when is None or when.date() != day:
            continue
        minute = when.hour * 60 + when.minute
        if minute < SESSION_START or minute > SESSION_END + 30:
            continue
        chosen.append(f)
    chosen.sort(key=lambda f: str(f.get("timestamp") or ""))
    return pair_fills(chosen)


def eod_text(day, trades):
    pts = round(sum(t["pts"] for t in trades), 1)
    dol = int(round(sum(t["pts"] * t["qty"] * 2 for t in trades)))
    w = sum(1 for t in trades if t["pts"] > 0)
    l = sum(1 for t in trades if t["pts"] < 0)
    lines = [
        f"EOD  {day.strftime('%m-%d')}",
        f"{len(trades)} trade{'s' if len(trades) != 1 else ''}  W {w}  L {l}",
        f"{pts:+.1f} pts  ${dol:+}",
    ]
    for t in trades:
        lines.append(
            f"{t['when']} {t['side']} {t['qty']} @{t['entry']:.2f} -> {t['exit']:.2f}  {t['pts']:+.1f}"
        )
    if not trades:
        lines.append("no fills")
    return "\n".join(lines)


def maybe_eod(now=None):
    now = now or datetime.now(TZ)
    if now.weekday() >= 5:
        return
    if now.hour * 60 + now.minute < SESSION_END:
        return
    day = now.date()
    key = day.isoformat()
    if eod_sent(key):
        return
    if key == HERMES_DAY:
        mark_eod(key)
        return
    try:
        trades = day_trades(day)
    except Exception as e:
        emit(event="eod_pnl_fail", day=key, err=str(e)[:200], note=NOTE)
        return
    text = eod_text(day, trades)
    if not discord(text):
        return
    pts = round(sum(t["pts"] for t in trades), 1)
    emit(event="eod_pnl", day=key, n=len(trades), pts=pts, note=NOTE, src="tradovate_fills")
    mark_eod(key)


def sr_kind(name):
    u = (name or "").upper().strip()
    if not u or any(u.startswith(x) or u == x for x in SKIP):
        return None
    if u in SUPPORT or u in RESIST or u in BARE:
        return u
    return None


def in_session(ts):
    dt = datetime.fromtimestamp(ts, TZ)
    if dt.weekday() >= 5:
        return False
    m = dt.hour * 60 + dt.minute
    return SESSION_START <= m < SESSION_END


def rails_asof(t):
    active = {}
    if not POI.exists():
        return active
    for ln in POI.read_text().splitlines():
        if not ln.strip():
            continue
        try:
            o = json.loads(ln)
        except Exception:
            continue
        kind = sr_kind(o.get("poi_name") or o.get("type") or "")
        if kind is None:
            continue
        try:
            px = round(float(o.get("price") or 0), 2)
            recv = float(o.get("recv_ts") or o.get("ts") or 0)
        except Exception:
            continue
        if px <= 0 or recv <= 0:
            continue
        if recv > 1e12:
            recv /= 1000.0
        if recv <= t:
            active[kind] = px
    return active


def send_book(side, name, rail, entry, hold, lift):
    env = os.environ.copy()
    env.update({
        "MNQ_SIDE": side,
        "MNQ_QTY": str(QTY),
        "TRADOVATE_ENV": "demo",
        "TRADOVATE_SYMBOL": SYMBOL,
        "MNQ_POI_NAME": str(name),
        "MNQ_POI_PX": str(rail),
        "MNQ_MID": str(entry),
        "MNQ_ENTRY": str(round(entry, 2)),
        "MNQ_STOP_PTS": str(STOP),
        "MNQ_T40": str(TP),
    })
    env.pop("MNQ_FLATTEN", None)
    r = subprocess.run(
        [str(PY), str(SUBMIT)], cwd=str(ROOT), env=env,
        capture_output=True, text=True, timeout=60,
    )
    return r.returncode, (r.stdout or "")[-300:]


def send_flat():
    env = os.environ.copy()
    env.update({
        "MNQ_FLATTEN": "1",
        "TRADOVATE_ENV": "demo",
        "TRADOVATE_SYMBOL": SYMBOL,
    })
    env.pop("MNQ_SIDE", None)
    r = subprocess.run(
        [str(PY), str(SUBMIT)], cwd=str(ROOT), env=env,
        capture_output=True, text=True, timeout=60,
    )
    return r.returncode, (r.stdout or "")[-300:]


def load_state():
    if not STATE.exists():
        return None, {}
    try:
        o = json.loads(STATE.read_text())
    except Exception:
        return None, {}
    quiet = {}
    for k, v in (o.get("quiet") or {}).items():
        try:
            quiet[str(k)] = float(v)
        except (TypeError, ValueError):
            continue
    raw = o.get("pos")
    if not raw:
        return None, quiet
    try:
        pos = (
            raw["side"], float(raw["entry"]), str(raw["name"]),
            float(raw["rail"]), float(raw["ts"]),
        )
    except (KeyError, TypeError, ValueError):
        return None, quiet
    if pos[0] not in ("Buy", "Sell"):
        return None, quiet
    return pos, quiet


def save_state(pos, quiet):
    rec = _state()
    rec["quiet"] = quiet
    rec["pos"] = None
    if pos is not None:
        side, entry, name, rail, ts = pos
        rec["pos"] = {
            "side": side, "entry": entry, "name": name, "rail": rail, "ts": ts,
        }
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(rec))


def session_over(pos, close_ts):
    opened = datetime.fromtimestamp(pos[4], TZ)
    close_dt = datetime.fromtimestamp(close_ts, TZ)
    if close_dt.date() > opened.date():
        return True
    if close_dt.date() < opened.date():
        return False
    return close_dt.hour * 60 + close_dt.minute >= SESSION_END


def pick(hold, lift, active):
    best = None
    for name, rail in active.items():
        if not (hold.l <= rail <= hold.h):
            continue
        dist = abs(hold.c - rail)
        if dist > NEAR:
            continue
        if hold.c > rail and hold.delta < 0:
            if not (lift.delta > 0 and lift.c > hold.c and lift.c > rail):
                continue
            side = "Buy"
        elif hold.c < rail and hold.delta > 0:
            if not (lift.delta < 0 and lift.c < hold.c and lift.c < rail):
                continue
            side = "Sell"
        else:
            continue
        if best is None or dist < best[0]:
            best = (dist, side, name, rail)
    return best


def finish(pos, quiet, how):
    quiet[pos[2]] = pos[3]
    save_state(None, quiet)
    return None


def main():
    envload()
    os.environ["TRADOVATE_SYMBOL"] = SYMBOL
    os.environ["TRADOVATE_ENV"] = "demo"
    try:
        from mnq_vol import start_from_env
        vol = start_from_env()
    except Exception as e:
        emit(event="fatal", err=str(e)[:300], note=NOTE)
        return
    pos, quiet = load_state()
    emit(
        event="seven_start", fire=True, note=NOTE, symbol=SYMBOL,
        book={"qty": QTY, "stop": STOP, "tp": TP, "symbol": SYMBOL},
        session_start="10:00", session_end="16:00", near=NEAR,
        tape="hold_then_lift", vol_src="databento_trades",
        exit="flat_1600", quiet="bar_close_20", relock="next_bar",
        open=None if pos is None else pos[0],
    )
    if pos is not None and session_over(pos, time.time()):
        rc, out = send_flat()
        emit(event="eod_flat", how="startup", rc=rc, poi=f"{pos[2]}@{pos[3]:.2f}", out=out, note=NOTE)
        if rc == 0:
            discord_out(pos[0], pos[2], pos[3], pos[1], "startup")
            pos = finish(pos, quiet, "startup")
    maybe_eod()
    prev = None
    seen = None
    last_hb = 0.0
    while True:
        now = time.time()
        if now - last_hb > 60:
            emit(
                event="heartbeat", note=NOTE, session=in_session(now),
                quiet=len(quiet), open=None if pos is None else pos[0],
            )
            last_hb = now
        bar = vol.last_closed_1()
        if bar is None or bar.t0 == seen:
            time.sleep(0.5)
            continue
        hold, prev, seen = prev, bar, bar.t0
        if pos is not None:
            if bar.t0 + 60 <= pos[4]:
                continue
            side, entry, name, rail, _ts = pos
            stop_px = entry - STOP if side == "Buy" else entry + STOP
            tp_px = entry + TP if side == "Buy" else entry - TP
            if side == "Buy":
                hit_stop = bar.l <= stop_px
                hit_tp = bar.h >= tp_px
            else:
                hit_stop = bar.h >= stop_px
                hit_tp = bar.l <= tp_px
            if hit_stop or hit_tp:
                how = "stop" if hit_stop else "tp"
                emit(
                    event="trade_done", how=how, side=side,
                    poi=f"{name}@{rail:.2f}", mid=bar.c, note=NOTE,
                )
                discord_out(side, name, rail, entry, how)
                pos = finish(pos, quiet, how)
            elif session_over(pos, bar.t0 + 60):
                rc, out = send_flat()
                emit(
                    event="eod_flat", how="16:00", rc=rc, side=side,
                    poi=f"{name}@{rail:.2f}", mid=bar.c, out=out, note=NOTE,
                )
                if rc == 0:
                    discord_out(side, name, rail, entry, "eod", bar.c)
                    pos = finish(pos, quiet, "eod")
            continue
        if hold is None or bar.t0 - hold.t0 != 60:
            continue
        for name, rail in list(quiet.items()):
            if abs(bar.c - rail) >= 20:
                del quiet[name]
                save_state(pos, quiet)
        if not in_session(bar.t0 + 60):
            maybe_eod()
            continue
        active = rails_asof(hold.t0 + 60)
        for name in quiet:
            active.pop(name, None)
        hit = pick(hold, bar, active)
        if hit is None:
            continue
        dist, side, name, rail = hit
        rc, out = send_book(side, name, rail, bar.c, hold, bar)
        emit(
            event="struct40_submit" if rc == 0 else "struct40_fail",
            submit=rc == 0, rc=rc, side=side, poi=f"{name}@{rail:.2f}",
            mid=bar.c, dist=round(dist, 2),
            hold_delta=hold.delta, lift_delta=bar.delta,
            hold_c=hold.c, lift_c=bar.c, note=NOTE, out=out,
        )
        if rc == 0:
            pos = (side, bar.c, name, rail, time.time())
            save_state(pos, quiet)
            discord_in(side, name, rail, bar.c)


if __name__ == "__main__":
    main()
