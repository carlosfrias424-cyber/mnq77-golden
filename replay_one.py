#!/usr/bin/env python3
"""Replay ONE frozen 7/7 version. Read-only. Does not touch live_77.

Default: tue_thu_restore_arm15  (1m close, last TV alert, hold, ARM 15,
stop 20, TP 40, session 04:00-11:30 CDT, Dual unused).

Data: logs/tv_poi.jsonl + logs/decision.jsonl mids (tape = delta_5s).
This is a log replay, not Databento MBO. One version per run.

Usage:
  python3 replay_one.py
  python3 replay_one.py --arm 15
"""
from __future__ import annotations

import argparse
import json
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

CDT = timezone(timedelta(hours=-5))
ROOT = Path("/home/administrator/.openclaw/workspace/mnq_hybrid/logs")
WATCH = 10.0
SL = 20.0
TP = 40.0
RESET = 20.0
SUPPORT = {"H4L", "H1L", "PDL", "PWL", "SUPPORT"}
RESIST = {"H4H", "H1H", "PDH", "PWH", "RESISTANCE"}
SKIP = ("ONH", "ONL", "EMA", "OPEN")
T0 = datetime(2026, 9, 15, 4, 0, tzinfo=CDT)
T1 = datetime(2026, 9, 17, 11, 30, tzinfo=CDT)
NOTE = "tue_thu_restore_arm15"


def dt_of(o):
    t = o.get("ts") or o.get("recv_ts")
    if isinstance(t, str):
        try:
            return datetime.fromisoformat(t.replace("Z", "+00:00")).astimezone(CDT)
        except Exception:
            return None
    try:
        t = int(float(t))
    except Exception:
        return None
    if t < 1e12:
        t *= 1000
    if t < 1e11:
        return None
    return datetime.fromtimestamp(t / 1000, tz=timezone.utc).astimezone(CDT)


def kind(name):
    u = (name or "").upper().strip()
    if not u or any(u.startswith(x) or u == x for x in SKIP):
        return None
    if u in SUPPORT or u in RESIST or u in ("H4", "H1"):
        return u
    return None


def bounce_of(k):
    if k in SUPPORT:
        return True
    if k in RESIST:
        return False
    return None


def load_pois():
    out = []
    p = ROOT / "tv_poi.jsonl"
    if not p.exists():
        print("MISSING", p)
        return out
    for ln in p.open():
        if not ln.strip():
            continue
        try:
            o = json.loads(ln)
        except Exception:
            continue
        dt = dt_of(o)
        if not dt:
            continue
        k = kind(o.get("poi_name") or o.get("type"))
        try:
            px = float(o.get("price") or 0)
        except Exception:
            continue
        if k is None or px <= 0:
            continue
        out.append((dt, k, px))
    out.sort()
    return out


def load_bars():
    bars = defaultdict(lambda: dict(h=None, l=None, o=None, c=None, d5=0.0, n=0))
    p = ROOT / "decision.jsonl"
    if not p.exists():
        print("MISSING", p)
        return {}
    for ln in p.open():
        if not ln.strip():
            continue
        try:
            o = json.loads(ln)
        except Exception:
            continue
        if o.get("mid") is None:
            continue
        dt = dt_of(o)
        if dt is None or dt < T0 or dt > T1:
            continue
        if dt.weekday() >= 5:
            continue
        mins = dt.hour * 60 + dt.minute
        if mins < 4 * 60 or mins >= 11 * 60 + 30:
            continue
        try:
            mid = float(o["mid"])
        except Exception:
            continue
        tbar = dt.replace(second=0, microsecond=0)
        b = bars[tbar]
        if b["o"] is None:
            b["o"] = mid
        b["c"] = mid
        b["h"] = mid if b["h"] is None else max(b["h"], mid)
        b["l"] = mid if b["l"] is None else min(b["l"], mid)
        if o.get("delta_5s") is not None:
            try:
                b["d5"] = float(o["delta_5s"])
            except Exception:
                pass
        b["n"] += 1
    return dict(bars)


