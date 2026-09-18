#!/usr/bin/env python3
"""Paper-score Wed/Thu/Fri with PINNED shelf15 rules. Dual unused. No orders.

Rules (same as live_77_shelf15.py on the box):
  closed 1m tagged last-webhook rail
  hold + close within 15 pts + 5m tape with the trade
  skip / off_shelf do NOT mute — next 1m can still fire
  spent only after a paper fill, clear at 20 pts
Book: 5 MNQ x $2, SL 20, TP 40, BE off. Session 04:00–11:30 CT.

Uses 1m bars 7/7 actually logged (c1). If the old bot muted a sit, that
bar may be missing — printed as a caveat.
"""
from __future__ import annotations
import json
from collections import Counter, defaultdict
from datetime import datetime, timezone, timedelta
from pathlib import Path

CDT = timezone(timedelta(hours=-5))
ROOT = Path("/home/administrator/.openclaw/workspace/mnq_hybrid/logs")
SL, TP, QTY, ARM, WATCH = 20.0, 40.0, 5, 15.0, 10.0
DAYS = [
    datetime(2026, 9, 16, tzinfo=CDT).date(),  # Wed
    datetime(2026, 9, 17, tzinfo=CDT).date(),  # Thu
    datetime(2026, 9, 18, tzinfo=CDT).date(),  # Fri
]
TAIL_SEVEN = 80_000_000
TAIL_DEC = 250_000_000


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


def load_tail(name, t0, t1, tail):
    path = ROOT / name
    if not path.exists():
        print("MISSING", name, flush=True)
        return []
    size = path.stat().st_size
    with path.open("rb") as f:
        if size > tail:
            f.seek(size - tail)
            f.readline()
        raw = f.read().decode("utf-8", "replace")
    out = []
    first = last = None
    for ln in raw.splitlines():
        if not ln.strip():
            continue
        try:
            o = json.loads(ln)
        except Exception:
            continue
        dt = dt_of(o)
        if not dt:
            continue
        if first is None:
            first = dt
        last = dt
        if t0 <= dt <= t1:
            out.append((dt, o))
    print(name, "file", size, "tail_span", first, "->", last, "kept", len(out), flush=True)
    return out


def path_hit(side, entry, after):
    mae = mfe = 0.0
    for dt, mid in after:
        if side == "Buy":
            mae = max(mae, entry - mid)
            mfe = max(mfe, mid - entry)
            if mid <= entry - SL:
                return "SL20", dt, mae, mfe
            if mid >= entry + TP:
                return "TP40", dt, mae, mfe
        else:
            mae = max(mae, mid - entry)
            mfe = max(mfe, entry - mid)
            if mid >= entry + SL:
                return "SL20", dt, mae, mfe
            if mid <= entry - TP:
                return "TP40", dt, mae, mfe
    return "OPEN", None, mae, mfe


def sess_ok(dt):
    if dt.weekday() >= 5:
        return False
    mins = dt.hour * 60 + dt.minute
    return (4 * 60) <= mins < (11 * 60 + 30)


def run_day(day, seven, ticks):
    t0 = datetime(day.year, day.month, day.day, 4, 0, tzinfo=CDT)
    t1 = datetime(day.year, day.month, day.day, 11, 30, tzinfo=CDT)
    rows = [(dt, o) for dt, o in seven if t0 <= dt <= t1]
    day_ticks = [(dt, m) for dt, m in ticks if t0 <= dt <= t1]
    if not day_ticks:
        day_ticks = [(dt, float(o["mid"])) for dt, o in rows if o.get("mid") is not None]
        day_ticks.sort()

    by_bar = {}
    bug = Counter()
    live_go = []
    for dt, o in rows:
        r = o.get("reason") or ""
        if r in ("visit_dead", "same_sweep_spent", "sit_muted", "lock_ghost",
                 "already_tried_this_1m"):
            bug[r] += 1
        if o.get("event") in ("paper_fire", "struct40_submit", "struct40_fail") or o.get("submit") or r == "fire":
            live_go.append((dt, o))
        c1 = o.get("c1") or {}
        if c1.get("t0") is None or not o.get("poi"):
            continue
        by_bar[c1["t0"]] = (dt, o)
    bars = [by_bar[k] for k in sorted(by_bar)]

    spent = {}
    in_until = None
    trades, skips = [], []
    for dt, o in bars:
        if not sess_ok(dt):
            continue
        c1 = o.get("c1") or {}
        try:
            c = float(c1.get("c"))
            px = float(o.get("px") or str(o.get("poi")).split("@")[-1])
        except Exception:
            continue
        poi = o.get("poi")
        if in_until and dt < in_until:
            skips.append("in_trade"); continue
        if in_until and dt >= in_until:
            in_until = None
        for k, spx in list(spent.items()):
            if abs(c - spx) >= 20:
                spent.pop(k, None)

        bounce = o.get("side_locked")
        if bounce is None:
            bounce = o.get("sr") == "support" or o.get("tag") == "low"
        side = o.get("side") if o.get("side") in ("Buy", "Sell") else ("Buy" if bounce else "Sell")
        hold = o.get("hold")
        if hold is None:
            hold = (c >= px) if bounce else (c <= px)
        shelf = abs(c - px) <= ARM
        lean = o.get("tape_lean")

        if o.get("reason") == "through_close" or hold is False:
            skips.append("through_or_gave"); continue
        if not shelf:
            skips.append("off_shelf"); continue
        if lean is not True:
            skips.append("tape_against" if lean is False else "tape_unknown"); continue
        if poi in spent:
            skips.append("rail_spent"); continue

        after = [(x, m) for x, m in day_ticks if x >= dt]
        hit, hit_t, mae, mfe = path_hit(side, c, after)
        pts = TP if hit == "TP40" else (-SL if hit == "SL20" else 0.0)
        trades.append(dict(t=dt, side=side, entry=c, poi=poi, hit=hit, hit_t=hit_t,
                           mae=mae, mfe=mfe, pts=pts, live_skip=o.get("skip"),
                           live_reason=o.get("reason")))
        spent[poi] = px
        in_until = hit_t

    return dict(bars=len(bars), bug=dict(bug), live_go=live_go, trades=trades, skips=dict(Counter(skips)))


