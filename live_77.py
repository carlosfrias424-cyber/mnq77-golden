#!/usr/bin/env python3
"""Fade. Demo only.

10:00–16:00 CT. Stop 20, target 40, 5 MNQZ6.
Hold bar trades the rail and closes on the hold side.
Sellers larger than buyers on a long. Buyers larger than sellers on a short.
The next 1-minute bar lifts off the rail, and that close is within 15.
Databento B is buying, A is selling. Delta is buy size minus sell size.
One position. A rail stays quiet until price is 20 points away.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path("/home/administrator/.openclaw/workspace/mnq_hybrid")
POI = ROOT / "logs/tv_poi.jsonl"
OUT = ROOT / "logs/seven.jsonl"
LOCK = ROOT / "logs/submit.lock"
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
NOTE = "fade_hold_lift_10_16"


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
    LOCK.write_text(json.dumps({
        "side": side, "poi": f"{name}@{rail:.2f}", "px": rail, "qty": QTY,
        "entry": round(entry, 2), "stop_pts": STOP, "tp": TP,
        "symbol": SYMBOL, "ts": time.time(),
    }))
    r = subprocess.run(
        [str(PY), str(SUBMIT)], cwd=str(ROOT), env=env,
        capture_output=True, text=True, timeout=60,
    )
    if r.returncode != 0:
        try:
            LOCK.unlink()
        except OSError:
            pass
    return r.returncode, (r.stdout or "")[-300:]


def pick(hold, lift, active):
    best = None
    for name, rail in active.items():
        if not (hold.l <= rail <= hold.h):
            continue
        if name in SUPPORT or (name in BARE and hold.c > rail):
            side = "Buy"
        elif name in RESIST or (name in BARE and hold.c < rail):
            side = "Sell"
        else:
            continue
        if side == "Buy":
            if not (hold.c > rail and hold.delta < 0):
                continue
            if not (lift.delta > 0 and lift.c > hold.c and lift.c > rail):
                continue
        else:
            if not (hold.c < rail and hold.delta > 0):
                continue
            if not (lift.delta < 0 and lift.c < hold.c and lift.c < rail):
                continue
        dist = abs(lift.c - rail)
        if dist > NEAR:
            continue
        if best is None or dist < best[0]:
            best = (dist, side, name, rail)
    return best


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
    emit(
        event="seven_start", fire=True, note=NOTE, symbol=SYMBOL,
        book={"qty": QTY, "stop": STOP, "tp": TP, "symbol": SYMBOL},
        session_start="10:00", session_end="16:00", near=NEAR,
        tape="hold_then_lift", vol_src="databento_trades",
    )
    prev = None
    seen = None
    quiet = {}
    last_hb = 0.0
    while True:
        now = time.time()
        if now - last_hb > 60:
            emit(event="heartbeat", note=NOTE, session=in_session(now), quiet=len(quiet))
            last_hb = now
        bar = vol.last_closed_1()
        if bar is None or bar.t0 == seen:
            time.sleep(0.5)
            continue
        hold, prev, seen = prev, bar, bar.t0
        px = vol.last_px()
        if px is not None:
            for name, rail in list(quiet.items()):
                if abs(px - rail) >= 20:
                    del quiet[name]
        if hold is None or bar.t0 - hold.t0 != 60:
            continue
        if not in_session(bar.t0 + 60):
            continue
        if LOCK.exists() and now - LOCK.stat().st_mtime < 120:
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
            quiet[name] = rail


if __name__ == "__main__":
    main()
