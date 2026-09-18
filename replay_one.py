#!/usr/bin/env python3
"""Replay ONE frozen 7/7 version. Read-only. Does not touch live_77.

Default: tue_thu_restore_arm15.
Prefers seven.jsonl closed 1m (c1) — same bars live used.
Falls back to decision.jsonl mids if seven has no c1 in window.
Last TV rail only if it is WITHIN 10 pts of that 1m (live no_rail_in_watch).
"""
from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

CDT = timezone(timedelta(hours=-5))
ROOT = Path("/home/administrator/.openclaw/workspace/mnq_hybrid/logs")
WATCH, SL, TP, RESET = 10.0, 20.0, 40.0, 20.0
SUPPORT = {"H4L", "H1L", "PDL", "PWL", "SUPPORT"}
RESIST = {"H4H", "H1H", "PDH", "PWH", "RESISTANCE"}
SKIP = ("ONH", "ONL", "EMA", "OPEN")
T0 = datetime(2026, 9, 15, 4, 0, tzinfo=CDT)
T1 = datetime(2026, 9, 17, 11, 30, tzinfo=CDT)
NOTE = "tue_thu_restore_arm15"


def dt_of(o):
    t = o.get("recv_ts") or o.get("ts")
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


def in_sess(dt):
    if dt is None or dt < T0 or dt > T1:
        return False
    if dt.weekday() >= 5:
        return False
    mins = dt.hour * 60 + dt.minute
    return 4 * 60 <= mins < 11 * 60 + 30


def kind(name):
    u = (name or "").upper().strip().split("@")[0]
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


def extreme_dist(px, lo, hi):
    if lo <= px <= hi:
        return 0.0
    if px > hi:
        return px - hi
    return lo - px


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


def last_rail(pois, t):
    best = None
    for dt, k, px in pois:
        if dt <= t:
            best = (k, px, dt)
        else:
            break
    return best


def load_seven_bars():
    """One row per closed 1m from live_77 c1."""
    bars = {}
    p = ROOT / "seven.jsonl"
    if not p.exists():
        return bars
    for ln in p.open():
        if not ln.strip():
            continue
        try:
            o = json.loads(ln)
        except Exception:
            continue
        dt = dt_of(o)
        if not in_sess(dt):
            continue
        c1 = o.get("c1")
        if not isinstance(c1, dict):
            continue
        try:
            h, l, c = float(c1["h"]), float(c1["l"]), float(c1["c"])
        except Exception:
            continue
        t0 = c1.get("t0")
        try:
            t0 = float(t0) if t0 is not None else dt.timestamp()
        except Exception:
            t0 = dt.timestamp()
        d5 = 0.0
        tape = o.get("tape") or {}
        if isinstance(tape, dict) and tape.get("d5") is not None:
            try:
                d5 = float(tape["d5"])
            except Exception:
                pass
        elif o.get("delta_5s") is not None:
            try:
                d5 = float(o["delta_5s"])
            except Exception:
                pass
        poi = o.get("poi")
        bars[t0] = dict(t=dt, h=h, l=l, c=c, d5=d5, poi=poi, n=1)
    return bars


def load_decision_bars():
    raw = defaultdict(lambda: dict(h=None, l=None, o=None, c=None, d5=0.0, t=None, poi=None))
    p = ROOT / "decision.jsonl"
    if not p.exists():
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
        if not in_sess(dt):
            continue
        try:
            mid = float(o["mid"])
        except Exception:
            continue
        tbar = dt.replace(second=0, microsecond=0)
        b = raw[tbar]
        if b["o"] is None:
            b["o"] = mid
        b["c"] = mid
        b["h"] = mid if b["h"] is None else max(b["h"], mid)
        b["l"] = mid if b["l"] is None else min(b["l"], mid)
        b["t"] = tbar
        if o.get("delta_5s") is not None:
            try:
                b["d5"] = float(o["delta_5s"])
            except Exception:
                pass
    return {t.timestamp(): raw[t] for t in raw}