def show_day(day, res):
    name = day.strftime("%a %Y-%m-%d")
    print("\n========", name, "shelf15 ========" , flush=True)
    print("scored 1m", res["bars"], "old_bug_rows", res["bug"], flush=True)
    print("live box go:", flush=True)
    if not res["live_go"]:
        print("  NONE", flush=True)
    for dt, o in res["live_go"]:
        print("  %s %s skip=%s poi=%s mid=%s hold=%s shelf=%s lean=%s" % (
            dt.strftime("%H:%M:%S"), o.get("event"), o.get("skip"), o.get("poi"),
            o.get("mid"), o.get("hold"), o.get("on_shelf"), o.get("tape_lean")), flush=True)
    pnl = 0.0
    for tr in res["trades"]:
        dol = tr["pts"] * 2 * QTY
        pnl += dol
        ht = tr["hit_t"].strftime("%H:%M:%S") if tr["hit_t"] else "open"
        print("%s %s %.2f %s %s %s MAE=%.1f MFE=%.1f %+.0fpt $%+.0f live=%s/%s" % (
            tr["t"].strftime("%H:%M:%S"), tr["side"], tr["entry"], tr["poi"],
            tr["hit"], ht, tr["mae"], tr["mfe"], tr["pts"], dol,
            tr["live_skip"], tr["live_reason"]), flush=True)
    print("n=%d  TP=%d SL=%d OPEN=%d  $%+.0f  (5 MNQ x $2)" % (
        len(res["trades"]),
        sum(1 for t in res["trades"] if t["hit"] == "TP40"),
        sum(1 for t in res["trades"] if t["hit"] == "SL20"),
        sum(1 for t in res["trades"] if t["hit"] == "OPEN"),
        pnl), flush=True)
    print("skips", res["skips"], flush=True)
    return pnl, len(res["trades"]), sum(1 for t in res["trades"] if t["hit"] == "TP40"), sum(1 for t in res["trades"] if t["hit"] == "SL20")


def main():
    t0 = datetime(2026, 9, 16, 4, 0, tzinfo=CDT)
    t1 = datetime(2026, 9, 18, 11, 30, tzinfo=CDT)
    print("SHELF15 3-day replay", t0, "->", t1, flush=True)
    print("ARM=15  sit-mute=OFF  Dual unused  no orders", flush=True)
    seven = load_tail("seven.jsonl", t0, t1, TAIL_SEVEN)
    dec = load_tail("decision.jsonl", t0, t1, TAIL_DEC)
    ticks = []
    for dt, o in dec:
        m = o.get("mid")
        if m is None:
            continue
        try:
            ticks.append((dt, float(m)))
        except Exception:
            pass
    ticks.sort()
    print("ticks", len(ticks), flush=True)

    tot_pnl = tot_n = tot_tp = tot_sl = 0
    for day in DAYS:
        res = run_day(day, seven, ticks)
        pnl, n, tp, sl = show_day(day, res)
        tot_pnl += pnl; tot_n += n; tot_tp += tp; tot_sl += sl
    print("\n======== 3-DAY TOTAL ========", flush=True)
    print("n=%d  TP=%d SL=%d  $%+.0f  (5 MNQ x $2, SL20/TP40, no BE)" % (
        tot_n, tot_tp, tot_sl, tot_pnl), flush=True)
    print("Caveat: bars the old bot never logged (visit_dead mute) cannot be invented.", flush=True)


if __name__ == "__main__":
    main()