def last_rail(pois, t):
    best = None
    for dt, k, px in pois:
        if dt <= t:
            best = (k, px)
        else:
            break
    return best


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--arm", type=float, default=15.0, help="close shelf pts (default 15)")
    args = ap.parse_args()
    arm = float(args.arm)
    print(f"VERSION {NOTE}  ARM={arm}  ONLY  9/15-9/17  04:00-11:30")
    print("tape = decision.jsonl delta_5s. Dual not used. Live bot not touched.\n")

    pois = load_pois()
    bars = load_bars()
    times = sorted(bars)
    print("1m bars", len(times), "poi alerts", len(pois))
    if not times:
        print("NO BARS — stop")
        return

    spent = {}
    in_trade = None
    visit = None
    goes = []
    eq = 0.0
    peak = 0.0
    maxdd = 0.0

    def close_trade(reason, px, t):
        nonlocal in_trade, eq, peak, maxdd
        tr = in_trade
        sign = 1 if tr["side"] == "Buy" else -1
        pnl = (px - tr["entry"]) * sign
        if reason == "SL":
            pnl = -SL
        if reason == "TP":
            pnl = TP
        tr["exit"] = px
        tr["hit"] = reason
        tr["pnl"] = round(pnl, 2)
        tr["out"] = t
        eq += pnl
        peak = max(peak, eq)
        maxdd = min(maxdd, eq - peak)
        goes.append(tr)
        in_trade = None

    for t in times:
        b = bars[t]
        last = b["c"]
        dead = [k for k, px in spent.items() if abs(last - px) >= RESET]
        for k in dead:
            spent.pop(k, None)
        if in_trade:
            sign = 1 if in_trade["side"] == "Buy" else -1
            hi, lo = b["h"], b["l"]
            mfe = (hi - in_trade["entry"]) if sign > 0 else (in_trade["entry"] - lo)
            mae = (in_trade["entry"] - lo) if sign > 0 else (hi - in_trade["entry"])
            in_trade["mfe"] = max(in_trade["mfe"], mfe)
            in_trade["mae"] = max(in_trade["mae"], mae)
            if in_trade["mae"] >= SL:
                close_trade("SL", last, t)
            elif in_trade["mfe"] >= TP:
                close_trade("TP", last, t)
            if in_trade is not None:
                continue
        rail = last_rail(pois, t)
        if rail is None:
            visit = None
            continue
        k, px = rail
        key = (k, round(px, 2))
        if visit is None or visit.get("key") != key:
            visit = dict(key=key, dead=False, locked=bounce_of(k))
        if in_trade:
            continue
        if visit["dead"]:
            continue
        if key in spent:
            continue
        bounce = visit["locked"]
        if bounce is None:
            if last > px:
                bounce = True
            elif last < px:
                bounce = False
            else:
                continue
            visit["locked"] = bounce
        loc = True if last > px else (False if last < px else None)
        if loc is None:
            continue
        if loc != bounce:
            visit["dead"] = True
            continue
        hit = abs(b["l"] - px) <= WATCH if bounce else abs(b["h"] - px) <= WATCH
        if not hit:
            continue
        hold = (b["c"] >= px) if bounce else (b["c"] <= px)
        on_shelf = abs(b["c"] - px) <= arm
        lean = (b["d5"] > 0) if bounce else (b["d5"] < 0)
        if not hold:
            visit["dead"] = True
            continue
        if not on_shelf:
            visit["dead"] = True
            continue
        if not lean:
            continue
        side = "Buy" if bounce else "Sell"
        in_trade = dict(
            when=t, side=side, entry=b["c"], poi=f"{k}@{px:.2f}",
            d5=b["d5"], dist=round(abs(b["c"] - px), 2), mae=0.0, mfe=0.0,
        )
        spent[key] = px

    if in_trade:
        close_trade("OPEN", bars[times[-1]]["c"], times[-1])

    print(f"\nGOs {len(goes)}  sum {eq:+.1f} pts  maxDD {maxdd:.1f}")
    w = sum(1 for g in goes if g["hit"] == "TP")
    nsl = sum(1 for g in goes if g["hit"] == "SL")
    print(f"TP {w}  SL {nsl}  other {len(goes) - w - nsl}")
    print("\nwhen           side  entry    dist  hit   MAE   MFE   pnl   poi")
    for g in goes:
        print(
            f"{g['when']:%m/%d %H:%M}  {g['side']:4} {g['entry']:8.2f} "
            f"{g['dist']:5.1f} {g['hit']:4} {g['mae']:5.1f} {g['mfe']:5.1f} "
            f"{g['pnl']:+6.1f}  {g['poi']}"
        )
    print("\nDONE one version. Do not mix ARM6 in this run.")


if __name__ == "__main__":
    main()