def parse_poi(s):
    if not s or "@" not in str(s):
        return None
    name, _, rest = str(s).partition("@")
    k = kind(name)
    try:
        px = float(rest.split(":")[0])
    except Exception:
        return None
    if k is None:
        return None
    return k, px


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--arm", type=float, default=15.0)
    args = ap.parse_args()
    arm = float(args.arm)
    print(f"VERSION {NOTE}  ARM={arm}  ONLY  9/15-9/17  04:00-11:30")
    print("Live bot not touched. Dual not used.\n")

    pois = load_pois()
    seven = load_seven_bars()
    src = "seven.jsonl c1"
    bars = seven
    if len(bars) < 50:
        bars = load_decision_bars()
        src = "decision.jsonl mids (seven c1 too thin)"
    times = sorted(bars)
    print("src", src, "1m bars", len(times), "poi alerts", len(pois))
    if times:
        b0 = bars[times[0]]
        print("first bar", b0.get("t"), "c", b0.get("c"))
        print("last  bar", bars[times[-1]].get("t"), "c", bars[times[-1]].get("c"))
    if not times:
        print("NO BARS — stop")
        return

    why = Counter()
    spent = {}
    in_trade = None
    visit = None
    goes = []
    eq = peak = maxdd = 0.0

    def close_trade(reason, px, t):
        nonlocal in_trade, eq, peak, maxdd
        tr = in_trade
        sign = 1 if tr["side"] == "Buy" else -1
        pnl = (px - tr["entry"]) * sign
        if reason == "SL":
            pnl = -SL
        if reason == "TP":
            pnl = TP
        tr["hit"] = reason
        tr["pnl"] = round(pnl, 2)
        eq += pnl
        peak = max(peak, eq)
        maxdd = min(maxdd, eq - peak)
        goes.append(tr)
        in_trade = None

    for ts in times:
        b = bars[ts]
        t = b.get("t") or datetime.fromtimestamp(ts, tz=timezone.utc).astimezone(CDT)
        last = b["c"]
        hi, lo = b["h"], b["l"]
        dead = [k for k, px in spent.items() if abs(last - px) >= RESET]
        for k in dead:
            spent.pop(k, None)
        if in_trade:
            sign = 1 if in_trade["side"] == "Buy" else -1
            mfe = (hi - in_trade["entry"]) if sign > 0 else (in_trade["entry"] - lo)
            mae = (in_trade["entry"] - lo) if sign > 0 else (hi - in_trade["entry"])
            in_trade["mfe"] = max(in_trade["mfe"], mfe)
            in_trade["mae"] = max(in_trade["mae"], mae)
            if in_trade["mae"] >= SL:
                close_trade("SL", last, t)
            elif in_trade["mfe"] >= TP:
                close_trade("TP", last, t)
            if in_trade is not None:
                why["in_trade"] += 1
                continue

        parsed = parse_poi(b.get("poi"))
        rail = last_rail(pois, t)
        if parsed:
            k, px = parsed
        elif rail:
            k, px, _ = rail
        else:
            why["no_poi"] += 1
            visit = None
            continue

        dist = extreme_dist(px, lo, hi)
        if dist > WATCH:
            why["no_rail_in_watch"] += 1
            visit = None
            continue

        key = (k, round(px, 2))
        if visit is None or visit.get("key") != key:
            visit = dict(key=key, dead=False, locked=bounce_of(k))
        if visit["dead"]:
            why["visit_dead"] += 1
            continue
        if key in spent:
            why["rail_spent"] += 1
            continue

        bounce = visit["locked"]
        if bounce is None:
            if last > px:
                bounce = True
            elif last < px:
                bounce = False
            else:
                why["at_rail"] += 1
                continue
            visit["locked"] = bounce
        loc = True if last > px else (False if last < px else None)
        if loc is None:
            why["at_rail"] += 1
            continue
        if loc != bounce:
            visit["dead"] = True
            why["through_close"] += 1
            continue
        hit = abs(lo - px) <= WATCH if bounce else abs(hi - px) <= WATCH
        if not hit:
            why["idle_no_hit"] += 1
            continue
        hold = (last >= px) if bounce else (last <= px)
        on_shelf = abs(last - px) <= arm
        lean = (b["d5"] > 0) if bounce else (b["d5"] < 0)
        if not hold:
            visit["dead"] = True
            why["body_gave_rail"] += 1
            continue
        if not on_shelf:
            visit["dead"] = True
            why["off_shelf"] += 1
            continue
        if not lean:
            why["tape_against"] += 1
            continue
        side = "Buy" if bounce else "Sell"
        in_trade = dict(
            when=t, side=side, entry=last, poi=f"{k}@{px:.2f}",
            d5=b["d5"], dist=round(abs(last - px), 2), mae=0.0, mfe=0.0,
        )
        spent[key] = px
        why["FIRE"] += 1

    if in_trade:
        close_trade("OPEN", bars[times[-1]]["c"], bars[times[-1]].get("t"))

    print("\nskips", dict(why.most_common()))
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
    print("\nDONE one version.")


if __name__ == "__main__":
    main()
