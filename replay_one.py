#!/usr/bin/env python3
"""Replay ONE frozen 7/7 version. Read-only. Does not touch live_77.

Prints:
  1) skip counts
  2) d5 tape stats (if mostly 0, tape veto is fake)
  3) structure GOs = tag+hold+shelf, tape ignored (diagnostic)
  4) live paper_fire / struct40_submit already in seven.jsonl that week
"""
from __future__ import annotations

import argparse
import json
from collections import Counter
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


def d5_of(o, c1=None):
    for src in (o.get("tape"), o.get("vol"), o):
        if not isinstance(src, dict):
            continue
        for key in ("d5", "delta_5s", "slope", "tape_d5"):
            if src.get(key) is None:
                continue
            try:
                return float(src[key])
            except Exception:
                pass
    return None


def load_seven():
    bars = {}
    fires = []
    p = ROOT / "seven.jsonl"
    if not p.exists():
        print("MISSING", p)
        return bars, fires
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
        ev = o.get("event")
        if ev in ("paper_fire", "struct40_submit") or o.get("submit"):
            fires.append((dt, ev, o))
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
        rec = dict(t=dt, h=h, l=l, c=c, d5=d5_of(o), poi=o.get("poi"),
                   lean=o.get("lean") or o.get("tape_ok"),
                   reason=o.get("reason"), hold=o.get("hold"))
        prev = bars.get(t0)
        if prev is None or (rec["d5"] is not None and prev.get("d5") is None):
            bars[t0] = rec
        else:
            if rec["lean"] is True:
                bars[t0]["lean"] = True
            if rec["hold"] is True:
                bars[t0]["hold"] = True
    return bars, fires


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--arm", type=float, default=15.0)
    args = ap.parse_args()
    arm = float(args.arm)
    print(f"VERSION {NOTE}  ARM={arm}  ONLY  9/15-9/17  04:00-11:30")
    print("Live bot not touched. Dual not used.\n")

    bars, live_fires = load_seven()
    times = sorted(bars)
    print("src seven.jsonl c1  1m bars", len(times), "live_fire_rows", len(live_fires))
    if times:
        print("first", bars[times[0]]["t"], "c", bars[times[0]]["c"])
        print("last ", bars[times[-1]]["t"], "c", bars[times[-1]]["c"])
    if not times:
        print("NO BARS")
        return

    d5s = [bars[t]["d5"] for t in times if bars[t]["d5"] is not None]
    z = sum(1 for x in d5s if x == 0)
    print(f"d5 n={len(d5s)}/{len(times)}  zeros={z}  min={min(d5s) if d5s else None}  max={max(d5s) if d5s else None}")
    lean_true = sum(1 for t in times if bars[t].get("lean") is True)
    print("lean True on bar", lean_true)

    why = Counter()
    struct = []
    spent = {}
    visit = None
    for ts in times:
        b = bars[ts]
        last, hi, lo = b["c"], b["h"], b["l"]
        dead = [k for k, px in list(spent.items()) if abs(last - px) >= RESET]
        for k in dead:
            spent.pop(k, None)
        parsed = parse_poi(b.get("poi"))
        if not parsed:
            why["no_poi"] += 1
            visit = None
            continue
        k, px = parsed
        if extreme_dist(px, lo, hi) > WATCH:
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
        if not hold:
            visit["dead"] = True
            why["body_gave_rail"] += 1
            continue
        if not on_shelf:
            visit["dead"] = True
            why["off_shelf"] += 1
            continue
        d5 = b["d5"]
        lean_log = b.get("lean")
        if d5 is None:
            lean = lean_log is True
            tape_src = "lean_flag" if lean_log is True else "no_tape"
        else:
            lean = (d5 > 0) if bounce else (d5 < 0)
            tape_src = "d5"
        side = "Buy" if bounce else "Sell"
        rec = dict(when=b["t"], side=side, c=last, poi=f"{k}@{px:.2f}",
                   dist=round(abs(last - px), 2), d5=d5, tape_src=tape_src, lean=lean)
        why["structure"] += 1
        struct.append(rec)
        spent[key] = px
        if lean:
            why["FIRE_with_tape"] += 1
        else:
            why["structure_tape_fail"] += 1

    print("\nskips", dict(why.most_common()))
    print(f"\nSTRUCTURE GOs (tag+hold+close<={arm}, tape not required): {len(struct)}")
    print("when           side  close    dist  d5      tape      poi")
    for g in struct:
        print(f"{g['when']:%m/%d %H:%M}  {g['side']:4} {g['c']:8.2f} {g['dist']:5.1f} "
              f"{str(g['d5']):7} {g['tape_src']:10} {g['poi']}")

    print(f"\nLIVE seven fires already logged that window: {len(live_fires)}")
    for dt, ev, o in live_fires:
        snap = o.get("snap") or {}
        print(f"{dt:%m/%d %H:%M:%S} {ev} side={o.get('side') or snap.get('side')} "
              f"mid={o.get('mid')} poi={o.get('poi')} reason={o.get('reason') or snap.get('reason')} "
              f"skip={o.get('skip')}")
    print("\nIf d5 n is 0 and LIVE fires > 0: replay tape is empty. Use live list, not 0 GOs.")
    print("DONE one version.")


if __name__ == "__main__":
    main()
