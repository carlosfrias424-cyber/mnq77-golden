#!/usr/bin/env python3
"""Thursday 9/17 ONLY. ARM 15 already in the live sends.
Sniper: last 15 seconds of delta_5s must lean WITH the side.
Read-only. Does not touch live_77. Dual unused.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

CDT = timezone(timedelta(hours=-5))
ROOT = Path("/home/administrator/.openclaw/workspace/mnq_hybrid/logs")
T0 = datetime(2026, 9, 17, 4, 0, tzinfo=CDT)
T1 = datetime(2026, 9, 17, 11, 30, tzinfo=CDT)
SL, TP = 20.0, 40.0
WIN = 15.0

# Live SIM book that day (same 8 you already lived).
KNOWN = {
    "04:31": ("Buy", 29554.25, 29552.50, "TP", 40.0),
    "06:01": ("Sell", 29595.75, 29600.00, "SL", -20.0),
    "07:38": ("Buy", 29727.25, 29714.25, "SL", -20.0),
    "08:39": ("Buy", 29615.25, 29600.00, "TP", 40.0),
    "08:48": ("Sell", 29660.25, 29670.00, "TP", 40.0),
    "09:17": ("Sell", 29712.00, 29714.25, "SL", -20.0),
    "09:30": ("Sell", 29712.25, 29714.25, "SL", -20.0),
    "10:11": ("Buy", 29677.00, 29670.00, "TP", 40.0),
}


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


def load_d5():
    out = []
    p = ROOT / "decision.jsonl"
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
        if o.get("delta_5s") is None:
            continue
        dt = dt_of(o)
        if dt is None or dt < T0 - timedelta(minutes=5) or dt > T1:
            continue
        try:
            d5 = float(o["delta_5s"])
        except Exception:
            continue
        out.append((dt, d5))
    out.sort()
    return out


def window(samples, t):
    lo = t - timedelta(seconds=WIN)
    return [d5 for ts, d5 in samples if lo <= ts <= t]


def sniper(side, xs):
    if not xs:
        return "no_tape", False, None
    last = xs[-1]
    mean = sum(xs) / len(xs)
    if side == "Buy":
        with_last = last > 0
        with_mean = mean > 0
        all_with = all(x > 0 for x in xs)
    else:
        with_last = last < 0
        with_mean = mean < 0
        all_with = all(x < 0 for x in xs)
    # Strict 15/5s: every sample in the 15s WITH.
    keep = all_with
    tag = "KEEP" if keep else "DROP"
    return tag, keep, dict(n=len(xs), last=round(last, 1), mean=round(mean, 1),
                           all_with=all_with, last_with=with_last, mean_with=with_mean)


def main():
    print("VERSION thu_arm15_plus_sniper_15s_d5")
    print("Thursday 9/17 04:00-11:30 only. Live 8 fills. Dual off. Live bot not touched.\n")
    samples = load_d5()
    print("delta_5s samples", len(samples))
    if not samples:
        print("NO TAPE — stop")
        return

    print("when  side  dist  hit   pnl   sniper  n  last   mean   all  lastW meanW")
    keep_pnl = drop_pnl = 0.0
    k_w = k_l = d_w = d_l = 0
    for hhmm, (side, entry, rail, hit, pnl) in KNOWN.items():
        h, m = map(int, hhmm.split(":"))
        t = datetime(2026, 9, 17, h, m, tzinfo=CDT)
        xs = window(samples, t)
        tag, keep, st = sniper(side, xs)
        dist = abs(entry - rail)
        if keep:
            keep_pnl += pnl
            if pnl > 0:
                k_w += 1
            else:
                k_l += 1
        else:
            drop_pnl += pnl
            if pnl > 0:
                d_w += 1
            else:
                d_l += 1
        st = st or dict(n=0, last=None, mean=None, all_with=None, last_with=None, mean_with=None)
        print(
            f"{hhmm}  {side:4} {dist:4.1f}  {hit:2} {pnl:+6.1f}  {tag:5}  "
            f"{st['n']:3} {str(st['last']):6} {str(st['mean']):7}  "
            f"{st['all_with']} {st['last_with']} {st['mean_with']}"
        )

    base = 80.0
    print("\nBASE ARM15 all 8:  4W/4L  WR 50%  PF 2.00  PnL +80")
    print(f"SNIPER KEEP: {k_w}W/{k_l}L  PnL {keep_pnl:+.0f}   (dropped {d_w}W/{d_l}L worth {drop_pnl:+.0f})")
    print(f"If sniper had been on: Thursday PnL {keep_pnl:+.0f} vs +80")
    if k_l:
        pf = (k_w * 40.0) / (k_l * 20.0)
        wr = 100.0 * k_w / (k_w + k_l)
        print(f"KEEP WR {wr:.0f}%  PF {pf:.2f}  RR 2:1")
    elif k_w:
        print("KEEP WR 100%  PF inf  RR 2:1")
    else:
        print("KEEP none")
    print("DONE one version. Strict = every 5s sample in last 15s WITH the side.")


if __name__ == "__main__":
    main()
